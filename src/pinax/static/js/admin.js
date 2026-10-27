// SPDX-License-Identifier: Apache-2.0
// Administrator views: the organization overview, folders and labs, storage health, scans, and people and access.
import {
  $, $$, ageText, api, bytes, date, displayPath, duration, emptyState, html, icon, modal, mount, num, parseQuery, pct,
  placeHash, raw, state, toast, when,
} from "/js/core.js";
import { renderSidebar } from "/js/shell.js";
import { ageBar, ageHistogram, bars, categories, coldBytes, thresholdText } from "/js/viz.js";

const signed = (n) => (n ? `${n > 0 ? "+" : "−"}${bytes(Math.abs(n))}` : "—");
const opt = (value, label, current) => html`<option value="${value}" ${String(value) === String(current ?? "") ? raw("selected") : ""}>${label}</option>`;

// ---------- Overview (the administrator's home) ----------
export async function viewOverview(el) {
  const [ov, home, tree] = await Promise.all([api("admin/overview"), api("home"), api("tree")]);
  const s = tree.node.stats;
  const cb = tree.cold_bucket;
  const folders = home.folders;
  const unmapped = folders.filter((f) => !f.lab && f.folder);
  const change = folders.reduce((t, f) => t + (f.change ? f.change.bytes : 0), 0);
  const errors = ov.filesystems.reduce((t, f) => t + (f.snapshot?.errors || 0), 0);
  const names = ov.filesystems.reduce((t, f) => t + (f.name_issues || 0), 0);
  const largest = [...folders].sort((a, b) => b.latest.bytes - a.latest.bytes).slice(0, 12);
  const movers = folders.filter((f) => f.change && f.change.bytes).sort((a, b) => Math.abs(b.change.bytes) - Math.abs(a.change.bytes)).slice(0, 8);
  const cats = categories().map((c, i) => ({ label: c.label, key: c.key, bytes: s.cat_bytes[i] })).filter((c) => c.bytes).sort((a, b) => b.bytes - a.bytes);
  const cold = coldBytes(s, cb);
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Overview</div><h1>All data</h1>
      <p>Every filesystem Pinax indexes, as of each one's latest scan.</p></div>
      <div class="actions"><a class="btn btn-primary" href="#/explore">${icon("treemap")}Open the age map</a>
        <a class="btn btn-secondary" href="#/folders">${icon("folder")}All folders</a></div></div>
    <div class="kpis">
      <div class="kpi"><div class="label">Total size</div><div class="value">${bytes(s.bytes)}</div><div class="sub">${bytes(s.alloc)} on disk</div></div>
      <div class="kpi" style="--kpi-accent:var(--fh-teal)"><div class="label">Files</div><div class="value">${num(s.files)}</div><div class="sub">${num(s.dirs)} folders</div></div>
      <div class="kpi" style="--kpi-accent:var(--age-5)"><div class="label">Unchanged for ${thresholdText(cb)}+</div><div class="value">${bytes(cold)}</div><div class="sub">${pct(s.bytes ? (cold / s.bytes) * 100 : 0)} of all data</div></div>
      <div class="kpi" style="--kpi-accent:var(--fh-bright-blue)"><div class="label">Change since the previous scans</div><div class="value">${signed(change)}</div><div class="sub">${movers.length} folders changed</div></div>
      <a class="kpi" href="#/folders?unmapped=1" style="--kpi-accent:var(--fh-purple)"><div class="label">Unmapped folders</div><div class="value">${unmapped.length}</div><div class="sub">${bytes(unmapped.reduce((t, f) => t + f.latest.bytes, 0))} no lab owns</div></a>
    </div>
    <div class="grid cols-${Math.min(3, ov.filesystems.length) || 1}" style="margin-bottom:20px">${ov.filesystems.map((f) => fsCard(f))}</div>
    <div class="grid split">
      <div class="stack">
        <div class="card flush"><div class="card-head"><h2>Largest folders</h2><a class="small" href="#/folders">All folders</a></div>
          ${folderTable(largest, cb, { compact: true })}</div>
        <div class="card flush"><div class="card-head"><h2>Biggest changes since the previous scan</h2></div>
          ${movers.length ? html`<div class="table-wrap"><table class="table compact"><thead><tr><th>Folder</th><th class="num">Change</th><th class="num">Now</th><th>Previous scan</th></tr></thead><tbody>
            ${movers.map((f) => html`<tr><td><a class="rowlink" href="${placeHash("explore", { fs: f.fs, path: f.path })}">${f.folder}</a><div class="sub">${f.lab ? f.lab.name : "Unmapped"} · ${f.fs_name}</div></td>
              <td class="num nowrap"><b>${signed(f.change.bytes)}</b><div class="sub">${f.change.files > 0 ? "+" : ""}${num(f.change.files)} files</div></td>
              <td class="num nowrap">${bytes(f.latest.bytes)}</td><td class="nowrap">${when(f.change.since)}</td></tr>`)}</tbody></table></div>`
            : html`<p class="muted" style="padding:0 22px 18px">No changes yet: each filesystem needs two scans.</p>`}</div>
      </div>
      <div class="stack">
        <div class="card"><div class="card-head"><h2>Age of all data</h2><span class="small muted">By last change</span></div>${ageHistogram(s.age_bytes)}</div>
        <div class="card"><div class="card-head"><h2>What kind of data</h2><a class="small" href="#/types">File types</a></div>
          ${bars(cats.slice(0, 8), { href: (c) => `#/types?cat=${c.key}` })}</div>
        <div class="card"><div class="card-head"><h2>Needs attention</h2><a class="small" href="#/health">Storage health</a></div>
          <ul class="feed">
            <li><span>${icon("folder")}</span><span><b>${unmapped.length} unmapped top-level folders</b> hold ${bytes(unmapped.reduce((t, f) => t + f.latest.bytes, 0))} that no PI can see. <a href="#/folders?unmapped=1">Map them</a></span></li>
            <li><span>${icon("alert")}</span><span><b>${num(errors)} scan errors</b>, such as folders the scanner could not open, so their totals are lower bounds. <a href="#/health">Review</a></span></li>
            <li><span>${icon("tag")}</span><span><b>${num(names)} problem file names</b> that break scripts and migrations. <a href="#/health">Review</a></span></li>
          </ul></div>
        <div class="card"><div class="card-head"><h2>Recent activity</h2></div>
          <ul class="feed">${ov.activity.map((a) => html`<li><span class="when">${when(a.ts)}</span><span>${a.summary}</span></li>`)}</ul></div>
      </div>
    </div>`);
}

function fsCard(f) {
  const snap = f.snapshot;
  const s = f.stats;
  return html`<div class="card"><div class="card-head"><h2>${f.filesystem.name}</h2>
      ${snap ? html`<a class="small" href="${placeHash("explore", { fs: f.filesystem.slug, path: f.filesystem.root })}">Explore</a>` : ""}</div>
    <p class="path muted" style="margin:-8px 0 12px">${f.filesystem.display_root}</p>
    ${snap ? html`<div class="stat-row" style="margin-bottom:12px"><div class="stat"><div class="n">${bytes(s.bytes)}</div><div class="l">${num(s.files)} files</div></div>
        <div class="stat"><div class="n">${ageText(s.mean_age_days)}</div><div class="l">Average age</div></div></div>
      ${ageBar(s.age_bytes)}
      <p class="small muted" style="margin:12px 0 0">Scanned ${date(snap.started_at, true)} in ${duration(snap.scan_seconds)} ·
        indexed in ${duration(snap.build_seconds)} · ${num(snap.errors)} error${snap.errors === 1 ? "" : "s"}</p>`
      : html`<p class="muted">Not scanned yet.</p>`}
  </div>`;
}

function folderTable(rows, cb, { compact = false, map = false } = {}) {
  if (!rows.length) return emptyState("folder", "No folders", "Nothing matches.");
  return html`<div class="table-wrap"><table class="table ${compact ? "compact" : ""}"><thead><tr>
      <th>Folder</th>${compact ? "" : html`<th>Filesystem</th>`}<th>Owner</th><th class="num">Size</th>${compact ? "" : html`<th class="num">Files</th>`}
      <th class="num">Cold</th>${compact ? "" : html`<th class="num">Average age</th><th>Last change</th>`}<th class="num">Change</th><th class="t-age">Age profile</th></tr></thead><tbody>
    ${rows.map((f) => {
      const s = f.latest;
      return html`<tr><td><a class="rowlink" href="${placeHash("explore", { fs: f.fs, path: f.path })}">${f.folder || "(root files)"}</a>
          ${compact ? html`<div class="sub">${f.fs_name}</div>` : ""}</td>
        ${compact ? "" : html`<td>${f.fs_name}</td>`}
        <td>${f.lab ? f.lab.name : html`<span class="badge badge-inside">Unmapped</span>`}
          ${map && state.me.is_admin && f.folder ? html` <button class="link-btn small" data-map="${f.fs}|${f.folder}" title="Change owner">${icon("users")}</button>` : ""}</td>
        <td class="num nowrap">${bytes(s.bytes)}</td>${compact ? "" : html`<td class="num">${num(s.files)}</td>`}
        <td class="num">${pct(s.bytes ? (s.cold_bytes / s.bytes) * 100 : 0)}</td>
        ${compact ? "" : html`<td class="num nowrap">${ageText(s.mean_age_days)}</td><td class="nowrap">${s.newest ? date(s.newest) : "—"}</td>`}
        <td class="num nowrap">${f.change ? signed(f.change.bytes) : "—"}</td><td>${ageBar(s.age_bytes)}</td></tr>`;
    })}</tbody></table></div>`;
}

// ---------- Folders and labs ----------
export async function viewFolders(el, query) {
  state.place = null;
  renderSidebar();
  const q = parseQuery(query);
  const [home, cfg] = await Promise.all([api("home"), Promise.resolve(state.config)]);
  const admin = state.me.is_admin;
  let rows = home.folders;
  if (q.fs) rows = rows.filter((f) => f.fs === q.fs);
  if (q.unmapped) rows = rows.filter((f) => !f.lab && f.folder);
  if (q.lab) rows = rows.filter((f) => f.lab && f.lab.slug === q.lab);
  rows = [...rows].sort((a, b) => b.latest.bytes - a.latest.bytes);
  const cb = cfg.cold_bucket;
  const set = (changes) => { location.hash = `#/folders?${new URLSearchParams(Object.entries({ ...q, ...changes }).filter(([, v]) => v))}`; };
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Overview</div><h1>${admin ? "Folders and labs" : "My folders"}</h1>
      <p>${admin ? "Every top-level folder, who owns it, and how it is changing." : "The top-level folders your labs own."}</p></div></div>
    ${admin ? html`<div class="toolbar">
      <label class="ctl"><span>Filesystem</span><select class="select sm" id="f-fs">${opt("", "All", q.fs)}${cfg.filesystems.map((f) => opt(f.slug, f.name, q.fs))}</select></label>
      <label class="ctl"><span>Owner</span><select class="select sm" id="f-lab">${opt("", "Anyone", q.lab)}${home.labs.map((l) => opt(l.slug, l.name, q.lab))}</select></label>
      <label class="small" style="align-self:center"><input type="checkbox" id="f-unmapped" ${q.unmapped ? raw("checked") : ""}> Unmapped only</label>
    </div>` : ""}
    <div class="card flush" style="margin-bottom:20px">${folderTable(rows, cb, { map: true })}</div>
    <div class="card flush"><div class="card-head"><h2>Labs and departments</h2></div>
      <div class="table-wrap"><table class="table"><thead><tr><th>Name</th><th>Division</th><th class="num">Folders</th><th class="num">Members</th><th class="num">Size</th><th class="num">Cold</th><th class="num">Change</th></tr></thead><tbody>
      ${home.labs.map((l) => html`<tr><td><a class="rowlink" href="#/explore?lab=${encodeURIComponent(l.slug)}">${l.name}</a><div class="sub">${l.pi ? `${l.kind === "department" ? "Owner" : "PI"}: ${l.pi}` : ""}</div></td>
        <td>${l.division}<div class="sub">${l.research_area}</div></td><td class="num">${l.folders}</td><td class="num">${l.members}</td>
        <td class="num nowrap">${bytes(l.bytes)}</td><td class="num">${pct(l.bytes ? (l.cold_bytes / l.bytes) * 100 : 0)}</td><td class="num nowrap">${signed(l.change)}</td></tr>`)}</tbody></table></div></div>`);
  $("#f-fs", el)?.addEventListener("change", (e) => set({ fs: e.target.value }));
  $("#f-lab", el)?.addEventListener("change", (e) => set({ lab: e.target.value }));
  $("#f-unmapped", el)?.addEventListener("change", (e) => set({ unmapped: e.target.checked ? "1" : "" }));
  $$("[data-map]", el).forEach((b) => b.addEventListener("click", async () => {
    const [fs, folder] = b.dataset.map.split("|");
    const current = home.folders.find((f) => f.fs === fs && f.folder === folder)?.lab?.slug || "";
    const ok = await modal({
      title: `Owner of ${folder}`,
      body: html`<p class="small muted">Members of the lab or department you choose will see this folder and everything in it.</p>
        <label class="field"><span class="label">Lab or department</span><select class="select" id="map-lab">${opt("", "No one (unmapped)", current)}
          ${home.labs.map((l) => opt(l.slug, l.name, current))}</select></label>`,
      confirm: "Save",
      onConfirm: async (wrap) => {
        await api("admin/folders", { method: "POST", body: { fs, folder, lab: $("#map-lab", wrap).value || null } });
        return true;
      },
    });
    if (ok) { toast("Owner updated"); viewFolders(el, query); }
  }));
}

// ---------- Storage health ----------
export async function viewHealth(el, query) {
  state.place = null;
  renderSidebar();
  const q = parseQuery(query);
  const cfg = state.config;
  const fs = q.fs || cfg.filesystems[0]?.slug;
  if (!fs) { mount(el, emptyState("database", "No filesystems", "Add and scan a filesystem first.")); return; }
  const huge = Number(q.huge || 10000), many = Number(q.many || 5000);
  const h = await api("admin/health", { query: { fs, huge_entries: huge, many_files: many } });
  const dirRow = (d, extra) => html`<tr><td><a class="rowlink" href="${placeHash("explore", { fs, path: d.path })}">${displayPath(fs, d.path)}</a></td>${extra(d)}</tr>`;
  const set = (changes) => { location.hash = `#/health?${new URLSearchParams(Object.entries({ fs, huge, many, ...changes }))}`; };
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>Storage health</h1>
      <p>Problems that slow storage down, break migrations, or hide data, as of the scan on ${date(h.as_of, true)}.</p></div></div>
    <div class="toolbar">
      <label class="ctl"><span>Filesystem</span><select class="select sm" id="h-fs">${cfg.filesystems.map((f) => opt(f.slug, f.name, fs))}</select></label>
      <label class="ctl"><span>Large folder: entries</span><select class="select sm" id="h-huge">${[1000, 10000, 100000, 1000000].map((n) => opt(n, `${num(n)} or more`, huge))}</select></label>
      <label class="ctl"><span>Small-file hotspot: files</span><select class="select sm" id="h-many">${[1000, 5000, 10000, 100000].map((n) => opt(n, `${num(n)} or more`, many))}</select></label>
    </div>
    <div class="grid cols-2">
      <div class="card flush"><div class="card-head"><h2>Scan errors</h2><span class="small muted">${num(h.errors.rows.length)} shown</span></div>
        ${h.errors.rows.length ? html`<div style="padding:0 22px 8px" class="tags">${h.errors.by_kind.map((k) => html`<span class="tag">${k.op} ${k.errno_name || ""}: ${num(k.n)}</span>`)}</div>
          <div class="table-wrap"><table class="table compact"><thead><tr><th>Path</th><th>Problem</th></tr></thead><tbody>
          ${h.errors.rows.map((r) => html`<tr><td class="path">${displayPath(fs, r.path)}</td><td>${r.op}: ${r.errno_name || r.message}</td></tr>`)}</tbody></table></div>
          <p class="small muted" style="padding:10px 22px 16px;margin:0">The scanner could not read these, so totals above them are lower bounds. Usually the cause is permissions.</p>`
          : html`<p class="muted" style="padding:0 22px 18px">No errors in this scan.</p>`}</div>
      <div class="card"><div class="card-head"><h2>Problem file names</h2></div>
        <div class="bars">${h.name_flags.filter((f) => f.entries).map((f) => html`<div class="bar-row compact" style="grid-template-columns: 1fr 70px">
          <span class="lab" title="${f.label}"><button class="link-btn small" style="font-size:13px;color:var(--link);font-weight:700" data-flag="${f.bit}">${f.label}</button></span>
          <span class="val">${num(f.entries)}</span></div>`)}</div>
        ${h.name_flags.some((f) => f.entries) ? html`<div id="name-list" style="margin-top:12px"></div>` : html`<p class="muted">No problem names.</p>`}</div>
      <div class="card flush"><div class="card-head"><h2>Large folders</h2><span class="small muted">${num(huge)}+ entries in one folder</span></div>
        ${h.huge_dirs.length ? html`<div class="table-wrap"><table class="table compact"><thead><tr><th>Folder</th><th class="num">Entries</th></tr></thead><tbody>
          ${h.huge_dirs.map((d) => dirRow(d, (x) => html`<td class="num">${num(x.entries)}</td>`))}</tbody></table></div>
          <p class="small muted" style="padding:10px 22px 16px;margin:0">Folders this large are slow to list and back up, for everyone on the filesystem.</p>`
          : html`<p class="muted" style="padding:0 22px 18px">None.</p>`}</div>
      <div class="card flush"><div class="card-head"><h2>Small-file hotspots</h2><span class="small muted">${num(many)}+ files averaging under 64 KB</span></div>
        ${h.small_file_dirs.length ? html`<div class="table-wrap"><table class="table compact"><thead><tr><th>Folder</th><th class="num">Files</th><th class="num">Average</th></tr></thead><tbody>
          ${h.small_file_dirs.map((d) => dirRow(d, (x) => html`<td class="num">${num(x.d_files)}</td><td class="num nowrap">${bytes(x.d_bytes / Math.max(1, x.d_files))}</td>`))}</tbody></table></div>
          <p class="small muted" style="padding:10px 22px 16px;margin:0">Bundling these into archives (tar, zip, HDF5, Zarr) cuts metadata load and makes them much faster to copy.</p>`
          : html`<p class="muted" style="padding:0 22px 18px">None.</p>`}</div>
      <div class="card flush"><div class="card-head"><h2>Accounts that no longer exist</h2></div>
        ${h.unknown_owners.length ? html`<div class="table-wrap"><table class="table compact"><thead><tr><th>Account</th><th class="num">Size</th><th class="num">Files</th><th>Last change</th></tr></thead><tbody>
          ${h.unknown_owners.map((o) => html`<tr><td><a class="rowlink" href="${placeHash("files", { fs, path: h.path }, { uid: o.uid })}">uid ${o.uid}</a></td>
            <td class="num nowrap">${bytes(o.bytes)}</td><td class="num">${num(o.files)}</td><td class="nowrap">${o.newest ? date(o.newest) : "—"}</td></tr>`)}</tbody></table></div>`
          : html`<p class="muted" style="padding:0 22px 18px">Every owner has an account.</p>`}</div>
      <div class="card flush"><div class="card-head"><h2>Folders not read completely</h2></div>
        ${h.incomplete.length ? html`<div class="table-wrap"><table class="table compact"><thead><tr><th>Folder</th><th>Error</th></tr></thead><tbody>
          ${h.incomplete.map((d) => html`<tr><td class="path">${displayPath(fs, d.path)}</td><td>${d.error}</td></tr>`)}</tbody></table></div>`
          : html`<p class="muted" style="padding:0 22px 18px">Every folder was read.</p>`}</div>
    </div>`);
  $("#h-fs", el).addEventListener("change", (e) => set({ fs: e.target.value }));
  $("#h-huge", el).addEventListener("change", (e) => set({ huge: e.target.value }));
  $("#h-many", el).addEventListener("change", (e) => set({ many: e.target.value }));
  $$("[data-flag]", el).forEach((b) => b.addEventListener("click", async () => {
    const res = await api("admin/names", { query: { fs, flag: b.dataset.flag, limit: 50 } });
    mount($("#name-list", el), html`<h3 style="margin-bottom:6px">${b.textContent}</h3><ul class="feed">${res.files.map((f) =>
      html`<li><span class="path">${JSON.stringify(displayPath(fs, f.path)).slice(1, -1)}</span></li>`)}</ul>`);
  }));
}

// ---------- Scans ----------
export async function viewScans(el) {
  state.place = null;
  renderSidebar();
  const [snaps, ingest] = await Promise.all([api("admin/snapshots"), api("admin/ingest")]);
  const chip = (s) => ({
    ready: html`<span class="chip chip-done">${icon("check")}Ready</span>`,
    building: html`<span class="chip chip-active"><span class="pulse"></span>Building</span>`,
    failed: html`<span class="chip chip-failed">${icon("alert")}Failed</span>`,
    skipped: html`<span class="chip chip-muted">${icon("x")}Skipped</span>`,
    pruned: html`<span class="chip chip-muted">${icon("archive")}History only</span>`,
  })[s] || s;
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>Scans</h1>
      <p>Each fs-inventory scan becomes a snapshot. The newest ones keep their full index; older ones keep folder totals as history.</p></div></div>
    ${watchCard(ingest.watch)}
    <div class="card flush" style="margin-bottom:20px"><div class="table-wrap"><table class="table"><thead><tr>
      <th>Filesystem</th><th>Scan started</th><th>Status</th><th class="num">Entries</th><th class="num">Size</th><th class="num">Errors</th>
      <th class="num">Scan</th><th class="num">Index</th><th>fs-inventory</th></tr></thead><tbody>
      ${snaps.map((s) => html`<tr><td><b>${s.filesystem}</b><div class="sub">${s.host}</div></td>
        <td class="nowrap">${date(s.started_at, true)}<div class="sub">${when(s.started_at)}</div></td><td>${chip(s.status)}${s.message ? html`<div class="sub">${s.message}</div>` : ""}</td>
        <td class="num">${num(s.entries)}<div class="sub">${num(s.files)} files</div></td><td class="num nowrap">${bytes(s.bytes)}</td><td class="num">${num(s.errors)}</td>
        <td class="num nowrap">${duration(s.scan_seconds)}</td><td class="num nowrap">${duration(s.build_seconds)}<div class="sub">${bytes(s.index_bytes)}</div></td>
        <td>${s.tool_version}<div class="sub">${s.scan_status}</div></td></tr>`)}</tbody></table></div></div>
    <div class="card"><div class="card-head"><h2>Adding scans</h2></div>
      <p>Scan a registered filesystem and ingest it in one step, ingest a scan made elsewhere (for example by a scheduled job on a storage node), or watch a folder that scans are written to:</p>
      <pre class="json">uv run pinax scan fast
uv run pinax ingest /scans/fast/2026-10-26 --fs fast
uv run pinax ingest --watch /scans</pre>
      <p class="small muted" style="margin:12px 0 0">Only complete scans are accepted, each once. A failed or skipped scan can be ingested by hand. See docs/DEPLOYMENT.md for scheduling, sizing, and retention.</p></div>`);
}

