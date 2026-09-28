"""
Shared plumbing for the three agent runners (run_agent1.py, run_agent2.py, run_agent3.py):
reading spec files, building <today>, and driving the Anthropic tool-use loop against
whatever tool set a given agent declares.

Each runner still owns its own system prompt assembly, tool declarations and tool
dispatch - this module just avoids repeating the request/response loop three times.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import anthropic

_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5")
MAX_TOOL_ROUNDS = 40


def read_spec(filename: str) -> str:
    with open(os.path.join(_DIR, filename), "r", encoding="utf-8") as f:
        return f.read()


def load_config() -> dict:
    with open(os.path.join(_DIR, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def today_block(tz_name: str) -> tuple[str, str]:
    """Returns (json string for <today>, plain YYYY-MM-DD date)."""
    now = datetime.now(ZoneInfo(tz_name))
    date_str = now.strftime("%Y-%m-%d")
    block = json.dumps({
        "date": date_str,
        "weekday": now.strftime("%A"),
        "timezone": tz_name,
    })
    return block, date_str


def require_api_key() -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY is not set. Set it and re-run:", file=sys.stderr)
        print('  $env:ANTHROPIC_API_KEY = "sk-ant-..."   (PowerShell)', file=sys.stderr)
        sys.exit(1)
    return api_key


def run_tool_loop(
    system_prompt: str,
    tools: list[dict],
    user_content: str,
    dispatch: Callable[[str, dict], dict],
    label_fn: Callable[[Any], str] | None = None,
) -> str:
    """
    Drives the Anthropic messages loop until the model stops calling tools.
    `dispatch(tool_name, tool_input) -> dict` is called for every tool_use block.
    Returns the model's final text (expected to be the agent's JSON output).
    """
    api_key = require_api_key()
    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": user_content}]

    for round_n in range(1, MAX_TOOL_ROUNDS + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        text_blocks = [b.text for b in response.content if b.type == "text"]

        for tu in tool_uses:
            label = label_fn(tu.input) if label_fn else ""
            print(f"  [round {round_n}] tool: {tu.name} {label}")

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return "\n".join(text_blocks)

        tool_results = []
        for tu in tool_uses:
            try:
                result = dispatch(tu.name, tu.input)
            except Exception as exc:  # noqa: BLE001 - surface tool errors to the model
                result = {"error": str(exc)}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu.id,
                "content": json.dumps(result, default=str),
            })
        messages.append({"role": "user", "content": tool_results})

    print("Hit MAX_TOOL_ROUNDS without a final answer.", file=sys.stderr)
    sys.exit(2)


def save_run(agent_name: str, date_str: str, text: str) -> str:
    out_dir = os.path.join(_DIR, "runs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(
        out_dir, f"{agent_name}_{date_str}_{datetime.now().strftime('%H%M%S')}.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text or "")
    return out_path
