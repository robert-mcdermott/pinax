// SPDX-License-Identifier: Apache-2.0
// The age map: a treemap of a folder's subfolders, sized by how much they hold and colored by how old
// it is. Click a tile to go one level down; the breadcrumbs go back up.
import {
  $, $$, ageText, api, bytes, date, displayPath, html, icon, mount, num, parseQuery, pct, placeHash, placeQuery,
  raw, state, when,
} from "/js/core.js";
import { scopeHeader } from "/js/shell.js";
import { renderTreemap } from "/js/treemap.js";
import {
  ageBar, ageHistogram, bars, categories, coldBytes, colorFor, colorOptions, daysSince, legend, metricLabel,
  metricOptions, metricText, metricValue, thresholdText,
} from "/js/viz.js";

const DEFAULTS = { size: "bytes", color: "mean", levels: "2", view: "map" };

export async function viewExplore(el, query) {
  const q = { ...DEFAULTS, ...parseQuery(query) };
  const place = q.fs ? { fs: q.fs, path: q.path } : null;
  const view = { size: q.size, color: q.color, levels: q.levels, view: q.view };
  const data = await api("tree", { query: { ...placeQuery(place), metric: q.size, depth: Number(q.levels), lab: q.lab } });

  const node = data.node;
  const asOf = data.as_of;
  const cb = data.cold_bucket;
  const keep = { ...view, lab: place ? undefined : q.lab };
  const hrefFor = (t) => {
    if (t.kind === "dir" || t.kind === "fs") return placeHash("explore", { fs: t.fs, path: t.path }, view);
    if (t.kind === "files") return placeHash("files", { fs: t.fs, path: t.path }, { direct: 1 });
    return null;
  };
  const parentCrumb = place && data.breadcrumbs.length >= 2 ? data.breadcrumbs[data.breadcrumbs.length - 2] : null;
  const total = metricValue(node.stats, q.size);
  const tiles = data.tiles;

  const select = (id, label, options, value) => html`<label class="ctl"><span>${label}</span>
    <select class="select sm" id="${id}">${options.map((o) => html`<option value="${o.key}" ${o.key === value ? raw("selected") : ""}>${o.label}</option>`)}</select></label>`;

  mount(el, html`
    ${scopeHeader("explore", place, { asOf, lab: node.lab && !place ? node.lab : null,
      lede: place ? "" : state.me.is_admin ? "Every filesystem, as of its latest scan." : "Every folder your labs own.",
      actions: parentCrumb ? html`<a class="btn btn-ghost btn-sm" href="${placeHash("explore", parentCrumb.fs ? { fs: parentCrumb.fs, path: parentCrumb.path } : null, keep)}">${icon("arrowUp")}Up one level</a>` : "" })}
    <div class="toolbar">
      ${select("ctl-size", "Size by", metricOptions(), q.size)}
      ${select("ctl-color", "Color by", colorOptions(), q.color)}
      <div class="ctl"><span>Levels</span><div class="pills" role="group" aria-label="Levels shown">
        <button data-levels="1" class="${q.levels === "1" ? "active" : ""}" aria-pressed="${q.levels === "1"}">One</button>
        <button data-levels="2" class="${q.levels === "2" ? "active" : ""}" aria-pressed="${q.levels === "2"}">Two</button></div></div>
      <div class="ctl"><span>View</span><div class="pills" role="group" aria-label="View">
        <button data-view="map" class="${q.view === "map" ? "active" : ""}" aria-pressed="${q.view === "map"}">${icon("treemap")} Map</button>
        <button data-view="table" class="${q.view === "table" ? "active" : ""}" aria-pressed="${q.view === "table"}">${icon("table")} Table</button></div></div>
    </div>
    <div class="explore-layout">
      <div class="card tm-card">
        ${q.view === "map" ? html`<div class="tm" id="tm" role="group" aria-label="Treemap of ${node.name}: ${metricLabel(q.size)} by ${colorOptions().find((c) => c.key === q.color)?.label}"></div>`
          : childTable(tiles, q, asOf, cb, hrefFor, total)}
        ${legend(q.color, cb)}
        <p class="small muted" style="margin:10px 0 0">Tile area is ${metricLabel(q.size).toLowerCase()}. ${node.subdirs > 60 ? `The largest 60 of ${num(node.subdirs)} subfolders are drawn; the rest are combined. ` : ""}Click a folder to open it.</p>
      </div>
      ${detailPanel(node, data, q, cb)}
    </div>`);

  if (q.view === "map") {
    const tm = $("#tm", el);
    const draw = () => renderTreemap(tm, tiles, {
      levels: Number(q.levels),
      value: (t) => metricValue(t.stats, q.size),
      color: (t) => colorFor(t.stats, q.color, asOf, cb).token,
      href: hrefFor,
      label: (t) => `${metricText(metricValue(t.stats, q.size), q.size)} · ${colorFor(t.stats, q.color, asOf, cb).text}`,
      tip: (t) => String(tileTip(t, q, asOf, cb, total)),
      onEmpty: () => String(html`<div class="empty">${icon("treemap")}<h3>Nothing to draw</h3><p>No subfolder here has any ${metricLabel(q.size).toLowerCase()}.</p></div>`),
    });
    draw();
    let timer;
    const redraw = () => { clearTimeout(timer); timer = setTimeout(() => { if (document.body.contains(tm)) draw(); }, 120); };
    window.addEventListener("resize", redraw);
    window.addEventListener("pinax:theme", redraw);
    window.addEventListener("hashchange", () => { window.removeEventListener("resize", redraw); window.removeEventListener("pinax:theme", redraw); }, { once: true });
  }

  const go = (changes) => { location.hash = placeHash("explore", place, { ...keep, ...changes }); };
  $("#ctl-size", el).addEventListener("change", (e) => go({ size: e.target.value }));
  $("#ctl-color", el).addEventListener("change", (e) => go({ color: e.target.value }));
  $$("[data-levels]", el).forEach((b) => b.addEventListener("click", () => go({ levels: b.dataset.levels })));
  $$("[data-view]", el).forEach((b) => b.addEventListener("click", () => go({ view: b.dataset.view })));
  $$("[data-hist]", el).forEach((b) => b.addEventListener("click", () => {
    $$("[data-hist]", el).forEach((x) => { x.classList.toggle("active", x === b); x.setAttribute("aria-pressed", String(x === b)); });
    mount($("#hist", el), ageHistogram(b.dataset.hist === "atime" ? node.stats.atime_bytes : node.stats.age_bytes));
  }));
}

