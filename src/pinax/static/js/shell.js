// SPDX-License-Identifier: Apache-2.0
// Shared page chrome for Pinax views: the sidebar, the scope header (breadcrumbs, scan date, and
// links to the same folder in every report), and folder cards.
import { saveButton } from "/js/views.js";
import { $, $$, api, bytes, date, displayPath, fsInfo, hashAt, html, icon, mount, num, pct, placeHash, raw, setActiveNav, state, when } from "/js/core.js";
import { ageBar, thresholdText } from "/js/viz.js";

state.place = null; // the folder the person is looking at, carried between report pages

// ---------- Sidebar ----------
export function renderSidebar() {
  const p = state.place;
  const link = (route, ic, label, scoped = true) => {
    const href = scoped ? placeHash(route, p) : `#/${route}`;
    return html`<a class="side-link" href="${href}" data-route="#/${route}">${icon(ic)}${label}</a>`;
  };
  const admin = state.me?.is_admin;
  mount($("#sidebar"), html`
    <h4>OVERVIEW</h4>
    <a class="side-link" href="#/" data-route="#/">${icon("home")}${admin ? "All data" : "My data"}</a>
    ${link("folders", "folder", admin ? "Folders and labs" : "My folders", false)}
    ${link("charges", "receipt", "Storage charges", false)}
    ${link("plans", "archive", "Cleanup plans", false)}
    ${link("collections", "layers", "Collections", false)}
    <h4 class="scope-label">EXPLORE${p && p.fs ? html` <span class="tiny" title="${p.name || ""}">· ${p.name || ""}</span>` : ""}</h4>
    ${link("explore", "treemap", "Age map")}
    ${link("changes", "activity", "Change map")}
    ${link("changed", "list", "What changed")}
    ${link("ages", "clock", "Age report")}
    ${link("history", "trend", "History")}
    ${link("types", "pie", "File types")}
    ${link("cold", "snowflake", "Cold data")}
    ${link("owners", "users", "Owners")}
    ${link("files", "search", "Find files")}
    ${state.me.is_admin ? link("searches", "inbox", "Searches", false) : ""}
    ${admin ? html`<h4>ADMINISTRATION</h4>
      ${link("health", "heart", "Storage health", false)}
      ${link("scans", "database", "Scans", false)}
      ${link("capacity", "server", "Capacity", false)}
      ${link("tags", "tag", "Tags", false)}
      ${link("costs", "banknote", "Costs", false)}
      ${link("chargeback", "table", "Chargeback", false)}
      ${link("access", "lock", "People and access", false)}` : ""}`);
  setActiveNav((location.hash || "#/").split("?")[0], ".side-link");
}

// ---------- Scope header ----------
export const REPORTS = [
  ["explore", "treemap", "Age map"], ["changes", "activity", "Change map"], ["changed", "list", "What changed"], ["ages", "clock", "Age report"], ["history", "trend", "History"], ["types", "pie", "File types"],
  ["cold", "snowflake", "Cold data"], ["owners", "users", "Owners"], ["files", "search", "Find files"],
];

/** Breadcrumbs for a place: home, then (for administrators) the filesystem, then each folder. */
export function crumbsFor(place, labName) {
  const admin = state.me.is_admin;
  const home = { label: labName || (admin ? "All data" : "My data"), place: labName ? { lab: place?.lab } : null };
  if (!place || !place.fs) return [home];
  const f = fsInfo(place.fs);
  const out = [{ label: admin ? "All data" : "My data", place: null }];
  if (!f) return out;
  if (admin) out.push({ label: f.name, place: { fs: f.slug, path: f.root } });
  if (!place.path || place.path === f.root) return out;
  const rel = place.path.slice(f.root.length + 1).split("/");
  rel.forEach((name, i) => out.push({ label: name, place: { fs: f.slug, path: `${f.root}/${rel.slice(0, i + 1).join("/")}` } }));
  return out;
}

export function placeName(place, labName) {
  if (!place || !place.fs) return labName || (state.me.is_admin ? "All data" : "My data");
  const f = fsInfo(place.fs);
  if (!place.path || (f && place.path === f.root)) return f ? f.name : place.fs;
  return place.path.split("/").pop();
}

// ---------- Choosing a scan ----------
// The days each place was scanned, fetched once per place per visit.
const scanDays = new Map();
function daysFor(place, lab) {
  const query = place && place.fs ? { fs: place.fs } : lab ? { lab: lab.slug } : {};
  const key = JSON.stringify(query);
  if (!scanDays.has(key)) scanDays.set(key, api("scan-days", { query, quiet: true }).then((r) => r.days).catch(() => []));
  return scanDays.get(key);
}

