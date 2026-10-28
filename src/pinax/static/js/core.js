// SPDX-License-Identifier: Apache-2.0
// Pinax shared UI core (adapted from Tundra's): safe templating, API client, formatting, icons, chrome, router.

// ---------- Safe HTML templating ----------
// html`...` escapes every interpolated value unless it is itself html`` output or raw().
class Safe {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v).replace(/[&<>"']/g, (c) => ESC[c]);

// File names can hold control characters (a newline shows as a space) and bidirectional overrides (which make
// "report‮txt.exe" display as "reportexe.txt"). Displayed text shows them as escapes, as fs-inventory's
// CSV does. Attribute values keep the exact text, because some are read back (data-map).
const INVISIBLE = /[\u0000-\u001f\u007f-\u009f؜‎‏‪-‮⁦-⁩]/g;
const NAMED = { "\n": "\\n", "\t": "\\t", "\r": "\\r" };
const hex = (c, n) => c.charCodeAt(0).toString(16).toUpperCase().padStart(n, "0");
export const visible = (v) => String(v).replace(INVISIBLE, (c) =>
  NAMED[c] ?? (c.charCodeAt(0) < 0x100 ? `\\x${hex(c, 2)}` : `\\u${hex(c, 4)}`));

const render = (v, inAttr) => {
  if (v == null || v === false) return "";
  if (v instanceof Safe) return v.s;
  if (Array.isArray(v)) return v.map((x) => render(x, inAttr)).join("");
  return esc(inAttr ? v : visible(v));
};
// Inside a double-quoted attribute when the template text so far has an odd number of quotes (values are
// escaped, so only the template text has quotes).
const quotes = (s) => s.split('"').length - 1;
export const html = (strings, ...vals) => {
  let out = "", n = 0;
  strings.forEach((s, i) => {
    out += s;
    n += quotes(s);
    if (i < vals.length) out += render(vals[i], n % 2 === 1);
  });
  return new Safe(out);
};
export const raw = (s) => new Safe(String(s));
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const mount = (el, content) => { el.innerHTML = String(html`${content}`); return el; };

// ---------- API ----------
export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
// A page's reads are cancelled when another page opens (startRouter), and Pinax stops their queries; changes are
// never cancelled.
let pageReads = null;
export async function api(path, { method = "GET", body, query, quiet = false } = {}) {
  let url = path.startsWith("/") ? path : `/api/${path}`;
  if (query) {
    const qs = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== ""));
    if ([...qs].length) url += `?${qs}`;
  }
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
    signal: method === "GET" ? pageReads?.signal : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch { /* not JSON */ }
    if (res.status === 401 && !quiet) {
      if (state.config && state.config.auth_mode === "local") showSignIn(); else showPersonaPicker(true);
    }
    if (res.status === 403 && detail === "Change your password before going on" && !quiet) showPasswordChange({ forced: true });
    throw new ApiError(res.status, detail);
  }
  const type = res.headers.get("content-type") || "";
  return type.includes("application/json") ? res.json() : res.text();
}

// ---------- Formatting ----------
const UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
export function bytes(n, digits = 1) {
  if (n == null) return "—";
  let i = 0;
  let v = Number(n);
  while (v >= 1024 && i < UNITS.length - 1) { v /= 1024; i++; }
  return `${i === 0 ? v : v.toFixed(v >= 100 ? 0 : digits)} ${UNITS[i]}`;
}
export const num = (n) => (n == null ? "—" : Number(n).toLocaleString());
export const pct = (n) => `${Math.round(Number(n) || 0)}%`;
const parseTs = (iso) => (iso ? new Date(iso) : null);
export function date(iso, withTime = false) {
  const d = parseTs(iso);
  if (!d) return "—";
  const opts = { year: "numeric", month: "short", day: "numeric" };
  if (withTime) Object.assign(opts, { hour: "numeric", minute: "2-digit" });
  return d.toLocaleString(undefined, opts);
}
export function ago(iso) {
  const d = parseTs(iso);
  if (!d) return "—";
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 45) return "just now";
  const steps = [[60, "minute"], [3600, "hour"], [86400, "day"], [2592000, "month"], [31536000, "year"]];
  for (let i = steps.length - 1; i >= 0; i--) {
    const [sec, unit] = steps[i];
    if (s >= sec) {
      const n = Math.floor(s / sec);
      return `${n} ${unit}${n === 1 ? "" : "s"} ago`;
    }
  }
  return `${Math.floor(s)} seconds ago`;
}
export const when = (iso) => html`<span title="${date(iso, true)}">${ago(iso)}</span>`;
export function duration(sec) {
  if (sec == null || !isFinite(sec)) return "—";
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
}
export const rate = (bps) => (bps ? `${bytes(bps)}/s` : "—");
export const shortHash = (h, n = 10) => (h ? `${h.slice(0, n)}…` : "—");
export const ageYears = (iso) => {
  const d = parseTs(iso);
  return d ? (Date.now() - d.getTime()) / (365.25 * 86400000) : null;
};

