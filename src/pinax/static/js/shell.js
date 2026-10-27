// SPDX-License-Identifier: Apache-2.0
// Shared page chrome for Pinax views: the sidebar, the scope header (breadcrumbs, scan date, and
// links to the same folder in every report), and folder cards.
import { $, bytes, date, displayPath, fsInfo, html, icon, mount, num, pct, placeHash, setActiveNav, state, when } from "/js/core.js";
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
    <h4>EXPLORE${p && p.fs ? html` <span class="tiny" style="font-weight:500;letter-spacing:0">· ${p.name || ""}</span>` : ""}</h4>
    ${link("explore", "treemap", "Age map")}
    ${link("ages", "clock", "Age report")}
    ${link("types", "pie", "File types")}
    ${link("cold", "snowflake", "Cold data")}
    ${link("owners", "users", "Owners")}
    ${link("files", "search", "Find files")}
    ${admin ? html`<h4>ADMINISTRATION</h4>
      ${link("health", "heart", "Storage health", false)}
      ${link("scans", "database", "Scans", false)}
      ${link("access", "lock", "People and access", false)}` : ""}`);
  setActiveNav((location.hash || "#/").split("?")[0], ".side-link");
}

// ---------- Scope header ----------
export const REPORTS = [
  ["explore", "treemap", "Age map"], ["ages", "clock", "Age report"], ["types", "pie", "File types"],
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

/** Remember the place for the sidebar, and render the header shared by every explore page. */
export function scopeHeader(route, place, { asOf, title, lede = "", actions = "", lab } = {}) {
  state.place = place && place.fs ? { ...place, name: placeName(place) } : null;
  renderSidebar();
  const crumbs = crumbsFor(place, lab?.name);
  const last = crumbs.length - 1;
  const extra = lab ? { lab: lab.slug } : {};
  return html`
    <div class="page-head"><div><div class="eyebrow">${REPORTS.find((r) => r[0] === route)?.[2] || ""}</div>
      <h1>${title || placeName(place, lab?.name)}</h1>
      ${place && place.fs ? html`<p class="path">${displayPath(place.fs, place.path || fsInfo(place.fs)?.root)}</p>` : lede ? html`<p>${lede}</p>` : ""}</div>
      <div class="actions">${actions}</div></div>
    <div class="scope">
      <nav class="crumbs" aria-label="Folder path">${crumbs.map((c, i) => html`${i ? html`<span class="sep">${icon("chevronRight")}</span>` : ""}${
        i === last ? html`<span class="here" aria-current="page">${c.label}</span>`
        : html`<a href="${placeHash(route, c.place, c.place ? {} : extra)}">${c.label}</a>`}`)}</nav>
      ${asOf ? html`<span class="asof">${icon("clock")}As of the scan on ${date(asOf, true)}</span>` : ""}
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

