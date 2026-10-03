"""Best-effort fundamentals for single stocks, cached on disk because Yahoo throttles these calls.

Used only as a tilt on the long-term score. It is not part of any backtest: free point-in-time
fundamentals do not exist, so backtesting them would leak today's numbers into the past.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

FIELDS = ("returnOnEquity", "profitMargins", "revenueGrowth", "forwardPE", "debtToEquity")
HIGHER_IS_BETTER = {"returnOnEquity": True, "profitMargins": True, "revenueGrowth": True,
                    "forwardPE": False, "debtToEquity": False}
MAX_AGE_HOURS = 24


def load(cache_file: Path, tickers: list[str], refresh: bool = True) -> dict[str, dict]:
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
    age_hours = (time.time() - cache.get("fetched_at", 0)) / 3600
    if refresh and (age_hours > MAX_AGE_HOURS or not cache.get("data")):
        fetched = _fetch(tickers)
        if len(fetched) >= len(tickers) // 2:  # keep the old cache when Yahoo throttled us
            cache = {"fetched_at": time.time(), "data": fetched}
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
    return cache.get("data", {})


def _fetch(tickers: list[str]) -> dict[str, dict]:
    import yfinance as yf

    out = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
        except Exception as exc:
            log.warning("fundamentals for %s failed: %s", ticker, exc)
            continue
        row = {f: info.get(f) for f in FIELDS if isinstance(info.get(f), int | float)}
        if row:
            out[ticker] = row
        time.sleep(0.15)
    return out


def quality_rank(data: dict[str, dict], tickers: list[str]) -> pd.Series:
    """Mean percentile rank over the available fields, 1 = best. NaN when fewer than 3 fields exist."""
    frame = pd.DataFrame.from_dict(data, orient="index").reindex(tickers).reindex(columns=list(FIELDS))
    frame = frame.apply(pd.to_numeric, errors="coerce")
    frame.loc[frame["forwardPE"] <= 0, "forwardPE"] = float("nan")  # loss-makers have no meaningful P/E
    ranks = pd.DataFrame({f: frame[f].rank(pct=True, ascending=HIGHER_IS_BETTER[f]) for f in FIELDS})
    return ranks.mean(axis=1).where(ranks.notna().sum(axis=1) >= 3)