function tileTip(t, q, asOf, cb, total) {
  const s = t.stats;
  const v = metricValue(s, q.size);
  const c = colorFor(s, q.color, asOf, cb);
  const where = t.kind === "other" ? `${num(t.count)} folders, each smaller than those shown`
    : t.kind === "files" ? `${num(t.count)} files directly in this folder` : displayPath(t.fs, t.path);
  return html`<div class="t">${t.name}</div><div class="p">${where}</div>
    ${ageBar(s.age_bytes)}
    <dl>
      <dt>${metricLabel(q.size)}</dt><dd>${metricText(v, q.size)}${total ? ` (${pct((v / total) * 100)})` : ""}</dd>
      ${q.size !== "bytes" ? html`<dt>Size</dt><dd>${bytes(s.bytes)}</dd>` : ""}
      <dt>Files</dt><dd>${num(s.files)}</dd>
      <dt>Average age</dt><dd>${ageText(s.mean_age_days)}</dd>
      <dt>Last change</dt><dd>${s.newest ? ageText(daysSince(s.newest, asOf)) + " ago" : "—"}</dd>
      <dt>Oldest file</dt><dd>${s.oldest ? ageText(daysSince(s.oldest, asOf)) : "—"}</dd>
      <dt>Unchanged ${thresholdText(cb)}+</dt><dd>${pct(s.bytes ? (coldBytes(s, cb) / s.bytes) * 100 : 0)}</dd>
      ${q.color.startsWith("share:") || q.color === "access" ? html`<dt>Color</dt><dd>${c.text}</dd>` : ""}
      ${t.lab ? html`<dt>Owned by</dt><dd>${t.lab.name}</dd>` : ""}
      ${t.error ? html`<dt>Scan error</dt><dd>${t.error}</dd>` : ""}
    </dl>`;
}

