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

Config limits enforced here (not just in the prompts): MAX_NEW_MATCHES_PER_DAY,
MATCH_THRESHOLD_RECOMMEND, MAX_POSTING_AGE_DAYS (when creating a Discovered record),
MAX_APPLICATIONS_PER_DAY, MAX_APPS_PER_COMPANY_30_DAYS (when a record is queued to apply or
sent) and MAX_FOLLOW_UPS. EXPERIENCE_GAP_MAX_YEARS and MAX_FORM_PAGES have no structured
field to check, so they stay with the agents.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import filelock

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
QUEUED_STATUSES = {"Ready to Apply", "Application Started"}
APPLIED_OR_LATER = set(PIPELINE_ORDER[PIPELINE_ORDER.index("Applied"):])

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


def _today(config: dict) -> str:
    if config.get("today"):
        return config["today"]
    try:
        return datetime.now(ZoneInfo(config["TIMEZONE"])).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001 - no/invalid timezone: fall back to local time
        return datetime.now().strftime("%Y-%m-%d")


def _parse_date(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _score_value(value):
    if isinstance(value, dict):
        value = value.get("total")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _check_discovery_rules(record: dict, config: dict) -> None:
    """Refuses to create a Discovered record that the config says should not be recommended."""
    threshold = config.get("MATCH_THRESHOLD_RECOMMEND")
    score = _score_value(record.get("match_score"))
    if threshold is not None and score is not None and score < threshold:
        raise TrackerError(
            f"match_score {score} is below MATCH_THRESHOLD_RECOMMEND ({threshold}); "
            "create it with status Skipped instead."
        )
    max_age = config.get("MAX_POSTING_AGE_DAYS")
    posted = _parse_date(record.get("date_posted"))
    today = _parse_date(_today(config))
    if max_age is not None and posted and today and (today - posted).days > max_age:
        raise TrackerError(
            f"Posting is {(today - posted).days} days old, over MAX_POSTING_AGE_DAYS "
            f"({max_age}); create it with status Skipped instead."
        )


def _check_application_caps(data: dict, app_id: str | None, record: dict, config: dict,
                            include_queued: bool) -> None:
    """
    MAX_APPLICATIONS_PER_DAY and MAX_APPS_PER_COMPANY_30_DAYS. `include_queued` also counts
    other records already waiting to be applied to (used when a record is first queued).
    """
    today = _parse_date(_today(config))
    others = [r for r in data.values() if r.get("application_id") != app_id]

    per_day = config.get("MAX_APPLICATIONS_PER_DAY")
    if per_day is not None:
        used = sum(1 for r in others if _parse_date(r.get("date_applied")) == today)
        queued = sum(1 for r in others if r.get("status") in QUEUED_STATUSES) if include_queued else 0
        if used + queued >= per_day:
            raise TrackerError(
                f"MAX_APPLICATIONS_PER_DAY ({per_day}) reached for today "
                f"({used} applied, {queued} already queued). Try again tomorrow."
            )

    per_company = config.get("MAX_APPS_PER_COMPANY_30_DAYS")
    company = record.get("company_normalized")
    if per_company is not None and company:
        count = 0
        for r in others:
            if r.get("company_normalized") != company:
                continue
            applied = _parse_date(r.get("date_applied"))
            recent = applied is not None and today is not None and 0 <= (today - applied).days < 30
            if recent or (include_queued and r.get("status") in QUEUED_STATUSES):
                count += 1
        if count >= per_company:
            raise TrackerError(
                f"MAX_APPS_PER_COMPANY_30_DAYS ({per_company}) reached for "
                f"{record.get('company') or company}."
            )


def check_can_send(application_id: str, kind: str, config: dict) -> dict | None:
    """
    Last check before an email actually goes out. Raises TrackerError if the record's state
    doesn't allow this kind of email. Returns the record (None if the email isn't tied to a
    tracker record).

    apply:     not already applied or closed, and the daily / per-company caps aren't reached.
    follow_up: status Applied, MAX_FOLLOW_UPS not reached, recruiter email verified.
    withdraw:  the application is still open.
    draft:     any other staged email; no status rules.
    """
    with filelock.locked("tracker"):
        data = _load()
        record = data.get(application_id)
        if record is None:
            return None
        status = record.get("status")
        if kind == "follow_up":
            if status != "Applied":
                raise TrackerError(f"{application_id} is {status!r}, not Applied; no follow-up to send.")
            cap = config.get("MAX_FOLLOW_UPS")
            if cap is not None and (record.get("follow_ups_sent") or 0) >= cap:
                raise TrackerError(f"MAX_FOLLOW_UPS ({cap}) already reached for {application_id}.")
            if not record.get("recruiter_email_verified"):
                raise TrackerError(f"The recruiter email for {application_id} isn't verified; not sending.")
        elif kind == "withdraw":
            if status in TERMINAL_STATUSES:
                raise TrackerError(f"{application_id} is already {status!r}; nothing to withdraw.")
        elif kind == "apply":
            if status in TERMINAL_STATUSES or status in APPLIED_OR_LATER:
                raise TrackerError(f"{application_id} is already {status!r}; not sending another application.")
            _check_application_caps(data, application_id, record, config, include_queued=False)
        return record


def tracker_search(filters: dict[str, Any] | None = None) -> list[dict]:
    """Find records by any combination of id, fingerprint, url, company, role, status, etc."""
    filters = filters or {}
    with filelock.locked("tracker"):
        data = _load()
    results = [r for r in data.values() if _matches(r, filters)]
    results.sort(key=lambda r: r.get("last_update") or "", reverse=True)
    return results


def tracker_get(application_id: str) -> dict:
    """Read one record with its full history. Raises TrackerError if it doesn't exist."""
    with filelock.locked("tracker"):
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
    with filelock.locked("tracker"):
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
            new_status = record.get("status") or "Discovered"
            if new_status not in ALL_STATUSES:
                raise TrackerError(f"Unknown status {new_status!r}")
            record = {**record, "status": new_status}
            if new_status == "Discovered":
                today = _today(config)
                record.setdefault("date_found", today)
                _check_discovery_rules(record, config)
                cap = config.get("MAX_NEW_MATCHES_PER_DAY")
                if cap is not None:
                    # Count by how the record was created, so moving a match on to Awaiting
                    # Approval / Skipped doesn't free up room for more.
                    today_count = sum(
                        1 for r in data.values()
                        if r.get("date_found") == today
                        and (r.get("history") or [{}])[0].get("status") == "Discovered"
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

        if "follow_ups_sent" in record:
            new_count, old_count = record["follow_ups_sent"], existing.get("follow_ups_sent") or 0
            if not isinstance(new_count, int) or isinstance(new_count, bool) or new_count < old_count:
                raise TrackerError("follow_ups_sent can only increase.")
            max_follow_ups = config.get("MAX_FOLLOW_UPS")
            if max_follow_ups is not None and new_count > max_follow_ups:
                raise TrackerError(f"MAX_FOLLOW_UPS ({max_follow_ups}) already reached.")

        old_status = existing.get("status")
        if new_status != old_status and new_status in QUEUED_STATUSES:
            probe = {**existing, **{k: v for k, v in record.items() if k in RECORD_FIELDS}}
            _check_application_caps(
                data, app_id, probe, config,
                include_queued=(old_status not in QUEUED_STATUSES and new_status == "Ready to Apply"),
            )

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
