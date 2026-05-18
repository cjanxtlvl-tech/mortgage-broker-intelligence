from __future__ import annotations

import hmac
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError

from src.cache_manager import (
    LEI_CACHE_PATH,
    NMLS_CACHE_PATH,
    WEBSITE_SCAN_CACHE_PATH,
    load_cache,
    save_cache,
)
from src.config import load_settings
from src.classifier import classify_dataframe
from src.exporters import export_all
from src.hmda_client import HmdaApiError, HmdaClient
from src.lei_client import enrich_dataframe_with_lei, get_lei_record
from src.main import build_ranked_dataframe
from src.website_scanner import scan_website_for_broker_signals
from src.utils import dataframe_to_csv_bytes, parse_state_list, to_download_name_prefix, top10_per_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger(__name__)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _cached_hmda_download(year: int, target_states: tuple[str, ...]) -> pd.DataFrame:
    settings = load_settings()
    client = HmdaClient(settings)
    return client.download_csv(year=year, states=list(target_states), actions_taken="1")


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def _cached_lei_lookup(lei: str) -> dict[str, str]:
    return get_lei_record(lei)


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def _cached_website_scan(url: str) -> dict:
    return scan_website_for_broker_signals(url)


def _initialize_cache_files() -> None:
    for cache_path in (WEBSITE_SCAN_CACHE_PATH, LEI_CACHE_PATH, NMLS_CACHE_PATH):
        cache_data = load_cache(cache_path)
        save_cache(cache_path, cache_data)


def _check_password() -> bool:
    # Support local development via .env while keeping Streamlit secrets as primary.
    load_dotenv(dotenv_path=Path(__file__).resolve().with_name(".env"))
    try:
        app_password_raw = st.secrets.get("APP_PASSWORD") or os.getenv("APP_PASSWORD")
    except StreamlitSecretNotFoundError:
        app_password_raw = os.getenv("APP_PASSWORD")

    app_password = str(app_password_raw) if app_password_raw else ""
    if not app_password:
        st.error(
            "APP_PASSWORD is not configured. Set it in Streamlit Cloud Secrets as\n"
            'APP_PASSWORD = "change-me-now-2026"\n'
            "or set APP_PASSWORD in your local .env file."
        )
        st.stop()

    if st.session_state.get("authenticated"):
        return True

    st.title("Mortgage Broker Intelligence")
    st.subheader("Internal Access")
    password_input = st.text_input("Enter password", type="password")
    submitted = st.button("Unlock")

    if submitted:
        if hmac.compare_digest(password_input, app_password):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")

    return False


def _load_source_data(
    source_mode: str,
    year: int,
    target_states: list[str],
    uploaded_file: Any,
) -> tuple[pd.DataFrame, str]:
    if source_mode == "API":
        if not target_states:
            raise ValueError("API mode requires at least one target state.")
        normalized_states = tuple(
            state.strip().upper() for state in target_states if state and state.strip()
        )
        df = _cached_hmda_download(year=year, target_states=normalized_states).copy()
        source_label = f"API ({year}, {','.join(target_states)})"
        return df, source_label

    if uploaded_file is None:
        raise ValueError("Upload CSV mode requires a CSV file.")

    df = pd.read_csv(uploaded_file, low_memory=False)
    source_label = f"Upload ({uploaded_file.name})"
    return df, source_label


def _state_metric_cards(df: pd.DataFrame) -> None:
    top_state_rows = top10_per_state(df).groupby("state", as_index=False).first()
    if top_state_rows.empty:
        return

    st.subheader("Top 10 Summary Cards")
    cols = st.columns(min(4, len(top_state_rows)))
    for idx, row in enumerate(top_state_rows.itertuples(index=False)):
        col = cols[idx % len(cols)]
        col.metric(
            label=f"{row.state} top lender",
            value=str(row.company_name),
            delta=f"Score {row.dominance_score}",
        )


