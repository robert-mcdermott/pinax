// SPDX-License-Identifier: Apache-2.0
// File categories, for administrators (categories.py): what each file counts as, by its extension and by patterns on
// its name, checked first. Changes apply to indexes built after them; the page says which snapshots were built with
// other categories and how long rebuilding them takes (`pinax rebuild --outdated`), and shows what a change would
// do before it is saved. Auditors read it.
import { $, $$, api, bytes, duration, html, icon, modal, mount, num, toast } from "/js/core.js";
import { renderSidebar } from "/js/shell.js";

const lines = (text) => String(text || "").split(/[\n,]+/).map((s) => s.trim()).filter(Boolean);
const label = (cats, key) => (cats.find((c) => c.key === key) || {}).label || key;

function rebuildNote(r, preview = false) {
  if (!r.outdated) return html`<p class="callout">${icon("check")} <b>Every snapshot ${preview ? "would be" : "is"} built with these categories.</b></p>`;
  const one = r.outdated === 1;
  const which = r.outdated === r.snapshots ? (one ? "the snapshot" : `all ${num(r.snapshots)} snapshots`)
    : `${num(r.outdated)} of the ${num(r.snapshots)} snapshots`;
  const head = preview
    ? html`<b>Saved, this would leave ${which} built with other categories</b>, each keeping its own until rebuilt, and new scans would use these.`
    : html`<b>${which[0].toUpperCase() + which.slice(1)} ${one ? "was" : "were"} built with other categories than these</b>, and
      keep${one ? "s its" : " their"} own until rebuilt; new scans use these.`;
  const latest = !r.latest ? html` Each filesystem's latest ${preview ? "would be" : "is"} built with them already.`
    : r.latest < r.outdated ? html` Or <code class="nowrap">pinax rebuild --outdated --latest</code> builds only each filesystem's latest (${num(r.latest)}), in
      about ${duration(r.latest_seconds)}, and older scans keep theirs.` : "";
  return html`<p class="callout">${icon("info")} ${head} On the server, <code class="nowrap">pinax rebuild --outdated</code> builds ${one ? "it" : "them"}
    again, in about ${duration(r.seconds)} (${one ? "its last build" : "their last builds"}).${latest}</p>`;
}

function categoryForm(c) {
  const v = c || { label: "", description: "", extensions: [], patterns: [] };
  const other = c && c.key === "other";
  return html`<form class="stack" style="gap:10px">
    <label class="field"><span class="small">Name</span><input class="input" name="label" value="${v.label}" required maxlength="80"></label>
    <label class="field"><span class="small">What it holds</span><input class="input" name="description" value="${v.description}" maxlength="300"></label>
    ${other ? html`<p class="small muted" style="margin:0">Other takes every file no other category does, so it has no extensions or patterns, and stays last.</p>` : html`
    <label class="field"><span class="small">Extensions, separated by commas or one per line (<code>bam</code>, <code>fastq</code>; a compressed
      file counts by the extension inside, so <code>fastq</code> takes <code>.fastq.gz</code>)</span>
      <textarea class="input" name="extensions" rows="3">${v.extensions.join(", ")}</textarea></label>
    <label class="field"><span class="small">Patterns on file names, one per line: regular expressions, checked before any extension and
      before later categories' patterns. They match anywhere in a name unless anchored with <code>^</code> and <code>$</code>; <code>(?i)</code> ignores case</span>
      <textarea class="input mono" name="patterns" rows="3">${v.patterns.join("\n")}</textarea></label>
    <div class="toolbar"><button type="button" class="btn btn-secondary btn-sm" data-try>${icon("search")}Try the patterns</button>
      <button type="button" class="btn btn-secondary btn-sm" data-preview>${icon("eye")}What would change</button></div>`}
    <div data-result></div></form>`;
}

const formBody = (wrap) => {
  const f = new FormData($("form", wrap));
  const body = { label: f.get("label"), description: f.get("description") };
  if (f.has("extensions")) Object.assign(body, { extensions: lines(f.get("extensions")), patterns: String(f.get("patterns") || "").split("\n").map((p) => p.trim()).filter(Boolean) });
  return body;
};

