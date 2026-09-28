"""
Shared plumbing for the three agent runners (run_agent1.py, run_agent2.py, run_agent3.py):
reading spec files, building <today>, and driving the Anthropic tool-use loop against
whatever tool set a given agent declares.

Two LLM backends, chosen by which key is set:
  - LLM_API_KEY set  -> any OpenAI-compatible endpoint (free options: Google Gemini's free
                        tier, Groq, OpenRouter free models). Defaults to Gemini.
                        LLM_BASE_URL and LLM_MODEL override the defaults.
  - otherwise        -> Anthropic, using ANTHROPIC_API_KEY.

Each runner still owns its own system prompt assembly, tool declarations and tool
dispatch - this module just avoids repeating the request/response loop three times.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

import events

_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LLM_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_LLM_MODEL = "gemini-3.8-flash"


def _use_openai_compat() -> bool:
    return bool(os.environ.get("LLM_API_KEY"))


if _use_openai_compat():
    MODEL = os.environ.get("LLM_MODEL", DEFAULT_LLM_MODEL)
else:
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
    if _use_openai_compat():
        return os.environ["LLM_API_KEY"]
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("No LLM key is set. Set one and re-run (PowerShell):", file=sys.stderr)
        print('  $env:LLM_API_KEY = "..."         (free: Google AI Studio / Groq key)', file=sys.stderr)
        print('  $env:ANTHROPIC_API_KEY = "sk-ant-..."   (paid alternative)', file=sys.stderr)
        sys.exit(1)
    return api_key


def _run_tool_loop_anthropic(
    system_prompt: str,
    tools: list[dict],
    user_content: str,
    dispatch: Callable[[str, dict], dict],
    label_fn: Callable[[Any], str] | None = None,
    on_round: Callable[[int], None] | None = None,
) -> str:
    """
    Drives the Anthropic messages loop until the model stops calling tools.
    `dispatch(tool_name, tool_input) -> dict` is called for every tool_use block.
    Returns the model's final text (expected to be the agent's JSON output).
    """
    import anthropic

    api_key = require_api_key()
    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": user_content}]

    for round_n in range(1, MAX_TOOL_ROUNDS + 1):
        if on_round:
            on_round(round_n)
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


WEB_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string"}},
    "required": ["query"],
}


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    # Anthropic's server-side web_search has no input_schema; expose it as a normal function
    # (dispatched client-side to tools.web_search) for other backends.
    tools = [
        {"name": "web_search", "description": "Search the web. Returns titles, URLs and snippets.",
         "input_schema": WEB_SEARCH_SCHEMA}
        if t.get("type", "").startswith("web_search") else t
        for t in tools
    ]
    return [
        {"type": "function", "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
        }}
        for t in tools
    ]


DEFAULT_FALLBACK_MODELS = "gemini-3.7-flash,gemini-3.5-flash,gemini-3.1-flash-lite"
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 90          # a single model call that has produced nothing for this long is treated as hung
CALL_BUDGET_SECONDS = 300  # ...and one logical call (all its retries and fallbacks) never takes longer than this


def _chat_completion(payload: dict) -> dict:
    """
    POSTs to /chat/completions. Server overload or rate limits, and calls that hang or drop the
    connection, are retried with a short backoff and then handed to the next model in
    LLM_FALLBACK_MODELS (comma-separated; set it empty to disable). The whole call is capped at
    CALL_BUDGET_SECONDS so one stuck request can't stall a run for many minutes.
    """
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_LLM_BASE_URL).rstrip("/")
    headers = {"Authorization": f"Bearer {require_api_key()}"}
    fallbacks = os.environ.get("LLM_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS)
    models = [payload["model"]] + [m.strip() for m in fallbacks.split(",") if m.strip()]
    deadline = time.monotonic() + CALL_BUDGET_SECONDS
    last_error = "no models tried"
    for model in models:
        for attempt in range(3):
            if time.monotonic() > deadline:
                raise RuntimeError(f"LLM call gave up after {CALL_BUDGET_SECONDS}s. Last error: {last_error}")
            try:
                resp = requests.post(base_url + "/chat/completions", json={**payload, "model": model},
                                     headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_error = f"{model}: {type(exc).__name__}"
                if attempt < 1:            # one quick retry, then try the next model
                    time.sleep(3)
                    continue
                break
            if resp.ok:
                return resp.json()
            last_error = f"{model}: {resp.status_code} {resp.text[:300]}"
            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < 2:
                    time.sleep(5 * 2 ** attempt)
                    continue
                break  # still overloaded: try the next model
            if resp.status_code == 404:
                break  # model unavailable to this key: try the next one
            raise RuntimeError(f"LLM request failed ({resp.status_code}): {resp.text[:500]}")
        print(f"  LLM model {model} unavailable, trying next...", file=sys.stderr)
    raise RuntimeError(f"LLM request failed on all models. Last error: {last_error}")


PARALLEL_HINT = (
    "\n\nTool use: when several tool calls do not depend on each other (for example several searches, "
    "or fetching several pages), request them together in a single turn instead of one per turn. "
    "That keeps the number of turns down."
)


def _run_tool_loop_openai(
    system_prompt: str,
    tools: list[dict],
    user_content: str,
    dispatch: Callable[[str, dict], dict],
    label_fn: Callable[[Any], str] | None = None,
    on_round: Callable[[int], None] | None = None,
) -> str:
    """Same loop as the Anthropic one, over an OpenAI-compatible /chat/completions API."""
    oa_tools = _to_openai_tools(tools)
    messages = [
        {"role": "system", "content": system_prompt + PARALLEL_HINT},
        {"role": "user", "content": user_content},
    ]

    for round_n in range(1, MAX_TOOL_ROUNDS + 1):
        if on_round:
            on_round(round_n)
        payload = {"model": MODEL, "messages": messages}
        if oa_tools:
            payload["tools"] = oa_tools
        message = _chat_completion(payload)["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []

        assistant_msg = {"role": "assistant", "content": message.get("content") or ""}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        if not tool_calls:
            return message.get("content") or ""

        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            label = label_fn(args) if label_fn else ""
            print(f"  [round {round_n}] tool: {name} {label}")
            try:
                result = dispatch(name, args)
            except Exception as exc:  # noqa: BLE001 - surface tool errors to the model
                result = {"error": str(exc)}
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, default=str),
            })

    print("Hit MAX_TOOL_ROUNDS without a final answer.", file=sys.stderr)
    sys.exit(2)


def _result_summary(result: Any) -> str:
    """One short line describing a tool result, for the activity log (never the content)."""
    if not isinstance(result, dict):
        return "ok"
    if result.get("error"):
        return f"error: {str(result['error'])[:90]}"
    if isinstance(result.get("results"), list):
        return f"{len(result['results'])} results"
    if result.get("ok") is False:
        return "not ok"
    if isinstance(result.get("record"), dict) and result["record"].get("application_id"):
        return f"saved {result['record']['application_id']} ({result['record'].get('status')})"
    return "ok"


def run_tool_loop(
    system_prompt: str,
    tools: list[dict],
    user_content: str,
    dispatch: Callable[[str, dict], dict],
    label_fn: Callable[[Any], str] | None = None,
    agent: str = "agent",
    application_id: str | None = None,
    context: str | None = None,
) -> str:
    """
    Drives the model <-> tools loop on whichever backend is configured (see module docstring)
    and reports what happens to the activity log (events.py) so the dashboard can show it live.
    """
    run_id = events.new_run_id()

    def emit(type_: str, summary: str, **data) -> None:
        events.emit(agent, type_, summary, application_id=application_id, run_id=run_id, **data)

    def logged_dispatch(name: str, tool_input: dict) -> dict:
        label = str(label_fn(tool_input)) if label_fn else ""
        emit("tool", f"{name} {label}".strip(), tool=name)
        try:
            result = dispatch(name, tool_input)
        except Exception as exc:  # noqa: BLE001 - report, then let the loop handle it as before
            emit("tool_result", f"{name}: error: {str(exc)[:90]}", tool=name, ok=False)
            raise
        summary = _result_summary(result)
        emit("tool_result", f"{name}: {summary}", tool=name, ok=not summary.startswith("error"))
        return result

    emit("run_start", f"Started{' - ' + context if context else ''} ({MODEL})")
    loop = _run_tool_loop_openai if _use_openai_compat() else _run_tool_loop_anthropic
    try:
        text = loop(system_prompt, tools, user_content, logged_dispatch, label_fn,
                    on_round=lambda n: emit("llm", f"Round {n}: asking the model", round=n))
    except SystemExit:
        emit("error", "Stopped: hit the tool-round limit without a final answer")
        emit("run_end", "Failed", ok=False)
        raise
    except Exception as exc:  # noqa: BLE001
        emit("error", str(exc)[:180])
        emit("run_end", "Failed", ok=False)
        raise
    emit("run_end", "Finished", ok=True)
    return text


def save_run(agent_name: str, date_str: str, text: str) -> str:
    out_dir = os.path.join(_DIR, "runs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(
        out_dir, f"{agent_name}_{date_str}_{datetime.now().strftime('%H%M%S')}.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text or "")
    return out_path
