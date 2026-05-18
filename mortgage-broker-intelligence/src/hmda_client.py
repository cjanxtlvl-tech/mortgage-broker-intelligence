from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import Settings

LOGGER = logging.getLogger(__name__)


class HmdaApiError(RuntimeError):
    pass


class HmdaClient:
    BASE_URL = "https://ffiec.cfpb.gov/v2/data-browser-api"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()

        retry = Retry(
            total=4,
            connect=4,
            read=4,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    @staticmethod
    def _normalize_states(states: list[str]) -> list[str]:
        return [state.strip().upper() for state in states if state and state.strip()]

    @staticmethod
    def _build_state_slug(states: list[str]) -> str:
        normalized = HmdaClient._normalize_states(states)
        return "-".join(normalized) if normalized else "NATIONWIDE"

    @staticmethod
    def _validate_year(year: int) -> None:
        if year <= 0:
            raise ValueError("years is required and must be a positive integer.")

    @staticmethod
    def _validate_geography(states: list[str]) -> None:
        if not states:
            raise ValueError(
                "At least one geography filter is required for non-nationwide requests. "
                "Provide one or more states."
            )

    @staticmethod
    def _validate_hmda_filters(filters: dict[str, Any]) -> None:
        has_non_empty_filter = any(value not in (None, "", [], ()) for value in filters.values())
        if not has_non_empty_filter:
            raise ValueError(
                "At least one HMDA data filter is required for CSV and aggregation endpoints."
            )

    def _build_url(self, endpoint: str) -> str:
        return f"{self.BASE_URL}{endpoint}"

    def _request_json(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        url = self._build_url(endpoint)
        LOGGER.info("HMDA API request: %s?%s", url, urlencode(params, doseq=True))

        try:
            response = self.session.get(url, params=params, timeout=(10, 120))
        except requests.RequestException as exc:
            raise HmdaApiError(f"HMDA API request failed: {exc}") from exc

        if response.status_code >= 400:
            raise HmdaApiError(
                f"HMDA API error {response.status_code} for {response.url}: {response.text[:400]}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise HmdaApiError(f"Invalid JSON response from HMDA API: {response.url}") from exc

    def _request_csv_stream_to_file(
        self,
        endpoint: str,
        params: dict[str, Any],
        output_path: Path,
    ) -> Path:
        url = self._build_url(endpoint)
        LOGGER.info("HMDA API request: %s?%s", url, urlencode(params, doseq=True))
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with self.session.get(url, params=params, timeout=(10, 300), stream=True) as response:
                if response.status_code >= 400:
                    body_preview = response.text[:400]
                    raise HmdaApiError(
                        f"HMDA API error {response.status_code} for {response.url}: {body_preview}"
                    )

                with output_path.open("wb") as raw_file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            raw_file.write(chunk)
        except requests.RequestException as exc:
            raise HmdaApiError(f"HMDA CSV download failed: {exc}") from exc

        LOGGER.info("Downloaded HMDA CSV to %s", output_path)
        return output_path

    def get_filers(self, year: int, states: list[str]) -> pd.DataFrame:
        self._validate_year(year)
        normalized_states = self._normalize_states(states)
        self._validate_geography(normalized_states)

        params = {
            "states": ",".join(normalized_states),
            "years": str(year),
        }
        payload = self._request_json("/view/filers", params)

        if isinstance(payload, list):
            return pd.DataFrame(payload)
        if isinstance(payload, dict):
            for key in ("filers", "results", "data"):
                if isinstance(payload.get(key), list):
                    return pd.DataFrame(payload[key])
            return pd.json_normalize(payload)

        raise HmdaApiError("Unexpected /view/filers response format.")

    def download_csv(
        self,
        year: int,
        states: list[str],
        actions_taken: str = "1",
    ) -> pd.DataFrame:
        self._validate_year(year)
        normalized_states = self._normalize_states(states)
        self._validate_geography(normalized_states)

        hmda_filters = {"actions_taken": actions_taken}
        self._validate_hmda_filters(hmda_filters)

        params = {
            "states": ",".join(normalized_states),
            "years": str(year),
            "actions_taken": actions_taken,
        }

        state_slug = self._build_state_slug(normalized_states)
        output_file = Path("data/raw") / f"hmda_{year}_{state_slug}.csv"
        csv_path = self._request_csv_stream_to_file("/view/csv", params, output_file)

        LOGGER.info("Loading downloaded CSV from %s", csv_path)
        df = pd.read_csv(csv_path, low_memory=False)
        LOGGER.info("Loaded %s rows and %s columns", len(df), len(df.columns))
        return df

    def get_aggregations(self, year: int, states: list[str], **filters: Any) -> dict[str, Any]:
        self._validate_year(year)
        normalized_states = self._normalize_states(states)
        self._validate_geography(normalized_states)
        self._validate_hmda_filters(filters)

        params: dict[str, Any] = {
            "states": ",".join(normalized_states),
            "years": str(year),
        }
        params.update(filters)
        return self._request_json("/view/aggregations", params)

    def load_lar_data(self) -> pd.DataFrame:
        local_path = self.settings.hmda_local_csv
        if local_path.exists():
            return self._load_local_csv(local_path)

        if self.settings.hmda_source_url:
            raise NotImplementedError(
                "HMDA_SOURCE_URL support is not enabled in load_lar_data. "
                "Use download_csv(year, states, actions_taken='1') for API mode."
            )

        raise FileNotFoundError(
            f"HMDA local CSV not found at {local_path}. "
            "Set HMDA_LOCAL_CSV in .env or use CLI local mode with --input."
        )

    @staticmethod
    def _load_local_csv(file_path: Path) -> pd.DataFrame:
        LOGGER.info("Loading HMDA CSV from %s", file_path)
        df = pd.read_csv(file_path, low_memory=False)
        LOGGER.info("Loaded %s rows and %s columns", len(df), len(df.columns))
        return df