// ---------- Icons (24px stroke icons) ----------
const P = {
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  folderOpen: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v1"/><path d="M3 19l2.6-7.2A2 2 0 0 1 7.5 10.5H21l-2.7 7.3a2 2 0 0 1-1.9 1.2H5a2 2 0 0 1-2-2z"/>',
  chevronRight: '<path d="M9 6l6 6-6 6"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  alert: '<path d="M12 3l10 18H2z"/><path d="M12 10v5"/><path d="M12 18h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><path d="M12 7.5h.01"/>',
  archive: '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/><path d="M10 13h4"/>',
  upload: '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  download: '<path d="M12 4v12"/><path d="M7 11l5 5 5-5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  play: '<path d="M7 5l12 7-12 7z"/>',
  pause: '<path d="M8 5v14"/><path d="M16 5v14"/>',
  x: '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
  refresh: '<path d="M20 11a8 8 0 0 0-14.6-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.6 4.5L20 16"/><path d="M20 20v-4h-4"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/>',
  inbox: '<path d="M3 13l3-8h12l3 8"/><path d="M3 13v6a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-6h-5l-2 3h-4l-2-3z"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.6-3.5 3.3-5.5 6.5-5.5s5.9 2 6.5 5.5"/><path d="M16 4.6a3.5 3.5 0 0 1 0 6.8"/><path d="M18 14.8c2 .8 3.2 2.6 3.5 5.2"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.01"/><path d="M7 16.5h.01"/>',
  file: '<path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z"/><path d="M14 3v5h5"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  arrowLeft: '<path d="M19 12H5"/><path d="M11 6l-6 6 6 6"/>',
  arrowRight: '<path d="M5 12h14"/><path d="M13 6l6 6-6 6"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="1.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21c.8-4 4-6 8-6s7.2 2 8 6"/>',
  scan: '<path d="M4 8V5a1 1 0 0 1 1-1h3"/><path d="M16 4h3a1 1 0 0 1 1 1v3"/><path d="M20 16v3a1 1 0 0 1-1 1h-3"/><path d="M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M4 12h16"/>',
  package: '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5"/><path d="M12 12v9"/><path d="M12 12L4 7.5"/>',
  list: '<path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/><path d="M4 6h.01"/><path d="M4 12h.01"/><path d="M4 18h.01"/>',
  trash: '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
  flag: '<path d="M5 21V4"/><path d="M5 4h11l-2 4 2 4H5"/>',
  send: '<path d="M21 3L10 14"/><path d="M21 3l-7 18-4-7-7-4z"/>',
  moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  treemap: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M11 3v18"/><path d="M11 12h10"/><path d="M16 12v9"/>',
  pie: '<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15z"/>',
  tag: '<path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9z"/><path d="M8 8h.01"/>',
  snowflake: '<path d="M12 2v20"/><path d="M4.9 7l14.2 10"/><path d="M4.9 17L19.1 7"/><path d="M9 4l3 2 3-2"/><path d="M9 20l3-2 3 2"/>',
  heart: '<path d="M20.8 5.6a5 5 0 0 0-7.1 0L12 7.3l-1.7-1.7a5 5 0 1 0-7.1 7.1L12 21.5l8.8-8.8a5 5 0 0 0 0-7.1z"/>',
  home: '<path d="M3 11l9-8 9 8"/><path d="M5 9.5V20a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V9.5"/>',
  arrowUp: '<path d="M12 19V5"/><path d="M6 11l6-6 6 6"/>',
  chevronDown: '<path d="M6 9l6 6 6-6"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="1.5"/><path d="M3 10h18"/><path d="M9 10v10"/>',
  trend: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="M4.9 4.9l1.4 1.4"/><path d="M17.7 17.7l1.4 1.4"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="M4.9 19.1l1.4-1.4"/><path d="M17.7 6.3l1.4-1.4"/>',
};
export const icon = (name, cls = "") =>
  raw(`<svg class="i ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || ""}</svg>`);

