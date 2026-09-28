"""
Client-side tools shared by the agent runners.

`web_search` is Anthropic's server-side web search tool (declared per-runner) and needs no
implementation here. `fetch_page` is a plain HTTP fetch + text extraction so Agent 1 and
Agent 2 can read a listing or company page. `get_resume`, `email_draft` and `email_send`
back Agent 2 (02_application_agent.md); `tracker_search` / `tracker_upsert` (tracker.py)
back all three agents.

Safety: `email_send` never sends immediately, even once real SMTP sending is wired in
(email_smtp.py) and config["EMAIL_SENDING_ENABLED"] is true. It always stages the email
under data/outbox/pending_send/<id>.json and sends a Telegram notification asking the
candidate to reply "send <id>" first - mirroring the manual-pack / needs_user_submit
pattern used everywhere else in this system, so an email is never dispatched without an
explicit human confirmation. `dispatch_pending_email()` is what actually calls SMTP, and
is only meant to be invoked after that confirmation is received. There is no
`browser_fill` implementation, so Agent 2 always falls back to a manual pack for form
applications (build order stage 1).
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import requests

import email_smtp
import notifier
import tracker

MAX_PAGE_CHARS = 15000
REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; job-application-agent/1.0)"

_DIR = os.path.dirname(os.path.abspath(__file__))
_OUTBOX_LOCK = threading.Lock()

_SKIP_TAGS = {"script", "style", "noscript", "svg", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return "\n".join(parser.chunks)


def fetch_page(url: str) -> dict:
    """Return a page's visible text. Never follows redirects off the original host silently."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        return {"url": url, "ok": False, "error": str(exc), "text": ""}

    if resp.status_code >= 400:
        return {
            "url": url,
            "ok": False,
            "error": f"HTTP {resp.status_code}",
            "text": "",
        }

    content_type = resp.headers.get("Content-Type", "")
    if "html" not in content_type and "text" not in content_type:
        return {
            "url": url,
            "ok": False,
            "error": f"Unsupported content type: {content_type}",
            "text": "",
        }

    text = _html_to_text(resp.text)
    truncated = len(text) > MAX_PAGE_CHARS
    return {
        "url": resp.url,
        "ok": True,
        "status_code": resp.status_code,
        "text": text[:MAX_PAGE_CHARS],
        "truncated": truncated,
    }


def web_search(query: str, max_results: int = 5) -> dict:
    """
    Client-side web search for backends without a built-in search tool (e.g. Gemini's free
    tier). Uses Tavily (TAVILY_API_KEY) or Brave Search (BRAVE_API_KEY) - both have free tiers.
    Returns {"ok": False, "error": ...} if neither key is set, so the agent can carry on
    without searching instead of looping.
    """
    tavily_key = os.environ.get("TAVILY_API_KEY")
    brave_key = os.environ.get("BRAVE_API_KEY")
    try:
        if tavily_key:
            resp = requests.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {tavily_key}"},
                json={"query": query, "max_results": max_results},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            results = [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
                       for r in resp.json().get("results", [])]
            return {"ok": True, "query": query, "results": results}
        if brave_key:
            resp = requests.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": brave_key, "Accept": "application/json"},
                params={"q": query, "count": max_results},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            results = [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("description")}
                       for r in resp.json().get("web", {}).get("results", [])]
            return {"ok": True, "query": query, "results": results}
    except requests.RequestException as exc:
        return {"ok": False, "query": query, "error": f"Search failed: {exc}", "results": []}
    return {
        "ok": False, "query": query, "results": [],
        "error": "No search backend configured (set TAVILY_API_KEY or BRAVE_API_KEY). "
                 "Work from the provided new_listings and known career pages instead.",
    }


def get_resume(version: str) -> dict:
    """Return the file path for a resume version listed in resumes.json / the profile's
    Resume versions table. Never edits the file - just locates it."""
    resumes_path = os.path.join(_DIR, "resumes.json")
    if not os.path.exists(resumes_path):
        return {"version": version, "ok": False, "error": "resumes.json not found"}
    with open(resumes_path, "r", encoding="utf-8") as f:
        resumes = json.load(f)
    path = resumes.get(version)
    if not path:
        return {"version": version, "ok": False, "error": f"No resume version {version!r} on file"}
    if not os.path.exists(path):
        return {"version": version, "ok": False, "error": f"File not found: {path}"}
    return {"version": version, "ok": True, "path": path}


