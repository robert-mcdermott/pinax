// SPDX-License-Identifier: Apache-2.0
// Administrator views: the organization overview, folders and labs, storage health, scans, and people and access.
import {
  $, $$, ageText, api, bytes, date, displayPath, duration, emptyState, html, icon, kindLabel, modal, mount, num,
  parseQuery, pct, placeHash, raw, state, toast, when,
} from "/js/core.js";
import { fillChip, fillRange, fillShort } from "/js/capacity.js";
import { renderSidebar } from "/js/shell.js";
import { bindPersonTokens, personTokens } from "/js/tokens.js";
import { viewsCard } from "/js/views.js";
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
  const filling = ov.filesystems.filter((f) => f.capacity && (f.capacity.soon || f.capacity.full));
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Overview</div><h1>All data</h1>
      <p>Every filesystem Pinax indexes, as of each one's latest scan.</p></div>
      <div class="actions"><a class="btn btn-primary" href="#/explore">${icon("treemap")}Open the age map</a>
        <a class="btn btn-secondary" href="#/folders">${icon("folder")}All folders</a></div></div>
    <div class="kpis">
      <div class="kpi"><div class="label">Total size</div><div class="value">${bytes(s.bytes)}</div><div class="sub">${bytes(s.alloc)} on disk</div></div>
      <div class="kpi" style="--kpi-accent:var(--fh-teal)"><div class="label">Files</div><div class="value">${num(s.files)}</div><div class="sub">${num(s.dirs)} folders</div></div>
      <div class="kpi" style="--kpi-accent:var(--age-5)"><div class="label">Unchanged for ${thresholdText(cb)}+</div><div class="value">${bytes(cold)}</div><div class="sub">${pct(s.bytes ? (cold / s.bytes) * 100 : 0)} of all data</div></div>
      <a class="kpi" href="#/history" style="--kpi-accent:var(--fh-bright-blue)"><div class="label">Change since the previous scans</div><div class="value">${signed(change)}</div><div class="sub">${movers.length} folders changed · history</div></a>
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
            ${filling.map((f) => html`<li><span>${icon("server")}</span><span><b>${f.filesystem.name} ${f.capacity.full ? "is full" : `fills within ${f.capacity.warning_months} months`}</b>${f.capacity.full ? "" : `: about ${fillShort(f.capacity)} at its trend, ${fillRange(f.capacity)}`}. <a href="#/capacity">Capacity</a></span></li>`)}
            <li><span>${icon("folder")}</span><span><b>${unmapped.length} unmapped top-level folders</b> hold ${bytes(unmapped.reduce((t, f) => t + f.latest.bytes, 0))} that no PI can see. <a href="#/folders?unmapped=1">Map them</a></span></li>
            <li><span>${icon("alert")}</span><span><b>${num(errors)} scan errors</b>, such as folders the scanner could not open, so their totals are lower bounds. <a href="#/health">Review</a></span></li>
            <li><span>${icon("tag")}</span><span><b>${num(names)} problem file names</b> that break scripts and migrations. <a href="#/health">Review</a></span></li>
          </ul></div>
        <div class="card"><div class="card-head"><h2>Recent activity</h2><a class="small" href="#/audit">Audit log</a></div>
          <ul class="feed">${ov.activity.map((a) => html`<li><span class="when">${when(a.ts)}</span><span>${a.summary}</span></li>`)}</ul></div>
      </div>
    </div>
    <div id="home-views"></div>`);
  viewsCard(el.querySelector("#home-views"));
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
        indexed in ${duration(snap.build_seconds)} · ${num(snap.errors)} error${snap.errors === 1 ? "" : "s"}</p>
      ${f.capacity ? capacityLine(f.capacity) : ""}`
      : html`<p class="muted">Not scanned yet.</p>`}
  </div>`;
}

/** How full a filesystem is, and when it fills at its trend. */
function capacityLine(c) {
  if (!c.capacity.bytes) return html`<p class="small muted" style="margin:10px 0 0"><a href="#/capacity">Set its capacity</a> to see when it fills.</p>`;
  const share = Math.min(1, c.share ?? 0);
  return html`<div style="margin-top:14px"><div class="progress" role="img" aria-label="${pct(c.share * 100)} of the capacity used"><span style="width:${(share * 100).toFixed(1)}%"></span></div>
    <p class="small" style="margin:8px 0 0"><b>${pct(c.share * 100)}</b> of ${bytes(c.capacity.bytes)} used ·
      ${c.fills?.at ? `fills about ${fillShort(c)}` : fillShort(c)} ${fillChip(c)} <a href="#/capacity">Capacity</a></p></div>`;
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
  const admin = state.me.sees_all;
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
        <td class="nowrap">${s.status === "ready" || s.status === "pruned"
          ? html`<a href="${s.status === "ready" ? `#/explore?fs=${encodeURIComponent(s.filesystem)}&at=${s.started_at.slice(0, 10)}`
            : `#/history?fs=${encodeURIComponent(s.filesystem)}&at=${s.started_at.slice(0, 10)}&pruned=1`}">${date(s.started_at, true)}</a>`
          : date(s.started_at, true)}<div class="sub">${when(s.started_at)}</div></td><td>${chip(s.status)}${s.message ? html`<div class="sub">${s.message}</div>` : ""}</td>
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
// ---------- People and access ----------
const TABS = [["people", "People"], ["labs", "Labs and departments"]];
const statusChip = (active) => (active ? html`<span class="chip chip-approved">${icon("check")}Active</span>`
  : html`<span class="chip chip-muted">${icon("x")}Inactive</span>`);