// Internal role "pi" is the PI of a lab or the owner of a department.
export const roleLabel = (role, kind = "lab") => (role === "pi" ? (kind === "department" ? "Owner" : "PI") : "Delegate");
export const kindLabel = (kind) => (kind === "department" ? "Department" : "Lab");

// ---------- Toasts and modals ----------
export function toast(message, kind = "ok") {
  let box = $(".toasts");
  if (!box) { box = document.createElement("div"); box.className = "toasts"; box.setAttribute("role", "status"); document.body.append(box); }
  const el = document.createElement("div");
  el.className = `toast ${kind === "error" ? "error" : ""}`;
  el.innerHTML = String(html`${icon(kind === "error" ? "alert" : "check")}<span>${message}</span>`);
  box.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 7000 : 4000);
}

export function modal({ title, body, confirm = "Confirm", cancel = "Cancel", tone = "primary", onConfirm }) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "modal-backdrop";
    wrap.innerHTML = String(html`<div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div class="modal-head"><h2 id="modal-title">${title}</h2></div>
      <div class="modal-body">${body}</div>
      <div class="modal-foot">
        <button class="btn btn-ghost" data-act="cancel">${cancel}</button>
        <button class="btn btn-${tone}" data-act="ok">${confirm}</button>
      </div></div>`);
    const close = (val) => { wrap.remove(); document.removeEventListener("keydown", onKey); resolve(val); };
    const onKey = (e) => { if (e.key === "Escape") close(null); };
    wrap.addEventListener("click", async (e) => {
      if (e.target === wrap || e.target.closest("[data-act=cancel]")) return close(null);
      if (e.target.closest("[data-act=ok]")) {
        try {
          const result = onConfirm ? await onConfirm(wrap) : true;
          if (result !== false) close(result ?? true);
        } catch (err) { toast(err.message, "error"); }
      }
    });
    document.addEventListener("keydown", onKey);
    document.body.append(wrap);
    ($("textarea, input", wrap) || $("[data-act=ok]", wrap)).focus();
  });
}

// ---------- Theme (light by default; the choice is remembered per browser) ----------
const THEME_KEY = "pinax-theme";
export const currentTheme = () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");
export function setTheme(theme) {
  if (theme === "dark") document.documentElement.dataset.theme = "dark";
  else delete document.documentElement.dataset.theme;
  try { localStorage.setItem(THEME_KEY, theme); } catch { /* storage unavailable: applies to this page only */ }
}
function themeToggle() {
  const dark = currentTheme() === "dark";
  return html`<button class="theme-toggle" id="theme-toggle" aria-pressed="${dark}"
    aria-label="${dark ? "Switch to light theme" : "Switch to dark theme"}" title="${dark ? "Light theme" : "Dark theme"}">${icon(dark ? "sun" : "moon")}</button>`;
}

// ---------- Identity and chrome ----------
// `at`: the day of the scan being looked at (YYYY-MM-DD), or null for the latest. Read from the address on every
// route, and kept by every place link and report request (placeQuery), so a chosen scan stays chosen.
export const state = { me: null, config: null, users: null, at: null };

export async function loadIdentity() {
  state.config = await api("config");
  try {
    state.me = await api("me", { quiet: true });
  } catch (e) {
    if (e.status !== 401) throw e;
    state.me = null;
  }
  return state.me;
}

