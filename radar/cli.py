"""Command line entry point: `python -m radar run`."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from . import ai, backtest, fundamentals, regime, report, scoring
from .data import MARKETS, all_tickers, clean_market, download_prices, load_universe

log = logging.getLogger("radar")


def run(data_dir: Path, out_dir: Path, with_fundamentals: bool = True) -> dict:
    universe = load_universe()
    prices = download_prices(all_tickers(universe))
    names = {t: n for m in MARKETS for t, n in universe[m]["tickers"].items()} | universe["benchmarks"]

    latest_file = data_dir / "latest.json"
    previous = json.loads(latest_file.read_text(encoding="utf-8")) if latest_file.exists() else None

    regime_tickers = [t for t in (*universe["benchmarks"], "BTC-USD") if t in prices.close]
    market_regime = regime.market_regime(prices.close[regime_tickers], names)
    risk_off = market_regime["state"] == "risk_off"

    stocks = [t for m in ("us", "eu") for t in universe[m]["tickers"]]
    fundamental_data = fundamentals.load(data_dir / "fundamentals.json", stocks, refresh=with_fundamentals)

    today = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    assets, closes, dropped = [], {}, []
    for market in MARKETS:
        tickers = list(universe[market]["tickers"])
        cleaned, gone = clean_market(prices.subset(tickers), now=today)
        dropped += gone + [t for t in tickers if t not in prices.close.columns]
        closes[market] = cleaned.close
        if cleaned.close.empty:  # Yahoo returned nothing usable for this market; publish the others
            log.warning("Keine aktuellen Kursdaten für den Markt %s", market)
            continue
        periods = 365 if market == "crypto" else 252
        quality = fundamentals.quality_rank(fundamental_data, tickers) if market in ("us", "eu") else None
        # Crypto follows its own cycle, so the equity regime does not gate it.
        scored = scoring.score_market(cleaned, periods, quality, risk_off and market != "crypto")
        assets += report.asset_rows(scored, market, names)

    backtests = backtest.run_all(closes)
    markets = {m: universe[m]["label"] for m in MARKETS}
    payload = report.build_payload(datetime.now(UTC), market_regime, assets, markets, backtests, sorted(set(dropped)))

    previous_picks = previous["picks"]["overall"] if previous else None
    by_ticker = {a["ticker"]: a for a in assets}
    ai_input = {
        "regime": payload["regime"],
        "picks": payload["picks"],
        "previous_picks": previous_picks,
        "candidates": {h: [by_ticker[t] for t in payload["picks"]["overall"][h]] for h in ("short", "long")},
    }
    payload["ai"] = ai.commentary(ai_input, previous.get("ai") if previous else None)

    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    latest_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report.update_history(data_dir / "history.json", payload)

    (out_dir / "report.md").write_text(report.markdown(payload, previous_picks), encoding="utf-8")
    changed = previous_picks is None or previous_picks != payload["picks"]["overall"]
    (out_dir / "picks_changed").write_text("true" if changed else "false", encoding="utf-8")
    top = payload["picks"]["overall"]
    summary = (f"kurzfristig {names.get(top['short'][0]) if top['short'] else 'kein Signal'}, "
               f"langfristig {names.get(top['long'][0]) if top['long'] else 'kein Signal'}")
    (out_dir / "summary.txt").write_text(summary, encoding="utf-8")
    log.info("Fertig: %s (%d Werte, %d ohne Daten)", summary, len(assets), len(payload["dropped"]))
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