const ROLE_BADGE = { auditor: "Auditor", finance: "Finance" };
const roleBadge = (r) => (r.role === "division" ? `Head of ${r.division}` : ROLE_BADGE[r.role] || r.role);
const accessBadges = (p) => html`<div class="tags">
  ${p.is_admin ? html`<span class="badge badge-archived">${icon("shield")}Storage administrator</span>` : ""}
  ${p.emergency ? html`<span class="badge badge-requested" title="Signs in with a password at /auth/emergency when sign-in with the organization's account fails; marked from the server's command line">${icon("alert")}Emergency account</span>` : ""}
  ${p.roles.map((r) => html`<span class="badge badge-archived">${icon(r.role === "finance" ? "receipt" : r.role === "auditor" ? "eye" : "users")}${roleBadge(r)}</span>`)}
  ${p.labs.map((l) => html`<span class="badge badge-inside">${l.name} · ${l.role === "pi" ? (l.kind === "department" ? "Owner" : "PI") : "Delegate"}</span>`)}
  ${!p.is_admin && !p.labs.length && !p.roles.length ? html`<span class="small muted">No access</span>` : ""}</div>`;

export async function viewAccess(el, query = "") {
  state.place = null;
  renderSidebar();
  const q = parseQuery(query);
  const tab = q.tab === "labs" ? "labs" : "people";
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>People and access</h1>
      <p>Who can sign in, what each person sees, and who administers Pinax. People see the top-level folders their labs and
        departments own, and division heads those of their divisions' labs; finance sees every lab's charges; auditors see
        everything, and storage administrators see everything and change it. For many changes at once, use <code>pinax import</code> (docs/IMPORT.md).</p></div>
      <div class="actions admin-only">${tab === "people" ? html`<a class="btn btn-primary" href="#/access/people/new">${icon("plus")}Add person</a>`
        : html`<a class="btn btn-primary" href="#/access/labs/new">${icon("plus")}Add lab or department</a>`}</div></div>
    <div class="tabs" role="tablist">${TABS.map(([k, l]) => html`<button class="tab ${tab === k ? "active" : ""}" role="tab" aria-selected="${tab === k}" data-tab="${k}">${l}</button>`)}</div>
    <div id="access-body"></div>`);
  $$(".tab", el).forEach((b) => b.addEventListener("click", () => { location.hash = `#/access?tab=${b.dataset.tab}`; }));
  const body = $("#access-body", el);
  if (tab === "labs") await labsTab(body);
  else await peopleTab(body, q);
}

const VIEWS = [["active", "All active"], ["admins", "Storage administrators"], ["roles", "Site roles"], ["none", "No access"], ["inactive", "Inactive"]];
const VIEW_PICK = {
  active: (p) => p.active, admins: (p) => p.active && p.is_admin, roles: (p) => p.active && p.roles.length > 0,
  none: (p) => p.active && !p.is_admin && !p.labs.length && !p.roles.length, inactive: (p) => !p.active,
};

async function peopleTab(el, q) {
  const view = VIEWS.some(([k]) => k === q.view) ? q.view : "active";
  const text = (q.q || "").trim().toLowerCase();
  const all = await api("admin/people");
  const pick = VIEW_PICK[view];
  const people = all.filter(pick).filter((p) => !text || [p.display_name, p.username, p.email, p.title].some((v) => (v || "").toLowerCase().includes(text)));
  mount(el, html`
    <div class="toolbar">
      <div class="pills" role="group" aria-label="Show">${VIEWS.map(([k, l]) => html`<button class="${view === k ? "active" : ""}" aria-pressed="${view === k}" data-view="${k}">${l}
        <span class="tiny">${num(all.filter(VIEW_PICK[k]).length)}</span></button>`)}</div>
      <label class="ctl grow"><span>Search</span><div class="search-input">${icon("search")}<input class="input" id="people-q" placeholder="Name, username, email, or title" value="${q.q || ""}" autocomplete="off"></div></label>
    </div>
    <div class="card flush">${people.length ? html`<div class="table-wrap"><table class="table"><thead><tr>
      <th>Person</th><th>Username</th><th>Access</th><th>Status</th></tr></thead><tbody>
      ${people.map((p) => html`<tr class="clickable" data-href="#/access/people/${encodeURIComponent(p.username)}">
        <td><div class="row" style="flex-wrap:nowrap"><span class="avatar ${p.is_admin ? "strong" : ""}">${p.initials}</span>
          <div><a class="title-cell" href="#/access/people/${encodeURIComponent(p.username)}">${p.display_name}</a><div class="sub">${p.title || "—"}</div></div></div></td>
        <td><div class="mono">${p.username}</div><div class="sub">${p.email}</div></td>
        <td>${accessBadges(p)}</td><td>${statusChip(p.active)}${state.config.auth_mode === "local" && passwordNote(p) ? html`<div class="sub">${passwordNote(p)}</div>` : ""}</td></tr>`)}
      </tbody></table></div>` : emptyState("users", "No people here", view === "inactive" ? "Nobody has been deactivated." : "Try another view or search.")}</div>`);
  const go = (changes) => { location.hash = `#/access?${new URLSearchParams({ tab: "people", view, q: $("#people-q", el).value, ...changes })}`; };
  $$("[data-view]", el).forEach((b) => b.addEventListener("click", () => go({ view: b.dataset.view })));
  $("#people-q", el).addEventListener("keydown", (e) => { if (e.key === "Enter") go({}); });
  $$("tr[data-href]", el).forEach((tr) => tr.addEventListener("click", (e) => {
    if (!e.target.closest("a, button")) location.hash = tr.dataset.href;
  }));
}