function tried(results) {
  return html`${results.map(({ pattern, res }) => html`<div style="margin-top:8px"><code>${pattern}</code>
    ${res.problem ? html`<p class="small" style="margin:4px 0 0">${icon("alert")} ${res.problem}</p>`
      : res.filesystems.map((f) => html`<p class="small muted" style="margin:4px 0 0">${f.fs_name}: ${num(f.matches)} of a sample of ${num(f.sample)} names${f.examples.length
          ? html`, such as ${f.examples.slice(0, 5).map((e, i) => html`${i ? ", " : ""}<code>${e}</code>`)}` : ""}</p>`)}</div>`)}`;
}

function changes(p, saved) {
  const cats = [...Object.entries(p.labels).map(([key, l]) => ({ key, label: l })), ...saved];  // as changed, then as saved
  const fsMoves = p.moves.map((m) => {
    const sample = m.sample && m.sample.moves.length ? html`<p class="small" style="margin:8px 0 2px"><b>By name</b>, an estimate from
        ${num(m.sample.files)} of its ${num(m.sample.of)} files:</p>
      <ul class="small" style="margin:0;padding-left:18px">${m.sample.moves.map((x) => html`<li>About ${num(x.estimate_files)} files (${bytes(x.estimate_bytes)})
        from ${label(cats, x.from)} to ${label(cats, x.to)}${x.examples.length ? html`, such as <code>${x.examples[0]}</code>` : ""}</li>`)}</ul>` : "";
    const typed = m.by_type.length ? html`<p class="small" style="margin:8px 0 2px"><b>By extension</b>, from its table of types:</p>
      <ul class="small" style="margin:0;padding-left:18px">${m.by_type.slice(0, 12).map((x) => html`<li><code>.${x.ext || "(none)"}</code>, ${num(x.files)} files
        (${bytes(x.bytes)}), from ${label(cats, x.from)} to ${label(cats, x.to)}</li>`)}${m.by_type.length > 12 ? html`<li>and ${num(m.by_type.length - 12)} more types</li>` : ""}</ul>` : "";
    return html`<div style="margin-top:10px"><b>${m.fs_name}</b>, its latest scan${typed || sample ? html`${typed}${sample}`
      : html`<p class="small muted" style="margin:4px 0 0">No file would change category${m.sample ? ` in a sample of ${num(m.sample.files)}` : ""}.</p>`}</div>`;
  });
  return html`<div class="stack" style="gap:4px;margin-top:6px">${fsMoves}${rebuildNote(p.rebuild, true)}</div>`;
}

function bindTools(wrap, key, cats) {
  const out = $("[data-result]", wrap);
  $("[data-try]", wrap)?.addEventListener("click", async () => {
    const pats = formBody(wrap).patterns;
    if (!pats.length) return mount(out, html`<p class="small muted">No patterns to try.</p>`);
    mount(out, html`<p class="small muted">Trying…</p>`);
    try {
      const results = await Promise.all(pats.map(async (pattern) => ({ pattern, res: await api("admin/categories/try", { method: "POST", body: { pattern } }) })));
      mount(out, tried(results));
    } catch (e) { mount(out, html`<p class="small">${icon("alert")} ${e.message}</p>`); }
  });
  $("[data-preview]", wrap)?.addEventListener("click", async () => {
    mount(out, html`<p class="small muted">Looking at the latest scans…</p>`);
    try {
      mount(out, changes(await api("admin/categories/preview", { method: "POST", body: { key, ...formBody(wrap) } }), cats));
    } catch (e) { mount(out, html`<p class="small">${icon("alert")} ${e.message}</p>`); }
  });
}

function editor(title, c, cats, save) {
  const pending = modal({ title, body: categoryForm(c), confirm: "Save", onConfirm: (wrap) => save(formBody(wrap)) });
  bindTools(document.querySelector(".modal-backdrop:last-of-type"), c ? c.key : null, cats);
  return pending;
}

