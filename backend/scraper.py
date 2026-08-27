# -*- coding: utf-8 -*-
"""
scraper.py - Fetches live IPO data from investorgain.com API.

Caching layer uses Upstash Redis (REST-based) instead of an in-process
dict, so the cache: (a) survives restarts/redeploys, (b) stays consistent
if you ever run more than one worker process, and (c) works fine even
when the host process spins down between requests (e.g. Render free
tier), since Upstash's REST protocol doesn't need a persistent
connection.

Requires UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN env vars.
"""

import os
import re
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

import requests
from bs4 import BeautifulSoup
from upstash_redis import Redis

import store  # ← Postgres-backed manual overrides

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

API_BASE = "https://webnodejs.investorgain.com/"
REPORT_ID = 331          # Live IPO GMP report ID on investorgain.com
CACHE_TTL = 1800          # seconds — re-fetch after 30 minutes

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.investorgain.com",
    "Referer": "https://www.investorgain.com/report/ipo-gmp-live/331/",
}

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN")
if not UPSTASH_URL or not UPSTASH_TOKEN:
    raise RuntimeError(
        "UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN are not set. "
        "Copy them from your Upstash database's REST API panel into .env."
    )

_redis = Redis(url=UPSTASH_URL, token=UPSTASH_TOKEN)

# Keys used in Redis:
#   ipo:data       - last successfully scraped IPO list (no expiry — kept
#                     around so a fetch failure can still serve something)
#   ipo:data:fresh - TTL'd marker; its presence means ipo:data is still
#                     within CACHE_TTL and doesn't need re-fetching
#   ipo:ranked     - last ranked list with Apply_Probability, set by /predict
_DATA_KEY = "ipo:data"
_FRESH_KEY = "ipo:data:fresh"
_RANKED_KEY = "ipo:ranked"


def _cache_get(key: str):
    raw = _redis.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("Corrupt cache value for key %s — ignoring", key)
        return None


def _cache_set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    payload = json.dumps(value)
    if ttl:
        _redis.set(key, payload, ex=ttl)
    else:
        _redis.set(key, payload)


# ── Helper: parse HTML-embedded text ──────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Remove HTML tags and decode entities, returning clean text."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    return soup.get_text(separator=" ").strip()


def _parse_gmp(raw: str) -> float:
    """
    Extract the numeric GMP value from an HTML-formatted GMP field.
    E.g. '₹<b>27</b> (20.45%)...' → 27.0
         '₹<b>--</b> (0.00%)...'  → 0.0
    Used as a fallback when the numeric API field is missing/unparseable.
    """
    text = _strip_html(raw)
    m = re.search(r"[\d.]+", text)
    if m:
        try:
            return float(m.group())
        except ValueError:
            pass
    return 0.0


def _extract_gmp(row: Dict[str, Any]) -> float:
    """
    Extract GMP, preferring the numeric API field (~max_gmp1) and falling
    back to parsing the HTML-formatted field if that's missing or malformed.
    """
    raw = row.get("~max_gmp1")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return _parse_gmp(row.get("GMP", ""))


def _parse_ipo_size(raw: str) -> float:
    """
    Extract IPO size in absolute rupees from '₹125.00 Cr' → 1_25_00_00_000.0
    The model expects size in individual rupees (Cr × 1e7).
    """
    text = _strip_html(raw)
    m = re.search(r"[\d.,]+", text.replace(",", ""))
    if m:
        try:
            cr = float(m.group())
            return cr * 1e7
        except ValueError:
            pass
    return 0.0


def _parse_price(raw: Any) -> float:
    """
    Extract numeric price from a value like '239', '₹ 239', or a price
    band such as '100-105' (common for IPOs whose band isn't final yet).
    For bands, returns the upper bound (the cap price), which is what
    GMP percentages are usually calculated against.
    """
    text = _strip_html(str(raw)).replace(",", "")
    nums = re.findall(r"[\d.]+", text)
    if not nums:
        return 0.0
    try:
        return max(float(n) for n in nums)
    except ValueError:
        return 0.0


