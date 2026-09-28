"""
Made-up data for `python dashboard.py --demo`, so you can watch the dashboard work without
running the real agents or touching your tracker. Nothing here is real: the companies are
fictional and nothing is written to disk.

A background thread replays a realistic day over and over: the scheduler starts the search,
Agent 1 looks things up and saves a match, you get an approval request, you approve, Agent 2
prepares the application, and you send it.
"""

from __future__ import annotations

import copy
import threading
import time
from datetime import datetime, timedelta, timezone

import events

STEP_SECONDS = 1.3
PAUSE_BETWEEN_LOOPS = 6

SEED = [
    ("APP-0101", "Northwind Analytics", "Senior Business Analyst", "Interview Invited", 88, "Colombo"),
    ("APP-0102", "Contoso Digital", "Product Analyst", "Applied", 81, "Colombo"),
    ("APP-0103", "Fabrikam Labs", "Business Analyst", "Applied", 76, "Remote"),
    ("APP-0104", "Adventure Works", "Data Analyst", "Recruiter Replied", 79, "Colombo"),
    ("APP-0105", "Tailspin Systems", "Systems Analyst", "Application Started", 72, "Kandy"),
    ("APP-0106", "Litware Group", "BA / Scrum Master", "Awaiting Approval", 84, "Colombo"),
    ("APP-0107", "Proseware", "Process Analyst", "Awaiting Approval", 69, "Remote"),
    ("APP-0108", "Wingtip Toys", "Analyst", "Rejected", 61, "Galle"),
    ("APP-0109", "Alpine Ski House", "Reporting Analyst", "Skipped", 52, "Colombo"),
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DemoSource:
    demo = True

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict] = []
        self._last_id = 0
        self._records: dict[str, dict] = {}
        self._pending: list[dict] = []
        self._job_state: dict = {}
        self._started = _now()
        for i, (app_id, company, role, status, score, location) in enumerate(SEED):
            self._records[app_id] = {
                "application_id": app_id, "company": company, "role": role, "status": status,
                "match_score": score, "location": location, "flags": [],
                "date_found": (_now() - timedelta(days=9 - i)).strftime("%Y-%m-%d"),
                "date_applied": (_now() - timedelta(days=7 - i)).strftime("%Y-%m-%d")
                if status in ("Applied", "Recruiter Replied", "Interview Invited", "Rejected") else None,
                "next_action": None, "follow_ups_sent": 0,
                "last_update": (_now() - timedelta(hours=30 - i * 3)).isoformat(timespec="seconds"),
            }
        self._records["APP-0105"]["flags"] = ["needs_user_submit"]

    # ---- the same interface as dashboard.LiveSource
    def config(self) -> dict:
        return {"MODE": "APPROVAL", "PAUSED": False, "TIMEZONE": "Asia/Colombo"}

    def records(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(list(self._records.values()))

    def pending(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._pending)

    def heartbeats(self) -> dict:
        stamp = _now().isoformat(timespec="seconds")
        return {"poller": stamp, "scheduler": stamp}

    def events(self, after: int, limit: int) -> list[dict]:
        with self._lock:
            fresh = [e for e in self._events if e["id"] > after]
        return fresh[-limit:] if after == 0 else fresh[:limit]

    def jobs(self, config: dict) -> list[dict]:
        now = _now()
        plan = [
            ("daily_search", "Daily job search", 11, 0), ("apply_queue", "Apply queue", None, 30),
            ("follow_up_check", "Follow-up check", 15, 0), ("no_response", "Mark no-response", 15, 0),
            ("daily_summary", "Daily summary", 19, 0), ("weekly_summary", "Weekly summary", 19, 0),
        ]
        rows = []
        for name, label, hour, minute in plan:
            job = self._job_state.get(name, {})
            if hour is None:
                upcoming = now + timedelta(minutes=minute - (now.minute % minute))
            else:
                upcoming = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if upcoming <= now:
                    upcoming += timedelta(days=1)
            rows.append({"name": name, "label": label, "schedule": "", "paused_skips": False,
                         "last_run": job.get("last_run"), "last_result": job.get("last_result"),
                         "last_error": None, "next_run": upcoming.isoformat(timespec="seconds")})
        return rows

    # ---- the scripted day
    def _emit(self, agent: str, type_: str, summary: str, app: str | None = None,
              run_id: str | None = None, **data) -> None:
        if type_ in ("tool", "tool_result"):  # real events carry the tool name too
            data.setdefault("tool", summary.split()[0].rstrip(":"))
        with self._lock:
            self._last_id = max(int(time.time() * 1_000_000), self._last_id + 1)
            self._events.append({
                "id": self._last_id, "ts": _now().isoformat(timespec="milliseconds"), "agent": agent,
                "type": type_, "summary": summary, "application_id": app, "run_id": run_id,
                "data": data or None,
            })
            self._events = self._events[-600:]

    def _set(self, app_id: str, **fields) -> None:
        with self._lock:
            rec = self._records[app_id]
            rec.update(fields)
            rec["last_update"] = _now().isoformat(timespec="seconds")

    def _record_job(self, name: str, result: str) -> None:
        self._job_state[name] = {"last_run": _now().isoformat(timespec="seconds"), "last_result": result}

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        n, generated = 200, []
        while True:
            n += 1
            generated.append(f"APP-0{n}")
            self._day(generated[-1])
            while len(generated) > 4:  # keep the fake pipeline a sensible size
                with self._lock:
                    self._records.pop(generated.pop(0), None)
            time.sleep(PAUSE_BETWEEN_LOOPS)

    def _day(self, new_id: str) -> None:
        step = lambda: time.sleep(STEP_SECONDS)  # noqa: E731
        e = self._emit

        # 1. Scheduler starts the daily search; Agent 1 works
        e("scheduler", "job_start", "daily_search: starting (attempt 1)", job="daily_search"); step()
        e("agent1", "run_start", "Started - daily job search (gemini-3.8-flash)", run_id="a1"); step()
        e("agent1", "llm", "Round 1: asking the model", run_id="a1"); step()
        e("agent1", "tool", "web_search business analyst Colombo", run_id="a1"); step()
        e("agent1", "tool_result", "web_search: 5 results", run_id="a1"); step()
        e("agent1", "tool", "fetch_page https://careers.example.com/ba-lead", run_id="a1"); step()
        e("agent1", "tool_result", "fetch_page: ok", run_id="a1"); step()
        e("agent1", "llm", "Round 2: asking the model", run_id="a1"); step()
        e("agent1", "tool", "tracker_search {'fingerprint': 'globex|business analyst|colombo'}", run_id="a1"); step()
        e("agent1", "tool_result", "tracker_search: 0 results", run_id="a1"); step()
        e("agent1", "tool", "tracker_upsert Globex Corporation", run_id="a1"); step()
        with self._lock:
            self._records[new_id] = {
                "application_id": new_id, "company": "Globex Corporation", "role": "Business Analyst",
                "status": "Discovered", "match_score": 83, "location": "Colombo", "flags": [],
                "date_found": _now().strftime("%Y-%m-%d"), "date_applied": None, "next_action": None,
                "follow_ups_sent": 0, "last_update": _now().isoformat(timespec="seconds"),
            }
        e("agent1", "tool_result", f"tracker_upsert: saved {new_id} (Discovered)", app=new_id, run_id="a1"); step()
        e("agent1", "run_end", "Finished", run_id="a1", ok=True); step()
        e("scheduler", "job_end", "daily_search: Agent 1 finished", job="daily_search", ok=True)
        self._record_job("daily_search", "Agent 1 finished"); step()

        # 2. You get an approval request
        self._set(new_id, status="Awaiting Approval")
        e("notifier", "notification", "Sent approval_request to you", app=new_id); step(); step()

        # 3. You reply "1"; Agent 3 moves it to Ready to Apply
        e("poller", "message_in", "You sent a message (1 chars)", app=new_id); step()
        e("agent3", "run_start", "Started - message mode (gemini-3.8-flash)", app=new_id, run_id="a3"); step()
        e("agent3", "llm", "Round 1: asking the model", app=new_id, run_id="a3"); step()
        e("agent3", "tool", f"tracker_upsert {new_id}", app=new_id, run_id="a3"); step()
        self._set(new_id, status="Ready to Apply")
        e("agent3", "tool_result", f"tracker_upsert: saved {new_id} (Ready to Apply)", app=new_id, run_id="a3"); step()
        e("agent3", "run_end", "Finished", app=new_id, run_id="a3", ok=True); step()
        e("poller", "message_out", "Replied to you (54 chars)", app=new_id); step(); step()

        # 4. Apply queue: Agent 2 prepares the application
        e("scheduler", "job_start", "apply_queue: starting (attempt 1)", job="apply_queue"); step()
        e("agent2", "run_start", f"Started - apply {new_id} (gemini-3.8-flash)", app=new_id, run_id="a2"); step()
        e("agent2", "llm", "Round 1: asking the model", app=new_id, run_id="a2"); step()
        e("agent2", "tool", f"fetch_page https://careers.example.com/ba", app=new_id, run_id="a2"); step()
        e("agent2", "tool_result", "fetch_page: ok", app=new_id, run_id="a2"); step()
        e("agent2", "tool", "get_resume ba-v3", app=new_id, run_id="a2"); step()
        e("agent2", "tool_result", "get_resume: ok", app=new_id, run_id="a2"); step()
        e("agent2", "llm", "Round 2: asking the model", app=new_id, run_id="a2"); step()
        e("agent2", "tool", "email_send hr@globex.example", app=new_id, run_id="a2"); step()
        e("mailer", "email", "Staged a apply email for your approval (send 7c1e90ab)", app=new_id, pending_id="7c1e90ab"); step()
        with self._lock:
            self._pending.append({"pending_id": "7c1e90ab", "kind": "apply", "application_id": new_id,
                                  "to": "hr@globex.example", "subject": "Business Analyst application"})
        e("agent2", "tool_result", "email_send: not ok", app=new_id, run_id="a2"); step()
        e("agent2", "run_end", "Finished", app=new_id, run_id="a2", ok=True); step()
        e("scheduler", "job_end", "apply_queue: Ready to Apply -> Application Started", job="apply_queue", ok=True)
        self._record_job("apply_queue", f"{new_id} staged"); step()
        e("notifier", "notification", "Sent email_ready to you", app=new_id); step(); step(); step()

        # 5. You reply "send 7c1e90ab"
        e("poller", "message_in", "You sent: send 7c1e90ab", app=new_id); step()
        e("mailer", "email", "Sent a apply email", app=new_id, pending_id="7c1e90ab"); step()
        with self._lock:
            self._pending = [p for p in self._pending if p["pending_id"] != "7c1e90ab"]
        self._set(new_id, status="Applied", date_applied=_now().strftime("%Y-%m-%d"))
        e("poller", "message_out", "Replied to you (28 chars)", app=new_id); step()
