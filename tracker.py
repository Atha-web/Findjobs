"""
The job tracker: the single source of truth for the system (00_shared_rules.md, section 4).

Backed by a plain JSON file so records are easy to read and diff by hand during the
manual-review build stage. Exposes exactly the three tracker tools every agent gets:

    tracker_search(filters)      -> list[dict]
    tracker_get(application_id)  -> dict
    tracker_upsert(record)       -> dict

Code, not the agents, is responsible for: assigning Application IDs, appending history,
refusing deletes, and enforcing that status never moves backwards. There is deliberately
no delete function.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.Lock()

_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(_DIR, "data", "tracker.json")

# Pipeline order for the "never move backwards" rule (00_shared_rules.md section 4).
# Terminal statuses can be set at any point from an explicit instruction / employer email.
PIPELINE_ORDER = [
    "Discovered",
    "Awaiting Approval",
    "Ready to Apply",
    "Application Started",
    "Applied",
    "Recruiter Replied",
    "Assessment",
    "Interview Invited",
    "Interview Completed",
    "Offer",
]
TERMINAL_STATUSES = {"Rejected", "Withdrawn", "Closed", "Skipped", "No Response"}
ALL_STATUSES = set(PIPELINE_ORDER) | TERMINAL_STATUSES

RECORD_FIELDS = [
    "application_id", "fingerprint", "company", "company_normalized", "role",
    "role_normalized", "location", "work_arrangement", "employment_type", "salary",
    "source", "job_urls", "date_posted", "closing_date", "recruiter_name",
    "recruiter_email", "recruiter_email_verified", "date_found", "match_score",
    "why_match", "gaps", "requires_approval_reasons", "status", "flags",
    "date_applied", "application_method", "resume_version", "cover_letter_text",
    "screening_answers", "emails_sent", "follow_ups_sent", "interview_date",
    "rejection_reason", "next_action", "last_update", "notes", "history",
]

LIST_FIELDS = {"job_urls", "why_match", "gaps", "requires_approval_reasons", "flags",
                "screening_answers", "emails_sent", "history"}


class TrackerError(ValueError):
    """Raised when a caller tries something the tracker rules forbid."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict[str, dict]:
    if not os.path.exists(DATA_PATH):
        return {}
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    tmp_path = DATA_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
    os.replace(tmp_path, DATA_PATH)


def _next_id(data: dict[str, dict]) -> str:
    max_n = 0
    for app_id in data:
        m = re.match(r"^APP-(\d+)$", app_id)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"APP-{max_n + 1:04d}"


def _blank_record() -> dict:
    rec = {field: None for field in RECORD_FIELDS}
    for field in LIST_FIELDS:
        rec[field] = []
    rec["follow_ups_sent"] = 0
    rec["flags"] = []
    return rec


def _matches(record: dict, filters: dict) -> bool:
    for key, value in filters.items():
        if key == "url":
            if value not in (record.get("job_urls") or []):
                return False
            continue
        if key == "status_in":
            if record.get("status") not in value:
                return False
            continue
        if key == "has_flag":
            if value not in (record.get("flags") or []):
                return False
            continue
        rec_value = record.get(key)
        if isinstance(rec_value, str) and isinstance(value, str):
            if rec_value.lower() != value.lower():
                return False
        elif rec_value != value:
            return False
    return True


def tracker_search(filters: dict[str, Any] | None = None) -> list[dict]:
    """Find records by any combination of id, fingerprint, url, company, role, status, etc."""
    filters = filters or {}
    with _LOCK:
        data = _load()
    results = [r for r in data.values() if _matches(r, filters)]
    results.sort(key=lambda r: r.get("last_update") or "", reverse=True)
    return results


def tracker_get(application_id: str) -> dict:
    """Read one record with its full history. Raises TrackerError if it doesn't exist."""
    with _LOCK:
        data = _load()
    record = data.get(application_id)
    if record is None:
        raise TrackerError(f"No record with application_id {application_id!r}")
    return record


