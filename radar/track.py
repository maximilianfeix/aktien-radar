"""What changed and how the signals did afterwards.

`signal_changes` lists upgrades and downgrades since the previous run. `update_track` keeps a forward
record of every buy signal from the moment it appeared to the moment it ended. Unlike a backtest this
record cannot be tuned after the fact, so it is the honest scoreboard.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd

from .scoring import BUY

HORIZONS = ("short", "long")
CHANGES_LIMIT = 300
CLOSED_LIMIT = 500


def signal_changes(previous_assets: list[dict], assets: list[dict], at: str) -> list[dict]:
    """Signals that differ from the previous run, moves to or from `Kaufen` first."""
    before = {a["ticker"]: a for a in previous_assets}
    changes = []
    for asset in assets:
        old = before.get(asset["ticker"])
        if not old:
            continue
        for horizon in HORIZONS:
            was, now = old[f"{horizon}_signal"], asset[f"{horizon}_signal"]
            if was != now:
                changes.append({"at": at, "ticker": asset["ticker"], "name": asset["name"], "horizon": horizon,
                                "from": was, "to": now, "price": asset["price"]})
    return sorted(changes, key=lambda c: BUY not in (c["from"], c["to"]))


def append_changes(changes_file: Path, new: list[dict]) -> list[dict]:
    log = json.loads(changes_file.read_text(encoding="utf-8")) if changes_file.exists() else []
    log = (new + log)[:CHANGES_LIMIT]
    if new or not changes_file.exists():
        changes_file.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
    return log


def _reference(closes: pd.DataFrame | None, ticker: str, date: str | None = None) -> tuple[str, float] | None:
    """Adjusted close of a finished session: the latest one, or the one on `date`."""
    if closes is None or ticker not in closes:
        return None
    series = closes[ticker].dropna()
    if date is not None:
        series = series[series.index == pd.Timestamp(date)]
    if series.empty:
        return None
    return series.index[-1].strftime("%Y-%m-%d"), float(series.iloc[-1])


def _adjust_for_corporate_actions(entry: dict, closes: pd.DataFrame | None, ticker: str) -> None:
    """Rescale the stored entry price after a split or dividend.

    Yahoo rewrites its adjusted history when that happens, so the close of the session we noted at entry
    changes. The same factor is applied to the entry price, which keeps the return free of fake jumps.
    """
    then = entry.get("ref_close")
    now = _reference(closes, ticker, entry.get("ref_date"))
    if then and now and now[1] > 0 and abs(now[1] / then - 1) > 1e-6:
        entry["entry"] = round(entry["entry"] * now[1] / then, 6)
        entry["ref_close"] = now[1]


def update_track(track_file: Path, assets: list[dict], at: str, closes: pd.DataFrame | None = None) -> dict:
    """Open a record when an asset turns `Kaufen`, close it when the signal ends, and recompute the stats.

    `closes` holds the adjusted closes of finished sessions; with it, entries survive splits and dividends.
    """
    track = json.loads(track_file.read_text(encoding="utf-8")) if track_file.exists() else {}
    track.setdefault("since", at)
    opened = track.setdefault("open", {h: {} for h in HORIZONS})
    closed = track.setdefault("closed", [])
    by_ticker = {a["ticker"]: a for a in assets}

    for horizon in HORIZONS:
        for ticker in list(opened[horizon]):
            asset = by_ticker.get(ticker)
            if asset is None or asset["price"] is None:
                continue  # no fresh price: keep the record open rather than closing it at a guess
            entry = opened[horizon][ticker]
            _adjust_for_corporate_actions(entry, closes, ticker)
            entry["price"] = asset["price"]
            entry["return"] = round(asset["price"] / entry["entry"] - 1, 4)
            if asset[f"{horizon}_signal"] != BUY:
                closed.insert(0, {"ticker": ticker, "name": entry["name"], "horizon": horizon,
                                  "since": entry["since"], "until": at, "entry": entry["entry"],
                                  "exit": asset["price"], "return": entry["return"]})
                del opened[horizon][ticker]
        for asset in assets:
            if asset[f"{horizon}_signal"] == BUY and asset["ticker"] not in opened[horizon] and asset["price"]:
                record = {"name": asset["name"], "since": at, "entry": asset["price"], "price": asset["price"],
                          "return": 0.0}
                if reference := _reference(closes, asset["ticker"]):
                    record["ref_date"], record["ref_close"] = reference
                opened[horizon][asset["ticker"]] = record
    del closed[CLOSED_LIMIT:]
    track["stats"] = _stats(closed, opened)
    track_file.write_text(json.dumps(track, ensure_ascii=False), encoding="utf-8")
    return track


def _stats(closed: list[dict], opened: dict) -> dict:
    returns = [c["return"] for c in closed]
    running = [e["return"] for h in HORIZONS for e in opened[h].values()]
    return {
        "closed": len(returns),
        "hit_rate": sum(r > 0 for r in returns) / len(returns) if returns else None,
        "avg_return": sum(returns) / len(returns) if returns else None,
        "open": len(running),
        "open_avg_return": round(sum(running) / len(running), 4) if running else None,
    }


def atom_feed(changes: list[dict], page_url: str, updated: str, limit: int = 50) -> str:
    """Atom feed of signal changes that involve `Kaufen`, newest first."""
    label = {"short": "kurzfristig", "long": "langfristig"}
    entries = []
    for change in [c for c in changes if BUY in (c["from"], c["to"])][:limit]:
        title = f"{change['name']} ({change['ticker']}), {label[change['horizon']]}: {change['from']} → {change['to']}"
        uid = f"{change['at']}-{change['ticker']}-{change['horizon']}"
        entries.append(
            f"<entry><id>urn:aktien-radar:{escape(uid)}</id><title>{escape(title)}</title>"
            f"<updated>{change['at']}</updated><link href=\"{page_url}#w/{escape(change['ticker'])}\"/>"
            f"<summary>Kurs beim Signalwechsel: {change['price']}</summary></entry>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<title>Aktien-Radar: Signalwechsel</title><id>{page_url}</id><link href=\"{page_url}\"/>"
        f"<updated>{updated}</updated><author><name>Aktien-Radar</name></author>{''.join(entries)}</feed>\n"
    )


def parse_time(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
