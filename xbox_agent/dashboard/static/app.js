"use strict";
// Everything coming from the agent (OCR text, messages, gamertags) is untrusted screen content:
// it is only ever put into the page with textContent / text nodes, never as HTML.

const $ = (sel) => document.querySelector(sel);
const KEY = "xbox_agent_admin_token";
let token = sessionStorage.getItem(KEY) || "";
let timer = null;

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

  show($("#card-login"), active && Boolean(s.login_code));
  if (active && s.login_code) { $("#login-code").textContent = s.login_code; showShot($("#login-shot"), s.login_shot); }

  const err = active ? s.error : null;
  show($("#card-error"), Boolean(err));
  if (err) {
    $("#err-title").textContent = `توقف الوكيل: ${err.category} (${err.state})`;
    $("#err-msg").textContent = err.message;
    $("#err-ocr").textContent = err.ocr_text || "(لا نص)";
    show($("#btn-skip"), err.can_skip);
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

  $("#events").replaceChildren(...st.events.slice().reverse().map((e) => el("li", {},
    el("span", { class: "t" }, new Date(e.ts * 1000).toLocaleTimeString()),
    el("span", { class: "s" }, `${e.state || e.kind} / ${e.action}`),
    el("span", { class: "m" }, e.message),
    e.shot ? el("button", { onclick: () => showShot($("#event-shot"), e.shot) }, "صورة") : null)));
}

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
