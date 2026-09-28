"""
Live dashboard: a web page that shows what the agents are doing as it happens.

    python dashboard.py            # http://127.0.0.1:8765
    python dashboard.py --demo     # made-up data and a looping fake run, to see it working
    python dashboard.py --port 9000 --open

It only reads: the tracker, the scheduler state, the staged emails and the activity log
(events.py). It has no way to change anything, and it listens on 127.0.0.1 only, so other
computers on your network can't see it. Requests whose Host header isn't localhost are
refused, to block DNS-rebinding tricks from other web pages.

The page polls /api/state and /api/events every couple of seconds.
"""

from __future__ import annotations

import argparse
import json
import os
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import agent_common as common
import events
import scheduler
import tools
import tracker

_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(_DIR, "dashboard", "index.html")

WORKING_RUN_MAX_AGE = timedelta(minutes=30)   # an unfinished run older than this is treated as dead
ERROR_SHOWN_FOR = timedelta(minutes=10)
ONLINE_WITHIN = timedelta(seconds=90)          # heartbeat freshness
BRIEF_ACTIVITY = timedelta(seconds=6)          # how long notifier/mailer/poller show as "working"

AGENT_INFO = {
    "scheduler": ("Scheduler", "Runs the daily jobs on time"),
    "agent1": ("Agent 1 - Research", "Finds and scores jobs"),
    "agent2": ("Agent 2 - Application", "Prepares applications and follow-ups"),
    "agent3": ("Agent 3 - Tracking", "Understands your replies and emails"),
    "poller": ("Telegram listener", "Receives your messages"),
    "notifier": ("Notifier", "Sends you updates"),
    "mailer": ("Mailer", "Stages and sends emails"),
}
JOB_LABELS = {
    "daily_search": "Daily job search",
    "apply_queue": "Apply queue",
    "follow_up_check": "Follow-up check",
    "no_response": "Mark no-response",
    "daily_summary": "Daily summary",
    "weekly_summary": "Weekly summary",
}
RECORD_FIELDS = ["application_id", "company", "role", "status", "match_score", "location", "flags",
                 "date_found", "date_applied", "next_action", "last_update", "follow_ups_sent"]


def _parse_ts(value: str | None) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value or "")
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- data sources

class LiveSource:
    demo = False

    def config(self) -> dict:
        return common.load_config()

    def records(self) -> list[dict]:
        return tracker.tracker_search({})

    def pending(self) -> list[dict]:
        return tools.pending_emails()

    def jobs(self, config: dict) -> list[dict]:
        now, state = scheduler._now(config), scheduler._load_state()
        rows = []
        for name, (_, schedule, skip_when_paused) in scheduler.JOBS.items():
            job = state.get(name, {})
            upcoming = scheduler.next_run(name, config, state, now)
            rows.append({
                "name": name, "label": JOB_LABELS.get(name, name), "schedule": schedule,
                "last_run": job.get("last_run"), "last_result": job.get("last_result"),
                "last_error": job.get("last_error"),
                "next_run": upcoming.isoformat(timespec="seconds") if upcoming else None,
                "paused_skips": skip_when_paused,
            })
        return rows

    def events(self, after: int, limit: int) -> list[dict]:
        return events.read_after(after, limit)

    def heartbeats(self) -> dict:
        return events.read_heartbeats()


# ---------------------------------------------------------------- state

def derive_agents(evts: list[dict], heartbeats: dict, now: datetime) -> dict:
    """Works out, from recent events, what each agent is doing right now."""
    day_ago = now - timedelta(hours=24)
    agents = {}
    for name, (label, role) in AGENT_INFO.items():
        mine = [e for e in evts if e.get("agent") == name]
        open_runs: dict = {}
        for e in mine:
            key = e.get("run_id") or (e.get("data") or {}).get("job")
            if e["type"] in ("run_start", "job_start"):
                open_runs[key] = e
            elif e["type"] in ("run_end", "job_end"):
                open_runs.pop(key, None)
        live = [e for e in open_runs.values()
                if (_parse_ts(e["ts"]) or now) > now - WORKING_RUN_MAX_AGE]

        last = mine[-1] if mine else None
        last_ts = _parse_ts(last["ts"]) if last else None
        status, current, application_id = "idle", None, None
        if live:
            status = "working"
            started = live[-1]
            key = started.get("run_id") or (started.get("data") or {}).get("job")
            in_run = [e for e in mine if (e.get("run_id") or (e.get("data") or {}).get("job")) == key]
            current = (in_run[-1] if in_run else started)["summary"]
            application_id = started.get("application_id")
        elif last and name in ("poller", "notifier", "mailer") and last_ts and now - last_ts < BRIEF_ACTIVITY:
            status, current = "working", last["summary"]
            application_id = last.get("application_id")
        if status != "working" and last and last["type"] == "error" and last_ts and now - last_ts < ERROR_SHOWN_FOR:
            status, current = "error", last["summary"]
        if status == "idle" and last:
            current = last["summary"]

        beat = _parse_ts(heartbeats.get(name)) if name in ("poller", "scheduler") else None
        online = None
        if name in ("poller", "scheduler"):
            online = bool(beat and now - beat < ONLINE_WITHIN)
        agents[name] = {
            "name": name, "label": label, "role": role, "status": status, "current": current,
            "application_id": application_id, "last_ts": last["ts"] if last else None,
            "online": online,
            "runs_24h": sum(1 for e in mine if e["type"] in ("run_start", "job_start")
                            and (_parse_ts(e["ts"]) or now) > day_ago),
        }
    return agents


