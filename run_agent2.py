"""
Runs Agent 2 (Application) on one tracker record: 00_shared_rules.md +
02_application_agent.md as the system prompt, candidate_profile/config/today/job_record/
task wrapped as input.

Nothing is actually submitted anywhere:
  - There is no browser_fill implementation, so a form application always becomes a
    manual pack for the candidate (README build order stage 1).
  - email_send is hard-gated by config["EMAIL_SENDING_ENABLED"] (false in config.json):
    it writes the drafted email to data/outbox/pending_send/ instead of delivering it.
  - email_draft always just saves a draft under data/outbox/drafts/.

The job record must already exist in the tracker (created by Agent 1) with status
"Ready to Apply" for task=apply, or "Applied" for task=follow_up.

Usage:
    python run_agent2.py --application-id APP-0001 --task apply
    python run_agent2.py --application-id APP-0001 --task follow_up
    python run_agent2.py --application-id APP-0001 --task withdraw

Requires:
    pip install requests   (plus `anthropic` only if using the Anthropic backend)
    set ANTHROPIC_API_KEY=sk-ant-...
"""

from __future__ import annotations

import argparse
import json

import agent_common as common
import notifier
import tools
import tracker

TOOLS = [
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
        "description": "Update a tracker record (application_id required here - Agent 2 "
                        "never creates new records). Code appends history and refuses "
                        "backward status moves.",
        "input_schema": {
            "type": "object",
            "properties": {"record": {"type": "object"}},
            "required": ["record"],
        },
    },
    {
        "name": "fetch_page",
        "description": "Return a page's visible text (to re-check a listing is still live).",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "get_resume",
        "description": "Return the file path for a resume version.",
        "input_schema": {
            "type": "object",
            "properties": {"version": {"type": "string"}},
            "required": ["version"],
        },
    },
    {
        "name": "email_draft",
        "description": "Stage an email draft (follow-up, withdrawal, or a paused application "
                        "question) and ask the candidate to approve it on Telegram. Never sends "
                        "anything itself.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "attachments": {"type": "array", "items": {"type": "string"}},
                "thread_id": {"type": ["string", "null"]},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "email_send",
        "description": "Stage an application email for sending. It is NOT sent yet: the "
                        "candidate is notified on Telegram and must reply \"send <id>\" first. "
                        "Report it as awaiting the candidate's confirmation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "attachments": {"type": "array", "items": {"type": "string"}},
                "thread_id": {"type": ["string", "null"]},
            },
            "required": ["to", "subject", "body"],
        },
    },
]


def make_dispatch(config: dict, application_id: str, task: str = "apply"):
    def dispatch(name: str, tool_input: dict) -> dict:
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
        if name == "fetch_page":
            return tools.fetch_page(tool_input["url"])
        if name == "get_resume":
            return tools.get_resume(tool_input["version"])
        if name == "email_draft":
            kind = task if task in ("follow_up", "withdraw") else "draft"
            return tools.email_draft(config=config, application_id=application_id, kind=kind, **tool_input)
        if name == "email_send":
            return tools.email_send(config=config, application_id=application_id, **tool_input)
        raise ValueError(f"Unknown tool: {name}")
    return dispatch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--task", choices=["apply", "follow_up", "withdraw"], default="apply")
    args = parser.parse_args()

    config = common.load_config()
    today_json, today_date = common.today_block(config["TIMEZONE"])

    if config.get("PAUSED"):
        print("PAUSED is true in config.json. Not running Agent 2 (tracking rules still apply).")
        return

    try:
        job_record = tracker.tracker_get(args.application_id)
    except tracker.TrackerError as exc:
        print(f"Error: {exc}")
        return

    candidate_profile = common.read_spec("04_candidate_profile_template.md")
    system_prompt = common.read_spec("00_shared_rules.md") + "\n\n" + common.read_spec("02_application_agent.md")

    user_content = "\n\n".join([
        f"<candidate_profile>\n{candidate_profile}\n</candidate_profile>",
        f"<config>\n{json.dumps(config, indent=2)}\n</config>",
        f"<today>\n{today_json}\n</today>",
        f"<job_record>\n{json.dumps(job_record, indent=2)}\n</job_record>",
        f"<task>{args.task}</task>",
    ])

    print(f"Running Agent 2 (model={common.MODEL}, application_id={args.application_id}, "
          f"task={args.task})...")
    print("NOTE: emails are only staged (you confirm with 'send <id>' on Telegram) and there "
          "is no browser_fill, so nothing is submitted or delivered by this run itself.")

    final_text = common.run_tool_loop(
        system_prompt=system_prompt,
        tools=TOOLS,
        user_content=user_content,
        dispatch=make_dispatch(config, args.application_id, args.task),
        label_fn=lambda inp: inp.get("application_id") or inp.get("to") or inp.get("version") or "",
        agent="agent2",
        application_id=args.application_id,
        context=f"{args.task} {args.application_id}",
    )

    print("\n----- Agent 2 output -----\n")
    print(final_text)
    out_path = common.save_run("agent2", today_date, final_text)
    print(f"\nSaved output to {out_path}")

    try:
        parsed = common.parse_agent_json(final_text)
        note = parsed.get("notification")
        if note and note.get("template"):
            result = notifier.send_notification(
                template=note["template"],
                variables=note.get("variables", []),
                application_id=parsed.get("application_id"),
                config=config,
                urgent=note.get("urgent", False),
            )
            if result["delivered"]:
                print(f"Sent {note['template']} notification over Telegram.")
            else:
                print(f"Notification logged but not delivered: {result['reason']}")
    except (json.JSONDecodeError, AttributeError):
        pass


if __name__ == "__main__":
    main()
