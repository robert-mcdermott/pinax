// SPDX-License-Identifier: Apache-2.0
// Reports over one place (or the person's home): ages, file types, cold folders, owners, and file search.
import {
  $, $$, ageText, api, bytes, date, displayPath, emptyState, html, icon, mount, num, parseQuery, pct, placeHash,
  placeQuery, raw, state,
} from "/js/core.js";
import { scopeHeader } from "/js/shell.js";
import {
  ageBar, ageHistogram, bars, buckets, catLabel, categories, coldBytes, daysSince, thresholdText,
} from "/js/viz.js";

const placeFrom = (q) => (q.fs ? { fs: q.fs, path: q.path } : null);
const labOf = (q) => (q.lab ? { lab: q.lab } : {});
const opt = (value, label, current) => html`<option value="${value}" ${String(value) === String(current ?? "") ? raw("selected") : ""}>${label}</option>`;

// ---------- Age report ----------
export async function viewAges(el, query) {
  const q = parseQuery(query);
  const place = placeFrom(q);
  const data = await api("tree", { query: { ...placeQuery(place), depth: 1, ...labOf(q) } });
  const s = data.node.stats;
  const cb = data.cold_bucket;
  const cold = coldBytes(s, cb);
  const notRead = (s.atime_bytes || []).slice(2).reduce((t, v) => t + v, 0);
  const rows = [...data.tiles].sort((a, b) => b.stats.bytes - a.stats.bytes);
  const href = (t) => (t.kind === "dir" || t.kind === "fs" ? placeHash("ages", { fs: t.fs, path: t.path }) : null);
  mount(el, html`
    ${scopeHeader("ages", place, { asOf: data.as_of, lab: !place ? data.node.lab : null })}
    <div class="kpis">
      <div class="kpi"><div class="label">Size</div><div class="value">${bytes(s.bytes)}</div><div class="sub">${num(s.files)} files</div></div>
      <div class="kpi" style="--kpi-accent:var(--age-4)"><div class="label">Average age</div><div class="value">${ageText(s.mean_age_days)}</div><div class="sub">Since last change, weighted by size</div></div>
      <div class="kpi" style="--kpi-accent:var(--age-5)"><div class="label">Unchanged for ${thresholdText(cb)}+</div><div class="value">${bytes(cold)}</div><div class="sub">${pct(s.bytes ? (cold / s.bytes) * 100 : 0)} of the data</div></div>
      <div class="kpi" style="--kpi-accent:var(--age-4)"><div class="label">Not read for a year+</div><div class="value">${bytes(notRead)}</div><div class="sub">By last access time, where recorded</div></div>
      <div class="kpi" style="--kpi-accent:var(--fh-bright-blue)"><div class="label">Last change</div><div class="value">${s.newest ? ageText(daysSince(s.newest, data.as_of)) : "—"}</div><div class="sub">${s.newest ? `ago, on ${date(s.newest)}` : "No files"}</div></div>
    </div>
    <div class="grid split">
      <div class="card"><div class="card-head"><h2>Age profile</h2>
        <div class="pills" role="group" aria-label="Measure"><button class="active" aria-pressed="true" data-m="age_bytes">Size by last change</button>
          <button data-m="age_files" aria-pressed="false">Files by last change</button><button data-m="atime_bytes" aria-pressed="false">Size by last access</button></div></div>
        <div id="hist">${ageHistogram(s.age_bytes)}</div></div>
      <div class="card"><div class="card-head"><h2>Reading ages</h2></div>
        <p class="small">Ages are measured back from the scan, using each file's <b>last change</b> (its modification time) unless noted.
          Copies and restores can keep an old modification time, so a recently copied folder can still look old.</p>
        <p class="small"><b>Last access</b> is only as good as the filesystem's settings: many storage systems update it rarely or never.
          Treat it as a hint that data is in use, not proof that it isn't.</p>
        <p class="small" style="margin:0"><b>Average age</b> is weighted by size, so one large old file counts for more than many small new ones.</p></div>
    </div>
    <div class="card flush" style="margin-top:20px"><div class="card-head"><h2>By folder</h2><span class="small muted">Largest first</span></div>
      <div class="table-wrap"><table class="table"><thead><tr><th>Folder</th><th class="num">Size</th><th class="num">Average age</th><th>Last change</th><th>Oldest file</th><th style="width:34%">Age profile (size)</th></tr></thead><tbody>
      ${rows.map((t) => html`<tr><td>${href(t) ? html`<a class="rowlink" href="${href(t)}">${t.name}</a>` : html`<span class="title-cell">${t.name}</span>`}
          ${t.lab ? html`<div class="sub">${t.lab.name}</div>` : ""}</td>
        <td class="num nowrap">${bytes(t.stats.bytes)}</td><td class="num nowrap">${ageText(t.stats.mean_age_days)}</td>
        <td class="nowrap">${t.stats.newest ? date(t.stats.newest) : "—"}</td><td class="nowrap">${t.stats.oldest ? date(t.stats.oldest) : "—"}</td>
        <td>${ageBar(t.stats.age_bytes, { large: true })}</td></tr>`)}
      </tbody></table></div>
      <div class="card-foot">${buckets().map((b, i) => html`<span class="nowrap" style="margin-right:14px"><i class="swatch" style="display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-1px;background:var(--age-${i})"></i> ${b.label}</span>`)}</div></div>`);
  $$("[data-m]", el).forEach((b) => b.addEventListener("click", () => {
    $$("[data-m]", el).forEach((x) => { x.classList.toggle("active", x === b); x.setAttribute("aria-pressed", String(x === b)); });
    mount($("#hist", el), ageHistogram(s[b.dataset.m], { unit: b.dataset.m === "age_files" ? "files" : "bytes" }));
  }));
}

