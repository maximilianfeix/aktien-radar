"""Pure indicator functions. Everything here works on pandas Series/DataFrames of prices."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.DataFrame | pd.Series, window: int):
    return close.rolling(window, min_periods=window).mean()


def rsi(close: pd.DataFrame | pd.Series, period: int = 14):
    """Wilder RSI. 100 when there were no down moves in the window, NaN when flat."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def atr(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    prev = close.shift(1)
    true_range = np.maximum(high - low, np.maximum((high - prev).abs(), (low - prev).abs()))
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def momentum(close: pd.DataFrame | pd.Series, lookback: int, skip: int = 0):
    """Return from `lookback` bars ago to `skip` bars ago (12-1 momentum: lookback=252, skip=21)."""
    return close.shift(skip) / close.shift(lookback) - 1


def annualized_vol(close: pd.DataFrame | pd.Series, window: int, periods_per_year: int):
    returns = close.pct_change(fill_method=None)
    return returns.rolling(window, min_periods=window // 2).std() * np.sqrt(periods_per_year)


def drawdown_from_high(close: pd.DataFrame | pd.Series, window: int):
    """Distance to the rolling high, <= 0."""
    return close / close.rolling(window, min_periods=window // 2).max() - 1


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return float("nan")
    return float((equity / equity.cummax() - 1).min())


def pct_rank(values: pd.Series) -> pd.Series:
    """Cross-sectional percentile rank in (0, 1]; NaN stays NaN."""
    return values.rank(pct=True)
