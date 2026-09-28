"""
Runs Agent 1 (Research) for one day: 00_shared_rules.md + 01_research_agent.md as the
system prompt, candidate_profile/config/today wrapped as input, tools wired to tracker.py
and tools.py, output is Agent 1's JSON report (also saved under runs/).

After Agent 1 returns, this is also where the README's "Notifier (code)" step for the
daily search job lives: for every job it left as Discovered, promote it per MODE and send
the candidate an approval_request over Telegram (APPROVAL mode), or move it straight to
Ready to Apply when AUTO mode and the score/approval-reason conditions are met.

Usage:
    python run_agent1.py [--new-listings path/to/listings.json]

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
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 15},
    {
        "name": "fetch_page",
        "description": "Return a job listing page's visible text.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
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
        "name": "tracker_upsert",
        "description": "Create or update a tracker record. Omit application_id to create. "
                        "Code assigns IDs, appends history, refuses duplicates and backward "
                        "status moves.",
        "input_schema": {
            "type": "object",
            "properties": {"record": {"type": "object"}},
            "required": ["record"],
        },
    },
]


DEFAULT_MAX_WEB_SEARCHES = 8


def make_dispatch(config: dict, today_date: str):
    searches = {"used": 0}
    budget = config.get("MAX_WEB_SEARCHES_PER_RUN", DEFAULT_MAX_WEB_SEARCHES)

    def dispatch(name: str, tool_input: dict) -> dict:
        if name == "web_search":  # only reached on non-Anthropic backends
            if searches["used"] >= budget:
                return {"ok": False, "results": [], "error": f"Search budget used up ({budget} searches this run). "
                        "Work with the listings you have already found: fetch, score and save them."}
            searches["used"] += 1
            return tools.web_search(tool_input["query"])
        if name == "fetch_page":
            return tools.fetch_page(tool_input["url"])
        if name == "tracker_search":
            return {"results": tracker.tracker_search(tool_input.get("filters") or {})}
        if name == "tracker_upsert":
            cfg = dict(config)
            cfg["today"] = today_date
            try:
                return {"record": tracker.tracker_upsert(tool_input["record"], config=cfg)}
            except tracker.TrackerError as exc:
                return {"error": str(exc)}
        raise ValueError(f"Unknown tool: {name}")
    return dispatch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--new-listings", help="Path to a JSON file of new listings", default=None)
    args = parser.parse_args()

    config = common.load_config()
    today_json, today_date = common.today_block(config["TIMEZONE"])

    if config.get("PAUSED"):
        print("PAUSED is true in config.json. Not running Agent 1 (tracking rules still apply).")
        return

    candidate_profile = common.read_spec("04_candidate_profile_template.md")
    system_prompt = common.read_spec("00_shared_rules.md") + "\n\n" + common.read_spec("01_research_agent.md")

    user_parts = [
        f"<candidate_profile>\n{candidate_profile}\n</candidate_profile>",
        f"<config>\n{json.dumps(config, indent=2)}\n</config>",
        f"<today>\n{today_json}\n</today>",
    ]
    if args.new_listings:
        with open(args.new_listings, "r", encoding="utf-8") as f:
            user_parts.append(f"<new_listings>\n{f.read()}\n</new_listings>")

    print(f"Running Agent 1 (model={common.MODEL}, date={today_date})...")

    final_text = common.run_tool_loop(
        system_prompt=system_prompt,
        tools=TOOLS,
        user_content="\n\n".join(user_parts),
        dispatch=make_dispatch(config, today_date),
        label_fn=lambda inp: inp.get("url") or inp.get("query") or (inp.get("record") or {}).get("company") or inp.get("filters") or "",
        agent="agent1",
        context="daily job search",
    )

    print("\n----- Agent 1 output -----\n")
    print(final_text)
    out_path = common.save_run("agent1", today_date, final_text)
    print(f"\nSaved output to {out_path}")

    try:
        parsed = json.loads(final_text)
    except json.JSONDecodeError:
        print("Could not parse Agent 1's output as JSON; skipping the notify/promote step.")
        return

    notify_discovered_jobs(parsed.get("jobs", []), config)


def notify_discovered_jobs(jobs: list[dict], config: dict) -> None:
    """The code-level 'Daily search' Notifier step (README.md, Scheduled jobs)."""
    mode = config.get("MODE", "APPROVAL")
    auto_threshold = config.get("MATCH_THRESHOLD_AUTO", 80)

    for job in jobs:
        if job.get("status") != "Discovered":
            continue
        app_id = job.get("application_id")
        if not app_id:
            continue  # tracker_upsert during the run should have filled this in

        score = job.get("score", {}).get("total", 0)
        approval_reasons = job.get("requires_approval_reasons") or []

        if mode == "AUTO" and score >= auto_threshold and not approval_reasons:
            try:
                tracker.tracker_upsert({"application_id": app_id, "status": "Ready to Apply"}, config=config)
                print(f"{app_id}: AUTO mode, score {score} >= {auto_threshold}, no approval "
                      f"reasons -> Ready to Apply (Agent 2 will pick this up).")
            except tracker.TrackerError as exc:
                print(f"{app_id}: could not move to Ready to Apply: {exc}")
            continue

        try:
            tracker.tracker_upsert({"application_id": app_id, "status": "Awaiting Approval"}, config=config)
        except tracker.TrackerError as exc:
            print(f"{app_id}: could not move to Awaiting Approval: {exc}")
            continue

        result = notifier.send_notification(
            template="approval_request",
            variables=[
                score, job.get("role"), job.get("company"), job.get("location"),
                job.get("salary"), "; ".join(job.get("why_match") or []),
                "; ".join(job.get("gaps") or []), app_id,
            ],
            application_id=app_id,
            config=config,
        )
        if result["delivered"]:
            print(f"{app_id}: sent approval_request over Telegram.")
        else:
            print(f"{app_id}: Awaiting Approval, but notification not delivered: {result['reason']}")


if __name__ == "__main__":
    main()
