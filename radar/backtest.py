"""Vectorised backtests with trading costs and an in-sample / out-of-sample split.

Convention: a weight decided on the close of day t earns the return of day t+1 (`weights.shift(1)`),
so no strategy can see the bar it trades on. Strategy parameters come from the literature and are not
optimised on this data; the out-of-sample column is what to trust.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind

OOS_START = "2021-01-01"


@dataclass
class Result:
    key: str
    name: str
    description: str
    returns: pd.Series
    periods_per_year: int
    survivorship_bias: bool = False


def portfolio_returns(close: pd.DataFrame, weights: pd.DataFrame, cost: float) -> pd.Series:
    """Daily strategy returns after costs. `cost` is charged per unit of turnover (0.001 = 0.1 %)."""
    asset_returns = close.pct_change(fill_method=None).fillna(0.0)
    held = weights.shift(1).fillna(0.0)
    turnover = (weights.fillna(0.0) - held).abs().sum(axis=1).shift(1).fillna(0.0)
    return (held * asset_returns).sum(axis=1) - turnover * cost


def monthly_hold(weights: pd.DataFrame) -> pd.DataFrame:
    """Keep only month-end decisions and hold them until the next month end."""
    month_end = weights.groupby([weights.index.year, weights.index.month]).tail(1).index
    return weights.loc[month_end].reindex(weights.index).ffill().fillna(0.0)


def buy_and_hold(close: pd.Series) -> pd.DataFrame:
    return close.notna().astype(float).to_frame()


def trend_sma(close: pd.Series, window: int = 200) -> pd.DataFrame:
    """Faber: hold while the month-end close is above its 200-day average, else cash."""
    return monthly_hold((close > ind.sma(close, window)).astype(float).to_frame())


def dual_momentum(close: pd.DataFrame, equities: list[str], safe: str, lookback: int = 252) -> pd.DataFrame:
    """Antonacci GEM: the stronger equity market if its 12-month return is positive, else bonds."""
    mom = ind.momentum(close, lookback)
    best = mom[equities].dropna().idxmax(axis=1)
    weights = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for day, winner in best.items():
        weights.at[day, winner if mom.at[day, winner] > 0 else safe] = 1.0
    return monthly_hold(weights)


def momentum_rotation(close: pd.DataFrame, top_n: int, periods_per_year: int = 252,
                      trend_window: int = 200) -> pd.DataFrame:
    """Top-N by 12-1 momentum, only assets above their 200-day line, inverse-volatility weighted.

    Slots that cannot be filled with an asset in an uptrend stay in cash.
    """
    month = periods_per_year // 12
    mom = ind.momentum(close, periods_per_year, month)
    eligible = (close > ind.sma(close, trend_window)) & (mom > 0)
    rank = mom.where(eligible).rank(axis=1, ascending=False)
    picked = rank <= top_n
    inv_vol = (1 / ind.annualized_vol(close, 63, periods_per_year)).where(picked)
    weights = inv_vol.div(inv_vol.sum(axis=1), axis=0).mul(picked.sum(axis=1) / top_n, axis=0)
    return monthly_hold(weights.fillna(0.0))


def rsi2_reversion(close: pd.Series) -> pd.DataFrame:
    """Connors: buy a sharp dip (RSI2 < 10) in an uptrend, sell once the close is back above its 5-day average."""
    entry = (ind.rsi(close, 2) < 10) & (close > ind.sma(close, 200))
    exit_ = close > ind.sma(close, 5)
    position = pd.Series(np.nan, index=close.index)
    position[exit_] = 0.0
    position[entry] = 1.0
    return position.ffill().fillna(0.0).to_frame(close.name)


def stats(returns: pd.Series, periods_per_year: int) -> dict:
    returns = returns.dropna()
    if len(returns) < periods_per_year // 4:
        return {}
    equity = (1 + returns).cumprod()
    years = len(returns) / periods_per_year
    cagr = float(equity.iloc[-1] ** (1 / years) - 1)
    vol = float(returns.std() * np.sqrt(periods_per_year))
    mdd = ind.max_drawdown(equity)
    return {
        "cagr": cagr,
        "vol": vol,
        "sharpe": float(returns.mean() * periods_per_year / vol) if vol > 0 else None,
        "max_drawdown": mdd,
        "calmar": cagr / abs(mdd) if mdd < 0 else None,
        "years": round(years, 1),
    }


def summarize(result: Result) -> dict:
    r = result.returns
    live = r.loc[r.ne(0).idxmax():] if r.ne(0).any() else r  # skip the warm-up before the first position
    equity = (1 + live).cumprod()
    weekly = equity.resample("W").last().dropna()
    return {
        "key": result.key,
        "name": result.name,
        "description": result.description,
        "survivorship_bias": result.survivorship_bias,
        "full": stats(live, result.periods_per_year),
        "in_sample": stats(live.loc[: pd.Timestamp(OOS_START) - pd.Timedelta(days=1)], result.periods_per_year),
        "out_of_sample": stats(live.loc[OOS_START:], result.periods_per_year),
        "curve": {"dates": [d.strftime("%Y-%m-%d") for d in weekly.index],
                  "equity": [round(float(v), 4) for v in weekly]},
    }


def run_all(closes: dict[str, pd.DataFrame], cost: float = 0.001, crypto_cost: float = 0.002) -> list[dict]:
    """`closes` maps market key -> cleaned close prices."""
    etf, us, eu, crypto = closes["etf"], closes["us"], closes["eu"], closes["crypto"]
    results: list[Result] = []

    def add(key, name, description, close, weights, cost_, ppy=252, biased=False):
        close = close.to_frame() if isinstance(close, pd.Series) else close
        results.append(Result(key, name, description, portfolio_returns(close, weights, cost_), ppy, biased))

    if "SPY" in etf:
        spy = etf["SPY"].dropna()
        add("bh_spy", "Buy & Hold S&P 500", "Referenz: SPY kaufen und liegen lassen.", spy, buy_and_hold(spy), cost)
        add("trend_spy", "Trendfolge S&P 500 (200-Tage-Linie)",
            "Investiert, solange SPY am Monatsende über der 200-Tage-Linie liegt, sonst Cash.",
            spy, trend_sma(spy), cost)
        add("rsi2_spy", "RSI(2)-Rücksetzer S&P 500",
            "Kauft scharfe Rücksetzer im Aufwärtstrend, verkauft über der 5-Tage-Linie.",
            spy, rsi2_reversion(spy), cost)
    gem = [t for t in ("SPY", "EFA", "IEF") if t in etf]
    if len(gem) == 3:
        sub = etf[gem].dropna()
        add("dual_momentum", "Dual Momentum (USA / Welt ex USA / Anleihen)",
            "Hält den stärkeren Aktienmarkt, wenn dessen 12-Monats-Rendite positiv ist, sonst Anleihen.",
            sub, dual_momentum(sub, ["SPY", "EFA"], "IEF"), cost)
    rotation = [t for t in ("SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD", "VNQ", "DBC") if t in etf]
    if len(rotation) >= 6:
        sub = etf[rotation].dropna()
        add("etf_rotation", "ETF-Rotation Top 3 (Momentum + Trendfilter)",
            "Monatlich die 3 stärksten Anlageklassen-ETFs über der 200-Tage-Linie, risikogewichtet.",
            sub, momentum_rotation(sub, 3), cost)
    for key, label, close in (("us", "US-Aktien", us), ("eu", "DE/EU-Aktien", eu)):
        if close.shape[1] >= 20:
            add(f"stock_momentum_{key}", f"Aktien-Momentum Top 10 ({label})",
                "Monatlich die 10 stärksten Aktien über der 200-Tage-Linie, risikogewichtet.",
                close, momentum_rotation(close, 10), cost, biased=True)
    if "BTC-USD" in crypto:
        btc = crypto["BTC-USD"].dropna()
        add("bh_btc", "Buy & Hold Bitcoin", "Referenz: Bitcoin kaufen und liegen lassen.",
            btc, buy_and_hold(btc), crypto_cost, ppy=365)
        add("trend_btc", "Trendfolge Bitcoin (200-Tage-Linie)",
            "Investiert, solange Bitcoin am Monatsende über der 200-Tage-Linie liegt, sonst Cash.",
            btc, trend_sma(btc), crypto_cost, ppy=365)
    if crypto.shape[1] >= 5:
        add("crypto_momentum", "Krypto-Momentum Top 3 (Trendfilter)",
            "Monatlich die 3 stärksten Coins über der 200-Tage-Linie, risikogewichtet.",
            crypto, momentum_rotation(crypto, 3, periods_per_year=365), crypto_cost, ppy=365, biased=True)
    return [summarize(r) for r in results]