export async function viewCategories(el) {
  renderSidebar();
  const data = await api("admin/categories");
  const cats = data.categories, admin = data.can_change;
  const patterns = cats.reduce((t, c) => t + c.patterns.length, 0);
  const total = Object.values(data.bytes).reduce((t, b) => t + b, 0);
  const row = (c, i) => html`<tr>
    <td class="num muted">${i + 1}</td>
    <td><b>${c.label}</b>${c.builtin ? "" : html` <span class="chip chip-neutral">added</span>`}<div class="sub">${c.description}</div></td>
    <td>${c.key === "other" ? html`<span class="muted small">Everything else</span>` : html`${c.extensions.slice(0, 14).join(", ")}${c.extensions.length > 14
      ? html` <span class="muted small">and ${num(c.extensions.length - 14)} more</span>` : ""}${c.patterns.length
      ? html`<div class="sub">${c.patterns.map((p) => html`<code>${p}</code> `)}</div>` : ""}`}</td>
    <td class="num nowrap">${bytes(data.bytes[c.key] || 0)}<div class="sub">${total ? `${Math.round((100 * (data.bytes[c.key] || 0)) / total)}%` : ""}</div></td>
    ${admin ? html`<td class="right nowrap">
      ${c.key !== "other" && i > 0 ? html`<button class="btn btn-ghost btn-sm" aria-label="Move ${c.label} up" data-move="${c.key}" data-to="${i - 1}">Up</button>` : ""}
      ${c.key !== "other" && i < cats.length - 2 ? html`<button class="btn btn-ghost btn-sm" aria-label="Move ${c.label} down" data-move="${c.key}" data-to="${i + 1}">Down</button>` : ""}
      <button class="btn btn-ghost btn-sm" data-edit="${c.key}">Change</button>
      ${c.builtin ? "" : html`<button class="icon-btn" title="Remove" aria-label="Remove ${c.label}" data-remove="${c.key}">${icon("x")}</button>`}</td>` : ""}</tr>`;
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>File categories</h1>
      <p>What each file counts as, on <i>File types</i>, the age map's sizes and colors, and <i>Find files</i>. A file's category is the first whose
        pattern its name matches, in the order here; then the category listing its extension; then Other. It is decided when a scan is
        indexed, so a change applies to the scans indexed after it, and to those rebuilt.</p></div>
      ${admin ? html`<div class="actions"><button class="btn btn-primary btn-sm" id="c-add" ${cats.length >= data.limits.categories ? "disabled" : ""}>${icon("plus")}Add category</button></div>` : ""}</div>
    ${rebuildNote(data.rebuild)}
    <div class="card flush"><div class="card-head"><h2>Categories</h2><span class="small muted">${num(cats.length)} of at most ${num(data.limits.categories)};
      ${num(patterns)} pattern${patterns === 1 ? "" : "s"} of at most ${num(data.limits.patterns)}</span></div>
      <div class="table-wrap"><table class="table"><thead><tr><th class="num">#</th><th style="width:30%">Category</th><th>Extensions and patterns</th>
        <th class="num">In the latest scans</th>${admin ? html`<th></th>` : ""}</tr></thead><tbody>${cats.map(row)}</tbody></table></div></div>`);

  const refresh = () => viewCategories(el);
  const save = async (path, method, body, done) => {
    await api(path, { method, body });
    toast(done);
    refresh();
    return true;
  };
  $("#c-add", el)?.addEventListener("click", () => editor("Add a file category", null, cats,
    (body) => save("admin/categories", "POST", body, "Category added: new scans use it")));
  $$("[data-edit]", el).forEach((b) => b.addEventListener("click", () => {
    const c = cats.find((x) => x.key === b.dataset.edit);
    editor(`Change ${c.label}`, c, cats, (body) => save(`admin/categories/${c.key}`, "PUT", body, "Saved: new scans use it"));
  }));
  $$("[data-move]", el).forEach((b) => b.addEventListener("click", async () => {
    try { await save(`admin/categories/${b.dataset.move}/move`, "POST", { to: Number(b.dataset.to) }, "Moved"); } catch (e) { toast(e.message, "error"); }
  }));
  $$("[data-remove]", el).forEach((b) => b.addEventListener("click", async () => {
    const c = cats.find((x) => x.key === b.dataset.remove);
    await modal({ title: `Remove ${c.label}`, tone: "navy", confirm: "Remove",
      body: html`<p style="margin:0">Its files will count by their extensions, or as Other, in the scans indexed after this. Scans already indexed keep it until rebuilt.</p>`,
      onConfirm: () => save(`admin/categories/${c.key}`, "DELETE", undefined, "Removed") });
  }));
}
