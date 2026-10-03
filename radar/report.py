"""Assemble the JSON the GitHub Page reads and the Markdown used for issues and pull requests."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import pandas as pd

from .scoring import BUY

DISCLAIMER = ("Keine Anlageberatung. Regelbasierte Signale aus Kursdaten – vergangene Renditen sind keine "
              "Garantie für die Zukunft. Verluste bis zum Totalverlust sind möglich.")
PAGE_URL = "https://maximilianfeix.github.io/aktien-radar/"
HISTORY_LIMIT = 24 * 60  # hourly entries kept (60 days)
ROW_FIELDS = ["price", "chg_1d", "mom_1w", "mom_1m", "mom_6m", "mom_12_1", "vs_sma200", "vol", "from_high",
              "rsi14", "rsi2", "atr_pct", "stop", "quality", "long_score", "short_score", "long_signal",
              "short_signal", "reasons", "risks", "target", "spark"]
META_FIELDS = ["sector", "market_cap", "pe", "dividend_yield", "earnings_in_days"]
EARNINGS_WARNING_DAYS = 7


def _clean(value):
    """Make a value JSON-safe: NaN/inf -> None, numpy scalars -> Python, floats rounded."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, str):
        value = value.item()
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else round(value, 4)
    return value


def asset_rows(scored: pd.DataFrame, market: str, names: dict[str, str],
               meta: dict[str, dict] | None = None) -> list[dict]:
    rows = []
    for ticker, r in scored.iterrows():
        row = {"ticker": ticker, "name": names.get(ticker, ticker), "market": market,
               **{f: r[f] for f in ROW_FIELDS}, **{f: (meta or {}).get(ticker, {}).get(f) for f in META_FIELDS}}
        days = row["earnings_in_days"]
        if days is not None and 0 <= days <= EARNINGS_WARNING_DAYS:
            when = "heute" if days == 0 else "morgen" if days == 1 else f"in {days} Tagen"
            row["risks"] = [*row["risks"], f"Quartalszahlen {when} – Kurssprünge in beide Richtungen möglich"]
        rows.append(row)
    return _clean(rows)


def breadth(assets: list[dict], markets: dict[str, str]) -> dict[str, dict]:
    """Share of assets trading above their 200-day line, per market."""
    out = {}
    for market in markets:
        known = [a for a in assets if a["market"] == market and a["vs_sma200"] is not None]
        if known:
            out[market] = {"above": sum(a["vs_sma200"] > 0 for a in known), "total": len(known)}
    return out


def top_picks(assets: list[dict], horizon: str, n: int = 3) -> list[dict]:
    """Best `Kaufen` candidates for a horizon ('long' or 'short'), strongest first."""
    buys = [a for a in assets if a[f"{horizon}_signal"] == BUY]
    return sorted(buys, key=lambda a: a[f"{horizon}_score"], reverse=True)[:n]


def build_payload(now: datetime, regime: dict, assets: list[dict], markets: dict[str, str], backtests: list[dict],
                  dropped: list[str]) -> dict:
    picks = {}
    for market in markets:
        in_market = [a for a in assets if a["market"] == market]
        picks[market] = {h: [a["ticker"] for a in top_picks(in_market, h)] for h in ("short", "long")}
    overall = {h: [a["ticker"] for a in top_picks(assets, h, 5)] for h in ("short", "long")}
    return _clean({
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "disclaimer": DISCLAIMER,
        "regime": regime,
        "markets": markets,
        "picks": {"overall": overall, **picks},
        "assets": assets,
        "breadth": breadth(assets, markets),
        "backtests": backtests,
        "core_strategy": core_strategy(backtests),
        "dropped": dropped,
        "ai": None,
    })


def core_strategy(backtests: list[dict]) -> str | None:
    """Key of the strategy with the best out-of-sample return per unit of drawdown (Calmar).

    Strategies with survivorship bias are excluded: their universe was picked with hindsight.
    """
    fair = [b for b in backtests
            if not b["survivorship_bias"] and (b["out_of_sample"] or {}).get("calmar") is not None]
    return max(fair, key=lambda b: b["out_of_sample"]["calmar"])["key"] if fair else None


def update_history(history_file: Path, payload: dict) -> list[dict]:
    history = json.loads(history_file.read_text(encoding="utf-8")) if history_file.exists() else []
    by_ticker = {a["ticker"]: a for a in payload["assets"]}
    entry = {"at": payload["generated_at"], "regime": payload["regime"]["state"]}
    for horizon in ("short", "long"):
        tickers = payload["picks"]["overall"][horizon]
        top = by_ticker.get(tickers[0]) if tickers else None
        entry[horizon] = top and {"ticker": top["ticker"], "name": top["name"], "price": top["price"],
                                  "score": top[f"{horizon}_score"]}
    history.append(entry)
    history = history[-HISTORY_LIMIT:]
    history_file.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
    return history


