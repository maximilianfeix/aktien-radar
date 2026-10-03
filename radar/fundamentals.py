"""Best-effort fundamentals for single stocks, cached on disk because Yahoo throttles these calls.

Used only as a tilt on the long-term score and for context on the page. It is not part of any backtest:
free point-in-time fundamentals do not exist, so backtesting them would leak today's numbers into the past.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

FIELDS = ("returnOnEquity", "profitMargins", "revenueGrowth", "forwardPE", "debtToEquity")
HIGHER_IS_BETTER = {"returnOnEquity": True, "profitMargins": True, "revenueGrowth": True,
                    "forwardPE": False, "debtToEquity": False}
CONTEXT_FIELDS = ("sector", "marketCap", "trailingPE", "dividendYield", "earningsTimestampStart", "earningsTimestamp")
MAX_AGE_HOURS = 24
CACHE_VERSION = 2
WORKERS = 8


def load(cache_file: Path, tickers: list[str], refresh: bool = True) -> dict[str, dict]:
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    age_hours = (time.time() - cache.get("fetched_at", 0)) / 3600
    outdated = age_hours > MAX_AGE_HOURS or not cache.get("data") or cache.get("version") != CACHE_VERSION
    if refresh and outdated:
        fetched = _fetch(tickers)
        if len(fetched) >= len(tickers) // 2:  # keep the old cache when Yahoo throttled us
            cache = {"version": CACHE_VERSION, "fetched_at": time.time(), "data": fetched}
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
    return cache.get("data", {})


def _fetch_one(ticker: str) -> dict:
    import yfinance as yf

    try:
        info = yf.Ticker(ticker).info
    except Exception as exc:  # yfinance raises a zoo of exception types
        log.warning("fundamentals for %s failed: %s", ticker, exc)
        return {}
    row = {f: info[f] for f in FIELDS + CONTEXT_FIELDS if isinstance(info.get(f), int | float) and f != "sector"}
    if isinstance(info.get("sector"), str):
        row["sector"] = info["sector"]
    return row


def _fetch(tickers: list[str]) -> dict[str, dict]:
    with ThreadPoolExecutor(WORKERS) as pool:
        rows = pool.map(_fetch_one, tickers)
    return {ticker: row for ticker, row in zip(tickers, rows, strict=True) if row}


def quality_rank(data: dict[str, dict], tickers: list[str]) -> pd.Series:
    """Mean percentile rank over the available fields, 1 = best. NaN when fewer than 3 fields exist."""
    frame = pd.DataFrame.from_dict(data, orient="index").reindex(tickers).reindex(columns=list(FIELDS))
    frame = frame.apply(pd.to_numeric, errors="coerce")
    frame.loc[frame["forwardPE"] <= 0, "forwardPE"] = float("nan")  # loss-makers have no meaningful P/E
    ranks = pd.DataFrame({f: frame[f].rank(pct=True, ascending=HIGHER_IS_BETTER[f]) for f in FIELDS})
    return ranks.mean(axis=1).where(ranks.notna().sum(axis=1) >= 3)


def context(data: dict[str, dict], now: float | None = None) -> dict[str, dict]:
    """Per ticker: sector, market cap, P/E, dividend yield and days until the next earnings date."""
    now = now or time.time()
    out = {}
    for ticker, row in data.items():
        upcoming = [row[f] for f in ("earningsTimestampStart", "earningsTimestamp") if row.get(f, 0) >= now - 86400]
        pe = row.get("trailingPE")
        out[ticker] = {
            "sector": row.get("sector"),
            "market_cap": row.get("marketCap"),
            "pe": pe if pe and pe > 0 else None,
            "dividend_yield": _as_fraction(row.get("dividendYield")),
            "earnings_in_days": max(0, int((min(upcoming) - now) // 86400)) if upcoming else None,
        }
    return out


def _as_fraction(dividend_yield: float | None) -> float | None:
    """yfinance reports the dividend yield in percent (1.8 means 1.8 %)."""
    return None if dividend_yield is None else dividend_yield / 100