def _charts(df: pd.DataFrame) -> None:
    st.subheader("Top Originated Volume")
    volume_chart_df = (
        df.sort_values("total_originated_volume", ascending=False)
        .head(20)
        .set_index("company_name")[["total_originated_volume"]]
    )
    st.bar_chart(volume_chart_df)

    st.subheader("FHA-Heavy Lenders")
    fha_chart_df = (
        df.sort_values("fha_share", ascending=False)
        .head(20)
        .set_index("company_name")[["fha_share"]]
    )
    st.bar_chart(fha_chart_df)

    st.subheader("Purchase-Heavy Lenders")
    purchase_chart_df = (
        df.sort_values("purchase_share", ascending=False)
        .head(20)
        .set_index("company_name")[["purchase_share"]]
    )
    st.bar_chart(purchase_chart_df)


def _apply_sector_filter(df: pd.DataFrame, selected_sector: str) -> pd.DataFrame:
    if selected_sector == "All":
        return df

    sector_to_column = {
        "FHA": "fha_loan_count",
        "VA": "va_loan_count",
        "Conventional": "conventional_loan_count",
    }
    column_name = sector_to_column.get(selected_sector)
    if not column_name or column_name not in df.columns:
        return df

    return df[df[column_name] > 0].reset_index(drop=True)


def _lookup_matches(
    df: pd.DataFrame,
    lei_query: str,
    company_query: str,
    exact_lei_match: bool,
) -> pd.DataFrame:
    if df.empty:
        return df

    matched = df.copy()
    if lei_query.strip():
        if exact_lei_match:
            normalized_lei = lei_query.strip().upper()
            lei_mask = matched["lei"].astype(str).str.strip().str.upper() == normalized_lei
        else:
            lei_mask = matched["lei"].astype(str).str.contains(lei_query.strip(), case=False, na=False)
        matched = matched[lei_mask]

    if company_query.strip():
        company_mask = matched["company_name"].astype(str).str.contains(
            company_query.strip(), case=False, na=False
        )
        matched = matched[company_mask]

    return matched.reset_index(drop=True)


def _lei_record_url(lei_value: str) -> str:
    lei = str(lei_value).strip().upper()
    if not lei:
        return ""
    return f"https://search.gleif.org/#/record/{lei}"


def _company_info_columns() -> list[str]:
    return [
        "state",
        "original_company_name",
        "company_name",
        "lei_lookup_url",
        "gleif_legal_name",
        "gleif_entity_status",
        "gleif_registration_status",
        "gleif_jurisdiction",
        "gleif_legal_address",
        "gleif_headquarters_address",
        "gleif_last_update",
        "gleif_next_renewal_date",
        "gleif_managing_lou",
        "total_originated_loans",
        "dominance_score",
    ]


def _ensure_company_info_columns(df: pd.DataFrame) -> pd.DataFrame:
    enriched = df.copy()
    for column_name in _company_info_columns():
        if column_name not in enriched.columns:
            enriched[column_name] = ""
    return enriched


def _force_enrich_lookup_matches(df: pd.DataFrame, *, max_forced_lookups: int = 25) -> pd.DataFrame:
    if df.empty or "lei" not in df.columns:
        return df

    forced = _ensure_company_info_columns(df)

    normalized_leis = (
        forced["lei"].fillna("").astype(str).str.strip().str.upper().replace("", pd.NA).dropna().unique().tolist()
    )
    if not normalized_leis:
        return forced

    for lei_value in normalized_leis[:max_forced_lookups]:
        record = _cached_lei_lookup(lei_value)
        if not any(record.values()):
            continue

        lei_mask = forced["lei"].fillna("").astype(str).str.strip().str.upper() == lei_value
        for column_name, value in record.items():
            if not value:
                continue
            existing_values = pd.Series(forced.loc[lei_mask, column_name]).fillna("").astype(str).str.strip()
            replaceable_value_mask = existing_values.str.lower().isin({"", "unknown", "none", "null", "nan"})
            if replaceable_value_mask.any():
                forced.loc[existing_values[replaceable_value_mask].index, column_name] = value

        gleif_legal_name = record.get("gleif_legal_name", "").strip()
        if gleif_legal_name:
            existing_company_names = (
                pd.Series(forced.loc[lei_mask, "company_name"]).fillna("").astype(str).str.strip()
            )
            unknown_name_mask = existing_company_names.str.lower().isin(
                {"", "unknown", "none", "null", "nan"}
            ) | existing_company_names.str.lower().str.contains("unknown", na=False)
            if unknown_name_mask.any():
                forced.loc[existing_company_names[unknown_name_mask].index, "company_name"] = gleif_legal_name

    return forced