def _validate_status_transition(old_status: str | None, new_status: str) -> None:
    if new_status not in ALL_STATUSES:
        raise TrackerError(f"Unknown status {new_status!r}")
    if old_status is None or old_status == new_status:
        return
    if new_status in TERMINAL_STATUSES:
        return  # terminal statuses may be set at any point
    if old_status in TERMINAL_STATUSES:
        raise TrackerError(
            f"Cannot move a record out of terminal status {old_status!r}; add a note instead."
        )
    old_idx = PIPELINE_ORDER.index(old_status) if old_status in PIPELINE_ORDER else -1
    new_idx = PIPELINE_ORDER.index(new_status)
    if new_idx < old_idx:
        raise TrackerError(
            f"Cannot move status backwards from {old_status!r} to {new_status!r}; "
            "add a note to the record instead (00_shared_rules.md section 4)."
        )


def tracker_upsert(record: dict[str, Any], config: dict[str, Any] | None = None) -> dict:
    """
    Create or update a record.

    - If `application_id` is missing, a new one is assigned and a history entry is added.
    - If it's present, the record is merged and a history entry describing the diff is added.
    - Refuses to create a new record with a fingerprint that already exists (the caller
      must upsert the existing application_id instead - see shared rules section 5).
    - Refuses a status change that moves the pipeline backwards.
    - If `config` is given, enforces MAX_NEW_MATCHES_PER_DAY for new Discovered records.
    """
    config = config or {}
    with _LOCK:
        data = _load()
        app_id = record.get("application_id")

        if app_id is None:
            fingerprint = record.get("fingerprint")
            if fingerprint:
                for existing in data.values():
                    if existing.get("fingerprint") == fingerprint:
                        raise TrackerError(
                            f"A record with fingerprint {fingerprint!r} already exists "
                            f"({existing['application_id']}). Upsert that application_id "
                            "to merge instead of creating a duplicate."
                        )
            new_status = record.get("status")
            if new_status == "Discovered":
                cap = config.get("MAX_NEW_MATCHES_PER_DAY")
                if cap is not None:
                    today = config.get("today") or datetime.now().strftime("%Y-%m-%d")
                    today_count = sum(
                        1 for r in data.values()
                        if r.get("date_found") == today and r.get("status") == "Discovered"
                    )
                    if today_count >= cap:
                        raise TrackerError(
                            f"MAX_NEW_MATCHES_PER_DAY ({cap}) already reached for {today}."
                        )

            app_id = _next_id(data)
            merged = _blank_record()
            merged.update({k: v for k, v in record.items() if k in RECORD_FIELDS})
            merged["application_id"] = app_id
            merged["last_update"] = _now_iso()
            merged["history"] = [{
                "at": _now_iso(),
                "event": "created",
                "status": merged.get("status"),
            }]
            data[app_id] = merged
            _save(data)
            return merged

        existing = data.get(app_id)
        if existing is None:
            raise TrackerError(
                f"application_id {app_id!r} does not exist; omit it to create a new record."
            )

        new_status = record.get("status", existing.get("status"))
        _validate_status_transition(existing.get("status"), new_status)

        changed_fields = []
        for key, value in record.items():
            if key not in RECORD_FIELDS or key in ("application_id", "history"):
                continue
            if existing.get(key) != value:
                changed_fields.append(key)
                existing[key] = value

        existing["last_update"] = _now_iso()
        history_event = record.get("history_note") or (
            f"updated: {', '.join(changed_fields)}" if changed_fields else "no-op update"
        )
        existing.setdefault("history", []).append({
            "at": _now_iso(),
            "event": history_event,
            "status": existing.get("status"),
        })
        data[app_id] = existing
        _save(data)
        return existing


# No delete function is exported. Deleting a record or erasing history is against the
# shared rules; if a mistake needs correcting, add a note and change the status instead.
