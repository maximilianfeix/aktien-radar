"""News notes researched by the scheduled Claude routine.

The routine can only push a file to its own branch, so the hourly workflow fetches that file and passes
it through `sanitize` before it reaches the page: the content comes from web research and is treated as
untrusted data. Only the expected fields survive, text is capped and links must be https.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

MAX_ITEMS = 6
MAX_POINTS = 4
MAX_TEXT = 320
SECTIONS = ("pro", "con", "dates")


def _point(raw) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str) or not raw["text"].strip():
        return None
    url = raw.get("url")
    safe_url = url if isinstance(url, str) and url.startswith("https://") and len(url) < 500 else None
    return {"text": raw["text"].strip()[:MAX_TEXT], "url": safe_url}


def _points(raw) -> list[dict]:
    return [p for p in map(_point, raw if isinstance(raw, list) else []) if p][:MAX_POINTS]


def sanitize(raw, known_tickers: set[str] | None = None) -> dict | None:
    """Return the cleaned news document, or None when it is unusable."""
    if not isinstance(raw, dict) or not isinstance(raw.get("at"), str):
        return None
    try:
        datetime.strptime(raw["at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    items = []
    for item in raw["items"] if isinstance(raw.get("items"), list) else []:
        ticker = item.get("ticker") if isinstance(item, dict) else None
        if not isinstance(ticker, str) or (known_tickers is not None and ticker not in known_tickers):
            continue
        sections = {s: _points(item.get(s)) for s in SECTIONS}
        if any(sections.values()):
            items.append({"ticker": ticker, **sections})
    market = _points(raw.get("market"))
    if not items and not market:
        return None
    return {"at": raw["at"], "market": market, "items": items[:MAX_ITEMS]}


def import_file(source: Path, target: Path, known_tickers: set[str] | None = None) -> bool:
    """Sanitize `source` into `target`. Leaves `target` untouched when the source is missing or unusable."""
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    clean = sanitize(raw, known_tickers)
    if clean is None:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
    return True
