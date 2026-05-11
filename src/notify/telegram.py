from __future__ import annotations

from typing import Optional

import requests

from src.utils.logging import get_logger

logger = get_logger(__name__)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, message: str) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as exc:
            logger.warning("Failed to send Telegram message: %s", exc)