// ---------- File types ----------
const UNCOMPRESSED_SEQ = ["fastq", "fq", "sam", "vcf", "fasta", "fa", "fna", "gvcf", "bedgraph", "pileup", "mpileup"];

export async function viewTypes(el, query) {
  const q = parseQuery(query);
  const place = placeFrom(q);
  const data = await api("types", { query: { ...placeQuery(place), ...labOf(q) } });
  const total = data.categories.reduce((t, c) => t + c.bytes, 0);
  const cats = data.categories.filter((c) => c.files > 0).sort((a, b) => b.bytes - a.bytes);
  const shown = q.cat ? data.types.filter((t) => t.category === q.cat) : data.types;
  const raw_ = data.types.filter((t) => UNCOMPRESSED_SEQ.includes(t.ext));
  const rawBytes = raw_.reduce((t, r) => t + r.bytes, 0);
  const extra = labOf(q);
  let sortKey = "bytes";
  const table = () => {
    const rows = [...shown].sort((a, b) => sortKey === "ext" ? String(a.ext).localeCompare(String(b.ext))
      : sortKey === "age" ? (b.mean_age_days || 0) - (a.mean_age_days || 0) : b[sortKey] - a[sortKey]);
    const th = (k, label, cls = "") => html`<th class="${cls}"><button class="sort-btn ${sortKey === k ? "on" : ""}" data-sort="${k}">${label}${sortKey === k ? icon("chevronDown") : ""}</button></th>`;
    return html`<table class="table compact"><thead><tr>${th("ext", "Type")}<th>Category</th>${th("files", "Files", "num")}${th("bytes", "Size", "num")}
        <th class="num">Average file</th>${th("age", "Average age", "num")}<th>Last change</th></tr></thead><tbody>
      ${rows.slice(0, 400).map((t) => html`<tr>
        <td><a class="rowlink" href="${placeHash("files", place, { ...extra, ext: t.ext || "", sort: "size" })}">${t.ext ? `.${t.ext}` : "(no extension)"}</a></td>
        <td>${catLabel(t.category)}</td><td class="num">${num(t.files)}</td><td class="num nowrap">${bytes(t.bytes)}</td>
        <td class="num nowrap">${bytes(t.files ? t.bytes / t.files : 0)}</td><td class="num nowrap">${ageText(t.mean_age_days)}</td>
        <td class="nowrap">${t.newest ? date(t.newest) : "—"}</td></tr>`)}</tbody></table>
      ${rows.length > 400 ? html`<p class="small muted" style="padding:10px 14px">Showing the first 400 of ${num(rows.length)} types.</p>` : ""}`;
  };
  mount(el, html`
    ${scopeHeader("types", place, { asOf: data.as_of })}
    <div class="grid split">
      <div class="card"><div class="card-head"><h2>By category</h2><span class="small muted">${bytes(total)} in ${num(data.types.length)} file types</span></div>
        ${cats.length ? bars(cats, { label: (c) => c.label, text: (c) => `${bytes(c.bytes)} · ${pct(total ? (c.bytes / total) * 100 : 0)}`,
          href: (c) => placeHash("types", place, { ...extra, cat: c.key }) }) : html`<p class="muted">No files.</p>`}
        <p class="small muted" style="margin:12px 0 0">A file's type is its extension; compressed files count as what they contain, so
          <code>.fastq.gz</code> is sequencing data. Select a category to list its types.</p></div>
      <div class="stack">
        ${rawBytes ? html`<div class="card"><div class="card-head"><h2>Uncompressed sequencing data</h2></div>
          <p><b>${bytes(rawBytes)}</b> is stored as uncompressed ${raw_.map((r) => `.${r.ext}`).join(", ")}.
            Gzipped FASTQ, BAM or CRAM instead of SAM, and bgzipped VCF are usually several times smaller, and every common tool reads them.</p>
          ${bars(raw_.slice(0, 6), { label: (r) => `.${r.ext}`, href: (r) => placeHash("files", place, { ...extra, ext: r.ext, sort: "size" }) })}</div>` : ""}
        <div class="card"><div class="card-head"><h2>Categories</h2></div>
          <dl class="kv">${categories().map((c) => html`<dt>${c.label}</dt><dd class="small muted">${c.description}</dd>`)}</dl></div>
      </div>
    </div>
    <div class="card flush" style="margin-top:20px"><div class="card-head"><h2>${q.cat ? catLabel(q.cat) : "All file types"}</h2>
      ${q.cat ? html`<a class="btn btn-ghost btn-sm" href="${placeHash("types", place, extra)}">${icon("x")}Show all categories</a>` : ""}</div>
      <div class="table-wrap" id="types-table">${table()}</div></div>`);
  $("#types-table", el).addEventListener("click", (e) => {
    const b = e.target.closest("[data-sort]");
    if (!b) return;
    sortKey = b.dataset.sort;
    mount($("#types-table", el), table());
  });
}

