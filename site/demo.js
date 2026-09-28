"use strict";
/*
  Plays a made-up day for the hosted demo: the scheduler starts the search, Agent 1 looks things
  up and saves a match, you get an approval request, you approve, Agent 2 prepares the
  application, and you send it. Everything here is invented (fictional companies, no real data)
  and it runs entirely in the browser: no server, no network calls.

  It feeds the same events and state to the shared Workspace scene that the live dashboard uses.
*/
(function () {
  const AGENTS = ["scheduler", "agent1", "agent2", "agent3", "poller", "notifier", "mailer"];
  const LABEL = { scheduler: "Scheduler", agent1: "Research", agent2: "Application", agent3: "Tracking",
                  poller: "Telegram listener", notifier: "Notifier", mailer: "Mailer" };
  const STEP_MS = 1300, PAUSE_BETWEEN_DAYS_MS = 5000;

  const state = { agents: {}, records: new Array(9) };
  for (const id of AGENTS) state.agents[id] = { status: "idle", online: id === "poller" || id === "scheduler" ? true : null };

  let speed = 1, paused = false, seq = 0, day = 200;
  const briefTimers = {};

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function step(times) {
    for (let i = 0; i < (times || 1); i++) {
      await sleep(STEP_MS / speed);
      while (paused) await sleep(150);
    }
  }

  // ---- the activity feed
  const feedEl = () => document.getElementById("feed");
  function addToFeed(ev) {
    const list = feedEl(); if (!list) return;
    const time = new Date(ev.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    const li = document.createElement("li");
    li.className = "fresh";
    const when = document.createElement("span"); when.className = "when"; when.textContent = time;
    const body = document.createElement("div");
    const who = document.createElement("span"); who.className = "who";
    const sw = document.createElement("span"); sw.className = "sw"; sw.style.background = `var(--c-${ev.agent})`;
    who.append(sw, LABEL[ev.agent] || ev.agent);
    const what = document.createElement("span"); what.className = "what" + (ev.type === "error" ? " err" : "");
    what.textContent = ev.summary + (ev.application_id ? "  " + ev.application_id : "");
    body.append(who, what);
    li.append(when, body);
    list.prepend(li);
    while (list.children.length > 60) list.lastChild.remove();
  }

  // ---- state that follows the events, the way the real dashboard derives it
  function track(ev) {
    const a = state.agents[ev.agent];
    if (!a) return;
    if (ev.type === "run_start" || ev.type === "job_start") a.status = "working";
    else if (ev.type === "run_end" || ev.type === "job_end") a.status = "idle";
    else if (["poller", "notifier", "mailer"].includes(ev.agent)) {
      a.status = "working";
      clearTimeout(briefTimers[ev.agent]);
      briefTimers[ev.agent] = setTimeout(() => { a.status = "idle"; window.Workspace.onState(state); }, 6000 / speed);
    }
    window.Workspace.onState(state);
  }
  function emit(agent, type, summary, app, data) {
    const ev = { id: ++seq, ts: new Date().toISOString(), agent, type, summary, application_id: app || null, data: data || {} };
    if ((type === "tool" || type === "tool_result") && !ev.data.tool) ev.data.tool = summary.split(" ")[0].replace(":", "");
    track(ev);
    window.Workspace.onEvent(ev);
    addToFeed(ev);
  }
  const ev = async (agent, type, summary, app, data, steps) => { emit(agent, type, summary, app, data); await step(steps); };

  // ---- one scripted day
  async function playDay() {
    const id = "APP-0" + (++day);

    // 1. the scheduler starts the daily search and Agent 1 works
    await ev("scheduler", "job_start", "daily_search: starting (attempt 1)", null, { job: "daily_search" });
    await ev("agent1", "run_start", "Started - daily job search");
    await ev("agent1", "llm", "Round 1: asking the model");
    await ev("agent1", "tool", "web_search business analyst Colombo");
    await ev("agent1", "tool_result", "web_search: 5 results");
    await ev("agent1", "tool", "fetch_page careers.example.com/ba-lead");
    await ev("agent1", "tool_result", "fetch_page: ok");
    await ev("agent1", "llm", "Round 2: asking the model");
    await ev("agent1", "tool", "tracker_search a matching job?");
    await ev("agent1", "tool_result", "tracker_search: 0 results", null, null, 2);
    await ev("agent1", "tool", "tracker_upsert Globex Corporation", null, null, 4);
    state.records.push(0);
    await ev("agent1", "tool_result", `tracker_upsert: saved ${id} (Discovered)`, id, null, 2);
    await ev("agent1", "run_end", "Finished", null, { ok: true });
    await ev("scheduler", "job_end", "daily_search: Agent 1 finished", null, { job: "daily_search", ok: true });

    // 2. you get an approval request
    await ev("notifier", "notification", "Sent approval_request to you", id, null, 4);

    // 3. you reply "1"; Agent 3 moves it to Ready to Apply
    await ev("poller", "message_in", "You sent a message (1 chars)", id, null, 3);
    await ev("agent3", "run_start", "Started - message mode", id);
    await ev("agent3", "llm", "Round 1: asking the model", id);
    await ev("agent3", "tool", `tracker_upsert ${id}`, id, null, 4);
    await ev("agent3", "tool_result", `tracker_upsert: saved ${id} (Ready to Apply)`, id);
    await ev("agent3", "run_end", "Finished", id, { ok: true });
    await ev("poller", "message_out", "Replied to you (54 chars)", id, null, 3);

    // 4. the apply queue: Agent 2 prepares the application
    await ev("scheduler", "job_start", "apply_queue: starting (attempt 1)", null, { job: "apply_queue" }, 3);
    await ev("agent2", "run_start", `Started - apply ${id}`, id);
    await ev("agent2", "llm", "Round 1: asking the model", id);
    await ev("agent2", "tool", "fetch_page careers.example.com/ba", id);
    await ev("agent2", "tool_result", "fetch_page: ok", id);
    await ev("agent2", "tool", "get_resume ba-v3", id);
    await ev("agent2", "tool_result", "get_resume: ok", id);
    await ev("agent2", "llm", "Round 2: asking the model", id);
    await ev("agent2", "tool", "email_send hr@globex.example", id, null, 3);
    await ev("mailer", "email", "Staged a apply email for your approval (send 7c1e90ab)", id, null, 4);
    await ev("agent2", "tool_result", "email_send: not ok", id);
    await ev("agent2", "run_end", "Finished", id, { ok: true });
    await ev("scheduler", "job_end", "apply_queue: Ready to Apply -> Application Started", null, { job: "apply_queue", ok: true });
    await ev("notifier", "notification", "Sent email_ready to you", id, null, 6);

    // 5. you reply "send 7c1e90ab"
    await ev("poller", "message_in", "You sent: send 7c1e90ab", id, null, 3);
    await ev("mailer", "email", "Sent a apply email", id, null, 6);
    await ev("poller", "message_out", "Replied to you (28 chars)", id, null, 2);
  }

  async function loop() {
    for (;;) {
      await playDay();
      await sleep(PAUSE_BETWEEN_DAYS_MS / speed);
      while (paused) await sleep(150);
    }
  }

  window.Demo = {
    start() { window.Workspace.onState(state); loop(); },
    setSpeed(x) { speed = x; },
    togglePause() { paused = !paused; return paused; },
  };
})();
