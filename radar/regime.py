"""Market regime: are the big indices in an uptrend, and how nervous is the market?"""

from __future__ import annotations

import pandas as pd

from . import indicators as ind

VIX_STRESS = 28.0


def _trend(close: pd.Series) -> dict | None:
    close = close.dropna()
    if len(close) < 200:
        return None
    sma200 = float(ind.sma(close, 200).iloc[-1])
    last = float(close.iloc[-1])
    return {"price": last, "vs_sma200": last / sma200 - 1, "uptrend": last > sma200}


def market_regime(close: pd.DataFrame, names: dict[str, str]) -> dict:
    """`close` holds the benchmark indices plus BTC-USD."""
    indices = {}
    for ticker in ("^GSPC", "^GDAXI", "^STOXX50E", "BTC-USD"):
        if ticker in close and (trend := _trend(close[ticker])):
            indices[ticker] = {"name": names.get(ticker, ticker), **trend}
    vix = float(close["^VIX"].dropna().iloc[-1]) if "^VIX" in close and close["^VIX"].notna().any() else None

    equity = [v["uptrend"] for k, v in indices.items() if k != "BTC-USD"]
    stressed = vix is not None and vix >= VIX_STRESS
    if equity and all(equity) and not stressed:
        state, label = "risk_on", "Risk-on"
        text = "Die großen Aktienindizes liegen über ihrer 200-Tage-Linie – Rückenwind für Käufe."
    elif equity and not any(equity):
        state, label = "risk_off", "Risk-off"
        text = "Die großen Aktienindizes liegen unter ihrer 200-Tage-Linie – Kapital schützen, kaum Neukäufe."
    else:
        state, label = "neutral", "Neutral"
        text = "Gemischtes Bild – nur die stärksten Signale handeln und Positionen kleiner halten."
    if stressed:
        text += f" Der VIX steht bei {vix:.0f} (Stress)."
    return {"state": state, "label": label, "text": text, "vix": vix, "indices": indices}