// ---------- Cold data ----------
const SIZE_FLOORS = [[0, "Any size"], [1e9, "1 GB or more"], [1e11, "100 GB or more"], [1e12, "1 TB or more"]];

export async function viewCold(el, query) {
  const q = parseQuery(query);
  const place = placeFrom(q);
  const cfg = state.config;
  const older = Number(q.older || cfg.cold_bucket);
  const minBytes = Number(q.min || 0);
  const data = await api("cold", { query: { ...placeQuery(place), older, min_bytes: minBytes, ...labOf(q) } });
  const extra = labOf(q);
  const go = (changes) => { location.hash = placeHash("cold", place, { ...extra, older, min: minBytes || undefined, ...changes }); };
  mount(el, html`
    ${scopeHeader("cold", place, { asOf: data.as_of })}
    <div class="toolbar">
      <label class="ctl"><span>Nothing changed for</span><select class="select sm" id="cold-older">
        ${buckets().slice(1).map((_, i) => opt(i + 1, `${thresholdText(i + 1)} or more`, older))}</select></label>
      <label class="ctl"><span>Folder size</span><select class="select sm" id="cold-min">${SIZE_FLOORS.map(([v, l]) => opt(v, l, minBytes))}</select></label>
      <div class="grow"></div>
      <button class="btn btn-ghost btn-sm" id="cold-csv" ${data.dirs.length ? "" : raw("disabled")}>${icon("download")}Download CSV</button>
    </div>
    <div class="kpis">
      <div class="kpi" style="--kpi-accent:var(--age-5)"><div class="label">In cold folders</div><div class="value">${bytes(data.bytes)}</div><div class="sub">${num(data.count)} folders, ${num(data.files)} files</div></div>
      <div class="kpi" style="--kpi-accent:var(--age-3)"><div class="label">All files unchanged for ${thresholdText(older)}+</div><div class="value">${bytes(data.cold_file_bytes)}</div><div class="sub">${pct(data.scope_bytes ? (data.cold_file_bytes / data.scope_bytes) * 100 : 0)} of ${bytes(data.scope_bytes)}, including files in active folders</div></div>
    </div>
    <div class="alert alert-info">${icon("info")}<div><b>What makes a folder cold.</b>
      <p>No file anywhere in it has changed for ${thresholdText(older)} or more, while its parent folder has newer changes. Each folder listed is
        a self-contained unit that could be archived as a whole; folders inside it are not listed separately.</p></div></div>
    <div class="card flush">${data.dirs.length ? html`<div class="table-wrap"><table class="table"><thead><tr>
        <th>Folder</th><th class="num">Size</th><th class="num">Files</th><th>Last change</th><th>Oldest file</th><th class="t-age">Age profile</th></tr></thead><tbody>
      ${data.dirs.map((d) => html`<tr>
        <td><a class="rowlink" href="${placeHash("explore", { fs: d.fs, path: d.path })}">${d.name}</a>
          <div class="sub path">${displayPath(d.fs, d.path)}</div>${d.lab ? html`<div class="sub">${d.lab.name}</div>` : ""}</td>
        <td class="num nowrap">${bytes(d.stats.bytes)}</td><td class="num">${num(d.stats.files)}</td>
        <td class="nowrap">${date(d.stats.newest)}<div class="sub">${ageText(daysSince(d.stats.newest, data.as_of))} ago</div></td>
        <td class="nowrap">${date(d.stats.oldest)}</td><td>${ageBar(d.stats.age_bytes)}</td></tr>`)}
      </tbody></table></div>${data.count > data.dirs.length ? html`<div class="card-foot">Showing the largest ${num(data.dirs.length)} of ${num(data.count)} cold folders.</div>` : ""}`
      : emptyState("snowflake", "No cold folders", `Every folder here has had a change within ${thresholdText(older)}${minBytes ? ", or is smaller than the size chosen" : ""}.`)}</div>`);
  $("#cold-older", el).addEventListener("change", (e) => go({ older: e.target.value }));
  $("#cold-min", el).addEventListener("change", (e) => go({ min: e.target.value || undefined }));
  $("#cold-csv", el)?.addEventListener("click", () => downloadCsv("pinax-cold-folders.csv",
    ["filesystem", "path", "bytes", "files", "last_change", "oldest_file", "lab"],
    data.dirs.map((d) => [d.fs, displayPath(d.fs, d.path), d.stats.bytes, d.stats.files, d.stats.newest, d.stats.oldest, d.lab?.name || ""])));
}

