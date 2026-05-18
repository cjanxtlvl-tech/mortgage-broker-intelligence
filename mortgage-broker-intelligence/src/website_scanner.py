from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

LOGGER = logging.getLogger(__name__)

CACHE_PATH = Path("data/processed/website_scan_cache.json")
REQUEST_TIMEOUT_SECONDS = 10
USER_AGENT = (
    "MortgageBrokerIntelligenceBot/1.0 "
    "(+https://github.com/cjanxtlvl-tech/mortgage-broker-intelligence; contact=admin)"
)

COMMON_PATHS = (
    "/about",
    "/about-us",
    "/licensing",
    "/licenses",
    "/disclosures",
    "/legal",
)

BROKER_POSITIVE_KEYWORDS = (
    "licensed mortgage broker",
    "mortgage broker",
    "independent mortgage broker",
    "brokered through",
    "shop multiple lenders",
    "access to multiple lenders",
    "wholesale lender",
    "mortgage brokerage",
)

LENDER_POSITIVE_KEYWORDS = (
    "direct lender",
    "in-house underwriting",
    "mortgage banker",
    "servicing",
    "portfolio lender",
    "correspondent lender",
)


def _default_scan_result(*, error: str = "") -> dict:
    return {
        "website_broker_signal_score": 0,
        "website_lender_signal_score": 0,
        "matched_broker_phrases": [],
        "matched_lender_phrases": [],
        "scanned_pages": [],
        "scan_error": error,
    }


def _normalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""

    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""

    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower().strip()
    path = parsed.path or "/"

    return urlunparse((scheme, netloc, path, "", "", ""))


def _cache_key(url: str) -> str:
    parsed = urlparse(url)
    base = urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))
    return base.rstrip("/")


def _load_cache(cache_path: Path = CACHE_PATH) -> dict[str, dict]:
    try:
        if not cache_path.exists():
            return {}
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    except (OSError, json.JSONDecodeError):
        LOGGER.warning("Unable to read website scan cache at %s", cache_path)
    return {}


def _save_cache(cache: dict[str, dict], cache_path: Path = CACHE_PATH) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        LOGGER.warning("Unable to write website scan cache at %s", cache_path)


def _extract_visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return " ".join(soup.stripped_strings).lower()


def _keyword_matches(text: str, keywords: tuple[str, ...]) -> list[str]:
    return [phrase for phrase in keywords if phrase in text]


def scan_website_for_broker_signals(url: str) -> dict:
    normalized_url = _normalize_url(url)
    if not normalized_url:
        return _default_scan_result(error="invalid or empty url")

    cache = _load_cache()
    key = _cache_key(normalized_url)
    cached = cache.get(key)
    if isinstance(cached, dict):
        return cached

    headers = {"User-Agent": USER_AGENT}
    result = _default_scan_result()

    try:
        pages_to_scan = [normalized_url]
        for suffix in COMMON_PATHS:
            pages_to_scan.append(urljoin(normalized_url, suffix))

        collected_text_parts: list[str] = []
        scanned_pages: list[str] = []

        for page_url in pages_to_scan:
            try:
                response = requests.get(
                    page_url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    allow_redirects=True,
                )
                response.raise_for_status()
            except requests.RequestException:
                continue

            content_type = str(response.headers.get("Content-Type", "")).lower()
            if "html" not in content_type:
                continue

            text = _extract_visible_text(response.text)
            if text:
                collected_text_parts.append(text)
                scanned_pages.append(page_url)

        full_text = " ".join(collected_text_parts)
        if not full_text:
            result = _default_scan_result(error="no readable html content found")
            result["scanned_pages"] = scanned_pages
        else:
            matched_broker = _keyword_matches(full_text, BROKER_POSITIVE_KEYWORDS)
            matched_lender = _keyword_matches(full_text, LENDER_POSITIVE_KEYWORDS)
            result = {
                "website_broker_signal_score": len(matched_broker),
                "website_lender_signal_score": len(matched_lender),
                "matched_broker_phrases": matched_broker,
                "matched_lender_phrases": matched_lender,
                "scanned_pages": scanned_pages,
                "scan_error": "",
            }
    except Exception as exc:  # Defensive fallback to avoid breaking app flow
        result = _default_scan_result(error=str(exc))

    cache[key] = result
    _save_cache(cache)
    return result