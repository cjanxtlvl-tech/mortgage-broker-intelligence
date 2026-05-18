from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger(__name__)

GLEIF_BASE_URL = "https://api.gleif.org/api/v1/lei-records"
DEFAULT_CACHE_PATH = Path("data/processed/lei_cache.json")
DEFAULT_TIMEOUT_SECONDS = 15
DEFAULT_SLEEP_SECONDS = 0.2
DEFAULT_MAX_LOOKUPS = 250

_MEM_CACHE: dict[str, dict[str, str]] = {}
_CACHE_LOADED = False
_CACHE_LOADED_FOR: Path | None = None
_ACTIVE_CACHE_PATH: Path = DEFAULT_CACHE_PATH
_SESSION: requests.Session | None = None


def _normalize_lei(lei: Any) -> str:
    return str(lei or "").strip().upper()


def _blank_record() -> dict[str, str]:
    return {
        "gleif_legal_name": "",
        "gleif_entity_status": "",
        "gleif_registration_status": "",
        "gleif_jurisdiction": "",
        "gleif_legal_address": "",
        "gleif_headquarters_address": "",
        "gleif_last_update": "",
        "gleif_next_renewal_date": "",
        "gleif_managing_lou": "",
    }


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_stringify(item) for item in value]
        return ", ".join(part for part in parts if part)
    if isinstance(value, dict):
        # Prefer common address keys but fall back to any meaningful values.
        ordered_keys = [
            "addressLines",
            "addressLine1",
            "addressLine2",
            "city",
            "region",
            "postalCode",
            "country",
        ]
        parts: list[str] = []
        for key in ordered_keys:
            if key in value:
                extracted = _stringify(value.get(key))
                if extracted:
                    parts.append(extracted)
        if parts:
            return ", ".join(parts)
        fallback_parts = []
        for item in value.values():
            extracted = _stringify(item)
            if extracted:
                fallback_parts.append(extracted)
        return ", ".join(fallback_parts)
    return str(value).strip()


