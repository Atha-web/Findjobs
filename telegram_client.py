"""
Thin wrapper around the Telegram Bot API (https://core.telegram.org/bots/api).

Chosen over WhatsApp automation libraries (e.g. OpenWA) because those drive WhatsApp Web
through a headless browser using a personal number, which violates WhatsApp's Terms of
Service and commonly gets the number banned. A Telegram bot uses Telegram's own official,
sanctioned Bot API - no ToS risk, no template approval process.

The bot token is a credential and is never stored in a file: it's read from the
TELEGRAM_BOT_TOKEN environment variable, the same pattern as ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import os
import time

import requests

API_BASE = "https://api.telegram.org/bot{token}"
REQUEST_TIMEOUT = 30
RETRIES = 4  # the connection to api.telegram.org is flaky on some networks: retry timeouts


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """requests.get/post with retries on timeouts and dropped connections."""
    for attempt in range(RETRIES):
        try:
            return requests.request(method, url, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt == RETRIES - 1:
                raise
            time.sleep(2 * (attempt + 1))


class TelegramNotConfigured(RuntimeError):
    pass


def _token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise TelegramNotConfigured(
            "TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather on Telegram, "
            "then set the token: $env:TELEGRAM_BOT_TOKEN = \"123456:ABC-...\""
        )
    return token


def get_me() -> dict:
    resp = _request("GET", API_BASE.format(token=_token()) + "/getMe", timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()["result"]


def send_message(chat_id: str, text: str, reply_to_message_id: int | None = None) -> dict:
    """Sends a message and returns the Telegram message object (has ["message_id"])."""
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id is not None:
        payload["reply_to_message_id"] = reply_to_message_id
    resp = _request(
        "POST",
        API_BASE.format(token=_token()) + "/sendMessage",
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {data}")
    return data["result"]


def get_updates(offset: int | None = None, timeout: int = 30) -> list[dict]:
    """Long-polls for new messages. Pass offset = last_update_id + 1 to avoid re-reading."""
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    resp = _request(
        "GET",
        API_BASE.format(token=_token()) + "/getUpdates",
        params=params,
        timeout=timeout + REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getUpdates failed: {data}")
    return data["result"]
