from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

LOGGER = logging.getLogger(__name__)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def export_full_csv(df: pd.DataFrame, output_path: Path) -> Path:
    _ensure_parent(output_path)
    df.to_csv(output_path, index=False)
    LOGGER.info("Exported full dataset to %s", output_path)
    return output_path


def export_by_state_csvs(df: pd.DataFrame, by_state_dir: Path) -> list[Path]:
    by_state_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for state, state_df in df.groupby("state"):
        state_path = by_state_dir / f"{state}_mortgage_companies.csv"
        state_df.to_csv(state_path, index=False)
        paths.append(state_path)

    LOGGER.info("Exported %s per-state files to %s", len(paths), by_state_dir)
    return paths


def export_top10_summary(df: pd.DataFrame, summary_path: Path) -> Path:
    _ensure_parent(summary_path)
    top10 = (
        df.sort_values(["state", "dominance_score"], ascending=[True, False])
        .groupby("state", as_index=False, group_keys=False)
        .head(10)
    )
    top10.to_csv(summary_path, index=False)
    LOGGER.info("Exported top-10-per-state summary to %s", summary_path)
    return summary_path


def export_all(df: pd.DataFrame, output_path: Path) -> dict[str, Path | list[Path]]:
    full_path = export_full_csv(df, output_path)
    by_state_dir = output_path.parent / "by_state"
    by_state_paths = export_by_state_csvs(df, by_state_dir)
    top10_path = output_path.parent / "top10_by_state_summary.csv"
    summary_path = export_top10_summary(df, top10_path)

    return {
        "full": full_path,
        "by_state": by_state_paths,
        "top10_summary": summary_path,
    }
