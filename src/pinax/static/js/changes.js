// SPDX-License-Identifier: Apache-2.0
// The change map: the age map's treemap, colored by how much each folder grew or shrank since an earlier scan
// (the previous one by default). Folders in only one of the two scans are drawn as new or removed; sized by the
// larger of the two sizes, a removed folder keeps the room it had. Click a folder to go down, as in the age map.
import { $, $$, ageText, api, bytes, date, displayPath, emptyState, html, icon, mount, num, parseQuery, pct, placeHash, placeQuery, raw } from "/js/core.js";
import { scopeHeader } from "/js/shell.js";
import { renderTreemap } from "/js/treemap.js";

const DEFAULTS = { size: "max", levels: "1", view: "map" };
const SIZES = [
  { key: "max", label: "The larger of then and now" },
  { key: "now", label: "Size now" },
  { key: "then", label: "Size at the earlier scan" },
];
// Steps out from no change, by the share of the earlier size gained or lost; new and removed are the last step.
const STEPS = [0.1, 0.5];
const LEGEND = [
  [-3, "Shrank 50%+, or removed"], [-2, "Shrank 10–50%"], [-1, "Shrank under 10%"], [0, "No change"],
  [1, "Grew under 10%"], [2, "Grew 10–50%"], [3, "Grew 50%+, or new"],
];

const size = (s) => (s ? s.bytes : 0);
const isNew = (t) => t.in_old === false || (!t.then && !!t.now);
const isGone = (t) => t.in_new === false || (!!t.then && !t.now);
const value = (t, how) => (how === "now" ? size(t.now) : how === "then" ? size(t.then) : Math.max(size(t.now), size(t.then)));

/** -3 to 3: which way and how far a tile changed in size. */
export function changeStep(t) {
  if (isGone(t)) return -3;
  if (isNew(t)) return 3;
  const a = size(t.then), b = size(t.now);
  if (a === b) return 0;
  if (!a) return 3;
  const r = Math.abs(b - a) / a;
  const step = r < STEPS[0] ? 1 : r < STEPS[1] ? 2 : 3;
  return b > a ? step : -step;
}
const token = (step) => (step < 0 ? `--chg-n${-step}` : step > 0 ? `--chg-p${step}` : "--chg-0");
const signed = (v) => (v ? `${v > 0 ? "+" : "−"}${bytes(Math.abs(v))}` : "No change");
const share = (t) => (size(t.then) ? ` (${size(t.now) >= size(t.then) ? "+" : "−"}${pct((Math.abs(size(t.now) - size(t.then)) / size(t.then)) * 100)})` : "");

/** The short text on a tile, and in the table's status column. */
function changeText(t) {
  if (isGone(t)) return `Removed · ${bytes(size(t.then))}`;
  if (isNew(t)) return `New · ${bytes(size(t.now))}`;
  const d = size(t.now) - size(t.then);
  return d ? `${signed(d)}${share(t)}` : "No change";
}

/** "Compare with": the previous scan, or the scan of any day with an earlier kept scan (select#ctl-since). */
export function sinceSelect(data, since) {
  const latest = data.compared.find((c) => c.previous) || data.compared[0];
  return html`<label class="ctl"><span>Compare with</span><select class="select sm" id="ctl-since">
    <option value="" ${since ? "" : raw("selected")}>The previous scan${data.compared.length === 1 && latest?.previous ? ` (${date(latest.started_at)})` : ""}</option>
    ${data.choices.map((d) => html`<option value="${d}" ${since === d ? raw("selected") : ""}>The scan of ${date(`${d}T12:00:00Z`)}</option>`)}
  </select></label>`;
}

