"""Market data access (Yahoo Finance via yfinance) and the asset universe."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

UNIVERSE_FILE = Path(__file__).with_name("universe.json")
MARKETS = ("us", "eu", "etf", "crypto")
MIN_HISTORY = 260  # bars needed before an asset is scored (SMA200 + buffer)
MAX_STALE_DAYS = 7


@dataclass
class Prices:
    close: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame

    def subset(self, tickers: list[str]) -> Prices:
        cols = [t for t in tickers if t in self.close.columns]
        rows = self.close[cols].dropna(how="all").index
        return Prices(self.close.loc[rows, cols], self.high.loc[rows, cols], self.low.loc[rows, cols])


def load_universe() -> dict:
    return json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))


def all_tickers(universe: dict) -> list[str]:
    tickers = [t for m in MARKETS for t in universe[m]["tickers"]]
    return tickers + list(universe["benchmarks"])


def _download_chunk(tickers: list[str], period: str, retries: int = 3) -> pd.DataFrame:
    import yfinance as yf

    for attempt in range(1, retries + 1):
        try:
            frame = yf.download(
                tickers, period=period, interval="1d", auto_adjust=True,
                progress=False, group_by="column", threads=True,
            )
            if not frame.empty:
                return frame
        except Exception as exc:  # yfinance raises a zoo of exception types
            log.warning("download attempt %d failed: %s", attempt, exc)
        time.sleep(5 * attempt)
    return pd.DataFrame()


def download_prices(tickers: list[str], period: str = "10y", chunk_size: int = 60) -> Prices:
    frames = []
    for i in range(0, len(tickers), chunk_size):
        frame = _download_chunk(tickers[i : i + chunk_size], period)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        raise RuntimeError("Keine Kursdaten erhalten (Yahoo Finance nicht erreichbar oder Rate-Limit).")
    data = pd.concat(frames, axis=1).sort_index()
    data.index = pd.to_datetime(data.index).tz_localize(None)
    return Prices(close=data["Close"], high=data["High"], low=data["Low"])


def clean_market(prices: Prices, now: pd.Timestamp | None = None) -> tuple[Prices, list[str]]:
    """Drop assets with too little or stale history; forward-fill short holiday gaps.

    Returns the cleaned prices and the list of dropped tickers.
    """
    now = now or prices.close.index.max()
    dropped = []
    for ticker in prices.close.columns:
        series = prices.close[ticker].dropna()
        stale = series.empty or (now - series.index.max()).days > MAX_STALE_DAYS
        if len(series) < MIN_HISTORY or stale:
            dropped.append(ticker)
    keep = [t for t in prices.close.columns if t not in dropped]
    return (
        Prices(
            close=prices.close[keep].ffill(limit=5),
            high=prices.high[keep].ffill(limit=5),
            low=prices.low[keep].ffill(limit=5),
        ),
        dropped,
    )