async function loadUsers() {
  if (!state.users) state.users = await api("demo/users");
  return state.users;
}

function personaRow(u) {
  const labs = u.is_admin ? u.title
    : u.labs.map((l) => `${l.name} (${roleLabel(l.role, l.kind)})`).join(", ") || `${u.title || "No title"} · no lab access`;
  const current = state.me && state.me.username === u.username;
  return html`<button class="persona ${current ? "current" : ""}" data-user="${u.username}" data-admin="${u.is_admin ? 1 : 0}">
    <span class="avatar ${u.is_admin ? "admin outlined" : ""}">${u.initials}</span>
    <span><span class="who">${u.display_name}</span><br><span class="what">${labs}</span></span></button>`;
}

// ---------- Local sign-in (PINAX_AUTH_MODE=local) ----------
const isLocal = () => state.config && state.config.auth_mode === "local";

/** The sign-in form, over whatever page is showing. After signing in: the password change, if one is required,
 * else Pinax itself. */
export function showSignIn() {
  if ($(".modal-backdrop.sign-in")) return;
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop sign-in";
  wrap.innerHTML = String(html`<form class="modal" role="dialog" aria-modal="true" aria-labelledby="si-title" novalidate>
    <div class="modal-head"><h2 id="si-title">Sign in to Pinax</h2></div>
    <div class="modal-body">
      <div class="field"><label for="si-user">Username</label><input class="input" id="si-user" autocomplete="username" autocapitalize="none" spellcheck="false" required></div>
      <div class="field"><label for="si-pass">Password</label><input class="input" id="si-pass" type="password" autocomplete="current-password" required></div>
      <p class="form-error" role="alert" hidden></p>
      <p class="small muted" style="margin:0">Forgotten your password? Ask a Pinax administrator for a temporary one.</p></div>
    <div class="modal-foot"><button class="btn btn-primary" type="submit">${icon("lock")}Sign in</button></div></form>`);
  document.body.append(wrap);
  const form = $("form", wrap);
  $("#si-user", wrap).focus();
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $(".form-error", wrap);
    const button = $("button[type=submit]", wrap);
    button.disabled = true;
    try {
      const r = await api("auth/login", { method: "POST", quiet: true,
        body: { username: $("#si-user", wrap).value, password: $("#si-pass", wrap).value } });
      wrap.remove();
      if (r.must_change_password) showPasswordChange({ forced: true });
      else if (location.pathname.startsWith("/app")) location.reload(); else location.href = "/app/";
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
      $("#si-pass", wrap).value = "";
      $("#si-pass", wrap).focus();
    } finally { button.disabled = false; }
  });
}

/** Change one's own password. Forced after signing in with a temporary password: nothing else works until then. */
export function showPasswordChange({ forced = false } = {}) {
  if ($(".modal-backdrop.password-change")) return;
  const min = state.config?.password_min_length || 12;
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop password-change";
  wrap.innerHTML = String(html`<form class="modal" role="dialog" aria-modal="true" aria-labelledby="pc-title" novalidate>
    <div class="modal-head"><h2 id="pc-title">${forced ? "Choose your password" : "Change your password"}</h2>
      ${forced ? html`<p class="small muted" style="margin-top:6px">You signed in with a temporary password. Choose your own to go on.</p>` : ""}</div>
    <div class="modal-body">
      <div class="field"><label for="pc-current">${forced ? "Temporary password" : "Current password"}</label><input class="input" id="pc-current" type="password" autocomplete="current-password"></div>
      <div class="field"><label for="pc-new">New password</label><input class="input" id="pc-new" type="password" autocomplete="new-password" aria-describedby="pc-hint">
        <span class="hint" id="pc-hint">At least ${min} characters, not containing your username. A few words make a good one.</span></div>
      <div class="field"><label for="pc-again">New password again</label><input class="input" id="pc-again" type="password" autocomplete="new-password"></div>
      <p class="form-error" role="alert" hidden></p></div>
    <div class="modal-foot">${forced ? html`<button class="btn btn-ghost" type="button" data-act="out">Sign out</button>`
      : html`<button class="btn btn-ghost" type="button" data-act="cancel">Cancel</button>`}
      <button class="btn btn-primary" type="submit">${icon("check")}Save password</button></div></form>`);
  document.body.append(wrap);
  $("#pc-current", wrap).focus();
  const err = $(".form-error", wrap);
  const fail = (message) => { err.textContent = message; err.hidden = false; };
  $("[data-act=cancel]", wrap)?.addEventListener("click", () => wrap.remove());
  $("[data-act=out]", wrap)?.addEventListener("click", signOut);
  $("form", wrap).addEventListener("submit", async (e) => {
    e.preventDefault();
    if ($("#pc-new", wrap).value !== $("#pc-again", wrap).value) return fail("The new passwords are not the same");
    try {
      await api("auth/password", { method: "POST", quiet: true,
        body: { current_password: $("#pc-current", wrap).value, new_password: $("#pc-new", wrap).value } });
      wrap.remove();
      toast("Password changed");
      if (forced) location.reload();
    } catch (ex) { fail(ex.message); }
  });
}