export async function viewChanges(el, query) {
  const q = { ...DEFAULTS, ...parseQuery(query) };
  const place = q.fs ? { fs: q.fs, path: q.path } : null;
  const data = await api("changes", { query: { ...placeQuery(place), since: q.since, metric: q.size, depth: Number(q.levels), lab: place ? undefined : q.lab } });
  const node = data.node;
  const keep = { size: q.size, levels: q.levels, view: q.view, since: q.since, lab: place ? undefined : q.lab };
  const hrefFor = (t) => (t.kind === "dir" || t.kind === "fs" ? placeHash("changes", { fs: t.fs, path: t.path }, { ...keep, lab: undefined }) : null);
  const parent = place && data.breadcrumbs.length >= 2 ? data.breadcrumbs[data.breadcrumbs.length - 2] : null;
  const head = scopeHeader("changes", place, { asOf: data.as_of, lab: node.lab && !place ? node.lab : null,
    lede: place ? "" : "Every folder, compared with an earlier scan.",
    actions: parent ? html`<a class="btn btn-ghost btn-sm" href="${placeHash("changes", parent.fs ? { fs: parent.fs, path: parent.path } : null, keep)}">${icon("arrowUp")}Up one level</a>` : "" });
  if (!data.compared.length) {
    mount(el, html`${head}<div class="card">${emptyState("activity", "Nothing to compare with yet",
      "The change map compares this scan with an earlier one whose index is kept. There is only one scan here so far.")}</div>`);
    return;
  }
  const when = data.compared.map((c) => c.started_at).sort()[0];
  const tiles = data.tiles;

  mount(el, html`
    ${head}
    <div class="toolbar">
      ${sinceSelect(data, q.since)}
      <label class="ctl"><span>Size by</span><select class="select sm" id="ctl-size">
        ${SIZES.map((s) => html`<option value="${s.key}" ${s.key === q.size ? raw("selected") : ""}>${s.label}</option>`)}</select></label>
      <div class="ctl"><span>Levels</span><div class="pills" role="group" aria-label="Levels shown">
        ${["1", "2"].map((n) => html`<button data-levels="${n}" class="${q.levels === n ? "active" : ""}" aria-pressed="${q.levels === n}">${n === "1" ? "One" : "Two"}</button>`)}</div></div>
      <div class="ctl"><span>View</span><div class="pills" role="group" aria-label="View">
        <button data-view="map" class="${q.view === "map" ? "active" : ""}" aria-pressed="${q.view === "map"}">${icon("treemap")} Map</button>
        <button data-view="table" class="${q.view === "table" ? "active" : ""}" aria-pressed="${q.view === "table"}">${icon("table")} Table</button></div></div>
    </div>
    <div class="explore-layout">
      <div class="card tm-card">
        ${q.view === "map" ? html`<div class="tm" id="tm" role="group" aria-label="Change map of ${node.name} since ${date(when)}"></div>` : table(tiles, q, hrefFor)}
        <div class="ramp-legend" aria-label="Color legend"><span class="lt">Change in size</span>
          ${LEGEND.map(([s, l]) => html`<span><i style="background:var(${token(s)})"></i>${l}</span>`)}</div>
        <p class="small muted" style="margin:10px 0 0">Compared with the scan of ${data.compared.map((c) => `${date(c.started_at)}${data.compared.length > 1 ? ` (${c.fs})` : ""}`).join(", ")}.
          Tile area is ${SIZES.find((s) => s.key === q.size).label.toLowerCase()}. Folders are matched by path, so a moved or renamed folder shows as one removed and one new.</p>
      </div>
      ${detail(node, tiles, when, hrefFor)}
    </div>`);

  if (q.view === "map") {
    const tm = $("#tm", el);
    const draw = () => renderTreemap(tm, tiles, {
      levels: Number(q.levels),
      value: (t) => value(t, q.size),
      color: (t) => token(changeStep(t)),
      href: hrefFor,
      label: changeText,
      tip: (t) => String(tip(t, when)),
      onEmpty: () => String(html`<div class="empty">${icon("activity")}<h3>Nothing to draw</h3><p>No subfolder here has any data in ${q.size === "then" ? "the earlier scan" : q.size === "now" ? "this scan" : "either scan"}.</p></div>`),
    });
    draw();
    let timer;
    const redraw = () => { clearTimeout(timer); timer = setTimeout(() => { if (document.body.contains(tm)) draw(); }, 120); };
    window.addEventListener("resize", redraw);
    window.addEventListener("pinax:theme", redraw);
    window.addEventListener("hashchange", () => { window.removeEventListener("resize", redraw); window.removeEventListener("pinax:theme", redraw); }, { once: true });
  }

  const go = (changes) => { location.hash = placeHash("changes", place, { ...keep, ...changes }); };
  $("#ctl-since", el).addEventListener("change", (e) => go({ since: e.target.value || undefined }));
  $("#ctl-size", el).addEventListener("change", (e) => go({ size: e.target.value }));
  $$("[data-levels]", el).forEach((b) => b.addEventListener("click", () => go({ levels: b.dataset.levels })));
  $$("[data-view]", el).forEach((b) => b.addEventListener("click", () => go({ view: b.dataset.view })));
}

function tip(t, when) {
  const where = t.kind === "other" ? `${num(t.count)} folders, each smaller than those shown`
    : t.kind === "files" ? "Files directly in this folder" : displayPath(t.fs, t.path);
  const row = (label, s, missing) => html`<dt>${label}</dt><dd>${s ? html`<b>${bytes(s.bytes)}</b> · ${num(s.files)} files` : missing}</dd>`;
  return html`<div class="t">${t.name}</div><div class="p">${where}</div>
    <dl>
      <dt>Change</dt><dd><b>${changeText(t)}</b></dd>
      ${row(`On ${date(when)}`, t.then, "Not in that scan")}
      ${row("Now", t.now, "Not in this scan")}
      ${t.then && t.now ? html`<dt>Files</dt><dd>${t.now.files - t.then.files ? `${t.now.files > t.then.files ? "+" : "−"}${num(Math.abs(t.now.files - t.then.files))}` : "No change"}</dd>` : ""}
      ${t.now?.mean_age_days != null ? html`<dt>Average age</dt><dd>${ageText(t.now.mean_age_days)}${t.then?.mean_age_days != null ? ` (was ${ageText(t.then.mean_age_days)})` : ""}</dd>` : ""}
      ${t.kind === "other" && (t.new || t.removed) ? html`<dt>Among them</dt><dd>${num(t.new)} new, ${num(t.removed)} removed</dd>` : ""}
      ${t.lab ? html`<dt>Owned by</dt><dd>${t.lab.name}</dd>` : ""}
    </dl>`;
}