/** Automatic ingest: what `pinax ingest --watch` last reported. */
function watchCard(w) {
  if (!w) {
    return html`<div class="card" style="margin-bottom:20px"><div class="card-head"><h2>Automatic ingest</h2>
      <span class="chip chip-muted">${icon("pause")}Not running</span></div>
      <p class="small muted" style="margin:0">Nothing is watching for new scans. Start <code>uv run pinax ingest --watch /scans</code>, or run it with <code>--once</code> from a timer.</p></div>`;
  }
  const last = w.last || {};
  const state = w.stale ? html`<span class="chip chip-failed">${icon("alert")}${w.state === "stopped" ? "Stopped" : "Not responding"}</span>`
    : w.state === "building" ? html`<span class="chip chip-active"><span class="pulse"></span>Ingesting</span>`
    : html`<span class="chip chip-done">${icon("check")}Watching</span>`;
  const counts = ["ingested", "skipped", "failed", "waiting", "unmatched"].filter((k) => last[k]).map((k) => `${num(last[k])} ${k}`);
  return html`<div class="card" style="margin-bottom:20px"><div class="card-head"><h2>Automatic ingest</h2>${state}</div>
    <p style="margin:0">Watching <span class="path">${w.folder}</span> on ${w.host} every ${w.interval % 60 ? `${w.interval} seconds` : w.interval === 60 ? "minute" : `${w.interval / 60} minutes`}${w.state === "building" ? html`; ingesting <span class="path">${w.scan}</span>` : ""}.</p>
    <p class="small muted" style="margin:6px 0 0">Last heard from ${when(w.updated_at)}.${last.at ? html` Last check ${when(last.at)}: ${num(last.found)} scans, ${counts.length ? counts.join(", ") : "nothing new"}.` : ""}${last.unmatched ? " Scans with no registered filesystem are named in its log." : ""}</p></div>`;
}

