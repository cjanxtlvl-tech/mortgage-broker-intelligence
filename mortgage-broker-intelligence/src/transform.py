from __future__ import annotations

import re

import pandas as pd


def _normalize_column_name(column_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", column_name.strip().lower())
    return normalized.strip("_")


def _find_column(columns: list[str], candidates: list[str]) -> str | None:
    normalized_map = {_normalize_column_name(col): col for col in columns}
    for candidate in candidates:
        if candidate in normalized_map:
            return normalized_map[candidate]
    return None


def prepare_hmda_columns(df: pd.DataFrame) -> pd.DataFrame:
    column_candidates: dict[str, list[str]] = {
        "state": ["property_state", "state", "state_code"],
        "company_name": [
            "respondent_name",
            "legal_name",
            "lender_name",
            "institution_name",
            "name",
        ],
        "lei": ["lei"],
        "action_taken": ["action_taken", "action_taken_type"],
        "loan_purpose": ["loan_purpose", "loan_purpose_type"],
        "loan_type": ["loan_type", "loan_type_code"],
        "loan_amount": ["loan_amount", "loan_amount_000s", "loan_amount_000"],
    }

    selected_columns: dict[str, str] = {}
    for canonical_name, candidates in column_candidates.items():
        found = _find_column(list(df.columns), candidates)
        if found:
            selected_columns[canonical_name] = found

    required = {"state", "action_taken", "loan_purpose", "loan_type", "loan_amount"}
    missing = sorted(required - set(selected_columns.keys()))
    if missing:
        raise ValueError(
            "Missing required HMDA columns after normalization: " + ", ".join(missing)
        )

    standardized = pd.DataFrame()
    for canonical_name, source_name in selected_columns.items():
        standardized[canonical_name] = df[source_name]

    if "company_name" not in standardized.columns:
        standardized["company_name"] = "unknown"
    standardized["company_name"] = standardized["company_name"].fillna("unknown").astype(str).str.strip()

    if "lei" not in standardized.columns:
        standardized["lei"] = ""
    standardized["lei"] = standardized["lei"].fillna("").astype(str).str.strip()

    amount_source_column = selected_columns["loan_amount"]
    amount_is_thousands = _normalize_column_name(amount_source_column) in {"loan_amount_000s", "loan_amount_000"}

    standardized["state"] = standardized["state"].astype(str).str.upper().str.strip()
    standardized["loan_amount"] = pd.to_numeric(standardized["loan_amount"], errors="coerce").fillna(0.0)
    if amount_is_thousands:
        standardized["loan_amount"] = standardized["loan_amount"] * 1000.0

    standardized["action_taken"] = standardized["action_taken"].astype(str).str.strip().str.lower()
    standardized["loan_purpose"] = standardized["loan_purpose"].astype(str).str.strip().str.lower()
    standardized["loan_type"] = standardized["loan_type"].astype(str).str.strip().str.lower()

    return standardized


def filter_originated_loans(df: pd.DataFrame) -> pd.DataFrame:
    originated_tokens = {
        "1",
        "1.0",
        "loan originated",
        "originated",
    }
    originated_mask = df["action_taken"].isin(originated_tokens)
    return df.loc[originated_mask].copy()


def _is_purchase(series: pd.Series) -> pd.Series:
    return series.isin({"1", "1.0", "home purchase", "purchase"})


def _is_fha(series: pd.Series) -> pd.Series:
    return series.isin({"2", "2.0", "fha", "fha-insured"})


def _is_va(series: pd.Series) -> pd.Series:
    return series.isin({"3", "3.0", "va", "veterans affairs"})


def _is_conventional(series: pd.Series) -> pd.Series:
    return series.isin({"1", "1.0", "conventional"})


def build_company_metrics(df: pd.DataFrame) -> pd.DataFrame:
    working = df.copy()

    working["is_purchase"] = _is_purchase(working["loan_purpose"]).astype(int)
    working["is_fha"] = _is_fha(working["loan_type"]).astype(int)
    working["is_va"] = _is_va(working["loan_type"]).astype(int)
    working["is_conventional"] = _is_conventional(working["loan_type"]).astype(int)

    working["purchase_loan_volume_component"] = working["loan_amount"] * working["is_purchase"]
    working["fha_loan_volume_component"] = working["loan_amount"] * working["is_fha"]

    grouped = (
        working.groupby(["state", "company_name", "lei"], dropna=False)
        .agg(
            total_originated_loans=("loan_amount", "size"),
            total_originated_volume=("loan_amount", "sum"),
            purchase_loan_count=("is_purchase", "sum"),
            purchase_loan_volume=("purchase_loan_volume_component", "sum"),
            fha_loan_count=("is_fha", "sum"),
            fha_loan_volume=("fha_loan_volume_component", "sum"),
            va_loan_count=("is_va", "sum"),
            conventional_loan_count=("is_conventional", "sum"),
        )
        .reset_index()
    )

    grouped["average_loan_amount"] = (
        grouped["total_originated_volume"] / grouped["total_originated_loans"].clip(lower=1)
    )
    grouped["purchase_share"] = (
        grouped["purchase_loan_count"] / grouped["total_originated_loans"].clip(lower=1)
    )
    grouped["fha_share"] = (
        grouped["fha_loan_count"] / grouped["total_originated_loans"].clip(lower=1)
    )

    numeric_columns = [
        "total_originated_volume",
        "purchase_loan_volume",
        "fha_loan_volume",
        "average_loan_amount",
    ]
    grouped[numeric_columns] = grouped[numeric_columns].round(2)
    grouped[["purchase_share", "fha_share"]] = grouped[["purchase_share", "fha_share"]].round(4)

    return grouped


def apply_state_filters(
    df: pd.DataFrame,
    target_states: list[str],
    exclude_states: list[str],
) -> pd.DataFrame:
    filtered = df.copy()

    if target_states:
        target_upper = {state.upper() for state in target_states}
        filtered = filtered[filtered["state"].isin(target_upper)]

    if exclude_states:
        exclude_upper = {state.upper() for state in exclude_states}
        filtered = filtered[~filtered["state"].isin(exclude_upper)]

    return filtered.reset_index(drop=True)
