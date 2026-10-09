"use strict";
// Everything coming from the agent (OCR text, messages, gamertags) is untrusted screen content:
// it is only ever put into the page with textContent / text nodes, never as HTML.

const $ = (sel) => document.querySelector(sel);
const KEY = "xbox_agent_admin_token";
let token = sessionStorage.getItem(KEY) || "";
let timer = null;
let questionKey = "";
let liveTs = null;

function show(node, on) { node.classList.toggle("hidden", !on); }
function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) n.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) n.append(kid);
  return n;
}
function banner(msg) { const b = $("#banner"); b.textContent = msg || ""; show(b, Boolean(msg)); }

async function call(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { "X-Admin-Token": token, "Content-Type": "application/json" } });
  if (res.status === 401) { logout(); throw new Error("التوكن غير صحيح"); }
  return res;
}
async function api(path, body) {
  const res = await call(path, body === undefined ? {} : { method: "POST", body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `خطأ ${res.status}`);
  return data;
}
async function act(button, fn) {
  button.disabled = true;
  try { await fn(); banner(""); await refresh(); } catch (e) { banner(e.message); } finally { button.disabled = false; }
}

const SKIP_LABELS = { use_profile: "استخدم هذا الحساب" };  // set by the agent when "skip" has a specific meaning
const QUESTION_LABELS = { start_game: "ابدأ اللعبة (زر A)", close_game: "أغلق اللعبة", keep: "اتركها وتابع",
  add_account: "سجّل بحساب جديد", use_profile: "استخدم الحساب المسجَّل" };

// ---- screenshots (fetched with the token, shown through blob URLs)
const shots = new Map();
async function showShot(img, id) {
  if (!id) { show(img, false); return; }
  if (!shots.has(id)) {
    const res = await call(`/api/admin/shots/${id}`);
    if (!res.ok) { show(img, false); return; }
    shots.set(id, URL.createObjectURL(await res.blob()));
    if (shots.size > 30) { const [old] = shots.keys(); URL.revokeObjectURL(shots.get(old)); shots.delete(old); }
  }
  img.src = shots.get(id);
  show(img, true);
}

// ---- rendering
function chip(text, cls) { return el("span", { class: `chip ${cls || ""}` }, text); }

function render(st) {
  const s = st.session;
  const active = Boolean(s) && s.status !== "ended";

  $("#chips").replaceChildren(
    chip(st.agent.online ? "الوكيل متصل" : "الوكيل غير متصل", st.agent.online ? "on" : "off"),
    ...(s ? [chip(`الجلسة ${s.id}`), chip(s.state), chip(s.status)] : []));

  show($("#card-start"), !active);
  const notice = active ? s.notice : null;
  show($("#card-notice"), Boolean(notice));
  if (notice) { $("#notice-text").textContent = notice.message; showShot($("#notice-shot"), notice.shot); }

  const q = active ? s.question : null;
  show($("#card-question"), Boolean(q));
  if (q) {
    $("#q-text").textContent = q.message;
    showShot($("#q-shot"), q.shot);
    const key = JSON.stringify([q.message, q.options]);
    if (key !== questionKey) {  // rebuild the buttons only when the question changed, so a click is never lost
      questionKey = key;
      $("#q-options").replaceChildren(...q.options.map((o, i) => el("button", {
        class: i === 0 ? "primary" : "", onclick: (ev) => act(ev.currentTarget, () => api("/api/admin/answer", { choice: o.id })),
      }, QUESTION_LABELS[o.id] || o.label)));
    }
  } else { questionKey = ""; }

  show($("#card-login"), active && Boolean(s.login_code));
  if (active && s.login_code) { $("#login-code").textContent = s.login_code; showShot($("#login-shot"), s.login_shot); }

  const err = active ? s.error : null;
  show($("#card-error"), Boolean(err));
  if (err) {
    $("#err-title").textContent = `توقف الوكيل: ${err.category} (${err.state})`;
    $("#err-msg").textContent = err.message;
    $("#err-ocr").textContent = err.ocr_text || "(لا نص)";
    show($("#btn-skip"), err.can_skip);
    $("#btn-skip").textContent = SKIP_LABELS[err.skip_label] || "تخطي";
    showShot($("#err-shot"), err.shot);
  }

  show($("#card-ready"), active && s.game_ready);

  const del = active ? s.delete_request : null;
  show($("#card-delete"), Boolean(del));
  if (del) $("#del-text").textContent = `الوكيل يطلب حذف البروفايل: ${del.gamertag}`;

  show($("#card-codes"), Boolean(s) && s.codes.length > 0);
  if (s) {
    $("#codes-body").replaceChildren(...s.codes.map((c) =>
      el("tr", {}, el("td", { class: "code" }, c.masked), el("td", {}, c.status))));
  }

  show($("#card-report"), Boolean(s && s.report));
  if (s && s.report) $("#report").textContent = JSON.stringify(s.report, null, 2);

  show($("#btn-abort"), active);

  const waiting = st.pad ? st.pad.waiting : 0;
  $("#pad-status").textContent = `${waiting} أمر بانتظار الوكيل`;
  show($("#pad-status"), waiting > 0);
  if (st.live && st.live.ts && st.live.ts !== liveTs) { liveTs = st.live.ts; loadLive(); }

  $("#events").replaceChildren(...st.events.slice().reverse().map((e) => el("li", {},
    el("span", { class: "t" }, new Date(e.ts * 1000).toLocaleTimeString()),
    el("span", { class: "s" }, `${e.state || e.kind} / ${e.action}`),
    el("span", { class: "m" }, e.message),
    e.shot ? el("button", { onclick: () => showShot($("#event-shot"), e.shot) }, "صورة") : null)));
}

// ---- joystick
let liveUrl = null;
async function loadLive() {
  const res = await call("/api/admin/live");
  if (!res.ok) return;
  const url = URL.createObjectURL(await res.blob());
  $("#live-img").src = url;
  show($("#live-img"), true);
  show($("#live-empty"), false);
  if (liveUrl) URL.revokeObjectURL(liveUrl);
  liveUrl = url;
}
async function pad(action, value) {
  try {
    await api("/api/admin/pad", { action, value: value || null });
    banner("");
  } catch (e) { banner(e.message); return; }
  for (const ms of [500, 1300, 2400]) setTimeout(refresh, ms);  // pick up the frame the agent posts after the press
}
document.querySelectorAll("[data-pad]").forEach((b) => b.addEventListener("click", () => {
  const [action, value] = b.dataset.pad.split(":");
  pad(action, value);
}));
const KEYS = { ArrowUp: "UP", ArrowDown: "DOWN", ArrowLeft: "LEFT", ArrowRight: "RIGHT", Enter: "A", Escape: "B",
  Backspace: "B", x: "X", y: "Y", g: "GUIDE", m: "START", v: "BACK", q: "LB", e: "RB" };
document.addEventListener("keydown", (ev) => {
  if (!$("#pad-keys").checked || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  const t = ev.target;  // typing in a text field must never press console buttons (the switch itself is an INPUT too)
  if (t.tagName === "TEXTAREA" || t.tagName === "SELECT" || (t.tagName === "INPUT" && t.type !== "checkbox")) return;
  const button = KEYS[ev.key] || KEYS[ev.key.toLowerCase()];
  if (!button) return;
  ev.preventDefault();
  pad("press", button);
});

async function refresh() {
  if (!token) return;
  try { render(await api("/api/admin/state")); banner(""); } catch (e) { banner(e.message); }
}

// ---- login gate
function logout() {
  token = ""; sessionStorage.removeItem(KEY); clearInterval(timer); timer = null;
  show($("#gate"), true);
}
function start() {
  show($("#gate"), false);
  refresh();
  timer = timer || setInterval(refresh, 2000);
}
$("#gate-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  token = $("#gate-token").value.trim();
  $("#gate-token").value = "";
  sessionStorage.setItem(KEY, token);
  start();
});

// ---- actions
const command = (type) => (ev) => act(ev.currentTarget, () => api("/api/admin/command", { type }));
$("#btn-start").addEventListener("click", (ev) => act(ev.currentTarget, async () => {
  await api("/api/admin/session", { codes: $("#codes").value, stop_after: $("#stop-after").value || null });
  $("#codes").value = "";
}));
$("#btn-login-ok").addEventListener("click", command("admin_login_confirmed"));
$("#btn-retry").addEventListener("click", command("retry"));
$("#btn-skip").addEventListener("click", command("skip"));
$("#btn-close").addEventListener("click", command("close_game"));
$("#btn-del-no").addEventListener("click", command("deny_delete_profile"));
$("#btn-del-ok").addEventListener("click", (ev) => act(ev.currentTarget, async () => {
  await api("/api/admin/delete/confirm", { gamertag: $("#del-input").value });
  $("#del-input").value = "";
}));
$("#btn-abort").addEventListener("click", (ev) => {
  if (confirm("إيقاف الجلسة الحالية؟")) command("abort_session")(ev);
});
$("#btn-reset").addEventListener("click", (ev) => {
  if (confirm("إنهاء الجلسة في اللوحة فقط (لا يوقف الوكيل)؟")) act(ev.currentTarget, () => api("/api/admin/session/reset", {}));
});

if (token) start(); else show($("#gate"), true);
