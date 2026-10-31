// SPDX-License-Identifier: Apache-2.0
// The age map: a treemap of a folder's subfolders, sized by how much they hold and colored by how old
// it is, or the same folders several levels deep as an icicle or a sunburst (depth.js). Click a folder to go
// into it; the breadcrumbs go back up.
import {
  $, $$, ageText, api, bytes, date, displayPath, html, icon, mount, num, parseQuery, pct, placeHash, placeQuery,
  raw, state, when,
} from "/js/core.js";
import { planBox } from "/js/plans.js";
import { tagBox } from "/js/tags.js";
import { scopeHeader } from "/js/shell.js";
import { renderIcicle, renderSunburst } from "/js/depth.js";
import { renderTreemap } from "/js/treemap.js";
import {
  ageBar, ageHistogram, bars, categories, coldBytes, colorFor, colorOptions, daysSince, legend, metricLabel,
  metricOptions, metricText, metricValue, thresholdText,
} from "/js/viz.js";

const DEFAULTS = { size: "bytes", color: "mean", levels: "2", depth: "6", view: "map" };
const DEEP = new Set(["icicle", "sunburst"]); // views of several levels, from /api/depth
const VIEWS = [["map", "treemap", "Map"], ["icicle", "icicle", "Icicle"], ["sunburst", "sunburst", "Sunburst"], ["table", "table", "Table"]];

export async function viewExplore(el, query) {
  const q = { ...DEFAULTS, ...parseQuery(query) };
  const place = q.fs ? { fs: q.fs, path: q.path } : null;
  const deep = DEEP.has(q.view);
  const view = { size: q.size, color: q.color, levels: q.levels, depth: q.depth, view: q.view };
  const [data, below, depth] = await Promise.all([
    api("tree", { query: { ...placeQuery(place), metric: q.size, depth: deep ? 1 : Number(q.levels), lab: q.lab } }),
    place ? api("tags/below", { query: { fs: place.fs, path: place.path }, quiet: true }).catch(() => ({ tagged: {} })) : { tagged: {} },
    deep ? api("depth", { query: { ...placeQuery(place), metric: q.size, levels: Number(q.depth), lab: q.lab } }) : null]);
  // A tile's own tags, or that it holds tagged folders (null: neither).
  const tagged = Object.entries(below.tagged);
  const tagsOf = (t) => {
    if (!t.path || t.kind !== "dir") return null;
    const own = below.tagged[t.path];
    if (own) return own.join(", ");
    return tagged.some(([p]) => p.startsWith(`${t.path}/`)) ? "Holds tagged folders" : null;
  };

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
      ${q.view === "table" ? "" : deep ? pills("Levels", "Levels shown", "depth", [["4", "Four"], ["6", "Six"], ["8", "Eight"]], q.depth)
        : pills("Levels", "Levels shown", "levels", [["1", "One"], ["2", "Two"]], q.levels)}
      ${pills("View", "View", "view", VIEWS.map(([key, ic, label]) => [key, html`${icon(ic)} ${label}`]), q.view)}
    </div>
    <div class="explore-layout">
      <div class="card tm-card">
        ${q.view === "table" ? childTable(tiles, q, asOf, cb, hrefFor, total, tagsOf)
          : html`<div class="tm${q.view === "sunburst" ? " square" : ""}" id="tm" role="group" aria-label="${{ map: "Treemap", icicle: "Icicle", sunburst: "Sunburst" }[q.view] || "Treemap"} of ${node.name}: ${metricLabel(q.size)} by ${colorOptions().find((c) => c.key === q.color)?.label}"></div>`}
        ${legend(q.color, cb)}
        <p class="small muted" style="margin:10px 0 0">${caption(q, node, depth)}</p>
      </div>
      ${detailPanel(node, data, q, cb)}
    </div>`);

  if (q.view !== "table") {
    const tm = $("#tm", el);
    const opts = {
      levels: Number(q.levels),
      value: (t) => metricValue(t.stats, q.size),
      color: (t) => colorFor(t.stats, q.color, asOf, cb).token,
      href: hrefFor,
      label: (t) => `${metricText(metricValue(t.stats, q.size), q.size)} · ${colorFor(t.stats, q.color, asOf, cb).text}${tagsOf(t) ? ` · tags: ${tagsOf(t)}` : ""}`,
      tip: (t) => String(tileTip(t, q, asOf, cb, total, tagsOf(t))),
      mark: (t) => Boolean(tagsOf(t)),
      onEmpty: () => String(html`<div class="empty">${icon(VIEWS.find(([k]) => k === q.view)?.[1] || "treemap")}<h3>Nothing to draw</h3><p>No subfolder here has any ${metricLabel(q.size).toLowerCase()}.</p></div>`),
      total: depth?.total,
      center: { name: node.name, sub: metricText(total, q.size),
        href: parentCrumb ? placeHash("explore", parentCrumb.fs ? { fs: parentCrumb.fs, path: parentCrumb.path } : null, keep) : null },
    };
    const render = { map: renderTreemap, icicle: renderIcicle, sunburst: renderSunburst }[q.view] || renderTreemap;
    const draw = () => render(tm, deep ? depth.tiles : tiles, opts);
    draw();
    let timer;
    const redraw = () => { clearTimeout(timer); timer = setTimeout(() => { if (document.body.contains(tm)) draw(); }, 120); };
    window.addEventListener("resize", redraw);
    window.addEventListener("pinax:theme", redraw);
    window.addEventListener("hashchange", () => { window.removeEventListener("resize", redraw); window.removeEventListener("pinax:theme", redraw); }, { once: true });
  }

  const tagEl = $("#tag-box", el);
  if (tagEl && node.fs && node.path) tagBox(tagEl, node.fs, node.path);
  const planEl = $("#plan-box", el);
  if (planEl && node.fs && node.path) planBox(planEl, node.fs, node.path);

  const go = (changes) => { location.hash = placeHash("explore", place, { ...keep, ...changes }); };
  $("#ctl-size", el).addEventListener("change", (e) => go({ size: e.target.value }));
  $("#ctl-color", el).addEventListener("change", (e) => go({ color: e.target.value }));
  for (const key of ["levels", "depth", "view"]) {
    $$(`[data-${key}]`, el).forEach((b) => b.addEventListener("click", () => go({ [key]: b.dataset[key] })));
  }
  $$("[data-hist]", el).forEach((b) => b.addEventListener("click", () => {
    $$("[data-hist]", el).forEach((x) => { x.classList.toggle("active", x === b); x.setAttribute("aria-pressed", String(x === b)); });
    mount($("#hist", el), ageHistogram(b.dataset.hist === "atime" ? node.stats.atime_bytes : node.stats.age_bytes));
  }));
}

const pills = (label, aria, key, options, value) => html`<div class="ctl"><span>${label}</span><div class="pills" role="group" aria-label="${aria}">
  ${options.map(([k, text]) => html`<button data-${key}="${k}" class="${k === value ? "active" : ""}" aria-pressed="${k === value}">${text}</button>`)}</div></div>`;

function caption(q, node, depth) {
  const measure = metricLabel(q.size).toLowerCase();
  if (q.view === "table") return "Click a folder to open it.";
  if (!depth) {
    return `Tile area is ${measure}. ${node.subdirs > 60 ? `The largest 60 of ${num(node.subdirs)} subfolders are drawn; the rest are combined. ` : ""}Click a folder to open it.`;
  }
  const how = q.view === "icicle" ? `Each column is a level deeper, and a folder's height is its ${measure}`
    : `Each ring is a level deeper, and a folder's angle is its ${measure}; compare angles, since outer rings are drawn larger`;
  const share = depth.total ? +((depth.least / depth.total) * 100).toFixed(2) : 0;
  return `${how}. Folders holding less than ${share}% of this place are left out, and the space after a folder's `
    + `subfolders is its own files and the folders too small to draw. Click a folder to open it${q.view === "sunburst" && node.fs ? ", or the middle to go up" : ""}.`;
}

