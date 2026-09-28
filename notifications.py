"""
A minimal notification log: `message_id -> application_id` mapping plus the last N
notifications sent, which Agent 3 needs as <recent_notifications> (03_tracking_agent.md).

notifier.py is what actually delivers a notification (via Telegram) and calls
record_notification() here with the real message_id it got back, so a reply quoting that
message can be resolved back to an application_id (find_by_message_id).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

_LOCK = threading.Lock()
_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_DIR, "data", "notifications.json")


def _load() -> list[dict]:
    if not os.path.exists(DATA_PATH):
        return []
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: list[dict]) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    tmp_path = DATA_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, DATA_PATH)


def record_notification(application_id: str, template: str, variables: list,
                         urgent: bool = False, message_id: int | None = None,
                         delivered: bool = False) -> dict:
    """Log a notification. `delivered` / `message_id` reflect whether it actually reached
    Telegram (set by notifier.py) - a False here means it was only logged, not sent."""
    entry = {
        "message_id": message_id,
        "application_id": application_id,
        "template": template,
        "variables": variables,
        "urgent": urgent,
        "delivered": delivered,
        "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _LOCK:
        data = _load()
        data.append(entry)
        data = data[-200:]  # keep the log bounded
        _save(data)
    return entry


def recent(n: int = 20) -> list[dict]:
    with _LOCK:
        data = _load()
    return data[-n:]


def find_by_message_id(message_id: int) -> dict | None:
    with _LOCK:
        data = _load()
    for entry in reversed(data):
        if entry.get("message_id") == message_id:
            return entry
    return None