/** Fill a header's scan picker: every day the place was scanned; days kept only as folder totals open History. */
async function fillPicker(select, place, lab, asOf) {
  const days = await daysFor(place, lab);
  if (!document.body.contains(select) || !days.length) return;
  const shown = (asOf || "").slice(0, 10);
  if (days[0].day === shown) { // a day was chosen, but it is the latest scan's
    select.closest(".asof")?.classList.remove("older");
    select.closest(".asof")?.querySelector(".asof-latest")?.remove();
  }
  select.innerHTML = days.map((d) => String(html`<option value="${d.latest ? "" : d.ready ? d.day : `totals:${d.day}`}"
    ${d.day === shown ? raw("selected") : ""}>${date(`${d.day}T12:00:00Z`)}${d.latest ? " (latest)" : d.ready ? "" : " (folder totals only)"}</option>`)).join("");
}

// One listener for every header's picker: the same page at the chosen scan, or History for a day only its folder
// totals remain from.
document.addEventListener("change", (e) => {
  const select = e.target.closest?.(".asof-pick");
  if (!select) return;
  const v = select.value;
  location.hash = v.startsWith("totals:") ? `${hashAt(v.slice(7), "history")}&pruned=1` : hashAt(v || null);
});

/** Remember the place for the sidebar, and render the header shared by every explore page. */
export function scopeHeader(route, place, { asOf, title, lede = "", actions = "", lab } = {}) {
  state.place = place && place.fs ? { ...place, name: placeName(place) } : null;
  renderSidebar();
  // After the view mounts the header: fill its scan picker.
  setTimeout(() => $$(".asof-pick").forEach((s) => fillPicker(s, place, lab, asOf)), 0);
  const all = crumbsFor(place, lab?.name);
  // Deep folders: keep the first two crumbs and the last four; the ellipsis leads to the deepest hidden one.
  const hidden = all.length > 7 ? all.slice(2, -4) : [];
  const crumbs = hidden.length
    ? [...all.slice(0, 2), { ...hidden.at(-1), label: "…", title: hidden.map((c) => c.label).join("/") }, ...all.slice(-4)]
    : all;
  const last = crumbs.length - 1;
  const extra = lab ? { lab: lab.slug } : {};
  const path = place && place.fs ? displayPath(place.fs, place.path || fsInfo(place.fs)?.root) : "";
  return html`
    <div class="page-head"><div><div class="eyebrow">${REPORTS.find((r) => r[0] === route)?.[2] || ""}</div>
      <h1>${title || placeName(place, lab?.name)}</h1>
      ${path ? html`<p class="path clamp" title="${path}">${path}</p>` : lede ? html`<p>${lede}</p>` : ""}</div>
      <div class="actions">${actions}${saveButton()}</div></div>
    <div class="scope">
      <nav class="crumbs" aria-label="Folder path">${crumbs.map((c, i) => html`${i ? html`<span class="sep">${icon("chevronRight")}</span>` : ""}${
        i === last ? html`<span class="here" aria-current="page" title="${c.label}">${c.label}</span>`
        : html`<a href="${placeHash(route, c.place, c.place ? {} : extra)}" title="${c.title || c.label}">${c.label}</a>`}`)}</nav>
      ${asOf ? html`<span class="asof ${state.at ? "older" : ""}">${icon("clock")}<label>As of the scan of <select class="select xs asof-pick" aria-label="Scan to show">
          <option selected>${date(asOf)}</option></select></label>${state.at ? html`<a class="asof-latest" href="${hashAt(null)}">Back to the latest</a>` : ""}</span>` : ""}
    </div>
    <nav class="subnav" aria-label="Reports for this folder">${REPORTS.map(([r, ic, label]) =>
      html`<a href="${placeHash(r, place, extra)}" class="${r === route ? "active" : ""}">${icon(ic)}${label}</a>`)}</nav>`;
}

// ---------- Folder cards ----------
export function folderCard(f, cb) {
  const s = f.latest;
  const coldShare = s.bytes ? (s.cold_bytes / s.bytes) * 100 : 0;
  return html`<a class="folder-card" href="${placeHash("explore", { fs: f.fs, path: f.path })}">
    <span class="t">${icon("folder")}${f.folder || "(files in the root)"}</span>
    <span class="m"><span>${f.lab ? f.lab.name : "Unmapped"}</span><span class="path">${displayPath(f.fs, f.path)}</span></span>
    <span class="big">${bytes(s.bytes)}</span>
    ${ageBar(s.age_bytes)}
    <span class="m"><span>${num(s.files)} files</span><span>${pct(coldShare)} unchanged for ${thresholdText(cb)}+</span>
      ${f.change && f.change.bytes ? html`<span class="delta ${f.change.bytes > 0 ? "up" : "down"}">${f.change.bytes > 0 ? "+" : "−"}${bytes(Math.abs(f.change.bytes))} since the previous scan</span>` : f.change ? html`<span>No change since the previous scan</span>` : ""}</span>
    <span class="m">Last change ${when(s.newest)}</span>
  </a>`;
}