function childTable(tiles, q, asOf, cb, hrefFor, total) {
  const rows = [...tiles].sort((a, b) => metricValue(b.stats, q.size) - metricValue(a.stats, q.size));
  return html`<div class="table-wrap"><table class="table compact"><thead><tr>
      <th>Folder</th><th class="num">${metricLabel(q.size)}</th><th class="num">Share</th><th class="num">Files</th>
      <th class="num">Average age</th><th>Last change</th><th class="t-age">Age profile</th></tr></thead><tbody>
    ${rows.map((t) => {
      const v = metricValue(t.stats, q.size);
      const href = hrefFor(t);
      return html`<tr>
        <td>${href ? html`<a class="rowlink" href="${href}">${t.name}</a>` : html`<span class="title-cell">${t.name}</span>`}
          ${t.lab ? html`<div class="sub">${t.lab.name}</div>` : ""}</td>
        <td class="num nowrap">${metricText(v, q.size)}</td><td class="num">${total ? pct((v / total) * 100) : "—"}</td>
        <td class="num">${num(t.stats.files)}</td><td class="num nowrap">${ageText(t.stats.mean_age_days)}</td>
        <td class="nowrap">${t.stats.newest ? date(t.stats.newest) : "—"}</td><td>${ageBar(t.stats.age_bytes)}</td></tr>`;
    })}</tbody></table></div>`;
}

function detailPanel(node, data, q, cb) {
  const s = node.stats;
  const place = node.fs ? { fs: node.fs, path: node.path } : null;
  const extra = node.lab && !place ? { lab: node.lab.slug } : {};
  const cold = coldBytes(s, cb);
  const cats = categories().map((c, i) => ({ key: c.key, label: c.label, bytes: s.cat_bytes[i] })).filter((c) => c.bytes > 0)
    .sort((a, b) => b.bytes - a.bytes).slice(0, 6);
  const sparse = s.alloc > 0 && s.alloc < s.bytes * 0.5; // compressed, deduplicated, sparse, or tiered
  return html`<aside class="card detail" aria-label="About this folder">
    <h2>${node.name}</h2>
    ${node.path ? html`<div class="path muted">${displayPath(node.fs, node.path)}</div>` : ""}
    ${node.lab ? html`<span class="tag">${icon("users")}${node.lab.name}</span>` : ""}
    <div class="mini-kpis">
      <div><b>${bytes(s.bytes)}</b><span>Size${sparse ? ` (${bytes(s.alloc)} on disk)` : ""}</span></div>
      <div><b>${num(s.files)}</b><span>Files in ${num(s.dirs)} folders</span></div>
      <div><b>${ageText(s.mean_age_days)}</b><span>Average age</span></div>
      <div><b>${s.newest ? when(s.newest) : "—"}</b><span>Last change</span></div>
    </div>
    ${cold ? html`<div class="callout"><b>${bytes(cold)}</b> (${pct((cold / s.bytes) * 100)}) has not changed for ${thresholdText(cb)} or more.
      <a href="${placeHash("cold", place, { ...extra, older: cb })}">Find cold folders</a></div>` : ""}
    ${s.incomplete ? html`<div class="alert alert-warn">${icon("alert")}<div>${num(s.incomplete)} folder${s.incomplete === 1 ? "" : "s"} here could not be read completely, so these totals are lower bounds.</div></div>` : ""}
    <div class="row between"><h3>Age of the data</h3>
      <div class="pills" role="group" aria-label="Age measured from"><button class="active" aria-pressed="true" data-hist="mtime">Last change</button><button data-hist="atime" aria-pressed="false">Last access</button></div></div>
    <div id="hist">${ageHistogram(s.age_bytes)}</div>
    <h3>What kind of data</h3>
    ${cats.length ? bars(cats, { href: (c) => placeHash("types", place, { ...extra, cat: c.key }) }) : html`<p class="muted small">No files.</p>`}
    <p class="small" style="margin:10px 0 0"><a href="${placeHash("types", place, extra)}">All file types</a> ·
      <a href="${placeHash("files", place, { ...extra, sort: "size" })}">Largest files</a> ·
      <a href="${placeHash("files", place, { ...extra, sort: "oldest" })}">Oldest files</a> ·
      <a href="${placeHash("owners", place, extra)}">Owners</a></p>
    ${node.direct && node.direct.files ? html`<p class="small muted" style="margin:10px 0 0">${num(node.direct.files)} files (${bytes(node.direct.bytes)}) sit directly in this folder.
      <a href="${placeHash("files", place, { direct: 1 })}">List them</a></p>` : ""}
  </aside>`;
}