def _fmt_pct(x, digits: int = 1) -> str:
    return "–" if x is None else f"{x * 100:+.{digits}f} %".replace(".", ",")


def _fmt_price(x) -> str:
    return "–" if x is None else f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pick_table(assets: list[dict], horizon: str) -> list[str]:
    if not assets:
        return ["_Aktuell kein Kaufsignal – Cash ist auch eine Position._", ""]
    lines = ["| Wert | Markt | Kurs | Score | 1 M | 12-1 M | Stop | Ziel | Warum |",
             "|---|---|--:|--:|--:|--:|--:|--:|---|"]
    for a in assets:
        why = "; ".join(a["reasons"][:2]) or "–"
        lines.append(f"| **{a['name']}** (`{a['ticker']}`) | {a['market_label']} | {_fmt_price(a['price'])} | "
                     f"{a[f'{horizon}_score']:.0f} | {_fmt_pct(a['mom_1m'])} | {_fmt_pct(a['mom_12_1'])} | "
                     f"{_fmt_price(a['stop'])} | {_fmt_price(a['target'])} | {why} |")
    return [*lines, ""]


HORIZON_LABEL = {"short": "kurzfristig", "long": "langfristig"}


def markdown(payload: dict, previous_picks: dict | None = None, signal_changes: list[dict] | None = None,
             track: dict | None = None) -> str:
    names = {a["ticker"]: a["name"] for a in payload["assets"]}
    by_ticker = {a["ticker"]: {**a, "market_label": payload["markets"][a["market"]]} for a in payload["assets"]}
    overall = payload["picks"]["overall"]
    regime = payload["regime"]
    stamp = payload["generated_at"].replace("T", " ").replace("Z", " UTC")
    lines = [f"## Aktien-Radar – Stand {stamp}", "", f"**Marktlage: {regime['label']}.** {regime['text']}", ""]
    if payload.get("ai"):
        lines += ["### Einordnung (Claude)", "", payload["ai"]["text"], ""]
    lines += ["### Kurzfristig (Tage bis Wochen)", ""]
    lines += _pick_table([by_ticker[t] for t in overall["short"]], "short")
    lines += ["### Langfristig (Monate bis Jahre)", ""]
    lines += _pick_table([by_ticker[t] for t in overall["long"]], "long")
    if previous_picks is not None:
        changes = []
        for horizon, label in (("short", "kurzfristig"), ("long", "langfristig")):
            new = [t for t in overall[horizon] if t not in previous_picks.get(horizon, [])]
            gone = [t for t in previous_picks.get(horizon, []) if t not in overall[horizon]]
            if new or gone:
                changes.append(f"- {label}: neu {', '.join(f'`{t}`' for t in new) or '–'}, "
                               f"raus {', '.join(f'`{t}`' for t in gone) or '–'}")
        lines += ["### Änderungen seit dem letzten Lauf", "", *(changes or ["- keine"]), ""]
    core = next((b for b in payload.get("backtests", []) if b["key"] == payload["core_strategy"]), None)
    if core:
        oos = core["out_of_sample"]
        lines += ["### Kernstrategie laut Backtest", "",
                  f"**{core['name']}** – Out-of-Sample seit 2021: {_fmt_pct(oos['cagr'])} p. a., "
                  f"Sharpe {oos['sharpe']:.2f}, maximaler Rückgang {_fmt_pct(oos['max_drawdown'])}.", ""]
        if core.get("allocation"):
            parts = [f"{names.get(t, t)} {w * 100:.0f} %" for t, w in core["allocation"].items()]
            lines += [f"Aktuelle Zielaufteilung: {', '.join(parts)}.", ""]
    if signal_changes:
        lines += ["### Signalwechsel in diesem Lauf", ""]
        lines += [f"- **{c['name']}** (`{c['ticker']}`), {HORIZON_LABEL[c['horizon']]}: {c['from']} → {c['to']}"
                  for c in signal_changes[:15]]
        lines += [""]
    if track and track["stats"]["closed"]:
        st = track["stats"]
        lines += ["### Live-Bilanz der Kaufsignale", "",
                  f"{st['closed']} abgeschlossene Signale, Trefferquote {st['hit_rate'] * 100:.0f} %, "
                  f"durchschnittlich {_fmt_pct(st['avg_return'])}.", ""]
    lines += [f"Alle Werte, Charts und Backtests: {PAGE_URL}", "", f"_{payload['disclaimer']}_", ""]
    return "\n".join(lines)
