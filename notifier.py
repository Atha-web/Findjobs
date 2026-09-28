"""
Renders and delivers the notification templates from README.md over Telegram, and logs
every one via notifications.py so Agent 3 can resolve a reply back to an application.

Telegram has no template-approval process (that requirement is specific to the WhatsApp
Business Platform), so these are just plain formatted text - same content and variable
order as the README templates, without the fixed-start/end-text workaround WhatsApp needs.

If TELEGRAM_BOT_TOKEN or config["TELEGRAM_CHAT_ID"] isn't set, nothing is sent: the
notification is only logged (delivered=False) and callers are told so.
"""

from __future__ import annotations

import os

import notifications
import telegram_client

TEMPLATES = {
    "approval_request": (
        "New match \U0001F50E Score {0}/100\n"
        "Role: {1}\n"
        "Company: {2}\n"
        "Location: {3}\n"
        "Salary: {4}\n"
        "Why: {5}\n"
        "Gaps: {6}\n"
        "ID: {7}\n"
        "Reply 1 to apply, 2 to skip, 3 for details."
    ),
    "application_submitted": (
        "Application submitted ✅\n"
        "Role: {0}\n"
        "Company: {1}\n"
        "Method: {2}\n"
        "Date: {3}\n"
        "ID: {4}\n"
        "Reply to this message with any update, like \"rejected\" or \"interview Friday\"."
    ),
    "manual_submit": (
        "Ready for you to submit \U0001F4DD\n"
        "Role: {0}\n"
        "Company: {1}\n"
        "Apply here: {2}\n"
        "Answers and cover letter: {3}\n"
        "Reply \"done\" once you've submitted."
    ),
    "status_changed": (
        "Application update \U0001F4E9\n"
        "{0} at {1}\n"
        "New status: {2}\n"
        "Reason: {3}\n"
        "Reply if this is wrong."
    ),
    "action_required": (
        "Action needed ⚠️\n"
        "{0} at {1}\n"
        "{2}\n"
        "ID: {3}\n"
        "Reply here to answer."
    ),
    "email_ready": (
        "Email drafted ✉️ - ready to send\n"
        "To: {0}\n"
        "Subject: {1}\n"
        "\n"
        "{2}\n"
        "\n"
        "Reply \"send {3}\" to send it as-is, or tell me what to change first."
    ),
    "daily_summary": (
        "Job summary for {0}\n"
        "Today: {1} found, {2} applied, {3} waiting for you, {4} skipped\n"
        "Pipeline: {5} applied, {6} interviews, {7} assessments, {8} offers\n"
        "Needs you: {9}\n"
        "Reply \"pending\" to see what's waiting."
    ),
}


def render(template: str, variables: list) -> str:
    fmt = TEMPLATES.get(template)
    if not fmt:
        raise ValueError(f"Unknown notification template: {template!r}")
    return fmt.format(*[v if v is not None else "-" for v in variables])


def send_notification(template: str, variables: list, application_id: str | None,
                       config: dict, urgent: bool = False) -> dict:
    """
    Renders and sends a notification over Telegram, logging it either way. Returns
    {"delivered": bool, "message_id": int|None, "reason": str|None}.
    """
    text = render(template, variables)
    chat_id = config.get("TELEGRAM_CHAT_ID")
    bot_token_set = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))

    if not chat_id or not bot_token_set:
        notifications.record_notification(application_id, template, variables, urgent, delivered=False)
        return {
            "delivered": False,
            "message_id": None,
            "reason": "Telegram not configured (need TELEGRAM_BOT_TOKEN env var and "
                      "config.json[\"TELEGRAM_CHAT_ID\"]); notification logged only.",
        }

    try:
        result = telegram_client.send_message(chat_id, text)
    except Exception as exc:  # noqa: BLE001 - never let a delivery failure crash the caller
        notifications.record_notification(application_id, template, variables, urgent, delivered=False)
        return {"delivered": False, "message_id": None, "reason": str(exc)}

    message_id = result.get("message_id")
    notifications.record_notification(
        application_id, template, variables, urgent,
        message_id=message_id, delivered=True,
    )
    return {"delivered": True, "message_id": message_id, "reason": None}


def reply(chat_id: str, text: str, reply_to_message_id: int | None = None) -> dict:
    """Sends a free-form reply (Agent 3's reply_text) - not a template."""
    return telegram_client.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
