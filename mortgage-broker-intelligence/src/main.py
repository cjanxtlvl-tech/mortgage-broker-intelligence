from __future__ import annotations

import logging

import pandas as pd

from src.config import Settings, load_settings
from src.classifier import classify_dataframe
from src.exporters import export_all
from src.hmda_client import HmdaClient
from src.scoring import add_company_type_guess, add_dominance_score
from src.transform import apply_state_filters, build_company_metrics, filter_originated_loans, prepare_hmda_columns

LOGGER = logging.getLogger(__name__)

OUTPUT_COLUMNS = [
    "state",
    "company_name",
    "lei",
    "total_originated_loans",
    "total_originated_volume",
    "purchase_loan_count",
    "purchase_loan_volume",
    "fha_loan_count",
    "fha_loan_volume",
    "va_loan_count",
    "conventional_loan_count",
    "average_loan_amount",
    "purchase_share",
    "fha_share",
    "dominance_score",
    "company_type_guess",
    "nmls_id",
    "website",
    "linkedin_url",
    "notes",
]

CLASSIFICATION_COLUMNS = [
    "company_category",
    "broker_probability_score",
    "confidence_score",
    "classification_reasons",
]


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _attach_placeholder_enrichment_fields(df: pd.DataFrame) -> pd.DataFrame:
    enriched = df.copy()
    enriched["nmls_id"] = ""
    enriched["website"] = ""
    enriched["linkedin_url"] = ""
    enriched["notes"] = ""
    return enriched


def _output_columns(include_classification: bool) -> list[str]:
    columns = OUTPUT_COLUMNS.copy()
    if include_classification:
        columns.extend(CLASSIFICATION_COLUMNS)
    return columns


def build_ranked_dataframe(
    raw_df: pd.DataFrame,
    settings: Settings,
    *,
    enable_broker_lender_classification: bool = False,
) -> pd.DataFrame:
    standardized_df = prepare_hmda_columns(raw_df)
    originated_df = filter_originated_loans(standardized_df)
    metrics_df = build_company_metrics(originated_df)

    filtered_df = apply_state_filters(
        metrics_df,
        target_states=settings.target_states,
        exclude_states=settings.exclude_licensed_states,
    )

    filtered_df = filtered_df[
        filtered_df["total_originated_loans"] >= settings.min_originated_loans
    ].reset_index(drop=True)

    scored_df = add_dominance_score(filtered_df)
    classified_df = add_company_type_guess(scored_df)
    if enable_broker_lender_classification:
        classified_df = classify_dataframe(classified_df)
    output_df = _attach_placeholder_enrichment_fields(classified_df)
    output_df = output_df[_output_columns(enable_broker_lender_classification)].copy()
    output_df = output_df.sort_values(
        by=["state", "dominance_score", "total_originated_loans"],
        ascending=[True, False, False],
    ).reset_index(drop=True)
    return output_df


def run_seed_build(settings: Settings | None = None, raw_df: pd.DataFrame | None = None) -> pd.DataFrame:
    _configure_logging()
    settings = settings or load_settings()

    LOGGER.info("Starting HMDA seed build for year %s", settings.hmda_year)

    if raw_df is None:
        raw_df = HmdaClient(settings).load_lar_data()
    output_df = build_ranked_dataframe(raw_df=raw_df, settings=settings)

    export_paths = export_all(output_df, settings.output_path)

    state_count = output_df["state"].nunique() if not output_df.empty else 0
    company_count = len(output_df)
    LOGGER.info(
        "Seed build complete: %s companies across %s states (min_originated_loans=%s)",
        company_count,
        state_count,
        settings.min_originated_loans,
    )
    LOGGER.info(
        "Exports: full=%s, by_state_files=%s, top10=%s",
        export_paths["full"],
        len(export_paths["by_state"]),
        export_paths["top10_summary"],
    )

    print(
        f"Built {company_count} ranked companies across {state_count} states. "
        f"Output written to {export_paths['full']}"
    )

    return output_df


if __name__ == "__main__":
    run_seed_build()
