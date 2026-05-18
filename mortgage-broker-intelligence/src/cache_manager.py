from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

LOGGER = logging.getLogger(__name__)

WEBSITE_SCAN_CACHE_PATH = Path("data/processed/website_scan_cache.json")
LEI_CACHE_PATH = Path("data/processed/lei_cache.json")
NMLS_CACHE_PATH = Path("data/processed/nmls_cache.json")


def _normalize_url_key(raw_key: str) -> str:
    key = str(raw_key or "").strip().lower()
    if not key:
        return ""

    if "://" not in key and ("." in key or key.startswith("www.")):
        key = f"https://{key}"

    parsed = urlparse(key)
    if not parsed.netloc:
        return key.rstrip("/")

    normalized = urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc,
            parsed.path or "/",
            "",
            "",
            "",
        )
    )
    return normalized.rstrip("/")


def _normalize_cache_key(key: str) -> str:
    normalized = str(key or "").strip().lower().rstrip("/")
    if not normalized:
        return ""
    return _normalize_url_key(normalized)


def load_cache(cache_path: Path | str) -> dict[str, Any]:
    normalized_path = Path(cache_path)
    if not normalized_path.exists():
        return {}

    try:
        loaded = json.loads(normalized_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            LOGGER.warning("Cache at %s was not a dict; using empty cache", normalized_path)
            return {}

        normalized_cache: dict[str, Any] = {}
        for key, value in loaded.items():
            normalized_key = _normalize_cache_key(str(key))
            if normalized_key:
                normalized_cache[normalized_key] = value
        return normalized_cache
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        LOGGER.warning("Failed to load cache from %s: %s", normalized_path, exc)
        return {}


def save_cache(cache_path: Path | str, cache_data: dict[str, Any]) -> None:
    normalized_path = Path(cache_path)
    normalized_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        normalized_path.write_text(
            json.dumps(cache_data, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        LOGGER.warning("Failed to save cache to %s: %s", normalized_path, exc)


def get_cached_result(cache: dict[str, Any], key: str, ttl_seconds: int | None = None) -> Any | None:
    normalized_key = _normalize_cache_key(key)
    if not normalized_key:
        LOGGER.info("cache miss: empty key")
        return None

    if normalized_key not in cache:
        LOGGER.info("cache miss: %s", normalized_key)
        return None

    entry = cache.get(normalized_key)

    if isinstance(entry, dict) and "value" in entry and "timestamp" in entry:
        timestamp = entry.get("timestamp")
        if ttl_seconds is not None and isinstance(timestamp, (int, float)):
            age_seconds = time.time() - float(timestamp)
            if age_seconds > ttl_seconds:
                LOGGER.info("expired cache: %s", normalized_key)
                cache.pop(normalized_key, None)
                return None

        LOGGER.info("cache hit: %s", normalized_key)
        return entry.get("value")

    # Backward compatibility for legacy cache payloads without wrapper metadata.
    LOGGER.info("cache hit: %s", normalized_key)
    return entry


def set_cached_result(cache: dict[str, Any], key: str, value: Any) -> None:
    normalized_key = _normalize_cache_key(key)
    if not normalized_key:
        return
    cache[normalized_key] = {
        "timestamp": time.time(),
        "value": value,
    }