def _load_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        session = requests.Session()
        retry = Retry(
            total=4,
            connect=4,
            read=4,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _SESSION = session
    return _SESSION


def _load_cache(cache_path: Path) -> dict[str, dict[str, str]]:
    global _CACHE_LOADED, _CACHE_LOADED_FOR, _MEM_CACHE
    normalized_cache_path = Path(cache_path)
    if _CACHE_LOADED and _CACHE_LOADED_FOR == normalized_cache_path:
        return _MEM_CACHE

    if normalized_cache_path.exists():
        try:
            with normalized_cache_path.open("r", encoding="utf-8") as cache_file:
                payload = json.load(cache_file)
            if isinstance(payload, dict):
                normalized_cache: dict[str, dict[str, str]] = {}
                for lei, record in payload.items():
                    normalized_lei = _normalize_lei(lei)
                    if normalized_lei and isinstance(record, dict):
                        normalized_cache[normalized_lei] = {
                            key: _stringify(value) for key, value in record.items()
                        }
                _MEM_CACHE = normalized_cache
        except (OSError, ValueError, TypeError) as exc:
            LOGGER.warning("Failed to load LEI cache from %s: %s", normalized_cache_path, exc)

    _CACHE_LOADED = True
    _CACHE_LOADED_FOR = normalized_cache_path
    return _MEM_CACHE


def _save_cache(cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with cache_path.open("w", encoding="utf-8") as cache_file:
            json.dump(_MEM_CACHE, cache_file, indent=2, sort_keys=True)
    except OSError as exc:
        LOGGER.warning("Failed to save LEI cache to %s: %s", cache_path, exc)


def _set_active_cache_path(cache_path: Path | str) -> Path:
    global _ACTIVE_CACHE_PATH, _CACHE_LOADED, _CACHE_LOADED_FOR
    normalized_cache_path = Path(cache_path)
    if normalized_cache_path != _ACTIVE_CACHE_PATH:
        _ACTIVE_CACHE_PATH = normalized_cache_path
        _CACHE_LOADED = False
        _CACHE_LOADED_FOR = None
    return normalized_cache_path


def _extract_address(attributes: dict[str, Any], key: str) -> str:
    entity = attributes.get("entity") or {}
    address = entity.get(key)
    return _stringify(address)


def _extract_record(payload: dict[str, Any], lei: str) -> dict[str, str]:
    record = _blank_record()
    data = payload.get("data") or {}
    attributes = data.get("attributes") or {}
    entity = attributes.get("entity") or {}
    registration = attributes.get("registration") or {}

    record["gleif_legal_name"] = _stringify((entity.get("legalName") or {}).get("name"))
    record["gleif_entity_status"] = _stringify(entity.get("status"))
    record["gleif_registration_status"] = _stringify(registration.get("status"))
    record["gleif_jurisdiction"] = _stringify(entity.get("jurisdiction"))
    record["gleif_legal_address"] = _extract_address(attributes, "legalAddress")
    record["gleif_headquarters_address"] = _extract_address(attributes, "headquartersAddress")
    record["gleif_last_update"] = _stringify(registration.get("lastUpdateDate"))
    record["gleif_next_renewal_date"] = _stringify(registration.get("nextRenewalDate"))
    record["gleif_managing_lou"] = _stringify(registration.get("managingLou"))
    return record


def get_lei_record(lei: str) -> dict[str, str]:
    normalized_lei = _normalize_lei(lei)
    if not normalized_lei:
        return _blank_record()

    cache_path = _ACTIVE_CACHE_PATH
    cached_records = _load_cache(cache_path)
    cached_record = cached_records.get(normalized_lei)
    if cached_record is not None:
        return {**_blank_record(), **cached_record}

    url = f"{GLEIF_BASE_URL}/{normalized_lei}"
    LOGGER.info("GLEIF API request: %s", url)

    try:
        response = _load_session().get(url, timeout=DEFAULT_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        LOGGER.warning("GLEIF lookup failed for %s: %s", normalized_lei, exc)
        return _blank_record()

    record = _extract_record(payload, normalized_lei)
    _MEM_CACHE[normalized_lei] = record
    _save_cache(cache_path)
    return record


def enrich_dataframe_with_lei(
    df: pd.DataFrame,
    *,
    cache_path: Path | str = DEFAULT_CACHE_PATH,
    max_lookups: int = DEFAULT_MAX_LOOKUPS,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> pd.DataFrame:
    if df.empty or "lei" not in df.columns:
        enriched = df.copy()
        if "original_company_name" not in enriched.columns and "company_name" in enriched.columns:
            enriched["original_company_name"] = enriched["company_name"]
        return enriched

    normalized_cache_path = _set_active_cache_path(cache_path)
    _load_cache(normalized_cache_path)

    enriched = df.copy()
    if "original_company_name" not in enriched.columns:
        enriched["original_company_name"] = enriched.get("company_name", "")

    company_name_series = (
        enriched["company_name"].fillna("").astype(str).str.strip()
        if "company_name" in enriched.columns
        else pd.Series([""] * len(enriched), index=enriched.index)
    )
    unknown_company_mask = company_name_series.str.lower().isin(
        {"", "unknown", "none", "null", "nan"}
    ) | company_name_series.str.lower().str.contains("unknown", na=False)

    unknown_first_df = pd.concat(
        [enriched.loc[unknown_company_mask], enriched.loc[~unknown_company_mask]],
        axis=0,
    )

    unique_leis = []
    seen_leis: set[str] = set()
    for lei_value in unknown_first_df["lei"].astype(str):
        normalized_lei = _normalize_lei(lei_value)
        if normalized_lei and normalized_lei not in seen_leis:
            seen_leis.add(normalized_lei)
            unique_leis.append(normalized_lei)

    if max_lookups > 0:
        unique_leis = unique_leis[:max_lookups]

    total_lookups = len(unique_leis)
    lei_records: dict[str, dict[str, str]] = {}

    for index, lei_value in enumerate(unique_leis, start=1):
        if progress_callback is not None:
            progress_callback(index, total_lookups, lei_value)

        lei_records[lei_value] = get_lei_record(lei_value)
        if sleep_seconds > 0 and index < total_lookups:
            time.sleep(sleep_seconds)

    if total_lookups and progress_callback is not None:
        progress_callback(total_lookups, total_lookups, "done")

    for column_name in _blank_record().keys():
        enriched[column_name] = ""

    def _lookup_record(lei_value: Any) -> dict[str, str]:
        normalized_lei = _normalize_lei(lei_value)
        if not normalized_lei:
            return _blank_record()
        return lei_records.get(normalized_lei, _MEM_CACHE.get(normalized_lei, _blank_record()))

    record_series = enriched["lei"].map(_lookup_record)
    for column_name in _blank_record().keys():
        enriched[column_name] = record_series.map(lambda record, name=column_name: record.get(name, ""))

    empty_company_mask = company_name_series.str.lower().isin(
        {"", "unknown", "none", "null", "nan"}
    ) | company_name_series.str.lower().str.contains("unknown", na=False)
    gleif_name_series = enriched["gleif_legal_name"].fillna("").astype(str).str.strip()
    replace_mask = empty_company_mask & gleif_name_series.ne("")
    enriched.loc[replace_mask, "company_name"] = gleif_name_series[replace_mask]

    return enriched
