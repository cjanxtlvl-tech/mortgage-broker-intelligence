from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd

LOGGER = logging.getLogger(__name__)

BROKER_KEYWORDS = (
    "broker",
    "brokerage",
    "mortgage broker",
    "loan broker",
)

LENDER_KEYWORDS = (
    "bank",
    "credit union",
    "lender",
    "lending",
    "funding",
    "financial",
    "savings",
    "trust",
    "association",
    "home loans",
)


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        result = float(value)
        if pd.isna(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _clip_probability(value: float) -> float:
    return max(0.0, min(1.0, value))


def classify_company(record: dict) -> dict:
    """Classify a company as broker, lender, or unknown using lightweight heuristics."""

    company_name = _normalize_text(record.get("company_name"))
    total_originated_loans = _as_float(record.get("total_originated_loans"))
    purchase_share = _as_float(record.get("purchase_share"))
    fha_share = _as_float(record.get("fha_share"))
    average_loan_amount = _as_float(record.get("average_loan_amount"))

    usable_signals = 0
    if company_name:
        usable_signals += 1
    for value in (total_originated_loans, purchase_share, fha_share, average_loan_amount):
        if value is not None:
            usable_signals += 1

    if usable_signals == 0:
        return {
            "company_category": "unknown",
            "broker_probability_score": 0.5,
            "confidence_score": 0.0,
            "classification_reasons": "insufficient data",
        }

    score = 0.5
    reasons: list[str] = []

    if company_name:
        if any(keyword in company_name for keyword in BROKER_KEYWORDS):
            score += 0.30
            reasons.append("broker keyword matched")
        if any(keyword in company_name for keyword in LENDER_KEYWORDS):
            score -= 0.22
            reasons.append("lender keyword matched")
        if re.search(r"\bbroker(s|age)?\b", company_name):
            score += 0.10
            reasons.append("explicit broker naming")

    if purchase_share is not None:
        if purchase_share >= 0.65:
            score += 0.08
            reasons.append(f"high purchase share ({purchase_share:.2f})")
        elif purchase_share <= 0.35:
            score -= 0.05
            reasons.append(f"low purchase share ({purchase_share:.2f})")

    if fha_share is not None:
        if fha_share >= 0.20:
            score += 0.07
            reasons.append(f"high FHA share ({fha_share:.2f})")
        elif fha_share <= 0.05:
            score -= 0.05
            reasons.append(f"low FHA share ({fha_share:.2f})")

    if total_originated_loans is not None:
        if total_originated_loans <= 25:
            score += 0.05
            reasons.append(f"low loan volume ({int(total_originated_loans)})")
        elif total_originated_loans >= 150:
            score -= 0.05
            reasons.append(f"high loan volume ({int(total_originated_loans)})")

    if average_loan_amount is not None:
        if average_loan_amount <= 250000:
            score += 0.08
            reasons.append(f"smaller average loan size ({average_loan_amount:,.0f})")
        elif average_loan_amount >= 450000:
            score -= 0.08
            reasons.append(f"larger average loan size ({average_loan_amount:,.0f})")

    broker_probability_score = _clip_probability(score)

    if broker_probability_score >= 0.60:
        company_category = "broker"
    elif broker_probability_score <= 0.40:
        company_category = "lender"
    else:
        company_category = "unknown"

    confidence_score = _clip_probability(
        0.25
        + abs(broker_probability_score - 0.5) * 1.1
        + min(usable_signals, 5) * 0.08
    )

    if not reasons:
        reasons.append("weak signal mix")

    return {
        "company_category": company_category,
        "broker_probability_score": round(broker_probability_score, 3),
        "confidence_score": round(confidence_score, 3),
        "classification_reasons": "; ".join(reasons),
    }


def classify_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        enriched = df.copy()
        enriched["company_category"] = ""
        enriched["broker_probability_score"] = ""
        enriched["confidence_score"] = ""
        enriched["classification_reasons"] = ""
        return enriched

    enriched = df.copy()
    classification_rows = enriched.apply(
        lambda row: classify_company(row.to_dict()),
        axis=1,
        result_type="expand",
    )

    for column_name in [
        "company_category",
        "broker_probability_score",
        "confidence_score",
        "classification_reasons",
    ]:
        enriched[column_name] = classification_rows[column_name]

    LOGGER.info("Applied broker/lender classification to %s rows", len(enriched))
    return enriched