function table(tiles, q, hrefFor) {
  const rows = [...tiles].sort((a, b) => value(b, q.size) - value(a, q.size));
  return html`<div class="table-wrap"><table class="table compact"><thead><tr>
      <th>Folder</th><th class="num">Earlier</th><th class="num">Now</th><th class="num">Change</th><th class="num">Files</th><th>Change in size</th></tr></thead><tbody>
    ${rows.map((t) => {
      const href = hrefFor(t);
      const df = t.then && t.now ? t.now.files - t.then.files : null;
      return html`<tr>
        <td>${href ? html`<a class="rowlink" href="${href}">${t.name}</a>` : html`<span class="title-cell">${t.name}</span>`}
          ${t.lab ? html`<div class="sub">${t.lab.name}</div>` : t.fs_name ? html`<div class="sub">${t.fs_name}</div>` : ""}</td>
        <td class="num nowrap">${t.then ? bytes(t.then.bytes) : "—"}</td><td class="num nowrap">${t.now ? bytes(t.now.bytes) : "—"}</td>
        <td class="num nowrap">${signed(size(t.now) - size(t.then))}</td>
        <td class="num nowrap">${df == null ? "—" : df ? `${df > 0 ? "+" : "−"}${num(Math.abs(df))}` : "0"}</td>
        <td class="nowrap"><i class="swatch-dot" style="background:var(${token(changeStep(t))})"></i>${changeText(t)}</td></tr>`;
    })}</tbody></table></div>`;
}

function detail(node, tiles, when, hrefFor) {
  const d = size(node.now) - size(node.then);
  const moved = tiles.filter((t) => t.kind === "dir" || t.kind === "fs").map((t) => ({ t, d: size(t.now) - size(t.then) }));
  const grew = moved.filter((x) => x.d > 0).sort((a, b) => b.d - a.d).slice(0, 5);
  const shrank = moved.filter((x) => x.d < 0).sort((a, b) => a.d - b.d).slice(0, 5);
  const list = (items) => html`<ul class="plain-list">${items.map(({ t, d: v }) => html`<li><a href="${hrefFor(t)}">${t.name}</a>
    <span class="muted nowrap">${signed(v)}${isNew(t) ? " · new" : isGone(t) ? " · removed" : ""}</span></li>`)}</ul>`;
  const sub = node.subfolders;
  return html`<aside class="card detail" aria-label="About this change">
    <h2>${node.name}</h2>
    ${node.path ? html`<div class="path muted clamp" title="${displayPath(node.fs, node.path)}">${displayPath(node.fs, node.path)}</div>` : ""}
    ${node.lab ? html`<span class="tag">${icon("users")}${node.lab.name}</span>` : ""}
    <div class="mini-kpis">
      <div><b>${signed(d)}</b><span>Since ${date(when)}${size(node.then) ? ` (${d >= 0 ? "+" : "−"}${pct((Math.abs(d) / size(node.then)) * 100)})` : ""}</span></div>
      <div><b>${node.now ? bytes(node.now.bytes) : "—"}</b><span>Now${node.now ? `, ${num(node.now.files)} files` : ""}</span></div>
      <div><b>${node.then ? bytes(node.then.bytes) : "—"}</b><span>On ${date(when)}</span></div>
      ${sub ? html`<div><b>${num(sub.new)} new · ${num(sub.removed)} removed</b><span>Of ${num(sub.n)} subfolders</span></div>` : ""}
    </div>
    ${node.in_new === false ? html`<div class="alert alert-warn">${icon("alert")}<div>This folder is not in the latest scan: it was removed or moved after ${date(when)}.</div></div>` : ""}
    ${node.in_old === false ? html`<div class="alert alert-info">${icon("info")}<div>This folder is new since ${date(when)}.</div></div>` : ""}
    <h3>Grew most</h3>${grew.length ? list(grew) : html`<p class="muted small">Nothing shown here grew.</p>`}
    <h3>Shrank most</h3>${shrank.length ? list(shrank) : html`<p class="muted small">Nothing shown here shrank.</p>`}
    ${node.in_new === false ? "" : html`<p class="small" style="margin:10px 0 0"><a href="${placeHash("history", node.fs ? { fs: node.fs, path: node.path } : null, node.lab && !node.fs ? { lab: node.lab.slug } : {})}">History of ${node.name}</a></p>`}
  </aside>`;
}