def _parse_subscription(raw: Any) -> float:
    """
    Extract subscription multiplier.
    '2.95x' → 2.95, '-' → 0.0, '0.84x' → 0.84
    """
    text = _strip_html(str(raw)).lower().replace("x", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _get_financial_year(month: int, year: int) -> str:
    if month >= 4:
        return f"{year}-{str(year + 1)[2:]}"
    return f"{year - 1}-{str(year)[2:]}"


def _parse_date(date_str: Optional[str]) -> Optional[str]:
    """
    Parse a YYYY-MM-DD date string. Returns None if invalid or empty.
    Also handles 'None' / empty strings gracefully.
    """
    if not date_str or str(date_str).strip() in ("", "None", "-"):
        return None
    try:
        dt = datetime.strptime(str(date_str).strip(), "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


# ── Core fetch function ────────────────────────────────────────────────────────

def fetch_all_ipos(page: int = 1) -> List[Dict[str, Any]]:
    """
    Call the investorgain.com internal API and return a list of cleaned
    IPO dicts. Each dict has the same field names as what the old SheetDB
    data provided, plus extras.

    Returned fields per IPO:
        IPO               : str   – company name
        Apply Date        : str   – open/apply date (YYYY-MM-DD)
        Close Date        : str   – close date (YYYY-MM-DD)
        Listing Date      : str   – listing date (YYYY-MM-DD)
        IPO_Size          : float – total issue size in ₹ (Cr × 1e7)
        IPO Price         : float – cap price in ₹
        Subscription      : float – total subscription multiplier (x)
        GMP               : float – grey market premium in ₹
        GMP_Percent       : float – GMP as % of issue price
        Status            : str   – U / O / CT / C / L
        Category          : str   – IPO / SME
        Apply_Probability : float – set to 0.0 until /predict is called
    """
    now = datetime.now()
    month, year = now.month, now.year
    fin_year = _get_financial_year(month, year)

    url = (
        f"{API_BASE}cloud/v2/report/data-read/"
        f"{REPORT_ID}/{page}/{month}/{year}/{fin_year}/0/all"
        f"?search="
    )

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        logger.error("Failed to fetch investorgain data: %s", exc)
        return []
    except ValueError as exc:
        logger.error("Failed to parse investorgain JSON: %s", exc)
        return []

    if payload.get("msg") != 1:
        logger.error("investorgain API returned error: %s", payload.get("error"))
        return []

    rows = payload.get("reportTableData", [])
    #print(rows)
    result = []

    for row in rows:
        try:
            ipo_name = row.get("~ipo_name", "") or _strip_html(row.get("Name", ""))
            if not ipo_name:
                continue  # can't do anything useful without a name

            price        = _parse_price(row.get("Price (₹)", 0))
            ipo_size     = _parse_ipo_size(row.get("IPO Size", ""))
            gmp          = _extract_gmp(row)
            gmp_pct      = float(row.get("~gmp_percent_calc", 0) or 0)
            subscription = _parse_subscription(row.get("Sub", "-"))
            apply_date   = _parse_date(row.get("~Srt_Open"))
            close_date   = _parse_date(row.get("~Srt_Close"))
            listing_date = _parse_date(row.get("~Str_Listing"))
            status       = row.get("~ipo_status1", "")
            category     = row.get("~IPO_Category", "")

            # NOTE: we deliberately do NOT skip rows where price == 0.
            # IPOs that haven't announced a price band yet are still
            # useful to show on the calendar (as upcoming, unpriced) —
            # the /predict route is responsible for filtering rows that
            # aren't usable as model input.
            gmp_to_ipo_ratio = (gmp / price) if price > 0 else 0.0

            result.append({
                "IPO":               ipo_name,
                "Apply Date":        apply_date or "",
                "Close Date":        close_date or "",
                "Listing Date":      listing_date or "",
                "IPO_Size":          ipo_size,
                "IPO Price":         price,
                "Subscription":      subscription,
                "GMP":               gmp,
                "GMP_Percent":       gmp_pct,
                "GMP_to_IPO_Ratio":  gmp_to_ipo_ratio,
                "Status":            status,
                "Category":          category,
                "Apply_Probability": 0.0,  # populated by /predict route
            })

        except Exception as exc:
            logger.warning("Skipping row due to parse error: %s – %s", row.get("~ipo_name"), exc)
            continue

    logger.info("Fetched %d IPOs from investorgain.com (page %d)", len(result), page)
    return result


def _merge_manual_overrides(scraped: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Layer manually-added IPOs (from store.py, replacing the old
    SheetDB-backed /api/add_ipo) on top of scraped data. A manual entry
    with the same (IPO, Apply Date) key overrides the scraped one;
    otherwise it's appended.
    """
    manual = store.get_manual_ipos()
    if not manual:
        return scraped

    merged = {(ipo["IPO"], ipo.get("Apply Date", "")): ipo for ipo in scraped}
    for ipo in manual:
        key = (ipo.get("IPO", ""), ipo.get("Apply Date", ""))
        merged[key] = {**merged.get(key, {}), **ipo}
    return list(merged.values())


# ── Cached public interface ────────────────────────────────────────────────────

def get_ipo_data(force_refresh: bool = False) -> List[Dict[str, Any]]:
    stale = force_refresh or _redis.get(_FRESH_KEY) is None

    if stale:
        logger.info("Cache stale or empty — fetching fresh IPO data from investorgain.com")
        fresh = fetch_all_ipos()
        if fresh:
            ranked = _cache_get(_RANKED_KEY)
            if ranked:
                ranked_map = {r["IPO"]: r.get("Apply_Probability", 0.0) for r in ranked}
                for ipo in fresh:
                    if ipo["IPO"] in ranked_map:
                        ipo["Apply_Probability"] = ranked_map[ipo["IPO"]]
            _cache_set(_DATA_KEY, fresh)
            _redis.set(_FRESH_KEY, "1", ex=CACHE_TTL)
        else:
            logger.warning("Fetch failed — falling back to last cached data, if any.")

    base = _cache_get(_DATA_KEY) or []
    ranked = _cache_get(_RANKED_KEY)
    if ranked:
        ranked_map = {r["IPO"]: r.get("Apply_Probability", 0.0) for r in ranked}
        for ipo in base:
            if ipo["IPO"] in ranked_map:
                ipo["Apply_Probability"] = ranked_map[ipo["IPO"]]

    return _merge_manual_overrides(base)


def update_ranked_ipos(ranked: List[Dict[str, Any]]) -> None:
    """
    Store ranked IPOs (with Apply_Probability) returned by /predict.
    These are served in preference to raw data until next predict call.
    """
    _cache_set(_RANKED_KEY, ranked)
    logger.info("Updated ranked IPO cache with %d entries.", len(ranked))


def get_raw_ipo_data() -> List[Dict[str, Any]]:
    """Return the raw (unranked, un-merged) fetched IPO data, refreshing if stale."""
    stale = _redis.get(_FRESH_KEY) is None
    if stale:
        fresh = fetch_all_ipos()
        if fresh:
            _cache_set(_DATA_KEY, fresh)
            _redis.set(_FRESH_KEY, "1", ex=CACHE_TTL)
    return _cache_get(_DATA_KEY) or []