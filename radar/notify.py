"""Optional push messages when the top picks change. Each channel is active only if its secrets are set.

DISCORD_WEBHOOK_URL                    -> Discord channel
TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID  -> Telegram chat
NTFY_TOPIC                             -> ntfy.sh topic (push to phone without an account)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger(__name__)
TIMEOUT = 15


def _post(url: str, payload: dict | bytes, headers: dict | None = None) -> None:
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    content_type = {} if isinstance(payload, bytes) else {"Content-Type": "application/json"}
    request = urllib.request.Request(url, data=data, headers={"User-Agent": "aktien-radar", **content_type,
                                                              **(headers or {})})
    with urllib.request.urlopen(request, timeout=TIMEOUT):  # noqa: S310 - fixed https endpoints
        pass


def send(title: str, text: str, url: str) -> list[str]:
    """Send to every configured channel. Returns the channels that worked; failures are logged, not raised."""
    channels = {}
    if hook := os.environ.get("DISCORD_WEBHOOK_URL"):
        channels["discord"] = lambda: _post(hook, {"content": f"**{title}**\n{text}\n{url}"[:1900]})
    if (token := os.environ.get("TELEGRAM_BOT_TOKEN")) and (chat := os.environ.get("TELEGRAM_CHAT_ID")):
        channels["telegram"] = lambda: _post(f"https://api.telegram.org/bot{token}/sendMessage",
                                             {"chat_id": chat, "text": f"{title}\n{text}\n{url}"[:4000]})
    if topic := os.environ.get("NTFY_TOPIC"):
        channels["ntfy"] = lambda: _post(f"https://ntfy.sh/{topic}", text.encode(),
                                         {"Title": title.encode("ascii", "ignore").decode(), "Click": url})
    sent = []
    for name, deliver in channels.items():
        try:
            deliver()
            sent.append(name)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            log.warning("Benachrichtigung über %s fehlgeschlagen: %s", name, exc)
    return sent