def _download_section(full_df: pd.DataFrame, settings_output_path: Path, year: int, states: list[str]) -> None:
    export_paths = export_all(full_df, settings_output_path)
    top10_df = top10_per_state(full_df)
    file_prefix = to_download_name_prefix(states, year)

    st.subheader("Downloadable Exports")
    st.download_button(
        label="Download Full Ranked CSV",
        data=dataframe_to_csv_bytes(full_df),
        file_name=f"{file_prefix}_full.csv",
        mime="text/csv",
    )
    st.download_button(
        label="Download Top 10 By State CSV",
        data=dataframe_to_csv_bytes(top10_df),
        file_name=f"{file_prefix}_top10_by_state.csv",
        mime="text/csv",
    )

    for state, state_df in full_df.groupby("state"):
        st.download_button(
            label=f"Download {state} CSV",
            data=dataframe_to_csv_bytes(state_df),
            file_name=f"{file_prefix}_{state}.csv",
            mime="text/csv",
            key=f"download-{state}",
        )

    by_state_paths = export_paths["by_state"]
    by_state_count = len(by_state_paths) if isinstance(by_state_paths, list) else 1
    st.caption(
        "Saved exports to data/processed: "
        f"full, top10 summary, and {by_state_count} state files."
    )


def _apply_website_scanning(df: pd.DataFrame, *, max_scans: int) -> pd.DataFrame:
    enriched = df.copy()
    scan_columns = {
        "website_broker_signal_score": 0,
        "website_lender_signal_score": 0,
        "matched_broker_phrases": "",
        "matched_lender_phrases": "",
        "scanned_pages": "",
        "scan_error": "",
    }
    for column_name, default_value in scan_columns.items():
        if column_name not in enriched.columns:
            enriched[column_name] = default_value

    if enriched.empty or "website" not in enriched.columns:
        return enriched

    websites = (
        enriched["website"]
        .fillna("")
        .astype(str)
        .str.strip()
    )
    unique_websites = [value for value in websites.unique().tolist() if value]
    if not unique_websites:
        return enriched

    for website in unique_websites[:max_scans]:
        scan_result = _cached_website_scan(website)
        mask = websites == website
        enriched.loc[mask, "website_broker_signal_score"] = scan_result.get("website_broker_signal_score", 0)
        enriched.loc[mask, "website_lender_signal_score"] = scan_result.get("website_lender_signal_score", 0)
        enriched.loc[mask, "matched_broker_phrases"] = "; ".join(scan_result.get("matched_broker_phrases", []))
        enriched.loc[mask, "matched_lender_phrases"] = "; ".join(scan_result.get("matched_lender_phrases", []))
        enriched.loc[mask, "scanned_pages"] = " | ".join(scan_result.get("scanned_pages", []))
        enriched.loc[mask, "scan_error"] = scan_result.get("scan_error", "")

    return enriched


