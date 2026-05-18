from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path


def _bootstrap_path() -> None:
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


def _parse_states(raw_states: str | None) -> list[str]:
    if not raw_states:
        return []
    return [state.strip().upper() for state in raw_states.split(",") if state.strip()]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build HMDA mortgage company seed outputs.")
    parser.add_argument(
        "--source",
        choices=["local", "api"],
        default="local",
        help="Data source mode: local CSV or FFIEC/CFPB API.",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Local CSV path for --source local (example: data/raw/hmda.csv).",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="HMDA year for API mode (example: 2024).",
    )
    parser.add_argument(
        "--states",
        default=None,
        help="Comma-separated states for API mode (example: FL,TX,GA).",
    )
    parser.add_argument(
        "--actions-taken",
        default="1",
        help="HMDA actions_taken filter for API CSV endpoint (default: 1 for originated).",
    )
    return parser


def main() -> None:
    _bootstrap_path()
    args = _build_parser().parse_args()

    from src.config import load_settings
    from src.hmda_client import HmdaClient
    from src.main import run_seed_build

    settings = load_settings()

    if args.year is not None:
        settings = replace(settings, hmda_year=args.year)

    cli_states = _parse_states(args.states)
    if cli_states:
        settings = replace(settings, target_states=cli_states)

    if args.source == "local":
        if args.input:
            settings = replace(settings, hmda_local_csv=Path(args.input))
        run_seed_build(settings=settings)
        return

    request_year = settings.hmda_year
    request_states = cli_states or settings.target_states
    if not request_states:
        raise ValueError(
            "API mode requires at least one state via --states or TARGET_STATES in .env."
        )

    client = HmdaClient(settings)
    raw_df = client.download_csv(
        year=request_year,
        states=request_states,
        actions_taken=args.actions_taken,
    )
    run_seed_build(settings=settings, raw_df=raw_df)


if __name__ == "__main__":
    main()