export async function signOut() {
  await api("auth/logout", { method: "POST", quiet: true }).catch(() => {});
  location.href = "/";
}

async function switchTo(username) {
  await api("demo/session", { method: "POST", body: { username } });
  if (location.pathname.startsWith("/app")) { location.hash = "#/"; location.reload(); } else location.href = "/app/";
}

export async function showPersonaPicker(force = false) {
  if ($(".modal-backdrop.persona-picker")) return;
  if (state.config && state.config.auth_mode !== "demo") return showNotSetUp();
  const users = await loadUsers();
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop persona-picker";
  const ops = users.filter((u) => u.is_admin);
  const owners = users.filter((u) => !u.is_admin);
  wrap.innerHTML = String(html`<div class="modal" role="dialog" aria-modal="true" aria-labelledby="pp-title">
    <div class="modal-head"><h2 id="pp-title">Choose a demo identity</h2>
    <p class="muted small" style="margin-top:6px">In production, people sign in with single sign-on and Pinax maps them to their labs.</p></div>
    <div class="modal-body"><h4 class="tiny muted">STORAGE ADMINISTRATORS</h4>${ops.map(personaRow)}
    <h4 class="tiny muted" style="margin-top:14px">PIS AND LAB MEMBERS</h4>${owners.map(personaRow)}</div>
    ${force ? "" : html`<div class="modal-foot"><button class="btn btn-ghost" data-act="cancel">Close</button></div>`}</div>`);
  wrap.addEventListener("click", (e) => {
    const p = e.target.closest(".persona");
    if (p) return switchTo(p.dataset.user);
    if (!force && (e.target === wrap || e.target.closest("[data-act=cancel]"))) wrap.remove();
  });
  document.body.append(wrap);
}

// Single sign-on mode: the person signed in, but Pinax has no active account for them.
function showNotSetUp() {
  if ($(".modal-backdrop.not-set-up")) return;
  const wrap = document.createElement("div");
  wrap.className = "modal-backdrop not-set-up";
  wrap.innerHTML = String(html`<div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="nsu-title">
    <div class="modal-head"><h2 id="nsu-title">Your account is not set up in Pinax</h2></div>
    <div class="modal-body"><p>You are signed in, but Pinax has no active account for you, or it has been deactivated.</p>
      <p class="small muted">Ask Research Data Services to add you, with your lab or department, under People and access.</p></div></div>`);
  document.body.append(wrap);
}