export function downloadCsv(name, header, rows) {
  const cell = (v) => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const text = [header, ...rows].map((r) => r.map(cell).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- Owners ----------
export async function viewOwners(el, query) {
  const q = parseQuery(query);
  const place = placeFrom(q);
  const data = await api("owners", { query: { ...placeQuery(place), ...labOf(q) } });
  const total = data.owners.reduce((t, o) => t + o.bytes, 0);
  const unknown = data.owners.filter((o) => !o.known);
  const unknownBytes = unknown.reduce((t, o) => t + o.bytes, 0);
  const extra = labOf(q);
  const who = (o) => (o.known ? o.name : `uid ${o.uid}`);
  mount(el, html`
    ${scopeHeader("owners", place, { asOf: data.as_of })}
    ${unknownBytes ? html`<div class="alert alert-warn">${icon("alert")}<div><b>${bytes(unknownBytes)} belongs to ${unknown.length} account${unknown.length === 1 ? "" : "s"} that no longer exist${unknown.length === 1 ? "s" : ""}.</b>
      <p>Usually these are people who have left. Decide who now looks after their data, and ask Research Data Services to change the owner.</p></div></div>` : ""}
    <div class="grid split">
      <div class="card flush"><div class="card-head"><h2>Accounts</h2><span class="small muted">${num(data.owners.length)} accounts own files here</span></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Account</th><th class="num">Size</th><th class="num">Share</th><th class="num">Files</th><th class="num">Average age</th><th>Last change</th></tr></thead><tbody>
        ${data.owners.map((o) => html`<tr>
          <td><a class="rowlink" href="${placeHash("files", place, { ...extra, uid: o.uid, sort: "size" })}">${who(o)}</a>
            ${o.known ? "" : html` <span class="badge badge-requested">${icon("alert")}No account</span>`}</td>
          <td class="num nowrap">${bytes(o.bytes)}</td><td class="num">${pct(total ? (o.bytes / total) * 100 : 0)}</td><td class="num">${num(o.files)}</td>
          <td class="num nowrap">${ageText(o.mean_age_days)}</td><td class="nowrap">${o.newest ? date(o.newest) : "—"}</td></tr>`)}</tbody></table></div></div>
      <div class="card"><div class="card-head"><h2>Largest owners</h2></div>
        ${bars(data.owners.slice(0, 10), { label: who, text: (o) => bytes(o.bytes) })}
        <p class="small muted" style="margin:12px 0 0">Owners are the accounts recorded on each file. Names are looked up when the scan runs;
          an account without a name has been deleted from the directory.</p></div>
    </div>`);
}

// ---------- Find files ----------
const MIN_SIZES = [["", "Any size"], [1e6, "1 MB or more"], [1e8, "100 MB or more"], [1e9, "1 GB or more"], [1e10, "10 GB or more"], [1e11, "100 GB or more"]];
const SORTS = [["size", "Largest first"], ["oldest", "Oldest change first"], ["newest", "Newest change first"], ["accessed", "Least recently read"], ["path", "Path"]];

export async function viewFiles(el, query) {
  const q = parseQuery(query);
  const place = placeFrom(q);
  const sort = q.sort || "size";
  const params = { ...placeQuery(place), ...labOf(q), q: q.q, in_path: q.in_path, category: q.category, ext: q.ext,
    min_size: q.min_size, older: q.older, newer: q.newer, uid: q.uid, direct: q.direct, sort };
  const data = await api("files", { query: { ...params, limit: state.config.search_max_rows } });
  const age = q.older ? `older:${q.older}` : q.newer ? `newer:${q.newer}` : "";
  const csv = `/api/files?${new URLSearchParams(Object.entries({ ...params, fmt: "csv", limit: state.config.export_max_rows })
    .filter(([, v]) => v !== undefined && v !== null && v !== ""))}`;
  const filters = [q.uid && `owned by uid ${q.uid}`, q.direct && "directly in this folder"].filter(Boolean);
  mount(el, html`
    ${scopeHeader("files", place, { asOf: data.as_of })}
    <form class="card" id="search" style="margin-bottom:20px">
      <div class="toolbar" style="margin:0">
        <label class="ctl grow"><span>Name</span><div class="search-input">${icon("search")}<input class="input" name="q" value="${q.q || ""}" placeholder="Part of a name, or a pattern such as *.bam or sample_??_R1*" autocomplete="off"></div></label>
        <label class="ctl"><span>Category</span><select class="select sm" name="category">${opt("", "Any", q.category)}${categories().map((c) => opt(c.key, c.label, q.category))}</select></label>
        <label class="ctl"><span>Type</span><input class="input sm" name="ext" value="${q.ext || ""}" placeholder="bam, fastq.gz"></label>
        <label class="ctl"><span>Size</span><select class="select sm" name="min_size">${MIN_SIZES.map(([v, l]) => opt(v, l, q.min_size))}</select></label>
        <label class="ctl"><span>Last change</span><select class="select sm" name="age">${opt("", "Any time", age)}
          ${buckets().slice(1).map((_, i) => opt(`newer:${i + 1}`, `Within ${thresholdText(i + 1)}`, age))}
          ${buckets().slice(1).map((_, i) => opt(`older:${i + 1}`, `${thresholdText(i + 1)} or more ago`, age))}</select></label>
        <label class="ctl"><span>Order</span><select class="select sm" name="sort">${SORTS.map(([v, l]) => opt(v, l, sort))}</select></label>
      </div>
      <div class="row between" style="margin-top:12px">
        <label class="small"><input type="checkbox" name="in_path" value="true" ${q.in_path ? raw("checked") : ""}> Match anywhere in the path, not just the name</label>
        <div class="actions">${filters.length ? html`<span class="pill-note">${filters.join(" · ")} <a href="${placeHash("files", place, labOf(q))}">Clear</a></span>` : ""}
          <button class="btn btn-primary btn-sm" type="submit">${icon("search")}Search</button></div>
      </div>
    </form>
    <div class="card flush"><div class="card-head"><h2>${num(data.total.files)} file${data.total.files === 1 ? "" : "s"} · ${bytes(data.total.bytes)}</h2>
      <a class="btn btn-ghost btn-sm" href="${csv}">${icon("download")}Download CSV</a></div>
      ${data.files.length ? html`<div class="table-wrap"><table class="table compact"><thead><tr><th>File</th><th class="num">Size</th><th>Last change</th><th>Last access</th><th>Type</th><th>Owner</th></tr></thead><tbody>
        ${data.files.map((f) => html`<tr><td><span class="title-cell">${f.name}</span><div class="sub path">${displayPath(f.fs, f.path)}</div></td>
          <td class="num nowrap">${bytes(f.size)}</td><td class="nowrap">${f.mtime ? date(f.mtime) : "—"}</td><td class="nowrap">${f.atime ? date(f.atime) : "—"}</td>
          <td class="nowrap">${f.ext ? `.${f.ext}` : "—"}<div class="sub">${catLabel(f.category)}</div></td>
          <td class="nowrap">${f.owner || html`uid ${f.uid}`}</td></tr>`)}</tbody></table></div>
        ${data.total.files > data.files.length ? html`<div class="card-foot">Showing the first ${num(data.files.length)}. Download the CSV for up to ${num(state.config.export_max_rows)}.</div>` : ""}`
        : emptyState("search", "No files match", "Try a shorter name, a wider age range, or another folder.")}</div>`);
  $("#search", el).addEventListener("submit", (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const [kind, n] = String(f.get("age") || "").split(":");
    location.hash = placeHash("files", place, {
      ...labOf(q), q: f.get("q"), category: f.get("category"), ext: f.get("ext"), min_size: f.get("min_size"),
      older: kind === "older" ? n : undefined, newer: kind === "newer" ? n : undefined, sort: f.get("sort"),
      in_path: f.get("in_path") ? "true" : undefined, uid: q.uid, direct: q.direct,
    });
  });
}

