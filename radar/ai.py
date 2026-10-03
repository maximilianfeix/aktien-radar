"""Optional commentary written by Claude. Runs only when ANTHROPIC_API_KEY is set.

The numbers always come from the rule-based engine; Claude only explains them. To keep costs down the
commentary is regenerated only when the picks or the market regime change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"

SYSTEM = """Du bist Analyst für ein regelbasiertes Aktien-Radar. Du bekommst die aktuellen Signale als JSON.

Schreibe auf Deutsch eine kurze Einordnung für einen Privatanleger:
1. Ein Absatz zur Marktlage.
2. Je ein Absatz zum besten kurzfristigen und zum besten langfristigen Kandidaten: warum das Signal plausibel
   ist und was dagegen spricht.
3. Ein Satz, was sich seit dem letzten Lauf geändert hat, falls `previous_picks` abweicht.

Regeln: Nutze ausschließlich die Zahlen aus dem JSON und erfinde keine Nachrichten, Kursziele oder
Fundamentaldaten. Nenne Risiken genauso deutlich wie Chancen. Keine Gewinnversprechen. Höchstens 180 Wörter,
Fließtext ohne Überschriften und ohne Aufzählungen."""


def fingerprint(payload: dict) -> str:
    key = {"regime": payload["regime"]["state"], "picks": payload["picks"]}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def commentary(payload: dict, previous: dict | None) -> dict | None:
    """Return {"text", "model", "fingerprint"}, the previous commentary if nothing changed, or None."""
    mark = fingerprint(payload)
    if previous and previous.get("fingerprint") == mark:
        return previous
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    import anthropic

    model = os.environ.get("RADAR_MODEL") or DEFAULT_MODEL  # the workflow passes "" when unset
    try:
        response = anthropic.Anthropic().beta.messages.create(
            model=model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium"},
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
    except anthropic.AuthenticationError:
        log.error("ANTHROPIC_API_KEY ist ungültig – KI-Kommentar übersprungen.")
        return previous
    except anthropic.RateLimitError:
        log.warning("Claude-Rate-Limit erreicht – KI-Kommentar übersprungen.")
        return previous
    except anthropic.APIStatusError as exc:
        log.warning("Claude-API-Fehler %s: %s – KI-Kommentar übersprungen.", exc.status_code, exc.message)
        return previous
    except anthropic.APIConnectionError:
        log.warning("Claude-API nicht erreichbar – KI-Kommentar übersprungen.")
        return previous

    if response.stop_reason == "refusal":
        log.warning("Claude hat die Anfrage abgelehnt – KI-Kommentar übersprungen.")
        return previous
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        return previous
    return {"text": text, "model": response.model, "fingerprint": mark}
