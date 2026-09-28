"use strict";
/*
  The Workspace view: a cutaway office. Each agent is a robot that sits at its own desk. When it
  needs something it stands up, walks to it (up the stairs if it is on the other floor), carries
  a folder or envelope there, and walks back. Real events drive all of it:

    tracker tools   -> the robot files a folder in (or searches) the records cabinets
    email tools     -> the robot leaves an envelope in the mailroom; the mailer robot carries it on
    a sent email    -> the mailer robot drops it down the tube to the Employer inbox
    a job starting  -> the scheduler robot walks a ticket to the agent's desk
    your messages   -> a note flies in to reception; the listener robot relays it

  Exposed as window.Workspace: init(svg), onState(state), onEvent(event).
*/
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const REDUCED = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

  function s(tag, attrs, ...kids) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) node.setAttribute(k, v);
    for (const kid of kids.flat()) if (kid != null) node.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return node;
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------------------------------------------------------------- the building
  const FLOOR = { 1: 230, 2: 455 };      // y of the floor line on each storey
  const STAIRS = { 1: 84, 2: 34 };       // x where a robot steps onto the stairs, per storey
  const WALK_SPEED = 115;                // pixels per second
  const COLOR = {
    scheduler: "var(--c-scheduler)", agent1: "var(--c-agent1)", agent2: "var(--c-agent2)", agent3: "var(--c-agent3)",
    poller: "var(--c-poller)", notifier: "var(--c-notifier)", mailer: "var(--c-mailer)",
  };
  const NAME = { scheduler: "Scheduler", agent1: "Research", agent2: "Application", agent3: "Tracking",
                 poller: "Telegram listener", notifier: "Notifier", mailer: "Mailer" };
  const ROOMS = [
    { label: "Control room", floor: 1, x: 90 }, { label: "Research lab", floor: 1, x: 310 },
    { label: "Records room", floor: 1, x: 530 }, { label: "Tracking desk", floor: 1, x: 750 },
    { label: "Application studio", floor: 2, x: 90 }, { label: "Mailroom", floor: 2, x: 310 },
    { label: "Notification desk", floor: 2, x: 530 }, { label: "Reception", floor: 2, x: 750 },
  ];
  // where each robot sits (its chair) and where a visitor stands to speak to it
  const DESK = { scheduler: { x: 150, f: 1 }, agent1: { x: 370, f: 1 }, agent3: { x: 810, f: 1 },
                 agent2: { x: 150, f: 2 }, mailer: { x: 370, f: 2 }, notifier: { x: 590, f: 2 }, poller: { x: 810, f: 2 } };
  const VISIT = { scheduler: { x: 118, f: 1 }, agent1: { x: 338, f: 1 }, agent3: { x: 778, f: 1 },
                  agent2: { x: 118, f: 2 }, mailer: { x: 338, f: 2 }, notifier: { x: 558, f: 2 }, poller: { x: 778, f: 2 } };
  const SPOT = { cabinet: { x: 590, f: 1 }, mailtable: { x: 492, f: 2 }, hatch: { x: 514, f: 2 }, notifIn: { x: 704, f: 2 } };
  const YOU = { x: 900, y: 500 };
  const TUBE = "M514 468 V500 H436";
  const MAILBOX = { x: 408, y: 500 };

  const layers = {};
  const robots = {};
  const screens = {};
  const props = {};
  let drawerCount = 0;
  let mailOnTable = false;

  function rect(x, y, w, h, cls, extra) { return s("rect", { x, y, width: w, height: h, class: cls, ...(extra || {}) }); }

  function desk(x, floor, w, owner) {
    const y = FLOOR[floor];
    return s("g", {}, rect(x, y - 34, w, 34, "ws-desk", { rx: 2 }), rect(x - 3, y - 39, w + 6, 6, "ws-desk-top", { rx: 2 }),
      rect(x, y - 5, w, 3, "", { fill: COLOR[owner] }));
  }
  function monitor(owner, x, floor) {
    const y = FLOOR[floor];
    const screen = rect(x, y - 66, 30, 22, "ws-screen", { rx: 3 });
    if (owner) screens[owner] = screen;
    return s("g", {}, rect(x + 11, y - 46, 8, 8, "ws-prop"), screen);
  }
  function chair(owner) {
    const { x, f } = DESK[owner], y = FLOOR[f];
    return s("g", {}, rect(x - 11, y - 17, 22, 4, "ws-prop", { rx: 2 }), rect(x - 15, y - 40, 4, 25, "ws-prop", { rx: 2 }),
      rect(x - 2, y - 13, 4, 13, "ws-prop"));
  }

  function buildScene(svg) {
    svg.replaceChildren();
    layers.bg = s("g"); layers.fg = s("g"); layers.robots = s("g"); layers.sprites = s("g");
    svg.append(layers.bg, layers.fg, layers.robots, layers.sprites);
    const bg = layers.bg, fg = layers.fg;

    // building, floors, ground, stairs
    bg.append(rect(0, 468, 1000, 72, "ws-ground"));
    bg.append(rect(90, 20, 880, 448, "ws-wall", { rx: 6 }));
    bg.append(rect(90, 230, 880, 12, "ws-slab"), rect(90, 455, 880, 13, "ws-slab"));
    for (const x of [310, 530, 750]) {
      bg.append(s("line", { x1: x, y1: 24, x2: x, y2: 230, class: "ws-part" }), s("line", { x1: x, y1: 242, x2: x, y2: 455, class: "ws-part" }));
    }
    for (let i = 0; i < 9; i++) {  // stairs between the storeys
      const t = i / 8, x = 34 + t * 50, y = 455 - t * 225;
      bg.append(rect(x - 12, y, 26, 5, "ws-desk-top", { rx: 1 }));
    }
    bg.append(s("text", { x: 60, y: 480, class: "ws-note", "text-anchor": "middle" }, "stairs"));
    for (const r of ROOMS) bg.append(s("text", { x: r.x + 12, y: (r.floor === 1 ? 20 : 242) + 20, class: "ws-room-label" }, r.label));

    // control room: wall clock and calendar
    props.clock = { cx: 262, cy: 84 };
    bg.append(s("circle", { cx: 262, cy: 84, r: 23, class: "ws-prop" }));
    props.hourHand = s("line", { x1: 262, y1: 84, x2: 262, y2: 70, class: "ws-ink", "stroke-width": 2.4 });
    props.minuteHand = s("line", { x1: 262, y1: 84, x2: 262, y2: 64, class: "ws-ink" });
    bg.append(props.hourHand, props.minuteHand);
    bg.append(rect(118, 52, 46, 48, "ws-paper", { rx: 3 }), rect(118, 52, 46, 12, "", { fill: COLOR.scheduler, rx: 3 }));
    for (let i = 0; i < 3; i++) bg.append(s("line", { x1: 124, x2: 158, y1: 74 + i * 9, y2: 74 + i * 9, class: "ws-ink", "stroke-width": 1 }));

    // research lab: whiteboard and globe
    bg.append(rect(340, 50, 88, 50, "ws-paper", { rx: 3 }), s("path", { d: "M350 86 L366 70 L380 80 L398 60 L416 74", class: "ws-ink", "stroke-width": 1.4 }));
    props.globe = s("g", { class: "ws-globe" }, s("line", { x1: 500, y1: 186, x2: 500, y2: 230, class: "ws-ink" }),
      s("circle", { cx: 500, cy: 172, r: 17, class: "ws-glass" }),
      s("ellipse", { cx: 500, cy: 172, rx: 8, ry: 17, class: "ws-ink ws-globe-spin", "stroke-width": 1.2 }),
      s("line", { x1: 483, y1: 172, x2: 517, y2: 172, class: "ws-ink", "stroke-width": 1.2 }));
    bg.append(props.globe);

    // records room: cabinets, sign, drawers
    props.drawers = [];
    fg.append(rect(606, 96, 132, 28, "ws-sign", { rx: 5 }), s("text", { x: 672, y: 114, class: "ws-sign-text", "text-anchor": "middle", id: "wsCount" }, "0 jobs"));
    for (let c = 0; c < 3; c++) {
      const x = 606 + c * 46;
      fg.append(rect(x, 134, 40, 96, "ws-desk", { rx: 3 }));
      for (let d = 0; d < 2; d++) {
        const drawer = s("g", { class: "ws-drawer" }, rect(x + 3, 138 + d * 46, 34, 40, "", { rx: 2 }), rect(x + 13, 154 + d * 46, 14, 3, "ws-desk-top", { rx: 1.5 }));
        fg.append(drawer);
        props.drawers.push(drawer);
      }
    }

    // furniture at each desk
    const placeDesk = (owner, dx, w) => {
      const { x, f } = DESK[owner];
      bg.append(chair(owner));
      fg.append(desk(x + dx, f, w, owner), monitor(owner, x + dx + 34, f));
    };
    for (const owner of Object.keys(DESK)) placeDesk(owner, 16, 92);
    fg.append(rect(272, 200, 30, 30, "ws-prop", { rx: 3 }), rect(276, 194, 22, 6, "ws-paper"));  // printer (control room is floor 1)
    // application studio: resume stack and printer
    for (let i = 0; i < 3; i++) fg.append(rect(226, FLOOR[2] - 42 - i * 3, 22, 3, "ws-paper"));
    fg.append(rect(276, FLOOR[2] - 30, 30, 30, "ws-prop", { rx: 3 }), rect(280, FLOOR[2] - 36, 22, 6, "ws-paper"));
    // mailroom: pigeonholes, mail envelope on the table, hatch and tube
    for (let r = 0; r < 3; r++) for (let c = 0; c < 4; c++) bg.append(rect(326 + c * 20, 300 + r * 20, 17, 16, "ws-prop", { rx: 2 }));
    props.mailEnv = s("g", { display: "none" }, rect(454, FLOOR[2] - 50, 20, 12, "ws-paper", { rx: 1.5 }), s("path", { d: `M454 ${FLOOR[2] - 50} L464 ${FLOOR[2] - 43} L474 ${FLOOR[2] - 50}`, class: "ws-ink", "stroke-width": 1.2 }));
    fg.append(props.mailEnv);
    bg.append(rect(502, 455, 24, 13, "ws-hole"));
    bg.append(s("path", { d: TUBE, class: "ws-tube" }), s("path", { d: TUBE, class: "ws-tube-in" }));
    // notification desk: handset, radio dish, envelope prop
    props.notifEnv = s("g", { display: "none" }, rect(672, FLOOR[2] - 50, 20, 12, "ws-paper", { rx: 1.5 }), s("path", { d: `M672 ${FLOOR[2] - 50} L682 ${FLOOR[2] - 43} L692 ${FLOOR[2] - 50}`, class: "ws-ink", "stroke-width": 1.2 }));
    fg.append(props.notifEnv);
    bg.append(s("path", { d: "M704 330 Q732 330 732 302", class: "ws-ink" }), s("circle", { cx: 732, cy: 298, r: 4, class: "ws-prop" }), s("path", { d: "M740 292 Q748 298 740 304 M746 288 Q758 298 746 308", class: "ws-signal" }));
    // reception: Telegram sign
    bg.append(rect(846, 272, 78, 32, "ws-sign", { rx: 6 }), s("text", { x: 885, y: 293, class: "ws-sign-text", "text-anchor": "middle" }, "Telegram"));
    // tracking desk: poster
    bg.append(rect(846, 52, 64, 44, "ws-paper", { rx: 3 }), s("path", { d: "M856 84 L872 68 L884 78 L900 60", class: "ws-ink", "stroke-width": 1.4 }));

    // outside: the Employer inbox and you
    props.flag = s("g", { class: "ws-flag" }, rect(MAILBOX.x + 26, MAILBOX.y - 34, 3, 26, "", { fill: "var(--critical)" }), rect(MAILBOX.x + 29, MAILBOX.y - 34, 12, 8, "", { fill: "var(--critical)" }));
    fg.append(rect(MAILBOX.x - 26, MAILBOX.y - 20, 54, 38, "ws-desk", { rx: 7 }), rect(MAILBOX.x - 14, MAILBOX.y - 6, 30, 4, "ws-hole", { rx: 2 }), props.flag);
    bg.append(s("text", { x: MAILBOX.x, y: 534, class: "ws-note", "text-anchor": "middle" }, "Employer inbox"));
    const you = s("g", { transform: `translate(${YOU.x} ${YOU.y})` },
      s("circle", { cx: 0, cy: -26, r: 8, fill: "var(--ink-2)" }), rect(-9, -16, 18, 24, "", { rx: 6, fill: "var(--ink-2)" }),
      rect(6, -10, 7, 12, "", { rx: 2, fill: "var(--accent)" }));
    fg.append(you, s("text", { x: YOU.x, y: 534, class: "ws-note", "text-anchor": "middle" }, "You (Telegram)"));
    props.youBubble = makeBubble("var(--accent)");
    props.youBubble.g.setAttribute("transform", `translate(${YOU.x} ${YOU.y - 52})`);
    layers.sprites.append(props.youBubble.g);
  }

  // ---------------------------------------------------------------- robots
  function makeBubble(color) {
    const g = s("g", { class: "bubble" });
    const box = s("rect", { x: -30, y: -10, width: 60, height: 20, rx: 10, stroke: color });
    const text = s("text", { x: 0, y: 4, "text-anchor": "middle" }, "");
    g.append(box, text);
    return { g, box, text, timer: null };
  }
  function makeRobot(id) {
    const color = COLOR[id];
    const root = s("g", { class: "rb sit idle" });
    const inner = s("g", { class: "inner" });
    const body = s("g", { class: "bodyflip" });
    body.append(
      s("rect", { class: "leg leg-l", x: -7, y: -14, width: 5, height: 14, rx: 2, fill: "var(--ink-2)" }),
      s("rect", { class: "leg leg-r", x: 2, y: -14, width: 5, height: 14, rx: 2, fill: "var(--ink-2)" }),
      s("rect", { class: "arm arm-l", x: -14, y: -31, width: 5, height: 15, rx: 2.5, fill: "var(--ink-2)" }),
      s("rect", { class: "torso", x: -10, y: -34, width: 20, height: 20, rx: 6, fill: color }),
      s("rect", { class: "arm arm-r", x: 9, y: -31, width: 5, height: 15, rx: 2.5, fill: "var(--ink-2)" }));
    const head = s("g", { class: "head" },
      s("line", { x1: 0, y1: -54, x2: 0, y2: -60, stroke: color, "stroke-width": 2, "stroke-linecap": "round" }),
      s("circle", { cx: 0, cy: -61, r: 2.6, fill: color }),
      s("rect", { x: -12, y: -54, width: 24, height: 20, rx: 8, fill: color }),
      s("rect", { x: -8.5, y: -50, width: 17, height: 11, rx: 5, fill: "var(--surface)" }),
      s("circle", { class: "eye", cx: -3.6, cy: -45, r: 1.9 }), s("circle", { class: "eye", cx: 3.6, cy: -45, r: 1.9 }),
      s("path", { class: "mouth", d: "M-3 -42 Q0 -40 3 -42" }));
    body.append(head);
    // what the robot is carrying (drawn in the body's frame, so it turns with it)
    const item = s("g", { transform: "translate(13 -22)" });
    const icons = {
      folder: s("g", { display: "none" }, rect(0, -6, 15, 11, "", { rx: 2, fill: "#f2c14e", stroke: "var(--line-strong)" }), rect(0, -9, 7, 4, "", { rx: 1, fill: "#e0a92f" })),
      mag: s("g", { display: "none" }, s("circle", { cx: 5, cy: -3, r: 5, fill: "var(--surface)", stroke: "var(--ink)", "stroke-width": 1.6 }), s("line", { x1: 9, y1: 1, x2: 14, y2: 6, stroke: "var(--ink)", "stroke-width": 2 })),
      env: s("g", { display: "none" }, rect(0, -7, 16, 11, "ws-paper", { rx: 1.5 }), s("path", { d: "M0 -7 L8 -1 L16 -7", class: "ws-ink", "stroke-width": 1.2 })),
      ticket: s("g", { display: "none" }, rect(0, -8, 16, 12, "", { rx: 2, fill: "var(--c-scheduler)" }), s("text", { x: 8, y: 1, "text-anchor": "middle", class: "ws-packet-glyph" }, "▶")),
      note: s("g", { display: "none" }, rect(0, -8, 12, 13, "ws-paper", { rx: 1.5 }), s("line", { x1: 2.5, y1: -4, x2: 9.5, y2: -4, class: "ws-ink", "stroke-width": 1 }), s("line", { x1: 2.5, y1: 0, x2: 9.5, y2: 0, class: "ws-ink", "stroke-width": 1 })),
      phone: s("g", { display: "none" }, rect(3, -9, 8, 14, "", { rx: 2, fill: "var(--ink)" }), rect(4.5, -7, 5, 8, "", { fill: "#6fd3ff" })),
    };
    item.append(...Object.values(icons));
    body.append(item);
    inner.append(body);
    const badge = s("text", { class: "badge", x: 20, y: -74, "text-anchor": "middle" }, "");
    const zzz = s("text", { class: "zzz", x: 18, y: -80 }, "z z");
    const bubble = makeBubble(color);
    bubble.g.setAttribute("transform", "translate(0 -94)");
    root.append(s("ellipse", { class: "ws-shadow", cx: 0, cy: 1, rx: 17, ry: 4 }), s("g", { transform: "scale(1.3)" }, inner), badge, zzz, bubble.g);
    layers.robots.append(root);
    const spot = DESK[id];
    const r = { id, root, body, icons, badge, bubble, x: spot.x, y: FLOOR[spot.f], spot: { ...spot }, seated: true, walking: false,
                mood: "idle", queue: Promise.resolve(), pending: 0, facing: 1 };
    setPos(r, r.x, r.y);
    pose(r);
    robots[id] = r;
  }
  function setPos(r, x, y) { r.x = x; r.y = y; r.root.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)})`); }
  function face(r, dir) { if (r.facing !== dir) { r.facing = dir; r.body.setAttribute("transform", dir < 0 ? "scale(-1 1)" : ""); } }
  function pose(r) {
    const p = r.walking ? "walk" : r.seated ? (r.mood === "offline" ? "sleep" : r.mood === "working" ? "type" : "sit") : (r.mood === "offline" ? "sleep" : "stand");
    for (const c of ["walk", "type", "sit", "sleep", "stand"]) r.root.classList.toggle(c, c === p);
  }
  function setMood(r, mood) {
    r.mood = mood;
    for (const m of ["idle", "working", "error", "offline"]) r.root.classList.toggle(m, m === mood);
    r.badge.textContent = mood === "error" ? "!" : "";
    const scr = screens[r.id];
    if (scr) { scr.classList.toggle("on", mood === "working"); scr.classList.toggle("err", mood === "error"); }
    pose(r);
  }
  function carry(r, what) { for (const [k, g] of Object.entries(r.icons)) g.setAttribute("display", k === what ? "" : "none"); }
  function say(id, text, ms) {
    const r = robots[id]; if (!r) return;
    const b = r.bubble, t = text.length > 34 ? text.slice(0, 33) + "…" : text, w = Math.max(44, t.length * 6.4 + 18);
    b.text.textContent = t;
    b.box.setAttribute("width", w); b.box.setAttribute("x", -w / 2);
    b.g.classList.add("show");
    clearTimeout(b.timer);
    b.timer = setTimeout(() => b.g.classList.remove("show"), ms || 4200);
  }

  // ---------------------------------------------------------------- walking
  function routePoints(r, to) {
    const pts = [{ x: r.x, y: r.y }];
    if (r.spot.f !== to.f) {
      pts.push({ x: STAIRS[r.spot.f], y: FLOOR[r.spot.f] }, { x: STAIRS[to.f], y: FLOOR[to.f] });
    }
    pts.push({ x: to.x, y: FLOOR[to.f] });
    return pts;
  }
  function walkPoints(r, pts) {
    return new Promise((resolve) => {
      const last = pts[pts.length - 1];
      if (REDUCED) { setPos(r, last.x, last.y); resolve(); return; }
      const seg = [0];
      for (let i = 1; i < pts.length; i++) seg.push(seg[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y));
      const total = seg[seg.length - 1];
      if (total < 1) { resolve(); return; }
      r.walking = true; pose(r);
      const t0 = performance.now();
      (function frame(t) {
        const k = Math.min(1, ((t - t0) / 1000 * WALK_SPEED) / total), dist = k * total;
        let i = 1; while (i < seg.length - 1 && seg[i] < dist) i++;
        const span = seg[i] - seg[i - 1] || 1, f = (dist - seg[i - 1]) / span;
        const dx = pts[i].x - pts[i - 1].x;
        if (Math.abs(dx) > 0.5) face(r, dx > 0 ? 1 : -1);
        setPos(r, pts[i - 1].x + dx * f, pts[i - 1].y + (pts[i].y - pts[i - 1].y) * f);
        if (k < 1) requestAnimationFrame(frame); else { r.walking = false; pose(r); resolve(); }
      })(t0);
    });
  }
  async function goTo(r, spot, facing) {
    if (r.seated) { r.seated = false; pose(r); await sleep(REDUCED ? 0 : 220); }
    await walkPoints(r, routePoints(r, spot));
    r.spot = { x: spot.x, f: spot.f };
    setPos(r, spot.x, FLOOR[spot.f]);
    if (facing) face(r, facing);
  }
  async function sitDown(r) {
    await goTo(r, DESK[r.id], 1);
    r.seated = true; face(r, 1); pose(r);
  }
  // actions run one after another per robot, so a burst of events plays out as a sequence of trips
  function act(id, fn) {
    const r = robots[id]; if (!r) return;
    if (r.pending >= 3) return;
    r.pending++;
    r.queue = r.queue.then(fn).catch(() => {}).then(() => { r.pending--; if (r.pending === 0) settle(r); });
  }
  function settle(r) { if (!r.seated) act(r.id, () => sitDown(r)); }

  // ---------------------------------------------------------------- things that fly, drop and pop up
  function floaty(x, y, text, color) {
    const t = s("text", { x, y, class: "ws-floaty", "text-anchor": "middle", fill: color || "var(--ink)" }, text);
    layers.sprites.append(t); setTimeout(() => t.remove(), 2700);
  }
  function fly(from, to, glyph, color, ms) {
    if (REDUCED || layers.sprites.childElementCount > 16) return;
    const g = s("g", {}, s("circle", { r: 9, fill: "var(--surface)", stroke: color, "stroke-width": 2 }), s("text", { class: "ws-packet-glyph", "text-anchor": "middle", y: 4 }, glyph));
    layers.sprites.append(g);
    const cx = (from.x + to.x) / 2, cy = Math.min(from.y, to.y) - 50, dur = ms || 1200, t0 = performance.now();
    (function frame(t) {
      const k = Math.min(1, (t - t0) / dur), a = (1 - k) * (1 - k), b = 2 * (1 - k) * k, c = k * k;
      g.setAttribute("transform", `translate(${(a * from.x + b * cx + c * to.x).toFixed(1)} ${(a * from.y + b * cy + c * to.y).toFixed(1)})`);
      if (k < 1) requestAnimationFrame(frame); else g.remove();
    })(t0);
  }
  function dropDownTube() {
    return new Promise((resolve) => {
      const path = s("path", { d: TUBE, fill: "none" });
      const g = s("g", {}, rect(-9, -6, 18, 12, "ws-paper", { rx: 2 }));
      layers.sprites.append(g);
      const total = 32 + 78, t0 = performance.now(), dur = REDUCED ? 1 : 1500;
      (function frame(t) {
        const k = Math.min(1, (t - t0) / dur), d = k * total;
        const x = d <= 32 ? 514 : 514 - (d - 32), y = d <= 32 ? 468 + d : 500;
        g.setAttribute("transform", `translate(${x} ${y})`);
        if (k < 1) requestAnimationFrame(frame); else { g.remove(); resolve(); }
      })(t0);
    });
  }
  function drawerAt(idx) { return props.drawers[idx % props.drawers.length]; }

  // ---------------------------------------------------------------- what the robots do
  async function fileAtCabinet(r, what) {
    carry(r, what);
    await goTo(r, SPOT.cabinet, 1);
    const d = drawerAt(drawerCount++);
    d.classList.add("open");
    await sleep(what === "mag" ? 900 : 1200);
    d.classList.remove("open");
    carry(r, null);
    await sleep(250);
    await sitDown(r);
  }
  async function leaveEnvelopeInMailroom(r) {
    carry(r, "env");
    await goTo(r, SPOT.mailtable, -1);
    await sleep(350);
    carry(r, null);
    props.mailEnv.setAttribute("display", ""); mailOnTable = true;
    await sleep(300);
    await sitDown(r);
  }
  async function waitForMail(ms) { const t0 = Date.now(); while (!mailOnTable && Date.now() - t0 < ms) await sleep(150); }
  async function mailerToNotifier(r) {
    await waitForMail(6000);
    await goTo(r, SPOT.mailtable, -1);
    props.mailEnv.setAttribute("display", "none"); mailOnTable = false;
    carry(r, "env");
    await goTo(r, SPOT.notifIn, -1);
    carry(r, null);
    props.notifEnv.setAttribute("display", "");
    say("notifier", "email to show you");
    await sleep(2600);
    props.notifEnv.setAttribute("display", "none");
    await sitDown(r);
  }
  async function mailerSend(r) {
    await goTo(r, SPOT.mailtable, -1);
    props.mailEnv.setAttribute("display", "none"); mailOnTable = false;
    carry(r, "env");
    await goTo(r, SPOT.hatch, 1);
    carry(r, null);
    await dropDownTube();
    props.flag.classList.add("up");
    floaty(MAILBOX.x, MAILBOX.y - 40, "✉ delivered", "var(--good)");
    setTimeout(() => props.flag.classList.remove("up"), 4200);
    await sitDown(r);
  }
  async function deliverTicket(r, job) {
    const target = job === "daily_search" ? "agent1" : "agent2";
    carry(r, "ticket");
    await goTo(r, VISIT[target], 1);
    say(target, "got a job!", 2600);
    await sleep(800);
    carry(r, null);
    await sitDown(r);
  }
  async function relayNote(r, target) {
    say("poller", "hello?", 1800);
    await sleep(500);
    carry(r, "note");
    await goTo(r, VISIT[target], 1);
    say(target, "got it", 2400);
    await sleep(800);
    carry(r, null);
    await sitDown(r);
  }

  // ---------------------------------------------------------------- events and state
  const TOOL_SAY = { web_search: "searching the web", fetch_page: "reading a job page", tracker_search: "checking the tracker",
    tracker_get: "opening a record", tracker_upsert: "saving to the tracker", get_resume: "picking a resume",
    email_send: "writing the email", email_draft: "drafting an email" };
  const JOB_SAY = { daily_search: "starting the search", apply_queue: "checking the queue", follow_up_check: "looking for follow-ups",
    no_response: "closing old ones", daily_summary: "writing the summary", weekly_summary: "writing the summary" };
  const at = (id, dy) => ({ x: DESK[id].x, y: FLOOR[DESK[id].f] + (dy || -75) });

  function onEvent(ev) {
    const id = ev.agent, data = ev.data || {}, tool = data.tool, text = ev.summary || "", r = robots[id];
    if (!r) return;
    if (ev.type === "llm") { say(id, "thinking…"); return; }
    if (ev.type === "run_start") { say(id, "on it…"); return; }
    if (ev.type === "run_end") { say(id, data.ok === false ? "that failed ✕" : "done ✓", 3200); return; }
    if (ev.type === "error") { say(id, "problem: " + text.replace(/^[^:]*:\s*/, ""), 6000); return; }
    if (ev.type === "tool") {
      say(id, TOOL_SAY[tool] || tool || "working");
      if (tool === "tracker_upsert") act(id, () => fileAtCabinet(r, "folder"));
      else if (tool === "tracker_search" || tool === "tracker_get") act(id, () => fileAtCabinet(r, "mag"));
      else if (tool === "web_search") { props.globe.classList.add("busy"); setTimeout(() => props.globe.classList.remove("busy"), 2400); }
      else if (id === "agent2" && tool && tool.startsWith("email_")) act(id, () => leaveEnvelopeInMailroom(r));
      return;
    }
    if (ev.type === "tool_result") {
      const m = /saved (APP-\d+) \(([^)]+)\)/.exec(text);
      if (m) floaty(672, 86, `${m[1]} · ${m[2]}`);
      return;
    }
    if (id === "scheduler" && ev.type === "job_start") {
      say("scheduler", JOB_SAY[data.job] || "starting a job");
      act("scheduler", () => deliverTicket(r, data.job));
    } else if (id === "mailer" && ev.type === "email") {
      if (text.startsWith("Staged")) { say("mailer", "email ready for you"); act("mailer", () => mailerToNotifier(r)); }
      else { say("mailer", "sending it off ✉"); act("mailer", () => mailerSend(r)); }
    } else if (id === "notifier" && ev.type === "notification") {
      say("notifier", "messaging you"); carry(r, "phone"); setTimeout(() => carry(r, null), 1600);
      fly(at("notifier"), { x: YOU.x, y: YOU.y - 30 }, "✉", "var(--c-notifier)", 1500);
      setTimeout(() => { say_you("ping!"); }, 1300);
    } else if (id === "poller" && ev.type === "message_in") {
      say_you(text.includes("send") ? "send it" : "…");
      fly({ x: YOU.x, y: YOU.y - 30 }, at("poller"), "✎", "var(--c-poller)", 1300);
      const target = text.includes("send") ? "mailer" : "agent3";
      setTimeout(() => act("poller", () => relayNote(r, target)), 1200);
    } else if (id === "poller" && ev.type === "message_out") {
      say("poller", "replying"); fly(at("poller"), { x: YOU.x, y: YOU.y - 30 }, "✉", "var(--c-poller)", 1300);
      setTimeout(() => say_you("got it"), 1200);
    }
  }
  function say_you(text) {
    const b = props.youBubble, w = Math.max(44, text.length * 6.4 + 18);
    b.text.textContent = text; b.box.setAttribute("width", w); b.box.setAttribute("x", -w / 2);
    b.g.classList.add("show"); clearTimeout(b.timer); b.timer = setTimeout(() => b.g.classList.remove("show"), 2600);
  }
  function onState(st) {
    for (const id of Object.keys(robots)) {
      const a = st.agents[id], r = robots[id]; if (!a) continue;
      const mood = a.online === false ? "offline" : a.status === "working" ? "working" : a.status === "error" ? "error" : "idle";
      if (mood !== r.mood) setMood(r, mood);
      if (mood === "working" && !r.seated && r.pending === 0) act(id, () => sitDown(r));
    }
    const count = document.getElementById("wsCount");
    if (count) count.textContent = st.records.length + " jobs";
  }
  function tickClock() {
    const c = props.clock; if (!c) return;
    const d = new Date();
    props.minuteHand.setAttribute("transform", `rotate(${d.getMinutes() * 6 + d.getSeconds() * 0.1} ${c.cx} ${c.cy})`);
    props.hourHand.setAttribute("transform", `rotate(${(d.getHours() % 12) * 30 + d.getMinutes() * 0.5} ${c.cx} ${c.cy})`);
  }
  function init(svg) {
    buildScene(svg);
    for (const id of Object.keys(DESK)) makeRobot(id);
    tickClock(); setInterval(tickClock, 1000);
  }
  window.Workspace = { init, onState, onEvent };
})();