// ---------- People and access ----------
export async function viewAccess(el) {
  state.place = null;
  renderSidebar();
  const [labs, people] = await Promise.all([api("admin/labs"), api("admin/people")]);
  const render = () => mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>People and access</h1>
      <p>Who can see what. People see the top-level folders their labs and departments own; storage administrators see everything.
        Map folders to labs on <a href="#/folders">Folders and labs</a>.</p></div></div>
    <div class="grid cols-2">${labs.map((l) => html`<div class="card"><div class="card-head"><h2>${l.name}</h2><span class="tag">${l.kind === "department" ? "Department" : "Lab"}</span></div>
      <p class="small muted" style="margin-top:-8px">${l.division} · ${l.research_area}</p>
      <h3 style="margin:10px 0 6px">Folders</h3>
      ${l.folders.length ? html`<ul class="feed">${l.folders.map((f) => html`<li><span class="path">${displayPath(f.filesystem, f.path)}</span></li>`)}</ul>` : html`<p class="small muted">None mapped.</p>`}
      <h3 style="margin:10px 0 6px">Members</h3>
      <ul class="feed">${l.members.map((m) => html`<li><span class="avatar sm">${m.initials}</span><span style="flex:1">${m.display_name}<span class="muted small"> · ${m.role === "pi" ? (l.kind === "department" ? "Owner" : "PI") : "Delegate"}</span></span>
        <button class="icon-btn" title="Remove ${m.display_name}" data-remove="${l.slug}|${m.username}">${icon("x")}</button></li>`)}</ul>
      <div class="row" style="margin-top:10px"><select class="select sm" data-add-user="${l.slug}">${opt("", "Add a person…", "")}
        ${people.filter((p) => !p.is_admin && !l.members.some((m) => m.username === p.username)).map((p) => opt(p.username, p.display_name, ""))}</select>
        <select class="select sm" data-add-role="${l.slug}">${opt("delegate", "Delegate", "delegate")}${opt("pi", l.kind === "department" ? "Owner" : "PI", "")}</select>
        <button class="btn btn-ghost btn-sm" data-add="${l.slug}">${icon("plus")}Add</button></div>
    </div>`)}</div>`);
  const refresh = async () => { labs.splice(0, labs.length, ...(await api("admin/labs"))); render(); bind(); };
  const bind = () => {
    $$("[data-remove]", el).forEach((b) => b.addEventListener("click", async () => {
      const [slug, username] = b.dataset.remove.split("|");
      await api(`admin/labs/${slug}/members`, { method: "POST", body: { username, role: null } });
      toast("Removed"); refresh();
    }));
    $$("[data-add]", el).forEach((b) => b.addEventListener("click", async () => {
      const slug = b.dataset.add;
      const username = $(`[data-add-user="${slug}"]`, el).value;
      if (!username) return toast("Choose a person first", "error");
      await api(`admin/labs/${slug}/members`, { method: "POST", body: { username, role: $(`[data-add-role="${slug}"]`, el).value } });
      toast("Added"); refresh();
    }));
  };
  render();
  bind();
}
