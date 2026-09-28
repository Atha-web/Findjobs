"""
Runs Agent 3 (Tracking): 00_shared_rules.md + 03_tracking_agent.md as the system prompt,
candidate_profile/config/today plus the message or email and <recent_notifications>
wrapped as input.

Agent 3 only ever touches the tracker (tracker_search / tracker_get / tracker_upsert). It
never sends a Telegram reply or an email itself - it returns reply_text / a
notification / a handoff, and the caller (poll_telegram.py for live use, or this file's
CLI for manual testing) is what actually delivers or prints them.

CLI usage (manual testing, does not deliver anything):
    python run_agent3.py --mode message --text "got an interview Friday 10am" \
        --quoted-application-id APP-0001
    python run_agent3.py --mode email --email-json path/to/email.json

Requires:
    pip install requests   (plus `anthropic` only if using the Anthropic backend)
    set ANTHROPIC_API_KEY=sk-ant-...
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import agent_common as common
import notifications
import tracker

TOOLS = [
    {
        "name": "tracker_search",
        "description": "Find tracker records by any combination of filters: id, fingerprint, "
                        "url, company, role, status, status_in (list), has_flag.",
        "input_schema": {
            "type": "object",
            "properties": {"filters": {"type": "object"}},
            "required": [],
        },
    },
    {
        "name": "tracker_get",
        "description": "Read one tracker record with its history.",
        "input_schema": {
            "type": "object",
            "properties": {"application_id": {"type": "string"}},
            "required": ["application_id"],
        },
    },
    {
        "name": "tracker_upsert",
        "description": "Update a tracker record. Code appends history and refuses backward "
                        "status moves.",
        "input_schema": {
            "type": "object",
            "properties": {"record": {"type": "object"}},
            "required": ["record"],
        },
    },
]


def make_dispatch(config: dict):
    def dispatch(name: str, tool_input: dict) -> dict:
        if name == "tracker_search":
            return {"results": tracker.tracker_search(tool_input.get("filters") or {})}
        if name == "tracker_get":
            try:
                return {"record": tracker.tracker_get(tool_input["application_id"])}
            except tracker.TrackerError as exc:
                return {"error": str(exc)}
        if name == "tracker_upsert":
            try:
                return {"record": tracker.tracker_upsert(tool_input["record"], config=config)}
            except tracker.TrackerError as exc:
                return {"error": str(exc)}
        raise ValueError(f"Unknown tool: {name}")
    return dispatch


def run(mode: str, *, text: str | None = None, quoted_application_id: str | None = None,
        quoted_notification_type: str | None = None, email: dict | None = None,
        config: dict | None = None) -> dict:
    """
    Runs Agent 3 once and returns its parsed JSON output. Does not deliver anything -
    that's the caller's job (see notifier.py / poll_telegram.py).
    """
    config = config or common.load_config()
    today_json, today_date = common.today_block(config["TIMEZONE"])
    candidate_profile = common.read_spec("04_candidate_profile_template.md")
    system_prompt = common.read_spec("00_shared_rules.md") + "\n\n" + common.read_spec("03_tracking_agent.md")

    parts = [
        f"<candidate_profile>\n{candidate_profile}\n</candidate_profile>",
        f"<config>\n{json.dumps(config, indent=2)}\n</config>",
        f"<today>\n{today_json}\n</today>",
        f"<mode>{mode}</mode>",
    ]

    if mode == "message":
        telegram_message = {
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        parts.append(f"<telegram_message>\n{json.dumps(telegram_message, indent=2)}\n</telegram_message>")
        if quoted_application_id:
            parts.append(f"<quoted_application_id>{quoted_application_id}</quoted_application_id>")
        if quoted_notification_type:
            parts.append(f"<quoted_notification_type>{quoted_notification_type}</quoted_notification_type>")
    else:
        parts.append(f"<email>\n{json.dumps(email, indent=2)}\n</email>")

    parts.append(f"<recent_notifications>\n{json.dumps(notifications.recent(20), indent=2)}\n</recent_notifications>")

    final_text = common.run_tool_loop(
        system_prompt=system_prompt,
        tools=TOOLS,
        user_content="\n\n".join(parts),
        dispatch=make_dispatch(config),
        label_fn=lambda inp: inp.get("application_id") or inp.get("filters") or "",
    )

    common.save_run("agent3", today_date, final_text)
    return json.loads(final_text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["message", "email"], required=True)
    parser.add_argument("--text", help="Message mode: the Telegram message text")
    parser.add_argument("--quoted-application-id", default=None)
    parser.add_argument("--quoted-notification-type", default=None)
    parser.add_argument("--email-json", help="Email mode: path to a JSON file with the email")
    args = parser.parse_args()

    if args.mode == "message" and not args.text:
        print("Error: --text is required for --mode message")
        return
    if args.mode == "email" and not args.email_json:
        print("Error: --email-json is required for --mode email")
        return

    email = None
    if args.mode == "email":
        with open(args.email_json, "r", encoding="utf-8") as f:
            email = json.load(f)

    print(f"Running Agent 3 (model={common.MODEL}, mode={args.mode})...")
    parsed = run(
        args.mode,
        text=args.text,
        quoted_application_id=args.quoted_application_id,
        quoted_notification_type=args.quoted_notification_type,
        email=email,
    )

    print("\n----- Agent 3 output -----\n")
    print(json.dumps(parsed, indent=2))
    print("\n(CLI mode: nothing was delivered - reply_text/notification are printed only. "
          "Live replies go through poll_telegram.py.)")


if __name__ == "__main__":
    main()
