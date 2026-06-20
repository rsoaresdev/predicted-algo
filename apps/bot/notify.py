"""Lightweight alerting (STRATEGY_IMPROVEMENTS #20).

Every alert is logged. If a Telegram bot token + chat id are configured
(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID), it is also pushed there. Uses only the
stdlib (urllib) so there is no new dependency, and a delivery failure NEVER
raises into the trading loop — alerting must not be able to crash the bot.
"""
from __future__ import annotations

import logging
import urllib.parse
import urllib.request

from config import settings

logger = logging.getLogger("notify")


class Notifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self.token = settings.telegram_bot_token if token is None else token
        self.chat_id = settings.telegram_chat_id if chat_id is None else chat_id

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, message: str, level: str = "info") -> None:
        log = getattr(logger, level if level in ("info", "warning", "error") else "info")
        log("ALERT: %s", message)
        if not self.telegram_enabled:
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = urllib.parse.urlencode(
                {"chat_id": self.chat_id, "text": message}
            ).encode()
            urllib.request.urlopen(  # noqa: S310 - fixed https host
                urllib.request.Request(url, data=data), timeout=5
            )
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("Telegram delivery failed: %s", exc)
