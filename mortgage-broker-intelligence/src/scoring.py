from __future__ import annotations

import re

import pandas as pd


def _safe_normalize(series: pd.Series) -> pd.Series:
    max_value = series.max()
    if max_value <= 0:
        return pd.Series(0.0, index=series.index)
    return series / max_value


def add_dominance_score(df: pd.DataFrame) -> pd.DataFrame:
    scored = df.copy()

    count_norm = _safe_normalize(scored["total_originated_loans"].astype(float))
    volume_norm = _safe_normalize(scored["total_originated_volume"].astype(float))
    purchase_component = scored["purchase_share"].clip(lower=0, upper=1)
    fha_component = scored["fha_share"].clip(lower=0, upper=1)

    # Directional ranking score only. This is not an official, regulatory, or legal score.
    scored["dominance_score"] = (
        100.0
        * (
            0.35 * count_norm
            + 0.35 * volume_norm
            + 0.15 * purchase_component
            + 0.15 * fha_component
        )
    ).round(2)

    return scored


def _guess_company_type(company_name: str) -> str:
    name = (company_name or "").strip().lower()

    if "credit union" in name or re.search(r"\bcu\b", name):
        return "credit_union"
    if "bank" in name:
        return "bank"
    if any(token in name for token in ["mortgage", "home loans", "funding", "lending"]):
        return "independent_mortgage_company"
    if "broker" in name:
        return "broker_unknown"
    if not name:
        return "unknown"
    return "unknown"


def add_company_type_guess(df: pd.DataFrame) -> pd.DataFrame:
    enriched = df.copy()
    enriched["company_type_guess"] = enriched["company_name"].astype(str).apply(_guess_company_type)
    return enriched