export function renderTopbar({ mode, nav = [] } = {}) {
  const me = state.me;
  const bar = $("#topbar");
  const isAdmin = me && me.is_admin;
  const sub = me ? (me.is_admin ? "Storage administrator" : me.labs.map((l) => l.name).join(", ")) : "";
  mount(bar, html`
    <a class="brand" href="${me ? "/app/" : "/"}" aria-label="Pinax home">
      <img class="brand-mark" src="/img/pinax-mark.svg" alt="">
      <span class="signpost"><span class="signpost-name">Pinax</span><span class="signpost-sub">Explore your data</span></span>
    </a>
    <nav class="topnav" aria-label="Primary">${nav.map((n) => html`<a href="${n.href}" data-route="${n.match}">${n.label}</a>`)}</nav>
    <div class="topbar-spacer"></div>
    ${mode === "home" && me ? html`<a class="mode-link" href="/app/">Open Pinax</a>` : ""}
    ${themeToggle()}
    <div class="user-menu">
      ${me
        ? html`<button class="user-button" id="user-button" aria-haspopup="true" aria-expanded="false">
            <span class="avatar ${isAdmin ? "admin" : ""}">${me.initials}</span>
            <span class="user-text"><span>${me.display_name}</span><small>${sub}</small></span></button>`
        : isLocal() ? html`<button class="btn btn-sm btn-light" id="sign-in">${icon("lock")}Sign in</button>`
        : state.config && state.config.auth_mode !== "demo" ? ""
        : html`<button class="btn btn-sm btn-light" id="user-button">Choose identity</button>`}
    </div>`);
  if (nav.length) setActiveNav(location.hash || "#/", ".topnav a");
  $("#theme-toggle").addEventListener("click", () => {
    setTheme(currentTheme() === "dark" ? "light" : "dark");
    renderTopbar({ mode, nav });
    $("#theme-toggle").focus();  // keep keyboard focus on the toggle after re-rendering
    window.dispatchEvent(new Event("pinax:theme"));
  });
  $("#sign-in")?.addEventListener("click", showSignIn);
  $("#user-button")?.addEventListener("click", async (e) => {
    e.stopPropagation();
    const existing = $(".dropdown");
    if (existing) { existing.remove(); return; }
    if (!me) return showPersonaPicker();
    const dd = document.createElement("div");
    dd.className = "dropdown";
    const demo = state.config && state.config.auth_mode === "demo";
    const users = demo ? await loadUsers() : [];
    dd.innerHTML = String(html`<h4>SIGNED IN AS</h4>
      <div style="padding:4px 10px 10px"><b>${me.display_name}</b><br><span class="small muted">${me.title} · ${me.email}</span></div>
      ${isLocal() ? html`<button class="menu-item" data-act="password">${icon("lock")}Change password</button>
        <button class="menu-item" data-act="sign-out">${icon("arrowRight")}Sign out</button>` : ""}
      ${demo ? html`<h4>SWITCH DEMO IDENTITY</h4>
      ${users.filter((u) => u.is_admin).map(personaRow)}
      ${users.filter((u) => !u.is_admin).map(personaRow)}
      <div class="demo-note">Demo mode: anyone can be anyone. For real use, set PINAX_AUTH_MODE=local (passwords) or header (single sign-on).</div>` : ""}`);
    dd.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const p = ev.target.closest(".persona");
      if (p) switchTo(p.dataset.user);
      const act = ev.target.closest("[data-act]")?.dataset.act;
      if (act === "password") { dd.remove(); showPasswordChange(); }
      if (act === "sign-out") signOut();
    });
    $(".user-menu").append(dd);
    $("#user-button").setAttribute("aria-expanded", "true");
  });
  if (!menuCloserBound) {
    const close = () => $(".dropdown")?.remove();
    document.addEventListener("click", close);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    window.addEventListener("hashchange", close);
    menuCloserBound = true;
  }
}
let menuCloserBound = false;

// ---------- Router with auto-cleared polling ----------
let timers = [];
export function poll(fn, ms) {
  let stopped = false;
  const id = setInterval(async () => {
    if (stopped || document.hidden) return;
    try { await fn(); } catch (e) { console.warn(e); }
  }, ms);
  const handle = { stop: () => { stopped = true; clearInterval(id); }, get stopped() { return stopped; } };
  timers.push(handle);
  return handle;
}