def _stage_email(kind: str, to: str, subject: str, body: str, attachments: list | None,
                 thread_id: str | None, application_id: str | None) -> tuple[str, str]:
    """Writes an email to data/outbox/pending_send/<id>.json. Nothing is sent from here."""
    pending_id = uuid.uuid4().hex[:8]
    payload = {
        "pending_id": pending_id,
        "kind": kind,  # apply | follow_up | withdraw | draft
        "application_id": application_id,
        "to": to, "subject": subject, "body": body,
        "attachments": attachments or [], "thread_id": thread_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = os.path.join(_DIR, "data", "outbox", "pending_send", f"{pending_id}.json")
    with _OUTBOX_LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    return pending_id, path


def _notify_email_ready(pending_id: str, to: str, subject: str, body: str,
                        application_id: str | None, config: dict) -> dict:
    preview = body if len(body) <= 500 else body[:500] + "…"
    return notifier.send_notification(
        template="email_ready",
        variables=[to, subject, preview, pending_id],
        application_id=application_id,
        config=config,
    )


def email_draft(to: str, subject: str, body: str, attachments: list | None = None,
                thread_id: str | None = None, config: dict | None = None,
                application_id: str | None = None, kind: str = "draft") -> dict:
    """
    Stages a follow-up, withdrawal or other draft and asks the candidate to approve it on
    Telegram. Never sends anything: it goes out only when the candidate replies
    "send <pending_id>" (see dispatch_pending_email).
    """
    pending_id, path = _stage_email(kind, to, subject, body, attachments, thread_id, application_id)
    result = _notify_email_ready(pending_id, to, subject, body, application_id, config or {})
    return {
        "ok": True, "status": "draft", "pending_id": pending_id, "saved_to": path,
        "notification_delivered": result["delivered"],
        "note": f'Waiting for the candidate to reply "send {pending_id}".',
    }


def email_send(to: str, subject: str, body: str, attachments: list | None = None,
                thread_id: str | None = None, config: dict | None = None,
                application_id: str | None = None) -> dict:
    """
    Stages an email application and notifies the candidate - never sends immediately.
    Returns a pending_id; the candidate confirms with "send <pending_id>" on Telegram,
    which calls dispatch_pending_email() to actually deliver it via SMTP.
    """
    pending_id, path = _stage_email("apply", to, subject, body, attachments, thread_id, application_id)
    result = _notify_email_ready(pending_id, to, subject, body, application_id, config or {})
    return {
        "ok": False,
        "sent": False,
        "pending_id": pending_id,
        "reason": 'Staged and notified on Telegram; not sent yet - waiting for the '
                  f'candidate to reply "send {pending_id}".',
        "notification_delivered": result["delivered"],
        "saved_to": path,
    }


def pending_emails(application_id: str | None = None, kind: str | None = None) -> list[dict]:
    """Staged emails still waiting for the candidate, optionally filtered."""
    pdir = os.path.join(_DIR, "data", "outbox", "pending_send")
    found = []
    if os.path.isdir(pdir):
        for fname in os.listdir(pdir):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(pdir, fname), "r", encoding="utf-8") as f:
                    payload = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            if application_id and payload.get("application_id") != application_id:
                continue
            if kind and payload.get("kind") != kind:
                continue
            found.append(payload)
    return found


def discard_pending_email(pending_id: str) -> dict:
    """Throws away a staged email (moved to outbox/discarded, never deleted)."""
    if not pending_id.isalnum():
        return {"ok": False, "error": "Invalid pending id."}
    path = os.path.join(_DIR, "data", "outbox", "pending_send", f"{pending_id}.json")
    if not os.path.exists(path):
        return {"ok": False, "error": f"No pending email with id {pending_id!r}"}
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    dest_dir = os.path.join(_DIR, "data", "outbox", "discarded")
    with _OUTBOX_LOCK:
        os.makedirs(dest_dir, exist_ok=True)
        os.replace(path, os.path.join(dest_dir, f"{pending_id}.json"))
    return {"ok": True, "application_id": payload.get("application_id"),
            "kind": payload.get("kind"), "to": payload.get("to")}


