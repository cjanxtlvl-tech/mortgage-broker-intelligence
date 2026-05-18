from __future__ import annotations

from io import BytesIO

import pandas as pd


def parse_state_list(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return [item.strip().upper() for item in raw_value.split(",") if item.strip()]


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def to_download_name_prefix(states: list[str], year: int) -> str:
    state_slug = "-".join(states) if states else "ALL"
    return f"hmda_{year}_{state_slug}"


def top10_per_state(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.sort_values(["state", "dominance_score"], ascending=[True, False])
        .groupby("state", as_index=False, group_keys=False)
        .head(10)
        .reset_index(drop=True)
    )
