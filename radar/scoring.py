"""Turn prices into a long-term and a short-term score per asset, with plain-language reasons.

Weights are fixed up front from the factor literature (momentum, trend, low volatility) and are
deliberately not fitted to the backtest, to keep overfitting out.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from .data import Prices

BUY, WATCH, HOLD, AVOID = "Kaufen", "Beobachten", "Halten", "Meiden"

LONG_WEIGHTS = {"mom_12_1": 0.35, "mom_6m": 0.15, "trend": 0.20, "low_vol": 0.15, "near_high": 0.15}
SHORT_WEIGHTS = {"mom_1m": 0.30, "near_high": 0.25, "pullback": 0.20, "trend": 0.25}
QUALITY_BLEND = 0.20  # share of the long score taken by fundamentals when available
STOP_ATR_MULTIPLE = 2.5


def compute_metrics(prices: Prices, periods_per_year: int) -> pd.DataFrame:
    """Latest-bar metrics, one row per asset."""
    close, year, month = prices.close, periods_per_year, periods_per_year // 12
    sma50, sma200 = ind.sma(close, 50), ind.sma(close, 200)
    last = close.iloc[-1]
    atr = ind.atr(prices.high, prices.low, close).iloc[-1]
    return pd.DataFrame({
        "price": last,
        "chg_1d": close.pct_change(fill_method=None).iloc[-1],
        "mom_1w": ind.momentum(close, 5).iloc[-1],
        "mom_1m": ind.momentum(close, month).iloc[-1],
        "mom_6m": ind.momentum(close, year // 2).iloc[-1],
        "mom_12_1": ind.momentum(close, year, month).iloc[-1],
        "vs_sma200": last / sma200.iloc[-1] - 1,
        "sma50_above_200": sma50.iloc[-1] > sma200.iloc[-1],
        "sma200_rising": sma200.iloc[-1] > sma200.shift(month).iloc[-1],
        "vol": ind.annualized_vol(close, 63, year).iloc[-1],
        "from_high": ind.drawdown_from_high(close, year).iloc[-1],
        "rsi14": ind.rsi(close, 14).iloc[-1],
        "rsi2": ind.rsi(close, 2).iloc[-1],
        "atr_pct": atr / last,
        "stop": last - STOP_ATR_MULTIPLE * atr,
    })


def _pullback(m: pd.DataFrame) -> pd.Series:
    """1 = fresh dip inside an uptrend, 0 = overbought or no uptrend."""
    uptrend = m["vs_sma200"] > 0
    score = pd.Series(0.4, index=m.index)
    score[uptrend & m["rsi14"].between(40, 60)] = 0.7
    score[uptrend & (m["rsi2"] < 15)] = 1.0
    score[m["rsi14"] > 75] = 0.0
    score[~uptrend] = 0.0
    return score


def score_market(prices: Prices, periods_per_year: int, quality: pd.Series | None = None,
                 risk_off: bool = False) -> pd.DataFrame:
    m = compute_metrics(prices, periods_per_year)
    uptrend = m["vs_sma200"] > 0
    trend = (uptrend.astype(float) + m["sma50_above_200"] + m["sma200_rising"]) / 3

    parts = {
        "mom_12_1": ind.pct_rank(m["mom_12_1"]),
        "mom_6m": ind.pct_rank(m["mom_6m"]),
        "mom_1m": ind.pct_rank(m["mom_1m"]),
        "low_vol": ind.pct_rank(-m["vol"]),
        "near_high": ind.pct_rank(m["from_high"]),
        "trend": trend,
        "pullback": _pullback(m),
    }
    # An asset without enough history for a factor gets the neutral 0.5 instead of dropping out.
    m["long_score"] = 100 * sum(w * parts[k].fillna(0.5) for k, w in LONG_WEIGHTS.items())
    m["short_score"] = 100 * sum(w * parts[k].fillna(0.5) for k, w in SHORT_WEIGHTS.items())

    m["quality"] = np.nan
    if quality is not None and quality.notna().any():
        m["quality"] = quality.reindex(m.index)
        blended = (1 - QUALITY_BLEND) * m["long_score"] + QUALITY_BLEND * 100 * m["quality"]
        m["long_score"] = blended.where(m["quality"].notna(), m["long_score"])

    # Absolute momentum gate (dual momentum): being the best of a falling market is not a buy.
    rising = (m["mom_12_1"] > 0) & (m["mom_6m"] > 0)
    m["long_signal"] = np.select(
        [uptrend & rising & (m["long_score"] >= 70), uptrend & (m["long_score"] >= 50), m["long_score"] >= 50],
        [BUY, HOLD, WATCH], default=AVOID,
    )
    short_buy = uptrend & (m["short_score"] >= 70) & (m["rsi14"] <= 75)
    m["short_signal"] = np.select(
        [short_buy & (not risk_off), short_buy | (m["short_score"] >= 55)], [BUY, WATCH], default=AVOID,
    )
    m["pct_12_1"] = parts["mom_12_1"]
    m["reasons"] = [_reasons(row) for _, row in m.iterrows()]
    m["risks"] = [_risks(row, risk_off) for _, row in m.iterrows()]
    return m.sort_values("long_score", ascending=False)


def _pct(x: float) -> str:
    return f"{x * 100:+.0f} %".replace(".", ",")


def _reasons(r: pd.Series) -> list[str]:
    out = []
    if pd.notna(r["mom_12_1"]) and r["mom_12_1"] > 0:
        top = f" (Top {max(1, round((1 - r['pct_12_1']) * 100))} % im Markt)" if r["pct_12_1"] >= 0.7 else ""
        out.append(f"12-1-Momentum {_pct(r['mom_12_1'])}{top}")
    if pd.notna(r["vs_sma200"]) and r["vs_sma200"] > 0:
        rising = ", 200-Tage-Linie steigt" if r["sma200_rising"] else ""
        out.append(f"Aufwärtstrend: Kurs {_pct(r['vs_sma200'])} über der 200-Tage-Linie{rising}")
    if pd.notna(r["from_high"]) and r["from_high"] > -0.05:
        out.append(f"Nahe am 52-Wochen-Hoch ({_pct(r['from_high'])})")
    if r["vs_sma200"] > 0 and r["rsi2"] < 15:
        out.append(f"Kurzer Rücksetzer im Aufwärtstrend (RSI2 {r['rsi2']:.0f}) – günstiger Einstieg")
    if pd.notna(r["mom_1m"]) and r["mom_1m"] > 0.05:
        out.append(f"Starker letzter Monat ({_pct(r['mom_1m'])})")
    if pd.notna(r["quality"]) and r["quality"] >= 0.7:
        out.append("Überdurchschnittliche Qualität/Bewertung (Marge, Eigenkapitalrendite, KGV)")
    return out


def _risks(r: pd.Series, risk_off: bool) -> list[str]:
    out = []
    if pd.notna(r["vs_sma200"]) and r["vs_sma200"] <= 0:
        out.append(f"Abwärtstrend: Kurs {_pct(r['vs_sma200'])} unter der 200-Tage-Linie")
    if r["rsi14"] > 75:
        out.append(f"Überkauft (RSI14 {r['rsi14']:.0f}) – Rücksetzer wahrscheinlich")
    if pd.notna(r["vol"]) and r["vol"] > 0.45:
        out.append(f"Hohe Schwankung ({r['vol'] * 100:.0f} % p. a.) – kleinere Position wählen")
    if pd.notna(r["from_high"]) and r["from_high"] < -0.25:
        out.append(f"{_pct(r['from_high'])} unter dem 52-Wochen-Hoch")
    if pd.notna(r["mom_12_1"]) and r["mom_12_1"] < 0:
        out.append(f"Negatives 12-1-Momentum ({_pct(r['mom_12_1'])})")
    if risk_off:
        out.append("Gesamtmarkt im Risk-off-Modus – kurzfristige Käufe ausgesetzt")
    return out
