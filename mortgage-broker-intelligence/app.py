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

from src.config import load_settings
from src.exporters import export_all
from src.hmda_client import HmdaApiError, HmdaClient
from src.lei_client import enrich_dataframe_with_lei
from src.main import build_ranked_dataframe
from src.utils import dataframe_to_csv_bytes, parse_state_list, to_download_name_prefix, top10_per_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger(__name__)


def _check_password() -> bool:
    load_dotenv()

    # Prefer Streamlit secrets (Cloud/local secrets.toml), fallback to .env for local dev.
    app_password_raw = st.secrets.get("APP_PASSWORD") or os.getenv("APP_PASSWORD")
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
    settings = load_settings()
    client = HmdaClient(settings)

    if source_mode == "API":
        if not target_states:
            raise ValueError("API mode requires at least one target state.")
        df = client.download_csv(year=year, states=target_states, actions_taken="1")
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


def main() -> None:
    st.set_page_config(page_title="Mortgage Broker Intelligence", layout="wide")

    if not _check_password():
        return

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
        value=False,
        help="Query GLEIF LEI records to fill in company names and add LEI metadata.",
    )
    max_lei_lookups = st.sidebar.number_input(
        "Max LEI lookups",
        min_value=1,
        max_value=5000,
        value=250,
        step=25,
        help="Limit how many unique LEIs are queried in a single run.",
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

            if enrich_company_names:
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

            filtered_ranked_df = _apply_sector_filter(ranked_df, sector_filter)

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

            lookup_display_df = lookup_df.copy()
            lookup_display_df["lei_lookup_url"] = lookup_display_df["lei"].map(_lei_record_url)
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
