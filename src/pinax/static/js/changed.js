// SPDX-License-Identifier: Apache-2.0
// What changed: for a folder and two scans, where it grew and shrank, the folders that appeared and went, and the
// largest files that did, each with a CSV of the full list. The scans are chosen as on the change map.
import { $, ageText, api, bytes, date, displayPath, emptyState, fsInfo, html, icon, mount, num, parseQuery, pct, placeHash, placeQuery, state } from "/js/core.js";
import { sinceSelect } from "/js/changes.js";
import { scopeHeader } from "/js/shell.js";

const signed = (v) => (v ? `${v > 0 ? "+" : "−"}${bytes(Math.abs(v))}` : "No change");
const size = (s) => (s ? s.bytes : 0);

export async function viewChanged(el, query) {
  const q = parseQuery(query);
  const place = q.fs ? { fs: q.fs, path: q.path } : null;
  const params = { ...placeQuery(place), since: q.since, lab: place ? undefined : q.lab };
  const data = await api("changed", { query: params });
  const node = data.node;
  const head = scopeHeader("changed", place, { asOf: data.as_of, lab: node.lab && !place ? node.lab : null,
    lede: place ? "" : "Every folder, compared with an earlier scan." });
  if (!data.compared.length) {
    mount(el, html`${head}<div class="card">${emptyState("list", "Nothing to compare with yet",
      "What changed compares this scan with an earlier one whose index is kept. There is only one scan here so far.")}</div>`);
    return;
  }
  const since = data.compared.map((c) => c.started_at).sort()[0];
  const csv = (list) => `/api/changed?${new URLSearchParams(Object.entries({ ...params, fmt: "csv", list, limit: state.config.export_max_rows })
    .filter(([, v]) => v !== undefined && v !== null && v !== ""))}`;
  // Paths read from the folder asked about; the change map opens at any of them, removed ones included.
  const base = place ? `${place.path || fsInfo(place.fs)?.root}/` : null;
  const rel = (r) => (base && r.path.startsWith(base) ? r.path.slice(base.length) : displayPath(r.fs, r.path));
  const mapAt = (r) => placeHash("changes", { fs: r.fs, path: r.path }, { since: q.since });
  const d = size(node.now) - size(node.then);
  const c = data.counts, f = data.files;
  const n = data.limit;

  const folders = (rows, { key, cols, empty }) => html`<div class="table-wrap"><table class="table compact"><thead><tr>
      <th>Folder</th>${cols.map(([label]) => html`<th class="num">${label}</th>`)}</tr></thead><tbody>
      ${rows.length ? rows.map((r) => html`<tr><td><a class="rowlink" href="${mapAt(r)}">${rel(r)}</a></td>
        ${cols.map(([, cell]) => html`<td class="num nowrap">${cell(r)}</td>`)}</tr>`)
        : html`<tr><td colspan="${cols.length + 1}" class="muted">${empty}</td></tr>`}</tbody></table></div>
    <div class="card-foot"><a href="${csv(key)}" download>${icon("download")} Download CSV</a></div>`;
  const filesTable = (key, empty) => html`<div class="table-wrap"><table class="table compact"><thead><tr>
      <th>File</th><th class="num">Size</th><th>Last change</th><th>Owner</th></tr></thead><tbody>
      ${data[key].length ? data[key].map((r) => html`<tr><td><span class="title-cell">${r.name}</span><div class="sub path clamp" title="${rel(r)}">${rel(r)}</div></td>
        <td class="num nowrap">${bytes(r.size)}</td><td class="nowrap">${date(r.mtime)}</td><td>${r.owner || html`<span class="muted">uid ${r.uid}</span>`}</td></tr>`)
        : html`<tr><td colspan="4" class="muted">${empty}</td></tr>`}</tbody></table></div>
    <div class="card-foot">${f[key].files > data[key].length ? `The largest ${num(data[key].length)} of ${num(f[key].files)}. ` : ""}<a href="${csv(key)}" download>${icon("download")} Download CSV</a></div>`;
  const card = (title, note, body) => html`<div class="card flush"><div class="card-head"><h2>${title}</h2>${note ? html`<span class="small muted">${note}</span>` : ""}</div>${body}</div>`;

  mount(el, html`
    ${head}
    <div class="toolbar">${sinceSelect(data, q.since)}</div>
    <div class="kpis">
      <div class="kpi" style="--kpi-accent:var(${d < 0 ? "--chg-n2" : "--chg-p2"})"><div class="label">Change in size</div><div class="value">${signed(d)}</div>
        <div class="sub">Since ${date(since)}${size(node.then) ? `, ${d >= 0 ? "+" : "−"}${pct((Math.abs(d) / size(node.then)) * 100)}` : ""}</div></div>
      <div class="kpi" style="--kpi-accent:var(--chg-p3)"><div class="label">New files</div><div class="value">${num(f.new_files.files)}</div><div class="sub">${bytes(f.new_files.bytes)}</div></div>
      <div class="kpi" style="--kpi-accent:var(--chg-n3)"><div class="label">Removed files</div><div class="value">${num(f.removed_files.files)}</div><div class="sub">${bytes(f.removed_files.bytes)}</div></div>
      <div class="kpi" style="--kpi-accent:var(--strong)"><div class="label">Folders</div><div class="value">${num(c.new_dirs || 0)} new</div><div class="sub">${num(c.removed_dirs || 0)} removed</div></div>
    </div>
    <div class="grid">
      ${card("Where it grew", `The ${n} largest`, folders(data.grew, { key: "grew", empty: "Nothing here grew.",
        cols: [["Earlier", (r) => (r.then ? bytes(r.then.bytes) : "—")], ["Now", (r) => bytes(size(r.now))], ["Change", (r) => signed(r.delta)]] }))}
      ${card("Where it shrank", `The ${n} largest`, folders(data.shrank, { key: "shrank", empty: "Nothing here shrank.",
        cols: [["Earlier", (r) => bytes(size(r.then))], ["Now", (r) => (r.now ? bytes(r.now.bytes) : "—")], ["Change", (r) => signed(r.delta)]] }))}
      ${card("New folders", `${num(c.new_tops || 0)} · ${bytes(c.new_bytes || 0)}`, folders(data.new_folders, { key: "new_folders", empty: "No new folders.",
        cols: [["Size", (r) => bytes(size(r.now))], ["Files", (r) => num(r.now?.files)], ["Average age", (r) => ageText(r.now?.mean_age_days)]] }))}
      ${card("Removed folders", `${num(c.removed_tops || 0)} · ${bytes(c.removed_bytes || 0)}`, folders(data.removed_folders, { key: "removed_folders", empty: "No folders were removed.",
        cols: [["Size", (r) => bytes(size(r.then))], ["Files", (r) => num(r.then?.files)]] }))}
      ${card("Largest new files", "", filesTable("new_files", "No new files."))}
      ${card("Largest removed files", "", filesTable("removed_files", "No files were removed."))}
    </div>
    <p class="small muted" style="margin:14px 0 0">Compared with the scan of ${data.compared.map((x) => `${date(x.started_at)}${data.compared.length > 1 ? ` (${x.fs})` : ""}`).join(", ")}.
      <b>Where it grew</b> and <b>where it shrank</b> list folders in both scans, at any depth, leaving out a folder when one of its subfolders holds nine tenths or more of its change, so the change is listed where it happened.
      <b>New</b> and <b>removed folders</b> are the outermost of those in one scan only. Folders and files are matched by path, so a moved or renamed one shows as removed and new.</p>`);

  $("#ctl-since", el).addEventListener("change", (e) => {
    location.hash = placeHash("changed", place, { since: e.target.value || undefined, lab: place ? undefined : q.lab });
  });
}
