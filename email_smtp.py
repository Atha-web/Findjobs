"""
Real SMTP sending via smtplib, using your own free email account (Gmail by default) -
not an anonymous "free SMTP server", which doesn't exist as a legitimate thing: every
real provider requires an account and authenticated credentials, and sending through an
open/unauthenticated relay looks like spam infrastructure and gets blocked.

Credentials are never stored in a file - they're read from environment variables, the
same pattern as ANTHROPIC_API_KEY and TELEGRAM_BOT_TOKEN:

    SMTP_HOST      default: smtp.gmail.com
    SMTP_PORT      default: 587 (STARTTLS)
    SMTP_USER      your email address
    SMTP_PASSWORD  an app password (NOT your normal account password)
    SMTP_FROM      optional, defaults to SMTP_USER

Gmail: needs 2-Step Verification enabled, then an App Password from
https://myaccount.google.com/apppasswords
"""

from __future__ import annotations

import mimetypes
import os
import smtplib
from email.message import EmailMessage

DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587


class SMTPNotConfigured(RuntimeError):
    pass


def _credentials() -> tuple[str, int, str, str, str]:
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not user or not password:
        raise SMTPNotConfigured(
            "SMTP_USER / SMTP_PASSWORD are not set. For Gmail: enable 2-Step Verification, "
            "create an app password at https://myaccount.google.com/apppasswords, then:\n"
            '  $env:SMTP_USER = "you@gmail.com"\n'
            '  $env:SMTP_PASSWORD = "the 16-character app password"'
        )
    host = os.environ.get("SMTP_HOST", DEFAULT_HOST)
    port = int(os.environ.get("SMTP_PORT", DEFAULT_PORT))
    from_addr = os.environ.get("SMTP_FROM", user)
    return host, port, user, password, from_addr


def send(to: str, subject: str, body: str, attachments: list[str] | None = None) -> dict:
    """Sends one email. `attachments` is a list of local file paths. Returns {"ok": bool, ...}."""
    try:
        host, port, user, password, from_addr = _credentials()
    except SMTPNotConfigured as exc:
        return {"ok": False, "error": str(exc)}

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    for path in attachments or []:
        if not os.path.exists(path):
            return {"ok": False, "error": f"Attachment not found: {path}"}
        ctype, _ = mimetypes.guess_type(path)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        with open(path, "rb") as f:
            msg.add_attachment(
                f.read(), maintype=maintype, subtype=subtype,
                filename=os.path.basename(path),
            )

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
    except Exception as exc:  # noqa: BLE001 - report any SMTP failure to the caller
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "error": None}
