"""
The scheduler: runs the daily jobs from README.md ("Scheduled jobs (code)") with plain code,
using the agents only where judgment is needed.

    daily_search     Agent 1 finds and scores jobs, then asks you to approve each one.
    apply_queue      Agent 2 prepares every "Ready to Apply" record (checked every
                     APPLY_QUEUE_EVERY_MINUTES). Emails are staged for your "send <id>".
    follow_up_check  Applied records past FOLLOW_UP_AFTER_BUSINESS_DAYS get a follow-up draft
                     from Agent 2, sent to you for approval.
    no_response      Applied records with no reply after NO_RESPONSE_AFTER_DAYS become
                     "No Response".
    daily_summary    Tracker counts to Telegram at DAILY_SUMMARY_TIME.
    weekly_summary   Same, for the past 7 days, on WEEKLY_SUMMARY_DAY.

Usage:
    python scheduler.py                 # keep running; check for due jobs every 30 seconds
    python scheduler.py --once          # run whatever is due now, then exit (for Task Scheduler)
    python scheduler.py --status        # show when each job last ran / is next due
    python scheduler.py --run daily_search   # run one job now, ignoring the schedule

Times come from config.json and use its TIMEZONE. If the computer was off at the scheduled
time, the job runs when the scheduler next starts (the same day). PAUSED skips the search,
apply and follow-up jobs; summaries and the no-response housekeeping still run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import agent_common as common
import events
import filelock
import notifier
import tools
import tracker

_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(_DIR, "data", "scheduler_state.json")
LOG_PATH = os.path.join(_DIR, "logs", "scheduler.log")

TICK_SECONDS = 30
MAX_ATTEMPTS_PER_DAY = 3
RETRY_AFTER_MINUTES = 30
MAX_APPLY_ATTEMPTS = 3
AGENT_TIMEOUT_SECONDS = 20 * 60

DEFAULTS = {
    "DAILY_SEARCH_TIME": "08:00",
    "APPLY_QUEUE_EVERY_MINUTES": 30,
    "FOLLOW_UP_CHECK_TIME": "10:00",
    "DAILY_SUMMARY_TIME": "19:00",
    "WEEKLY_SUMMARY_DAY": "Sunday",
    "FOLLOW_UP_AFTER_BUSINESS_DAYS": 10,
    "NO_RESPONSE_AFTER_DAYS": 30,
}


# ---------------------------------------------------------------- helpers

def log(message: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {message}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _cfg(config: dict, key: str):
    return config.get(key, DEFAULTS.get(key))


def _now(config: dict) -> datetime:
    return datetime.now(ZoneInfo(config["TIMEZONE"]))


def _parse_time(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


def _load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_PATH)


def business_days_between(start: date, end: date) -> int:
    """Whole weekdays from `start` to `end` (no public-holiday calendar)."""
    if end <= start:
        return 0
    days, current = 0, start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


def run_script(args: list[str], timeout: int = AGENT_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """Runs one of the run_agent*.py scripts. Returns (ok, last lines of output)."""
    try:
        proc = subprocess.run(
            [sys.executable, *args], cwd=_DIR, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    output = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(output.strip().splitlines()[-6:])
    return proc.returncode == 0, tail


# ---------------------------------------------------------------- jobs

def job_daily_search(config: dict) -> str:
    ok, tail = run_script(["run_agent1.py"])
    if not ok:
        raise RuntimeError(f"Agent 1 failed: {tail}")
    return "Agent 1 finished"


def job_apply_queue(config: dict) -> str:
    state = _load_state()
    attempts = state.setdefault("apply_attempts", {})
    queue = sorted(tracker.tracker_search({"status": "Ready to Apply"}),
                   key=lambda r: r.get("last_update") or "")
    if not queue:
        return "nothing queued"

    handled = []
    for record in queue:
        app_id = record["application_id"]
        if attempts.get(app_id, 0) >= MAX_APPLY_ATTEMPTS:
            continue
        ok, tail = run_script(["run_agent2.py", "--application-id", app_id, "--task", "apply"])
        after = tracker.tracker_get(app_id)
        moved = after.get("status") != "Ready to Apply"
        capped = "cap reached" in (after.get("next_action") or "")
        if moved:
            attempts.pop(app_id, None)
            handled.append(f"{app_id}->{after['status']}")
        elif capped:
            handled.append(f"{app_id} waiting (cap reached)")
        else:
            attempts[app_id] = attempts.get(app_id, 0) + 1
            log(f"apply_queue: {app_id} did not progress (attempt {attempts[app_id]}): {tail}")
            if attempts[app_id] >= MAX_APPLY_ATTEMPTS:
                notifier.notify_text(config, f"I couldn't prepare {app_id} "
                                             f"({record.get('role')} at {record.get('company')}) "
                                             f"after {MAX_APPLY_ATTEMPTS} tries. It's still Ready to Apply.")
        _save_state({**_load_state(), "apply_attempts": attempts})
    return ", ".join(handled) or "no progress"


def _follow_up_candidates(config: dict) -> list[dict]:
    today = _now(config).date()
    after_days = _cfg(config, "FOLLOW_UP_AFTER_BUSINESS_DAYS")
    cap = config.get("MAX_FOLLOW_UPS", 1)
    due = []
    for record in tracker.tracker_search({"status": "Applied"}):
        applied = tracker._parse_date(record.get("date_applied"))
        if applied is None or business_days_between(applied, today) < after_days:
            continue
        if not record.get("recruiter_email") or not record.get("recruiter_email_verified"):
            continue
        if (record.get("follow_ups_sent") or 0) >= cap:
            continue
        if "follow_up_due" in (record.get("flags") or []):
            continue  # a draft is already waiting for you
        if tools.pending_emails(record["application_id"], "follow_up"):
            continue
        due.append(record)
    return due


def _set_flag(record: dict, flag: str, present: bool, note: str, config: dict) -> None:
    flags = [f for f in (record.get("flags") or []) if f != flag]
    if present:
        flags.append(flag)
    tracker.tracker_upsert({"application_id": record["application_id"], "flags": flags,
                            "history_note": note}, config=config)


def job_follow_up_check(config: dict) -> str:
    drafted = []
    for record in _follow_up_candidates(config):
        app_id = record["application_id"]
        _set_flag(record, "follow_up_due", True, "follow-up due", config)
        ok, tail = run_script(["run_agent2.py", "--application-id", app_id, "--task", "follow_up"])
        if ok and tools.pending_emails(app_id, "follow_up"):
            drafted.append(app_id)
        else:
            # No draft reached you: clear the flag so tomorrow's check tries again.
            _set_flag(tracker.tracker_get(app_id), "follow_up_due", False,
                      "follow-up draft failed; will retry", config)
            log(f"follow_up_check: no draft produced for {app_id}: {tail}")
    return f"drafted {len(drafted)}: {', '.join(drafted)}" if drafted else "no follow-ups due"


def job_no_response(config: dict) -> str:
    today = _now(config).date()
    limit = _cfg(config, "NO_RESPONSE_AFTER_DAYS")
    changed = []
    for record in tracker.tracker_search({"status": "Applied"}):
        applied = tracker._parse_date(record.get("date_applied"))
        if applied is None or (today - applied).days < limit:
            continue
        tracker.tracker_upsert({
            "application_id": record["application_id"], "status": "No Response",
            "history_note": f"no employer reply {limit} days after applying",
        }, config=config)
        notifier.send_notification(
            "status_changed",
            [record.get("role"), record.get("company"), "No Response",
             f"No reply {limit} days after applying"],
            record["application_id"], config)
        changed.append(record["application_id"])
    return f"marked {len(changed)}: {', '.join(changed)}" if changed else "none"


def summary_counts(config: dict, days: int) -> list:
    """Variables for the daily_summary template, over the last `days` days (1 = today)."""
    today = _now(config).date()
    start = today - timedelta(days=days - 1)
    tz = ZoneInfo(config["TIMEZONE"])

    def in_window(value) -> bool:
        d = tracker._parse_date(value)
        return d is not None and start <= d <= today

    def updated_in_window(record) -> bool:
        try:
            moment = datetime.fromisoformat(record.get("last_update") or "")
            return start <= moment.astimezone(tz).date() <= today
        except ValueError:
            return False

    records = tracker.tracker_search({})
    found = sum(1 for r in records if in_window(r.get("date_found"))
                and (r.get("history") or [{}])[0].get("status") == "Discovered")
    applied_now = sum(1 for r in records if in_window(r.get("date_applied")))
    skipped = sum(1 for r in records if r.get("status") == "Skipped" and updated_in_window(r))

    waiting, needs = 0, []
    for r in records:
        flags = r.get("flags") or []
        if r.get("status") == "Awaiting Approval":
            waiting += 1
            needs.append(f"{r['application_id']} approval ({r.get('company')})")
        elif "needs_user_submit" in flags:
            waiting += 1
            needs.append(f"{r['application_id']} submit ({r.get('company')})")
        elif "needs_user_input" in flags:
            waiting += 1
            needs.append(f"{r['application_id']} question ({r.get('company')})")
    for pending in tools.pending_emails():
        needs.append(f"send {pending['pending_id']} ({pending.get('kind', 'email')})")

    def total(*statuses) -> int:
        return sum(1 for r in records if r.get("status") in statuses)

    label = today.strftime("%a %d %b") if days == 1 else f"the week to {today.strftime('%a %d %b')}"
    needs_text = "; ".join(needs[:5]) + (f" (+{len(needs) - 5} more)" if len(needs) > 5 else "")
    return [label, found, applied_now, waiting, skipped,
            total("Applied"), total("Interview Invited", "Interview Completed"),
            total("Assessment"), total("Offer"), needs_text or "nothing"]


def job_daily_summary(config: dict) -> str:
    if _now(config).strftime("%A").lower() == str(_cfg(config, "WEEKLY_SUMMARY_DAY")).lower():
        return "skipped (the weekly summary covers today)"
    result = notifier.send_notification("daily_summary", summary_counts(config, 1), None, config)
    return "sent" if result["delivered"] else f"not delivered: {result['reason']}"


def job_weekly_summary(config: dict) -> str:
    result = notifier.send_notification("daily_summary", summary_counts(config, 7), None, config)
    return "sent" if result["delivered"] else f"not delivered: {result['reason']}"


# name -> (function, schedule kind, skipped while PAUSED)
JOBS = {
    "daily_search": (job_daily_search, "daily:DAILY_SEARCH_TIME", True),
    "apply_queue": (job_apply_queue, "every:APPLY_QUEUE_EVERY_MINUTES", True),
    "follow_up_check": (job_follow_up_check, "daily:FOLLOW_UP_CHECK_TIME", True),
    "no_response": (job_no_response, "daily:FOLLOW_UP_CHECK_TIME", False),
    "daily_summary": (job_daily_summary, "daily:DAILY_SUMMARY_TIME", False),
    "weekly_summary": (job_weekly_summary, "weekly:DAILY_SUMMARY_TIME", False),
}


# ---------------------------------------------------------------- scheduling

def is_due(name: str, config: dict, state: dict, now: datetime) -> bool:
    _, schedule, _ = JOBS[name]
    kind, key = schedule.split(":")
    job_state = state.get(name, {})
    today = now.date().isoformat()

    if kind == "every":
        last = job_state.get("last_run")
        if not last:
            return True
        minutes = float(_cfg(config, key))
        return now - datetime.fromisoformat(last) >= timedelta(minutes=minutes)

    hour, minute = _parse_time(_cfg(config, key))
    if (now.hour, now.minute) < (hour, minute):
        return False
    if kind == "weekly" and now.strftime("%A").lower() != str(_cfg(config, "WEEKLY_SUMMARY_DAY")).lower():
        return False
    if job_state.get("done_date") == today:
        return False
    if job_state.get("attempt_date") == today:
        if job_state.get("attempts", 0) >= MAX_ATTEMPTS_PER_DAY:
            return False
        retry_at = job_state.get("retry_at")
        if retry_at and now < datetime.fromisoformat(retry_at):
            return False
    return True


def next_run(name: str, config: dict, state: dict, now: datetime) -> datetime | None:
    """When a job will next run (or `now` if it is due already). None if it can't run again today."""
    _, schedule, _ = JOBS[name]
    kind, key = schedule.split(":")
    job_state = state.get(name, {})

    if kind == "every":
        last = job_state.get("last_run")
        if not last:
            return now
        return max(now, datetime.fromisoformat(last) + timedelta(minutes=float(_cfg(config, key))))

    hour, minute = _parse_time(_cfg(config, key))
    weekly_day = str(_cfg(config, "WEEKLY_SUMMARY_DAY")).lower()
    for offset in range(0, 8):
        day = (now + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if kind == "weekly" and day.strftime("%A").lower() != weekly_day:
            continue
        if offset == 0 and job_state.get("done_date") == day.date().isoformat():
            continue
        if offset == 0 and job_state.get("attempts", 0) >= MAX_ATTEMPTS_PER_DAY \
                and job_state.get("attempt_date") == day.date().isoformat():
            continue
        return max(now, day) if day.date() == now.date() else day
    return None


def run_job(name: str, config: dict, force: bool = False) -> bool:
    """Runs one job and records the outcome. Returns True on success."""
    func, schedule, skip_when_paused = JOBS[name]
    now = _now(config)
    state = _load_state()
    job_state = state.setdefault(name, {})
    today = now.date().isoformat()

    if skip_when_paused and config.get("PAUSED") and not force:
        log(f"{name}: skipped (PAUSED)")
        events.emit("scheduler", "job_end", f"{name}: skipped (system is paused)", job=name, ok=True)
        if schedule.startswith("daily"):
            job_state["done_date"] = today  # don't keep re-checking all day
        job_state["last_run"] = now.isoformat(timespec="seconds")
        _save_state(state)
        return True

    if job_state.get("attempt_date") != today:
        job_state["attempt_date"], job_state["attempts"] = today, 0
    job_state["attempts"] = job_state.get("attempts", 0) + 1
    job_state["last_run"] = now.isoformat(timespec="seconds")
    log(f"{name}: starting (attempt {job_state['attempts']})")
    events.emit("scheduler", "job_start", f"{name}: starting (attempt {job_state['attempts']})", job=name)
    try:
        result = func(config)
    except Exception as exc:  # noqa: BLE001 - one failing job must never stop the scheduler
        job_state["last_error"] = str(exc)[:500]
        job_state["retry_at"] = (now + timedelta(minutes=RETRY_AFTER_MINUTES)).isoformat(timespec="seconds")
        log(f"{name}: FAILED - {exc}")
        events.emit("scheduler", "error", f"{name} failed: {str(exc)[:150]}", job=name)
        events.emit("scheduler", "job_end", f"{name}: failed", job=name, ok=False)
        if job_state["attempts"] >= MAX_ATTEMPTS_PER_DAY or schedule.startswith("every"):
            if job_state.get("alerted_date") != today:
                job_state["alerted_date"] = today
                notifier.notify_text(config, f"Scheduled job '{name}' failed: {str(exc)[:300]}")
        _save_state({**_load_state(), name: job_state})
        return False

    job_state.pop("last_error", None)
    job_state.pop("retry_at", None)
    job_state["done_date"] = today
    job_state["last_result"] = result
    log(f"{name}: done - {result}")
    events.emit("scheduler", "job_end", f"{name}: {result}", job=name, ok=True)
    _save_state({**_load_state(), name: job_state})
    return True


def run_due_jobs(config: dict) -> list[str]:
    ran = []
    now = _now(config)
    for name in JOBS:
        if is_due(name, config, _load_state(), now):
            run_job(name, config)
            ran.append(name)
    return ran


def print_status(config: dict) -> None:
    state, now = _load_state(), _now(config)
    print(f"Now: {now.strftime('%a %Y-%m-%d %H:%M %Z')}   PAUSED={config.get('PAUSED')}   MODE={config.get('MODE')}\n")
    for name, (_, schedule, _skip) in JOBS.items():
        kind, key = schedule.split(":")
        when = (f"every {_cfg(config, key)} min" if kind == "every"
                else f"{'weekly ' + str(_cfg(config, 'WEEKLY_SUMMARY_DAY')) + ' ' if kind == 'weekly' else 'daily '}{_cfg(config, key)}")
        js = state.get(name, {})
        print(f"{name:16} {when:24} last run: {js.get('last_run', 'never'):26} "
              f"due now: {is_due(name, config, state, now)}   {js.get('last_result') or js.get('last_error') or ''}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run due jobs once and exit")
    parser.add_argument("--status", action="store_true", help="show job status and exit")
    parser.add_argument("--run", choices=list(JOBS), help="run one job now and exit")
    args = parser.parse_args()

    config = common.load_config()
    if args.status:
        print_status(config)
        return
    if args.run:
        if args.run in ("daily_search", "apply_queue", "follow_up_check"):
            common.require_api_key()
        sys.exit(0 if run_job(args.run, config, force=True) else 1)

    try:
        with filelock.locked("scheduler"):
            if not args.once:
                log("Scheduler started (Ctrl+C to stop).")
            while True:
                config = common.load_config()  # re-read each tick: PAUSED / MODE can change
                run_due_jobs(config)
                if args.once:
                    return
                time.sleep(TICK_SECONDS)
    except TimeoutError:
        print("Another scheduler is already running. Stop it first.", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        log("Scheduler stopped.")


if __name__ == "__main__":
    main()