def main() -> None:
    st.set_page_config(page_title="Mortgage Broker Intelligence", layout="wide")

    if not _check_password():
        return

    _initialize_cache_files()

    default_settings = load_settings()

    st.sidebar.header("Analysis Settings")
    selected_year = st.sidebar.number_input(
        "HMDA year",
        min_value=2018,
        max_value=2100,
        value=default_settings.hmda_year,
        step=1,
    )
    target_states_input = st.sidebar.text_input(
        "Target states (comma-separated)",
        value=",".join(default_settings.target_states),
    )
    exclude_states_input = st.sidebar.text_input(
        "Exclude licensed states (comma-separated)",
        value=",".join(default_settings.exclude_licensed_states),
    )
    min_loans = st.sidebar.slider(
        "Minimum originated loans",
        min_value=1,
        max_value=1000,
        value=default_settings.min_originated_loans,
    )
    enrich_company_names = st.sidebar.checkbox(
        "Enrich company names from LEI",
        value=True,
        help="Query GLEIF LEI records to fill in company names and add LEI metadata.",
    )
    enable_broker_lender_classification = st.sidebar.checkbox(
        "Enable broker/lender classification",
        value=False,
        help="Add lightweight heuristic broker-vs-lender labels and scores.",
    )
    enable_website_scanning = st.sidebar.checkbox(
        "Scan websites for broker/lender language",
        value=False,
        help="Scan company websites for broker/lender keyword signals and append website scan columns.",
    )
    max_website_scans = st.sidebar.number_input(
        "Max website scans",
        min_value=1,
        max_value=500,
        value=25,
        step=5,
        help="Limit how many unique websites are scanned in a single run.",
    )
    max_lei_lookups = st.sidebar.number_input(
        "Max LEI lookups",
        min_value=1,
        max_value=5000,
        value=250,
        step=25,
        help="Limit how many unique LEIs are queried in a single run.",
    )
    lookup_force_lei_lookups = st.sidebar.number_input(
        "Max forced LEI lookups (search)",
        min_value=1,
        max_value=500,
        value=25,
        step=5,
        help="When using LEI/company search, force-refresh up to this many matched LEIs from GLEIF.",
    )
    sector_filter = st.sidebar.selectbox(
        "Sector filter",
        options=["All", "FHA", "VA", "Conventional"],
        index=0,
        help="Filter lenders by whether they originated loans in the selected sector.",
    )
    source_mode = st.sidebar.selectbox("Source mode", options=["API", "Upload CSV"])
    uploaded_file = None
    if source_mode == "Upload CSV":
        uploaded_file = st.sidebar.file_uploader("Upload HMDA CSV", type=["csv"])

    st.title("Mortgage Broker Intelligence")
    st.write(
        "Identify high-volume mortgage companies by state using public HMDA/CFPB data. "
        "This dashboard is designed for lightweight internal analysis and export workflows."
    )

    if enrich_company_names:
        st.warning(
            "LEI enrichment can take longer on large datasets because the app queries GLEIF for each unique LEI."
        )
    if enable_website_scanning:
        st.warning(
            "Website scanning can be slow because pages are fetched over the network and parsed for keyword signals."
        )

    run_clicked = st.button("Run analysis", type="primary")
    if not run_clicked:
        return

    target_states = parse_state_list(target_states_input)
    exclude_states = parse_state_list(exclude_states_input)
    runtime_settings = replace(
        default_settings,
        hmda_year=int(selected_year),
        target_states=target_states,
        exclude_licensed_states=exclude_states,
        min_originated_loans=min_loans,
    )

    try:
        with st.spinner("Loading HMDA data and building rankings..."):
            raw_df, source_label = _load_source_data(
                source_mode=source_mode,
                year=int(selected_year),
                target_states=target_states,
                uploaded_file=uploaded_file,
            )
            ranked_df = build_ranked_dataframe(raw_df=raw_df, settings=runtime_settings)

            unknown_with_lei_before = 0
            total_unique_leis = 0

            if enrich_company_names:
                company_series_before = (
                    ranked_df["company_name"].fillna("").astype(str).str.strip()
                    if "company_name" in ranked_df.columns
                    else pd.Series([""] * len(ranked_df), index=ranked_df.index)
                )
                unknown_company_before = company_series_before.str.lower().isin(
                    {"", "unknown", "none", "null", "nan"}
                ) | company_series_before.str.lower().str.contains("unknown", na=False)
                lei_series_before = ranked_df["lei"].fillna("").astype(str).str.strip().str.upper()
                has_lei_before = lei_series_before.ne("")
                unknown_with_lei_before = int((unknown_company_before & has_lei_before).sum())
                total_unique_leis = int(lei_series_before[has_lei_before].nunique())

                progress_placeholder = st.empty()
                progress_bar = st.progress(0)

                def _update_enrichment_progress(current: int, total: int, lei_value: str) -> None:
                    if total <= 0:
                        progress_bar.progress(0)
                        progress_placeholder.info("Preparing LEI enrichment...")
                        return

                    progress_value = min(current / total, 1.0)
                    progress_bar.progress(progress_value)
                    progress_placeholder.info(
                        f"Enriching LEI {current} of {total}: {lei_value}"
                    )

                ranked_df = enrich_dataframe_with_lei(
                    ranked_df,
                    max_lookups=int(max_lei_lookups),
                    cache_path=Path("data/processed/lei_cache.json"),
                    progress_callback=_update_enrichment_progress,
                )
                progress_bar.progress(1.0)
                progress_placeholder.success("LEI enrichment complete.")

                company_series_after = ranked_df["company_name"].fillna("").astype(str).str.strip()
                unknown_company_after = company_series_after.str.lower().isin(
                    {"", "unknown", "none", "null", "nan"}
                ) | company_series_after.str.lower().str.contains("unknown", na=False)
                lei_series_after = ranked_df["lei"].fillna("").astype(str).str.strip().str.upper()
                unknown_with_lei_after = int((unknown_company_after & lei_series_after.ne("")).sum())

                if total_unique_leis > int(max_lei_lookups) and unknown_with_lei_after > 0:
                    st.warning(
                        "Some companies remain unknown because the LEI lookup cap was reached. "
                        f"Unknown rows with LEI before/after enrichment: {unknown_with_lei_before} -> {unknown_with_lei_after}. "
                        f"Unique LEIs in run: {total_unique_leis}; Max LEI lookups: {int(max_lei_lookups)}. "
                        "Increase Max LEI lookups to enrich more results."
                    )

            if enable_broker_lender_classification:
                classification_placeholder = st.empty()
                classification_bar = st.progress(0)

                def _update_classification_progress(current: int, total: int) -> None:
                    if total <= 0:
                        classification_bar.progress(0)
                        classification_placeholder.info("Preparing broker/lender classification...")
                        return

                    progress_value = min(current / total, 1.0)
                    classification_bar.progress(progress_value)
                    classification_placeholder.info(
                        f"Classifying company {current} of {total}"
                    )

                ranked_df = classify_dataframe(
                    ranked_df,
                    progress_callback=_update_classification_progress,
                )
                classification_bar.progress(1.0)
                classification_placeholder.success("Broker/lender classification complete.")

            if enable_website_scanning:
                ranked_df = _apply_website_scanning(
                    ranked_df,
                    max_scans=int(max_website_scans),
                )

            filtered_ranked_df = _apply_sector_filter(ranked_df, sector_filter)

        # Always show broker/lender filter, but disable options if not available
        broker_lender_options = ["All", "Probable Broker Only", "Probable Lender Only"]
        broker_lender_disabled = not (enable_broker_lender_classification and "classification_label" in filtered_ranked_df.columns)
        broker_lender_filter = st.sidebar.radio(
            "Show:",
            options=broker_lender_options,
            index=0,
            help="Filter results to show only probable brokers, probable lenders, or all.",
            disabled=broker_lender_disabled
        )
        if not broker_lender_disabled:
            if broker_lender_filter == "Probable Broker Only":
                filtered_ranked_df = filtered_ranked_df[
                    filtered_ranked_df["classification_label"].str.lower() == "broker"
                ].reset_index(drop=True)
            elif broker_lender_filter == "Probable Lender Only":
                filtered_ranked_df = filtered_ranked_df[
                    filtered_ranked_df["classification_label"].str.lower() == "lender"
                ].reset_index(drop=True)

        st.success(f"Analysis complete using source: {source_label}")
        if sector_filter != "All":
            st.info(f"Applied sector filter: {sector_filter}")
        st.write(
            f"Ranked {len(filtered_ranked_df)} companies across "
            f"{filtered_ranked_df['state'].nunique() if not filtered_ranked_df.empty else 0} states."
        )

        if filtered_ranked_df.empty:
            st.warning("No companies match the current filters. Try a different sector or lower minimum loans.")
            return

        st.subheader("LEI and Company Lookup")
        lookup_col_1, lookup_col_2 = st.columns(2)
        lei_lookup = lookup_col_1.text_input("Find by LEI", value="", placeholder="e.g., 549300... ")
        company_lookup = lookup_col_2.text_input(
            "Find by Company Name", value="", placeholder="e.g., Rocket Mortgage"
        )
        exact_lei_match = st.checkbox("Exact LEI match only", value=False)

        if lei_lookup.strip() or company_lookup.strip():
            lookup_df = _lookup_matches(
                filtered_ranked_df,
                lei_lookup,
                company_lookup,
                exact_lei_match,
            )

            if enrich_company_names:
                lookup_df = _force_enrich_lookup_matches(
                    lookup_df,
                    max_forced_lookups=int(lookup_force_lei_lookups),
                )

            lookup_display_df = lookup_df.copy()
            lookup_display_df["lei_lookup_url"] = lookup_display_df["lei"].map(_lei_record_url)
            lookup_display_df = _ensure_company_info_columns(lookup_display_df)
            st.caption(f"Lookup matches: {len(lookup_df)}")
            if enrich_company_names:
                st.markdown("**Company info**")
                st.caption("HMDA names are preserved in original_company_name and GLEIF metadata fills the company info fields.")
            st.dataframe(
                lookup_display_df[_company_info_columns()],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "state": "State",
                    "original_company_name": "Original HMDA Company",
                    "company_name": "Company",
                    "lei_lookup_url": st.column_config.LinkColumn(
                        "LEI",
                        help="Click to open LEI record in GLEIF",
                        display_text=r".*/record/(.*)$",
                    ),
                    "gleif_legal_name": "GLEIF Legal Name",
                    "gleif_entity_status": "Entity Status",
                    "gleif_registration_status": "Registration Status",
                    "gleif_jurisdiction": "Jurisdiction",
                    "gleif_legal_address": "Legal Address",
                    "gleif_headquarters_address": "Headquarters Address",
                    "gleif_last_update": "Last Update",
                    "gleif_next_renewal_date": "Next Renewal",
                    "gleif_managing_lou": "Managing LOU",
                    "total_originated_loans": "Originated Loans",
                    "dominance_score": "Dominance Score",
                },
            )

        top10_df = top10_per_state(filtered_ranked_df)
        st.subheader("Top Mortgage Companies By State")
        st.dataframe(top10_df, use_container_width=True)

        st.subheader("Full Ranked Output")
        st.dataframe(filtered_ranked_df, use_container_width=True)

        _state_metric_cards(filtered_ranked_df)
        _charts(filtered_ranked_df)
        _download_section(
            full_df=filtered_ranked_df,
            settings_output_path=runtime_settings.output_path,
            year=int(selected_year),
            states=target_states,
        )
    except (ValueError, HmdaApiError, FileNotFoundError, pd.errors.ParserError) as exc:
        LOGGER.exception("Analysis failed")
        st.error(f"Analysis failed: {exc}")


if __name__ == "__main__":
    main()