function tileTip(t, q, asOf, cb, total, tags = null) {
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
      ${tags ? html`<dt>Tags</dt><dd>${tags}</dd>` : ""}
      ${t.error ? html`<dt>Scan error</dt><dd>${t.error}</dd>` : ""}
    </dl>`;
}

function childTable(tiles, q, asOf, cb, hrefFor, total, tagsOf = () => null) {
  const rows = [...tiles].sort((a, b) => metricValue(b.stats, q.size) - metricValue(a.stats, q.size));
  return html`<div class="table-wrap"><table class="table compact"><thead><tr>
      <th>Folder</th><th class="num">${metricLabel(q.size)}</th><th class="num">Share</th><th class="num">Files</th>
      <th class="num">Average age</th><th>Last change</th><th class="t-age">Age profile</th></tr></thead><tbody>
    ${rows.map((t) => {
      const v = metricValue(t.stats, q.size);
      const href = hrefFor(t);
      return html`<tr>
        <td>${href ? html`<a class="rowlink" href="${href}">${t.name}</a>` : html`<span class="title-cell">${t.name}</span>`}
          ${t.lab ? html`<div class="sub">${t.lab.name}</div>` : ""}${tagsOf(t) ? html`<div class="sub">${icon("tag")} ${tagsOf(t)}</div>` : ""}</td>
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
    ${node.path ? html`<div class="path muted clamp" title="${displayPath(node.fs, node.path)}">${displayPath(node.fs, node.path)}</div>` : ""}
    ${node.lab ? html`<span class="tag">${icon("users")}${node.lab.name}</span>` : ""}
    <div class="mini-kpis">
      <div><b>${bytes(s.bytes)}</b><span>Size${sparse ? ` (${bytes(s.alloc)} on disk)` : ""}</span></div>
      <div><b>${num(s.files)}</b><span>Files in ${num(s.dirs)} folders</span></div>
      <div><b>${ageText(s.mean_age_days)}</b><span>Average age</span></div>
      <div><b>${s.newest ? when(s.newest) : "—"}</b><span>Last change</span></div>
    </div>
    ${node.links?.files ? html`<p class="small muted" style="margin:0 0 10px">${icon("link")} ${num(node.links.files)} hard link${node.links.files === 1 ? "" : "s"} to files counted
      elsewhere here or in another folder: their ${bytes(node.links.bytes)} is counted once, at each file's first link.</p>` : ""}
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
    <div id="tag-box" class="plan-box"></div>
    <div id="plan-box" class="plan-box"></div>
  </aside>`;
}