/** Local sign-in: where a person's password stands, for the person page and the list. */
function passwordState(p) {
  if (!p.has_password) return "No password yet: they cannot sign in until you give them a temporary one.";
  if (p.locked_until && new Date(p.locked_until) > new Date()) return `Locked until ${date(p.locked_until, true)} after repeated failed sign-ins. A new temporary password unlocks it.`;
  if (p.must_change_password) return `Temporary password made ${date(p.password_set_at, true)}; they must change it when they sign in.`;
  return `Set ${date(p.password_set_at)}${p.last_login_at ? `; last signed in ${when(p.last_login_at)}` : "; not signed in since"}.`;
}
const passwordNote = (p) => (!p.active ? "" : !p.has_password ? "No password" : p.locked_until && new Date(p.locked_until) > new Date() ? "Locked"
  : p.must_change_password ? "Temporary password" : "");

// ---------- A person ----------
export async function viewPerson(el, username) {
  state.place = null;
  renderSidebar();
  const creating = username === "new";
  const [person, labs] = await Promise.all([creating ? null : api(`admin/people/${encodeURIComponent(username)}`), api("admin/labs")]);
  const active = labs.filter((l) => l.active !== false);
  const bySlug = Object.fromEntries(labs.map((l) => [l.slug, l]));
  const form = person
    ? { display_name: person.display_name, email: person.email, title: person.title, is_admin: person.is_admin,
        memberships: person.labs.map((l) => ({ lab: l.slug, role: l.role })),
        roles: person.roles.map((r) => ({ role: r.role, division: r.division })) }
    : { username: "", display_name: "", email: "", title: "", is_admin: false, memberships: [], roles: [] };
  const inactive = person && !person.active;
  const locked = inactive || !state.me.is_admin;  // auditors read it
  const self = person?.is_self;
  const off = (cond) => (cond ? raw("disabled") : "");
  const divisions = [...new Set(labs.map((l) => l.division).filter(Boolean))].sort();
  const has = (role, division = "") => form.roles.some((r) => r.role === role && (r.division || "") === division);
  // Roles the identity provider sets at sign-in (PINAX_OIDC_ROLE_MAP): shown, not changed here.
  const mapped = new Set(state.config.mapped_roles || []);
  const fromIdp = (key) => (mapped.has(key) ? html` <span class="badge badge-inside">From Entra ID</span>` : "");
  const toggle = (role, division, on) => {
    form.roles = form.roles.filter((r) => !(r.role === role && (r.division || "") === division));
    if (on) form.roles.push({ role, division });
  };

  const render = () => {
    const used = new Set(form.memberships.map((m) => m.lab));
    const heads = form.roles.filter((r) => r.role === "division").map((r) => r.division);
    const sees = [...form.memberships.flatMap((m) => (bySlug[m.lab] ? bySlug[m.lab].folders.map((f) => ({ f, lab: bySlug[m.lab] })) : [])),
      ...active.filter((l) => heads.includes(l.division) && !used.has(l.slug)).flatMap((l) => l.folders.map((f) => ({ f, lab: l, division: l.division })))];
    const kept = person ? [...person.labs.map((l) => `${l.name} (${l.role === "pi" ? "PI" : "delegate"})`), ...person.roles.map((r) => roleBadge(r).toLowerCase()),
      ...(person.is_admin ? ["storage administrator"] : [])] : [];
    mount(el, html`
      <div class="page-head"><div><div class="eyebrow"><a href="#/access?tab=people">${icon("arrowLeft")} People and access</a></div>
        <h1>${creating ? "Add person" : person.display_name}</h1>
        ${person ? html`<p>${person.title || "No title"} · <span class="mono">${person.username}</span></p>` : html`<p>Add someone who can sign in to Pinax: a PI, a delegate, or a storage administrator.</p>`}</div>
        ${person ? statusChip(person.active) : ""}</div>
      ${inactive ? html`<div class="alert alert-warn">${icon("alert")}<div><b>${person.display_name} is deactivated.</b>
        <p>They cannot sign in. Their access is kept, unused${kept.length ? `: ${kept.join(", ")}` : ""}. Reactivate them to restore it, or to make changes.</p>
        <button class="btn btn-primary btn-sm admin-only" id="reactivate" style="margin-top:8px">${icon("refresh")}Reactivate</button></div></div>` : ""}
      <div class="grid split">
        <div class="stack">
          <div class="card"><div class="card-head"><h2>Profile</h2></div>
            <div class="grid cols-2">
              <div class="field"><label for="p-username">Username</label>
                ${creating ? html`<input class="input mono" id="p-username" data-field="username" value="${form.username}" placeholder="e.g. jdoe or jdoe@example.org" autocomplete="off" spellcheck="false">
                  <span class="hint">The name the sign-on sends for this person. It cannot be changed later.</span>`
                  : html`<div class="input mono readonly">${person.username}</div><span class="hint">The name the sign-on sends; it cannot be changed.</span>`}</div>
              <div class="field"><label for="p-name">Full name</label><input class="input" id="p-name" data-field="display_name" value="${form.display_name}" ${off(locked)}></div>
              <div class="field"><label for="p-email">Email</label><input class="input" id="p-email" type="email" data-field="email" value="${form.email}" ${off(locked)}></div>
              <div class="field"><label for="p-title">Title</label><input class="input" id="p-title" data-field="title" value="${form.title}" placeholder="e.g. Lab manager" ${off(locked)}></div>
            </div></div>
          <div class="card"><div class="card-head"><h2>Labs and departments</h2></div>
            <p class="small muted" style="margin-top:-6px">PIs, owners, and delegates see every folder their lab or department owns.</p>
            ${form.memberships.length ? html`<div class="table-wrap"><table class="table compact"><thead><tr><th>Lab or department</th><th style="width:120px">Role</th><th class="hide-narrow">Folders</th><th></th></tr></thead><tbody>
              ${form.memberships.map((m, i) => {
                const lab = bySlug[m.lab];
                return html`<tr>
                  <td><select class="select sm" style="width:100%" data-row="${i}" data-key="lab" aria-label="Lab or department" ${off(locked)}>
                    ${active.map((l) => html`<option value="${l.slug}" ${l.slug === m.lab ? raw("selected") : ""} ${used.has(l.slug) && l.slug !== m.lab ? raw("disabled") : ""}>${l.name}</option>`)}</select></td>
                  <td><select class="select sm" style="width:100%" data-row="${i}" data-key="role" aria-label="Role" ${off(locked)}>
                    <option value="pi" ${m.role === "pi" ? raw("selected") : ""}>${lab && lab.kind === "department" ? "Owner" : "PI"}</option>
                    <option value="delegate" ${m.role === "delegate" ? raw("selected") : ""}>Delegate</option></select></td>
                  <td class="hide-narrow"><div class="tags">${lab && lab.folders.length ? lab.folders.map((f) => html`<span class="tag mono nowrap" title="${displayPath(f.filesystem, f.path)}">${icon("folder")}${f.filesystem}: ${f.folder}</span>`) : html`<span class="small muted">No folders mapped</span>`}</div></td>
                  <td class="right">${locked ? "" : html`<button class="icon-btn" data-remove="${i}" aria-label="Remove ${lab ? lab.name : "this row"}">${icon("x")}</button>`}</td></tr>`;
              })}</tbody></table></div>` : html`<p class="muted small">Not a member of any lab or department.</p>`}
            ${locked ? "" : html`<button class="btn btn-ghost btn-sm" id="add-lab" style="margin-top:12px" ${off(used.size >= active.length)}>${icon("plus")}Add lab or department</button>`}
          </div>
          <div class="card"><div class="card-head"><h2>Site roles</h2></div>
            <p class="small muted" style="margin-top:-6px">Beside any labs and departments. Only storage administrators change anything; the others read.</p>
            ${mapped.size ? html`<p class="small muted">Roles marked <i>From Entra ID</i> are set when the person signs in, from their app roles or groups there.</p>` : ""}
            <label class="check-row ${form.is_admin ? "checked" : ""}"><input type="checkbox" data-field="is_admin" ${form.is_admin ? raw("checked") : ""} ${off(locked || self || mapped.has("admin"))}>
              <span><b>Storage administrator</b>${fromIdp("admin")}<br><span class="small muted">Sees every filesystem and folder, the IT views, and Scans, and manages people, labs, and folder ownership.</span></span></label>
            ${self ? html`<p class="small muted">You cannot change your own administrator role.</p>` : ""}
            <label class="check-row ${has("auditor") ? "checked" : ""}"><input type="checkbox" data-role="auditor" ${has("auditor") ? raw("checked") : ""} ${off(locked || mapped.has("auditor"))}>
              <span><b>Auditor</b>${fromIdp("auditor")}<br><span class="small muted">Sees everything a storage administrator does, people and the audit log included, and changes nothing.</span></span></label>
            <label class="check-row ${has("finance") ? "checked" : ""}"><input type="checkbox" data-role="finance" ${has("finance") ? raw("checked") : ""} ${off(locked || mapped.has("finance"))}>
              <span><b>Finance</b>${fromIdp("finance")}<br><span class="small muted">Sees every lab's and department's charges, with the prices, free tiers, and capacities behind them; no folders or files.</span></span></label>
            <div class="check-row ${heads.length ? "checked" : ""}"><span><b>Division head</b><br><span class="small muted">Sees the labs of each division chosen, as their members do, and changes nothing.</span>
              ${divisions.length ? html`<span class="tags" style="margin-top:8px">${divisions.map((d) => html`<label class="radio"><input type="checkbox" data-division="${d}" ${has("division", d) ? raw("checked") : ""} ${off(locked || mapped.has(`division:${d}`))}> <span>${d}</span>${fromIdp(`division:${d}`)}</label>`)}</span>`
                : html`<br><span class="small muted">No lab has a division yet: give one on a lab's page.</span>`}</span></div>
          </div>
          ${locked ? "" : html`<div class="row between"><a class="btn btn-ghost" href="#/access?tab=people">Cancel</a>
            <button class="btn btn-primary" id="save">${icon("check")}${creating ? "Add person" : "Save changes"}</button></div>`}
        </div>
        <aside class="stack">
          <div class="card"><h3>${inactive ? "Would see, once reactivated" : "Sees"}</h3>
            ${form.is_admin ? html`<p class="small" style="margin-top:8px">${icon("shield")} Everything, as a storage administrator.</p>`
              : has("auditor") ? html`<p class="small" style="margin-top:8px">${icon("eye")} Everything, to read, as an auditor.</p>` : ""}
            ${has("finance") && !form.is_admin && !has("auditor") ? html`<p class="small" style="margin-top:8px">${icon("receipt")} Every lab's and department's charges, as finance.</p>` : ""}
            ${sees.length ? html`<ul class="feed" style="margin-top:6px">${sees.map(({ f, lab, division }) => html`<li style="padding:8px 0">${icon("folder")}<span><span class="mono">${displayPath(f.filesystem, f.path)}</span><br><span class="tiny muted">via ${lab.name}${division ? `, as head of ${division}` : ""}</span></span></li>`)}</ul>`
              : form.is_admin || has("auditor") || has("finance") ? "" : html`<p class="small muted" style="margin-top:8px">No folders. Add a lab or department that owns folders.</p>`}</div>
          ${person ? html`<div class="card"><h3>Record</h3><dl class="kv" style="margin-top:10px">
            <dt>Added</dt><dd>${date(person.created_at)}</dd></dl>
            ${person.history.length ? html`<div class="row between" style="margin-top:14px"><h3>Recent changes</h3><a class="small" href="#/audit?person=${encodeURIComponent(person.username)}">All</a></div><ul class="feed">${person.history.map((h) => html`<li style="padding:6px 0"><span><span class="small">${h.summary}</span><br>
              <span class="tiny muted">${date(h.ts, true)}${h.by ? ` · ${h.by}` : ""}${h.token ? ` · with the token “${h.token}”` : ""}</span></span></li>`)}</ul>` : ""}</div>` : ""}
          ${person ? personTokens(person) : ""}
          ${person && person.emergency ? html`<div class="card"><h3>Emergency account</h3>
            <p class="small" style="margin-top:8px">${icon("alert")} Signs in with a password at <span class="mono">/auth/emergency</span>
              when sign-in with the organization's account fails, for an hour at a time; every use is audited and reported.</p>
            <p class="small muted">Marked from the server's command line, and ended there:</p>
            <div class="mono small" style="white-space:nowrap;overflow-x:auto">pinax user emergency ${person.username} --off</div></div>` : ""}
          ${person && state.config.auth_mode === "local" && state.me.is_admin ? html`<div class="card"><h3>Password</h3>
            <p class="small" style="margin-top:8px">${passwordState(person)}</p>
            ${person.active ? html`<button class="btn btn-secondary btn-sm" id="set-password">${icon("lock")}${person.has_password ? "Set a new temporary password" : "Set a temporary password"}</button>` : ""}</div>` : ""}
          ${person && person.active && !self && state.me.is_admin ? html`<div class="card"><h3>Deactivate</h3>
            <p class="small muted" style="margin-top:8px">${person.display_name} will no longer be able to sign in. Their memberships and role are kept, unused, and come back if you reactivate them; nothing is deleted.</p>
            <button class="btn btn-warn btn-sm" id="deactivate">${icon("lock")}Deactivate</button></div>` : ""}
        </aside>
      </div>`);
    bind();
  };

  const bind = () => {
    $$("[data-field]", el).forEach((inp) => inp.addEventListener(inp.type === "checkbox" ? "change" : "input", () => {
      form[inp.dataset.field] = inp.type === "checkbox" ? inp.checked : inp.value;
      if (inp.type === "checkbox") render();
    }));
    $$("[data-row]", el).forEach((sel) => sel.addEventListener("change", () => {
      form.memberships[Number(sel.dataset.row)][sel.dataset.key] = sel.value;
      render();
    }));
    $$("[data-remove]", el).forEach((b) => b.addEventListener("click", () => { form.memberships.splice(Number(b.dataset.remove), 1); render(); }));
    $$("[data-role]", el).forEach((c) => c.addEventListener("change", () => { toggle(c.dataset.role, "", c.checked); render(); }));
    $$("[data-division]", el).forEach((c) => c.addEventListener("change", () => { toggle("division", c.dataset.division, c.checked); render(); }));
    $("#add-lab", el)?.addEventListener("click", () => {
      const used = new Set(form.memberships.map((m) => m.lab));
      const next = active.find((l) => !used.has(l.slug));
      if (next) form.memberships.push({ lab: next.slug, role: "delegate" });
      render();
    });
    $("#save", el)?.addEventListener("click", save);
    $("#deactivate", el)?.addEventListener("click", () => change("deactivate"));
    $("#set-password", el)?.addEventListener("click", setPassword);
    $("#reactivate", el)?.addEventListener("click", () => change("reactivate"));
    if (person) bindPersonTokens(el, person, () => viewPerson(el, username));
  };

  const save = async () => {
    try {
      const saved = await api(creating ? "admin/people" : `admin/people/${encodeURIComponent(username)}`,
        { method: creating ? "POST" : "PUT", body: form });
      toast(creating ? `${saved.display_name} added` : "Changes saved");
      state.users = null;
      if (creating) location.hash = `#/access/people/${encodeURIComponent(saved.username)}`;
      else viewPerson(el, username);
    } catch (e) { toast(e.message, "error"); }
  };

  const setPassword = async () => {
    const ok = await modal({
      title: `A temporary password for ${person.display_name}?`,
      body: html`<p>Pinax will make one up and show it to you once. ${person.display_name} must change it when they sign in.</p>
        <p class="small muted">${person.has_password ? "Their current password stops working, their sessions end, and a locked account is unlocked." : "Until then they cannot sign in."}</p>`,
      confirm: "Make a temporary password",
    });
    if (!ok) return;
    try {
      const r = await api(`admin/people/${encodeURIComponent(username)}/password`, { method: "POST" });
      await modal({
        title: `Temporary password for ${person.display_name}`,
        body: html`<p class="small">Give it to them privately. It is not shown again; you can make a new one at any time.</p>
          <div class="secret" id="temp-pass">${r.temporary_password}</div>
          <p class="small muted">Username: <span class="mono">${person.username}</span></p>`,
        confirm: "Copy and close", cancel: "Close",
        onConfirm: async () => { await navigator.clipboard?.writeText(r.temporary_password).catch(() => {}); toast("Copied"); },
      });
      viewPerson(el, username);
    } catch (e) { toast(e.message, "error"); }
  };

  const change = async (what) => {
    const kept = [...person.labs.map((l) => `${l.name} (${l.role === "pi" ? (l.kind === "department" ? "owner" : "PI") : "delegate"})`),
      ...(person.is_admin ? ["storage administrator"] : [])];
    const ok = await modal({
      title: `${what === "deactivate" ? "Deactivate" : "Reactivate"} ${person.display_name}?`,
      body: what === "deactivate"
        ? html`<p>${person.display_name} will no longer be able to sign in.</p><p class="small muted">Their access is kept, unused${kept.length ? `: ${kept.join(", ")}` : ""}. Reactivating them restores it.</p>`
        : html`<p>${person.display_name} will be able to sign in again, with ${kept.length ? `this access: ${kept.join(", ")}` : "no lab access until you add some"}.</p>`,
      confirm: what === "deactivate" ? "Deactivate" : "Reactivate", tone: "navy",
    });
    if (!ok) return;
    try {
      await api(`admin/people/${encodeURIComponent(username)}/${what}`, { method: "POST" });
      toast(`${person.display_name} ${what}d`);
      state.users = null;
      viewPerson(el, username);
    } catch (e) { toast(e.message, "error"); }
  };

  render();
}

