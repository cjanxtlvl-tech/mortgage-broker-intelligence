from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _parse_csv_list(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return [item.strip().upper() for item in raw_value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    hmda_year: int
    target_states: list[str]
    exclude_licensed_states: list[str]
    min_originated_loans: int
    output_path: Path
    hmda_local_csv: Path
    hmda_source_url: str | None


def load_settings() -> Settings:
    load_dotenv()

    hmda_year = int(os.getenv("HMDA_YEAR", "2024"))
    target_states = _parse_csv_list(os.getenv("TARGET_STATES", ""))
    exclude_licensed_states = _parse_csv_list(os.getenv("EXCLUDE_LICENSED_STATES", ""))
    min_originated_loans = int(os.getenv("MIN_ORIGINATED_LOANS", "25"))
    output_path = Path(os.getenv("OUTPUT_PATH", "data/processed/mortgage_company_seed.csv"))
    hmda_local_csv = Path(
        os.getenv("HMDA_LOCAL_CSV", f"data/raw/hmda_lar_{hmda_year}.csv")
    )
    hmda_source_url = os.getenv("HMDA_SOURCE_URL") or None

    return Settings(
        hmda_year=hmda_year,
        target_states=target_states,
        exclude_licensed_states=exclude_licensed_states,
        min_originated_loans=min_originated_loans,
        output_path=output_path,
        hmda_local_csv=hmda_local_csv,
        hmda_source_url=hmda_source_url,
    )