def build_state(source) -> dict:
    now = datetime.now(timezone.utc)
    config = source.config()
    records = source.records()
    evts = source.events(0, 400)
    pending = source.pending()

    counts: dict = {}
    for r in records:
        counts[r.get("status")] = counts.get(r.get("status"), 0) + 1

    needs = []
    for r in records:
        flags = r.get("flags") or []
        if r.get("status") == "Awaiting Approval":
            needs.append({"application_id": r["application_id"], "what": "Approve or skip", "company": r.get("company")})
        elif "needs_user_submit" in flags:
            needs.append({"application_id": r["application_id"], "what": "Submit the application yourself", "company": r.get("company")})
        elif "needs_user_input" in flags:
            needs.append({"application_id": r["application_id"], "what": "Answer a question", "company": r.get("company")})
    for p in pending:
        needs.append({"application_id": p.get("application_id"), "company": None,
                      "what": f"Reply \"send {p['pending_id']}\" to send a {str(p.get('kind', 'email')).replace('_', '-')} email"})

    slim = [{k: r.get(k) for k in RECORD_FIELDS} for r in
            sorted(records, key=lambda r: r.get("last_update") or "", reverse=True)]
    return {
        "server_time": now.isoformat(timespec="seconds"),
        "demo": source.demo,
        "config": {"MODE": config.get("MODE"), "PAUSED": bool(config.get("PAUSED")),
                   "TIMEZONE": config.get("TIMEZONE")},
        "counts": counts,
        "records": slim,
        "pending": [{k: p.get(k) for k in ("pending_id", "kind", "application_id", "to", "subject")} for p in pending],
        "needs_you": needs,
        "jobs": source.jobs(config),
        "agents": derive_agents(evts, source.heartbeats(), now),
        "last_event_id": evts[-1]["id"] if evts else 0,
    }


# ---------------------------------------------------------------- server

def make_handler(source, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "FindjobsDashboard"

        def log_message(self, *args):  # keep the terminal quiet
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict) -> None:
            self._send(200, json.dumps(payload, default=str).encode("utf-8"), "application/json")

        def do_GET(self):  # noqa: N802 - http.server API
            if self.headers.get("Host", "") not in allowed_hosts:
                self._send(403, b"Forbidden", "text/plain")
                return
            url = urlparse(self.path)
            try:
                if url.path in ("/", "/index.html"):
                    with open(INDEX_PATH, "rb") as f:
                        self._send(200, f.read(), "text/html; charset=utf-8")
                elif url.path == "/api/state":
                    self._json(build_state(source))
                elif url.path == "/api/events":
                    query = parse_qs(url.query)
                    try:
                        after = int((query.get("after") or ["0"])[0])
                    except ValueError:
                        after = 0
                    self._json({"events": source.events(after, 300)})
                else:
                    self._send(404, b"Not found", "text/plain")
            except Exception as exc:  # noqa: BLE001 - never crash the server on one bad request
                self._send(500, json.dumps({"error": str(exc)[:200]}).encode("utf-8"), "application/json")

    return Handler


def serve(source, port: int = 8765, open_browser: bool = False) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(source, port))
    server.daemon_threads = True
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true", help="use made-up data and a looping fake run")
    parser.add_argument("--open", action="store_true", help="open the page in your browser")
    args = parser.parse_args()

    if args.demo:
        import dashboard_demo
        source = dashboard_demo.DemoSource()
        source.start()
    else:
        source = LiveSource()

    server = serve(source, args.port, args.open)
    print(f"Dashboard{' (DEMO data)' if args.demo else ''}: http://127.0.0.1:{args.port}/   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