async function labsTab(el) {
  const [labs, people] = await Promise.all([api("admin/labs"), api("admin/people")]);
  const render = () => mount(el, html`
    <p class="small muted" style="margin-top:-6px">Each lab or department sees the top-level folders it owns. Map folders on <a href="#/folders">Folders and labs</a>.</p>
    <div class="grid cols-2">${[...labs].sort((a, b) => b.active - a.active).map((l) => html`<div class="card"><div class="card-head">
      <h2><a class="title-cell" href="#/access/labs/${encodeURIComponent(l.slug)}">${l.name}</a></h2>
      <span class="row" style="gap:6px">${l.active ? "" : statusChip(false)}<span class="tag">${kindLabel(l.kind)}</span></span></div>
      <p class="small muted" style="margin-top:-8px">${[l.division, l.research_area].filter(Boolean).join(" · ") || "No division or research area"}</p>
      <h3 style="margin:10px 0 6px">Folders</h3>
      ${l.folders.length ? html`<ul class="feed">${l.folders.map((f) => html`<li><span class="path">${displayPath(f.filesystem, f.path)}</span></li>`)}</ul>` : html`<p class="small muted">None mapped.</p>`}
      <h3 style="margin:10px 0 6px">Members</h3>
      ${l.members.length ? html`<ul class="feed">${l.members.map((m) => html`<li><span class="avatar sm">${m.initials}</span><span style="flex:1">${m.display_name}<span class="muted small"> · ${m.role === "pi" ? (l.kind === "department" ? "Owner" : "PI") : "Delegate"}${m.active ? "" : " · inactive"}</span></span>
        ${l.active ? html`<button class="icon-btn admin-only" title="Remove ${m.display_name}" data-remove="${l.slug}|${m.username}">${icon("x")}</button>` : ""}</li>`)}</ul>` : html`<p class="small muted">None yet.</p>`}
      ${l.active ? html`<div class="row admin-only" style="margin-top:10px"><select class="select sm" data-add-user="${l.slug}" aria-label="Person to add to ${l.name}">${opt("", "Add a person…", "")}
        ${people.filter((p) => p.active && !p.is_admin && !l.members.some((m) => m.username === p.username)).map((p) => opt(p.username, p.display_name, ""))}</select>
        <select class="select sm" data-add-role="${l.slug}" aria-label="Role">${opt("delegate", "Delegate", "delegate")}${opt("pi", l.kind === "department" ? "Owner" : "PI", "")}</select>
        <button class="btn btn-ghost btn-sm" data-add="${l.slug}">${icon("plus")}Add</button></div>`
        : html`<p class="small muted" style="margin-top:10px">Inactive: its members do not see its folders.<span class="admin-only"> <a href="#/access/labs/${encodeURIComponent(l.slug)}">Reactivate it</a> to change it.</span></p>`}
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

// ---------- A lab or department ----------
export async function viewLab(el, slug) {
  state.place = null;
  renderSidebar();
  const creating = slug === "new";
  const lab = creating ? null : await api(`admin/labs/${encodeURIComponent(slug)}`);
  const form = lab ? { name: lab.name, kind: lab.kind, division: lab.division, research_area: lab.research_area }
    : { slug: "", name: "", kind: "lab", division: "", research_area: "" };
  const inactive = lab && !lab.active;
  const locked = inactive || !state.me.is_admin;  // auditors read it
  const off = (cond) => (cond ? raw("disabled") : "");
  const role = (m) => (m.role === "pi" ? (form.kind === "department" ? "Owner" : "PI") : "Delegate");

  const render = () => {
    mount(el, html`
      <div class="page-head"><div><div class="eyebrow"><a href="#/access?tab=labs">${icon("arrowLeft")} People and access</a></div>
        <h1>${creating ? "Add lab or department" : lab.name}</h1>
        ${lab ? html`<p>${kindLabel(lab.kind)} · <span class="mono">${lab.slug}</span></p>`
          : html`<p>A lab or department owns top-level folders: its PI or owner and its delegates see them, and it is charged for them.</p>`}</div>
        ${lab ? statusChip(lab.active) : ""}</div>
      ${inactive ? html`<div class="alert alert-warn">${icon("alert")}<div><b>The ${lab.name} is deactivated.</b>
        <p>Its members do not see its folders. Its members and folders are kept, and its folders are still charged to it until they are moved or unmapped on <a href="#/folders">Folders and labs</a>. Reactivate it to restore its members' access, or to make changes.</p>
        <button class="btn btn-primary btn-sm admin-only" id="reactivate" style="margin-top:8px">${icon("refresh")}Reactivate</button></div></div>` : ""}
      <div class="grid split">
        <div class="stack">
          <div class="card"><div class="card-head"><h2>Details</h2></div>
            <div class="grid cols-2">
              <div class="field"><label for="l-slug">Slug</label>
                ${creating ? html`<input class="input mono" id="l-slug" data-field="slug" value="${form.slug}" placeholder="e.g. ortiz_v" autocomplete="off" spellcheck="false">
                  <span class="hint">Lowercase letters, digits, - and _. Links and import files use it, so it cannot be changed later.</span>`
                  : html`<div class="input mono readonly">${lab.slug}</div><span class="hint">Links and import files use it; it cannot be changed.</span>`}</div>
              <div class="field"><label for="l-name">Name</label><input class="input" id="l-name" data-field="name" value="${form.name}" placeholder="e.g. Ortiz Lab" ${off(locked)}></div>
              <div class="field"><label for="l-kind">Kind</label><select class="select" id="l-kind" data-field="kind" ${off(locked)}>
                ${opt("lab", "Lab", form.kind)}${opt("department", "Department", form.kind)}</select>
                <span class="hint">Labs and departments can have different free tiers on <a href="#/costs">Costs</a>.</span></div>
              <div class="field"><label for="l-division">Division</label><input class="input" id="l-division" data-field="division" value="${form.division}" placeholder="e.g. Public Health Sciences" ${off(locked)}></div>
              <div class="field"><label for="l-area">Research area</label><input class="input" id="l-area" data-field="research_area" value="${form.research_area}" placeholder="e.g. Biostatistics" ${off(locked)}></div>
            </div></div>
          ${locked ? "" : html`<div class="row between"><a class="btn btn-ghost" href="#/access?tab=labs">Cancel</a>
            <button class="btn btn-primary" id="save">${icon("check")}${creating ? "Add" : "Save changes"}</button></div>`}
        </div>
        <aside class="stack">
          ${lab ? html`<div class="card"><h3>Members</h3>
            ${lab.members.length ? html`<ul class="feed" style="margin-top:6px">${lab.members.map((m) => html`<li style="padding:8px 0"><span class="avatar sm">${m.initials}</span>
              <span style="flex:1"><a href="#/access/people/${encodeURIComponent(m.username)}">${m.display_name}</a><span class="muted small"> · ${role(m)}${m.active ? "" : " · inactive"}</span></span></li>`)}</ul>`
              : html`<p class="small muted" style="margin-top:8px">None yet. Add members on <a href="#/access?tab=labs">Labs and departments</a> or on a person's page.</p>`}</div>
          <div class="card"><h3>Folders</h3>
            ${lab.folders.length ? html`<ul class="feed" style="margin-top:6px">${lab.folders.map((f) => html`<li style="padding:8px 0">${icon("folder")}<span class="mono">${displayPath(f.filesystem, f.path)}</span></li>`)}</ul>`
              : html`<p class="small muted" style="margin-top:8px">None mapped.</p>`}
            <a class="small" href="#/folders?lab=${encodeURIComponent(lab.slug)}">${lab.folders.length ? "Change" : "Map folders"} on Folders and labs</a></div>
          <div class="card"><h3>Record</h3><dl class="kv" style="margin-top:10px"><dt>Added</dt><dd>${date(lab.created_at)}</dd></dl>
            ${lab.history.length ? html`<div class="row between" style="margin-top:14px"><h3>Recent changes</h3><a class="small" href="#/audit?lab=${encodeURIComponent(lab.slug)}">All</a></div><ul class="feed">${lab.history.map((h) => html`<li style="padding:6px 0"><span><span class="small">${h.summary}</span><br>
              <span class="tiny muted">${date(h.ts, true)}${h.by ? ` · ${h.by}` : ""}${h.token ? ` · with the token “${h.token}”` : ""}</span></span></li>`)}</ul>` : ""}</div>
          ${lab.active ? html`<div class="card admin-only"><h3>Deactivate</h3>
            <p class="small muted" style="margin-top:8px">Its members will no longer see its folders. Its members and folders are kept, unused, and come back if you reactivate it; its folders are still charged to it until you move or unmap them. Nothing is deleted.</p>
            <button class="btn btn-warn btn-sm" id="deactivate">${icon("lock")}Deactivate</button></div>` : ""}`
          : html`<div class="card"><h3>Next</h3><p class="small muted" style="margin-top:8px">Once it is added, give it members (a PI or owner, and delegates) on its card in
            <i>Labs and departments</i> or on each person's page, and its top-level folders on <i>Folders and labs</i>.</p></div>`}
        </aside>
      </div>`);
    bind();
  };

  const bind = () => {
    $$("[data-field]", el).forEach((inp) => inp.addEventListener(inp.tagName === "SELECT" ? "change" : "input", () => { form[inp.dataset.field] = inp.value; }));
    $("#save", el)?.addEventListener("click", save);
    $("#deactivate", el)?.addEventListener("click", () => change("deactivate"));
    $("#reactivate", el)?.addEventListener("click", () => change("reactivate"));
  };

  const save = async () => {
    if (lab && form.kind !== lab.kind) {
      const ok = await modal({
        title: `Make the ${lab.name} a ${form.kind}?`,
        body: html`<p>It will have the free tier for ${form.kind === "department" ? "departments" : "labs"} on each filesystem, unless it has its own.</p>
          <p class="small muted">Charges are worked out from the kind a lab has now, so its past months' charges change too.</p>`,
        confirm: `Make it a ${form.kind}`, tone: "navy",
      });
      if (!ok) return;
    }
    try {
      const saved = await api(creating ? "admin/labs" : `admin/labs/${encodeURIComponent(slug)}`, { method: creating ? "POST" : "PUT", body: form });
      toast(creating ? `${saved.name} added` : "Changes saved");
      if (creating) location.hash = `#/access/labs/${encodeURIComponent(saved.slug)}`;
      else viewLab(el, slug);
    } catch (e) { toast(e.message, "error"); }
  };

  const change = async (what) => {
    const n = lab.members.length;
    const ok = await modal({
      title: `${what === "deactivate" ? "Deactivate" : "Reactivate"} the ${lab.name}?`,
      body: what === "deactivate"
        ? html`<p>Its ${n ? `${num(n)} member${n === 1 ? "" : "s"}` : "members"} will no longer see its folders through it.</p>
          <p class="small muted">Its members and folders are kept. Its folders are still charged to it until you move or unmap them on Folders and labs.</p>`
        : html`<p>Its ${n ? `${num(n)} member${n === 1 ? "" : "s"}` : "members"} will see its folders again.</p>`,
      confirm: what === "deactivate" ? "Deactivate" : "Reactivate", tone: "navy",
    });
    if (!ok) return;
    try {
      await api(`admin/labs/${encodeURIComponent(slug)}/${what}`, { method: "POST" });
      toast(`${lab.name} ${what}d`);
      viewLab(el, slug);
    } catch (e) { toast(e.message, "error"); }
  };

  render();
}