export function startRouter(routes, outlet, { onRoute } = {}) {
  const go = async () => {
    timers.forEach((t) => t.stop());
    timers = [];
    pageReads?.abort();
    const reads = pageReads = new AbortController();
    const hash = location.hash || "#/";
    const at = new URLSearchParams(hash.split("?")[1] || "").get("at");
    state.at = at && /^\d{4}-\d{2}-\d{2}$/.test(at) ? at : null;
    for (const [pattern, view] of routes) {
      const m = hash.match(pattern);
      if (m) {
        onRoute && onRoute(hash);
        mount(outlet, html`<div class="loading-block"><div class="skeleton" style="width:40%;height:26px"></div><div class="skeleton"></div><div class="skeleton" style="width:80%"></div></div>`);
        try {
          await view(outlet, ...m.slice(1).map((g) => (g === undefined ? "" : decodeURIComponent(g))));
        } catch (e) {
          if (e.status === 401 || reads.signal.aborted) return;  // signed out, or another page opened
          console.error(e);
          mount(outlet, html`<div class="card empty">${icon("alert")}<h3>Something went wrong</h3><p>${e.message}</p><a class="btn btn-secondary" href="#/">Go back</a></div>`);
        }
        window.scrollTo({ top: 0 });
        return;
      }
    }
    mount(outlet, html`<div class="card empty">${icon("search")}<h3>Page not found</h3><a class="btn btn-secondary" href="#/">Home</a></div>`);
  };
  window.addEventListener("hashchange", go);
  go();
}

export function setActiveNav(hash, selector) {
  $$(selector).forEach((a) => {
    const m = a.dataset.route;
    const active = m === "#/" ? hash === "#/" || hash === "" : hash.startsWith(m);
    a.classList.toggle("active", active);
  });
}

export function emptyState(ic, title, text, action = "") {
  return html`<div class="empty">${icon(ic)}<h3>${title}</h3><p>${text}</p>${action}</div>`;
}

// ---------- Pinax helpers ----------
export function ageText(days) {
  if (days == null || !isFinite(days)) return "—";
  if (days < 1) return "today";
  if (days < 60) return `${Math.round(days)} days`;
  if (days < 730) return `${Math.round(days / 30.44)} months`;
  return `${(days / 365.25).toFixed(1)} years`;
}
export const agoText = (iso) => ago(iso);
export function daysBetween(fromIso, toIso) {
  if (!fromIso || !toIso) return null;
  return (new Date(toIso).getTime() - new Date(fromIso).getTime()) / 86400000;
}
export function fsInfo(slug) {
  return (state.config?.filesystems || []).find((f) => f.slug === slug) || null;
}
// Paths are shown with the filesystem's display root (the path people know), not the scan host's.
export function displayPath(fs, path) {
  const f = fsInfo(fs);
  if (!f || !path) return path || "";
  if (path === f.root) return f.display_root;
  if (path.startsWith(f.root + "/")) return f.display_root.replace(/\/$/, "") + path.slice(f.root.length);
  return path;
}
export function relPath(fs, path) {
  const f = fsInfo(fs);
  if (!f || !path || path === f.root) return "/";
  return path.startsWith(f.root + "/") ? path.slice(f.root.length) : path;
}
// Query parameters naming a place (filesystem and path), or nothing for the person's home, and the scan chosen.
export function placeQuery(place) {
  return { ...(place && place.fs ? { fs: place.fs, path: place.path || undefined } : {}), at: state.at || undefined };
}
/** The current address with `at` set to `day` (or removed for the latest), keeping every other choice on the page. */
export function hashAt(day, route) {
  const [path, query] = (location.hash || "#/").split("?");
  const q = new URLSearchParams(query || "");
  if (day) q.set("at", day); else q.delete("at");
  const qs = String(q);
  return `${route ? `#/${route}` : path}${qs ? `?${qs}` : ""}`;
}
export function placeHash(route, place, extra = {}) {
  const q = new URLSearchParams(Object.entries({ ...placeQuery(place), ...extra }).filter(([, v]) => v !== undefined && v !== null && v !== ""));
  const qs = String(q);
  return `#/${route}${qs ? `?${qs}` : ""}`;
}
export function parseQuery(query = "") {
  return Object.fromEntries(new URLSearchParams(query.replace(/^\?/, "")));
}