def _today_in_tz(config: dict) -> str:
    try:
        return datetime.now(ZoneInfo(config["TIMEZONE"])).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001 - no/invalid timezone: fall back to UTC
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sent_on(payload: dict, config: dict) -> str:
    """The candidate-timezone date an email was sent (falls back to when it was staged)."""
    stamp = payload.get("sent_at") or payload.get("created_at") or ""
    try:
        moment = datetime.fromisoformat(stamp)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(ZoneInfo(config["TIMEZONE"])).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return stamp[:10]


def dispatch_pending_email(pending_id: str, config: dict) -> dict:
    """
    Actually sends a staged email via SMTP. Only call this after the candidate has
    explicitly confirmed (e.g. replied "send <pending_id>" on Telegram) - never from
    inside an agent's own tool loop.

    Enforced here in code, whatever the model did: PAUSED, EMAIL_SENDING_ENABLED,
    MAX_EMAILS_PER_DAY, and per kind of email: applications need the application caps and
    "not already applied"; follow-ups need status Applied, MAX_FOLLOW_UPS not reached and a
    verified recruiter email; withdrawals need an open application. The recipient must match
    the record's recruiter email when one is on file.
    """
    if not pending_id.isalnum():
        return {"ok": False, "error": "Invalid pending id."}
    path = os.path.join(_DIR, "data", "outbox", "pending_send", f"{pending_id}.json")
    if not os.path.exists(path):
        return {"ok": False, "error": f"No pending email with id {pending_id!r}"}
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if config.get("PAUSED"):
        return {"ok": False, "error": "The system is paused (PAUSED is true), so nothing is sent. "
                                      "Reply CONFIRM RESUME to resume, then send again."}

    if not config.get("EMAIL_SENDING_ENABLED"):
        return {
            "ok": False,
            "error": 'EMAIL_SENDING_ENABLED is false in config.json - set it to true once '
                     "your SMTP account (SMTP_USER / SMTP_PASSWORD env vars) is set up.",
        }

    app_id = payload.get("application_id")
    kind = payload.get("kind") or "apply"
    if app_id:
        try:
            record = tracker.check_can_send(app_id, kind, config)
        except tracker.TrackerError as exc:
            return {"ok": False, "error": str(exc)}
        recruiter_email = ((record or {}).get("recruiter_email") or "").strip().lower()
        if recruiter_email and payload["to"].strip().lower() != recruiter_email:
            return {"ok": False, "error": f"Recipient {payload['to']} doesn't match the recruiter "
                                          f"email on file for {app_id} ({recruiter_email}). Not sent."}

    cap = config.get("MAX_EMAILS_PER_DAY")
    if cap is not None:
        sent_dir = os.path.join(_DIR, "data", "outbox", "sent")
        today = _today_in_tz(config)
        sent_today = 0
        if os.path.isdir(sent_dir):
            for fname in os.listdir(sent_dir):
                try:
                    with open(os.path.join(sent_dir, fname), "r", encoding="utf-8") as f:
                        if _sent_on(json.load(f), config) == today:
                            sent_today += 1
                except (OSError, json.JSONDecodeError):
                    continue
        if sent_today >= cap:
            return {"ok": False, "error": f"MAX_EMAILS_PER_DAY ({cap}) already reached for {today}."}

    result = email_smtp.send(payload["to"], payload["subject"], payload["body"], payload.get("attachments"))
    if not result["ok"]:
        return {"ok": False, "error": result["error"]}

    # The email is out. Record it as sent so it can never be sent twice, even if the
    # move below fails.
    payload["sent_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sent_dir = os.path.join(_DIR, "data", "outbox", "sent")
    with _OUTBOX_LOCK:
        os.makedirs(sent_dir, exist_ok=True)
        sent_path = os.path.join(sent_dir, f"{pending_id}.json")
        with open(sent_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        try:
            os.remove(path)
        except OSError:
            os.replace(path, path + ".sent")  # last resort: move it out of reach so it can't be re-sent

    return {"ok": True, "application_id": app_id, "to": payload["to"], "kind": kind,
            "subject": payload["subject"]}
