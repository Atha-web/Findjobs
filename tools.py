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

import requests

import email_smtp
import notifier

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


def _write_outbox(subdir: str, payload: dict) -> str:
    out_dir = os.path.join(_DIR, "data", "outbox", subdir)
    with _OUTBOX_LOCK:
        os.makedirs(out_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        safe_to = "".join(c for c in str(payload.get("to", "unknown")) if c.isalnum() or c in "@.-_")[:60]
        path = os.path.join(out_dir, f"{ts}_{safe_to}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def email_draft(to: str, subject: str, body: str, attachments: list | None = None,
                 thread_id: str | None = None, config: dict | None = None,
                 application_id: str | None = None) -> dict:
    """Save a draft. Never sends anything - the candidate approves it first (Telegram)."""
    payload = {
        "to": to, "subject": subject, "body": body,
        "attachments": attachments or [], "thread_id": thread_id,
        "status": "draft",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path = _write_outbox("drafts", payload)

    if config is not None:
        preview = body if len(body) <= 500 else body[:500] + "…"
        notifier.send_notification(
            template="email_ready",
            variables=[to, subject, preview, application_id or "-"],
            application_id=application_id,
            config=config,
        )

    return {"ok": True, "status": "draft", "saved_to": path}


def email_send(to: str, subject: str, body: str, attachments: list | None = None,
                thread_id: str | None = None, config: dict | None = None,
                application_id: str | None = None) -> dict:
    """
    Stages an email application and notifies the candidate - never sends immediately.
    Returns a pending_id; the candidate confirms with "send <pending_id>" on Telegram,
    which calls dispatch_pending_email() to actually deliver it via SMTP.
    """
    config = config or {}
    pending_id = uuid.uuid4().hex[:8]
    payload = {
        "pending_id": pending_id,
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

    preview = body if len(body) <= 500 else body[:500] + "…"
    result = notifier.send_notification(
        template="email_ready",
        variables=[to, subject, preview, pending_id],
        application_id=application_id,
        config=config,
    )

    return {
        "ok": False,
        "sent": False,
        "pending_id": pending_id,
        "reason": 'Staged and notified on Telegram; not sent yet - waiting for the '
                  f'candidate to reply "send {pending_id}".',
        "notification_delivered": result["delivered"],
        "saved_to": path,
    }


def dispatch_pending_email(pending_id: str, config: dict) -> dict:
    """
    Actually sends a staged email via SMTP. Only call this after the candidate has
    explicitly confirmed (e.g. replied "send <pending_id>" on Telegram) - never from
    inside an agent's own tool loop.
    """
    path = os.path.join(_DIR, "data", "outbox", "pending_send", f"{pending_id}.json")
    if not os.path.exists(path):
        return {"ok": False, "error": f"No pending email with id {pending_id!r}"}
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    if not config.get("EMAIL_SENDING_ENABLED"):
        return {
            "ok": False,
            "error": 'EMAIL_SENDING_ENABLED is false in config.json - set it to true once '
                     "your SMTP account (SMTP_USER / SMTP_PASSWORD env vars) is set up.",
        }

    cap = config.get("MAX_EMAILS_PER_DAY")
    if cap is not None:
        sent_dir = os.path.join(_DIR, "data", "outbox", "sent")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        sent_today = 0
        if os.path.isdir(sent_dir):
            for fname in os.listdir(sent_dir):
                fpath = os.path.join(sent_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        sent_payload = json.load(f)
                    if sent_payload.get("created_at", "").startswith(today):
                        sent_today += 1
                except (OSError, json.JSONDecodeError):
                    continue
        if sent_today >= cap:
            return {"ok": False, "error": f"MAX_EMAILS_PER_DAY ({cap}) already reached for {today}."}

    result = email_smtp.send(payload["to"], payload["subject"], payload["body"], payload.get("attachments"))
    if not result["ok"]:
        return {"ok": False, "error": result["error"]}

    sent_dir = os.path.join(_DIR, "data", "outbox", "sent")
    with _OUTBOX_LOCK:
        os.makedirs(sent_dir, exist_ok=True)
        os.replace(path, os.path.join(sent_dir, f"{pending_id}.json"))

    return {"ok": True, "application_id": payload.get("application_id"), "to": payload["to"]}
