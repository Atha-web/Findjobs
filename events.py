"""
A small activity log the dashboard reads to show what the agents are doing, live.

Every agent run, tool call, scheduler job, Telegram message and notification appends one
line to data/events.jsonl. Each event is short on purpose: who did what, one line of
detail, and (when relevant) which application. Full emails, cover letters and the text of
your messages are never logged here (only short commands such as "send ab12cd").

Logging is best-effort: emit() never raises, so a full disk or a locked file can't break
an agent run.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone

import filelock

_DIR = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("FINDJOBS_EVENTS_PATH") or os.path.join(_DIR, "data", "events.jsonl")

MAX_SUMMARY_CHARS = 200
TRIM_ABOVE_BYTES = 1_000_000
KEEP_LINES = 2000

# Who can emit events. The dashboard draws one lane per name.
AGENTS = ("scheduler", "agent1", "agent2", "agent3", "poller", "notifier", "mailer")


def new_run_id() -> str:
    return uuid.uuid4().hex[:8]


def emit(agent: str, type_: str, summary: str, application_id: str | None = None,
         run_id: str | None = None, **data) -> None:
    """
    type_: run_start | run_end | llm | tool | tool_result | error | job_start | job_end |
           message_in | message_out | notification | email | info
    """
    try:
        entry = {
            "id": time.time_ns(),
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "agent": agent,
            "type": type_,
            "summary": " ".join(str(summary).split())[:MAX_SUMMARY_CHARS],
            "application_id": application_id,
            "run_id": run_id,
            "data": data or None,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with filelock.locked("events"):
            with open(PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if os.path.getsize(PATH) > TRIM_ABOVE_BYTES:
                _trim()
    except Exception:  # noqa: BLE001 - logging must never break the caller
        pass


def _trim() -> None:
    with open(PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()[-KEEP_LINES:]
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, PATH)


def read_after(after_id: int = 0, limit: int = 500, path: str | None = None) -> list[dict]:
    """Events newer than `after_id`, oldest first. With after_id=0, the most recent `limit`."""
    path = path or PATH
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("id", 0) > after_id:
            events.append(event)
    return events[-limit:] if after_id == 0 else events[:limit]


def heartbeat(name: str) -> None:
    """Marks a long-running process (poller, scheduler) as alive. Kept out of the event feed:
    one small file per process that is overwritten each time."""
    try:
        path = os.path.join(os.path.dirname(PATH), f"heartbeat_{name}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}, f)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        pass


def read_heartbeats() -> dict[str, str]:
    """{process name: ISO timestamp of its last heartbeat}."""
    found = {}
    folder = os.path.dirname(PATH)
    for name in ("poller", "scheduler"):
        try:
            with open(os.path.join(folder, f"heartbeat_{name}.json"), "r", encoding="utf-8") as f:
                found[name] = json.load(f)["ts"]
        except (OSError, ValueError, KeyError):
            continue
    return found
