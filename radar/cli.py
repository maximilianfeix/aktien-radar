"""Command line entry point: `python -m radar run`."""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from . import ai, backtest, fundamentals, notify, regime, report, scoring, track
from . import indicators as ind
from .data import MARKETS, all_tickers, clean_market, download_prices, load_universe

log = logging.getLogger("radar")

CHART_SESSIONS = 260  # about one year
CHART_STEP = 2  # keep every second session to halve the file


def _read(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_if_changed(path: Path, value) -> bool:
    """Skip the write when nothing changed, so files that only move once a day do not churn the repo hourly."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def chart_series(close: pd.DataFrame) -> dict:
    """One year of finished sessions per asset with its 50- and 200-day averages, for the detail chart."""
    rows = close.index[-CHART_SESSIONS:][::-1][::CHART_STEP][::-1]
    sma50, sma200 = ind.sma(close, 50).loc[rows], ind.sma(close, 200).loc[rows]

    def column(frame: pd.DataFrame, ticker: str) -> list:
        return [None if pd.isna(v) else float(f"{v:.5g}") for v in frame[ticker]]

    prices = close.loc[rows]
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in rows],
        "series": {t: {"c": column(prices, t), "s50": column(sma50, t), "s200": column(sma200, t)}
                   for t in close.columns},
    }


def run(data_dir: Path, out_dir: Path, with_fundamentals: bool = True) -> dict:
    started = time.perf_counter()
    universe = load_universe()
    prices = download_prices(all_tickers(universe))
    names = {t: n for m in MARKETS for t, n in universe[m]["tickers"].items()} | universe["benchmarks"]
    log.info("Kursdaten geladen in %.1f s", time.perf_counter() - started)

    latest_file = data_dir / "latest.json"
    previous = _read(latest_file, None)

    regime_tickers = [t for t in (*universe["benchmarks"], "BTC-USD") if t in prices.close]
    market_regime = regime.market_regime(prices.close[regime_tickers], names)
    risk_off = market_regime["state"] == "risk_off"

    stocks = [t for m in ("us", "eu") for t in universe[m]["tickers"]]
    fundamental_data = fundamentals.load(data_dir / "fundamentals.json", stocks, refresh=with_fundamentals)
    meta = fundamentals.context(fundamental_data)

    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    assets, closes, charts, dropped = [], {}, {}, []
    for market in MARKETS:
        tickers = list(universe[market]["tickers"])
        cleaned, gone = clean_market(prices.subset(tickers), now=today)
        dropped += gone + [t for t in tickers if t not in prices.close.columns]
        # Signals use the latest price; backtests and charts only count finished sessions.
        closes[market] = cleaned.close.loc[cleaned.close.index < today]
        if cleaned.close.empty:  # Yahoo returned nothing usable for this market; publish the others
            log.warning("Keine aktuellen Kursdaten für den Markt %s", market)
            continue
        charts[market] = chart_series(closes[market])
        periods = 365 if market == "crypto" else 252
        quality = fundamentals.quality_rank(fundamental_data, tickers) if market in ("us", "eu") else None
        # Crypto follows its own cycle, so the equity regime does not gate it.
        scored = scoring.score_market(cleaned, periods, quality, risk_off and market != "crypto")
        assets += report.asset_rows(scored, market, names, meta)

    backtests = backtest.run_all(closes)
    markets = {m: universe[m]["label"] for m in MARKETS}
    payload = report.build_payload(datetime.now(UTC), market_regime, assets, markets, backtests, sorted(set(dropped)))
    stamp = payload["generated_at"]

    previous_picks = previous["picks"]["overall"] if previous else None
    by_ticker = {a["ticker"]: a for a in assets}
    ai_input = {
        "regime": payload["regime"],
        "picks": payload["picks"],
        "previous_picks": previous_picks,
        "candidates": {h: [{k: v for k, v in by_ticker[t].items() if k != "spark"}
                           for t in payload["picks"]["overall"][h]] for h in ("short", "long")},
    }
    payload["ai"] = ai.commentary(ai_input, previous.get("ai") if previous else None)

    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    changes = track.signal_changes(previous["assets"] if previous else [], payload["assets"], stamp)
    change_log = track.append_changes(data_dir / "changes.json", changes)
    finished = pd.concat([c for c in closes.values() if not c.empty], axis=1) if any(
        not c.empty for c in closes.values()) else None
    record = track.update_track(data_dir / "track.json", payload["assets"], stamp, finished)
    payload["track"] = record["stats"] | {"since": record["since"]}
    payload["core"] = next((
        {k: b[k] for k in ("key", "name", "description", "allocation", "out_of_sample")}
        for b in backtests if b["key"] == payload["core_strategy"]), None)

    (out_dir / "report.md").write_text(report.markdown(payload, previous_picks, changes, record), encoding="utf-8")
    # The page loads backtests and charts on demand; they only change once a day.
    _write_if_changed(data_dir / "backtests.json", payload.pop("backtests"))
    _write_if_changed(data_dir / "charts.json", charts)
    _write_if_changed(latest_file, payload)
    report.update_history(data_dir / "history.json", payload)
    (data_dir.parent / "feed.xml").write_text(track.atom_feed(change_log, report.PAGE_URL, stamp), encoding="utf-8")

    picks_changed = previous_picks is None or previous_picks != payload["picks"]["overall"]
    (out_dir / "picks_changed").write_text("true" if picks_changed else "false", encoding="utf-8")
    top = payload["picks"]["overall"]
    summary = (f"kurzfristig {names.get(top['short'][0]) if top['short'] else 'kein Signal'}, "
               f"langfristig {names.get(top['long'][0]) if top['long'] else 'kein Signal'}")
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    if picks_changed and previous_picks is not None:
        sent = notify.send("Aktien-Radar: neue Top-Kandidaten", summary, report.PAGE_URL)
        if sent:
            log.info("Benachrichtigt über: %s", ", ".join(sent))
    log.info("Fertig in %.1f s: %s (%d Werte, %d ohne Daten, %d Signalwechsel)", time.perf_counter() - started,
             summary, len(assets), len(payload["dropped"]), len(changes))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(prog="radar", description="Aktien-Radar: Signale und Backtests erzeugen")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="Daten laden, Signale und Backtests berechnen, Dateien schreiben")
    run_parser.add_argument("--data-dir", type=Path, default=Path("docs/data"))
    run_parser.add_argument("--out-dir", type=Path, default=Path("out"))
    run_parser.add_argument("--no-fundamentals", action="store_true", help="Fundamentaldaten nicht neu laden")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run(args.data_dir, args.out_dir, with_fundamentals=not args.no_fundamentals)


if __name__ == "__main__":
    main()
