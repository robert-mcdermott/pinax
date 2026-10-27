// SPDX-License-Identifier: Apache-2.0
// History: how a place grew and aged over its scans. Size by age as stacked columns, then files, cold share,
// and average age as small multiples on the same dates (never two scales on one chart), with one readout for
// all four; or the same as a table.
import { $, $$, ageText, api, bytes, date, emptyState, html, icon, mount, num, parseQuery, pct, placeHash, placeQuery, state } from "/js/core.js";
import { downloadCsv } from "/js/reports.js";
import { scopeHeader } from "/js/shell.js";
import { link, niceTicks, renderTimeChart } from "/js/timechart.js";
import { ageBar, ageColor, buckets, thresholdText } from "/js/viz.js";

const YEAR = 365.25;
const signed = (v, f) => (v ? `${v > 0 ? "+" : "−"}${f(Math.abs(v))}` : "No change");
const share = (p) => (p.present && p.bytes ? p.cold_bytes / p.bytes : null);
const years = (p) => (p.present && p.mean_age_days != null ? p.mean_age_days / YEAR : null);

/** Ticks in the binary unit `bytes` would show the largest value in. */
function byteTicks(max) {
  let unit = 1;
  while (max / unit >= 1024) unit *= 1024;
  return niceTicks(max, { unit });
}
const byteTick = (v) => bytes(v, 1).replace(".0 ", " ");
const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

export async function viewHistory(el, query) {
  const q = parseQuery(query);
  const place = q.fs ? { fs: q.fs, path: q.path } : null;
  const data = await api("history", { query: { ...placeQuery(place), lab: place ? undefined : q.lab } });
  const pts = data.points;
  const cb = data.cold_bucket;
  const view = q.view === "table" ? "table" : "charts";
  const shown = pts.filter((p) => p.present);
  const first = shown[0], now = shown[shown.length - 1];
  // A chosen scan (from the picker) is marked: the newest point on or before that day.
  const marked = state.at ? pts.map((p) => p.taken_at.slice(0, 10)).findLastIndex((d) => d <= state.at) : -1;
  const head = scopeHeader("history", place, { asOf: marked >= 0 ? pts[marked].taken_at : data.as_of, lab: !place ? data.lab : null,
    lede: place ? "" : data.kind === "lab" ? `Every folder of the ${data.name}, over its scans.` : "Over every scan." });
  if (!shown.length) {
    mount(el, html`${head}<div class="card">${emptyState("trend", "No history yet",
      "Nothing here has been scanned. History starts with the first scan that includes this folder.")}</div>`);
    return;
  }
  const filesOf = (p) => p.files, bytesOf = (p) => p.bytes;
  const since = (f) => (shown.length > 1 ? `${signed(f(now) - f(first), f === filesOf ? num : bytes)} since ${date(first.taken_at)}` : "One scan so far");
  const cold = `Unchanged for ${thresholdText(cb)}+`;
  const keep = { view: view === "table" ? "table" : undefined, lab: place ? undefined : q.lab, pruned: q.pruned };

  mount(el, html`
    ${head}
    <div class="kpis">
      <div class="kpi"><div class="label">Size</div><div class="value">${bytes(now.bytes)}</div><div class="sub">${since(bytesOf)}</div></div>
      <div class="kpi" style="--kpi-accent:var(--series-1)"><div class="label">Files</div><div class="value">${num(now.files)}</div><div class="sub">${since(filesOf)}</div></div>
      <div class="kpi" style="--kpi-accent:var(--age-5)"><div class="label">${cold}</div><div class="value">${pct(share(now) * 100)}</div>
        <div class="sub">${shown.length > 1 ? `${pct(share(first) * 100)} at the first scan` : `${bytes(now.cold_bytes)} of the data`}</div></div>
      <div class="kpi" style="--kpi-accent:var(--age-4)"><div class="label">Average age</div><div class="value">${ageText(now.mean_age_days)}</div>
        <div class="sub">${shown.length > 1 ? `${ageText(first.mean_age_days)} at the first scan` : "Since last change, weighted by size"}</div></div>
    </div>
    <div class="toolbar">
      <div class="ctl"><span>View</span><div class="pills" role="group" aria-label="View">
        <button data-view="charts" class="${view === "charts" ? "active" : ""}" aria-pressed="${view === "charts"}">${icon("trend")} Charts</button>
        <button data-view="table" class="${view === "table" ? "active" : ""}" aria-pressed="${view === "table"}">${icon("table")} Table</button></div></div>
      <button class="btn btn-ghost btn-sm" id="h-csv">${icon("download")}Download CSV</button>
    </div>
    ${marked >= 0 ? html`<div class="alert alert-info">${icon("info")}<div>${q.pruned
      ? `The scan of ${date(pts[marked].taken_at)} is no longer kept in full, so the other reports cannot show it: only its folder totals remain, marked here.`
      : `The scan of ${date(pts[marked].taken_at)} is marked.`}</div></div>` : ""}
    ${view === "charts" ? html`
      <div class="card"><div class="card-head"><h2>Size by age</h2><span class="small muted">${num(pts.length)} scan${pts.length === 1 ? "" : "s"}, oldest first</span></div>
        <div class="tc" id="h-size" style="height:300px"></div>
        <div class="ramp-legend" aria-label="Color legend"><span class="lt">Last change</span>
          ${buckets().map((b, i) => html`<span><i style="background:var(${ageColor(i)})"></i>${b.label}</span>`)}</div></div>
      <div class="grid cols-3" style="margin-top:20px">
        <div class="card"><div class="card-head"><h2>Files</h2></div><div class="tc" id="h-files" style="height:190px"></div></div>
        <div class="card"><div class="card-head"><h2>Share ${cold.toLowerCase()}</h2></div><div class="tc" id="h-cold" style="height:190px"></div></div>
        <div class="card"><div class="card-head"><h2>Average age in years</h2></div><div class="tc" id="h-age" style="height:190px"></div></div>
      </div>` : table(pts, cold, marked)}
    ${notes(data)}`);

  $$("[data-view]", el).forEach((b) => b.addEventListener("click", () => {
    location.hash = placeHash("history", place, { ...keep, view: b.dataset.view === "table" ? "table" : undefined });
  }));
  $("#h-csv", el).addEventListener("click", () => downloadCsv("pinax-history.csv",
    ["scan", "present", "bytes", "alloc_bytes", "files", "cold_bytes", "cold_share", "mean_age_days", ...buckets().map((b) => `bytes_${b.short}`)],
    pts.map((p) => [p.taken_at, p.present, p.bytes, p.alloc_bytes, p.files, p.cold_bytes, share(p)?.toFixed(4) ?? "",
      p.mean_age_days ?? "", ...p.age_bytes])));
  if (view === "charts") drawCharts(el, pts, cold, marked);
}

function drawCharts(el, pts, cold, mark) {
  const times = pts.map((p) => new Date(p.taken_at));
  const tip = (i) => String(readout(pts, i, cold));
  let charts = [];
  const onHover = (i) => link(charts)(i);
  const draw = () => {
    charts = [
      renderTimeChart($("#h-size", el), { times, kind: "stack", tip, onHover, mark, ticks: byteTicks, tick: byteTick,
        // The oldest (coldest, blue) data at the bottom: new data lands on top in red, and old data settles.
        series: buckets().map((b, i) => ({ token: ageColor(i), label: b.label, values: pts.map((p) => (p.present ? p.age_bytes[i] : null)) })).reverse(),
        value: (i) => bytes(pts[i].bytes),
        label: `Size by age over ${pts.length} scans, from ${bytes(pts[0].bytes)} to ${bytes(pts[pts.length - 1].bytes)}` }),
      renderTimeChart($("#h-files", el), { times, kind: "line", tip, onHover, mark, ticks: (m) => niceTicks(m, { target: 3 }),
        tick: (v) => compact.format(v), series: [{ token: "--series-1", label: "Files", values: pts.map((p) => (p.present ? p.files : null)) }],
        value: (i) => compact.format(pts[i].files), label: `Files over ${pts.length} scans` }),
      renderTimeChart($("#h-cold", el), { times, kind: "line", tip, onHover, mark, ticks: () => [0, 0.25, 0.5, 0.75, 1],
        tick: (v) => pct(v * 100), series: [{ token: "--age-5", label: cold, values: pts.map(share) }],
        value: (i) => pct(share(pts[i]) * 100), label: `${cold} over ${pts.length} scans` }),
      renderTimeChart($("#h-age", el), { times, kind: "line", tip, onHover, mark, ticks: (m) => niceTicks(m, { target: 3 }),
        tick: (v) => (v ? `${+v.toFixed(1)}` : "0"), series: [{ token: "--age-4", label: "Average age", values: pts.map(years) }],
        value: (i) => ageText(pts[i].mean_age_days), label: `Average age over ${pts.length} scans` }),
    ];
  };
  draw();
  let timer;
  const redraw = () => { clearTimeout(timer); timer = setTimeout(() => { if (document.body.contains($("#h-size", el))) draw(); }, 120); };
  window.addEventListener("resize", redraw);
  window.addEventListener("hashchange", () => window.removeEventListener("resize", redraw), { once: true });
}

/** Everything at one scan: the readout every chart shows. Values lead; labels follow. */
function readout(pts, i, cold) {
  const p = pts[i];
  const prev = pts.slice(0, i).reverse().find((x) => x.present);
  const total = p.bytes || 1;
  return html`<div class="t">${date(p.taken_at, p.snapshots.length === 1)}</div>
    ${p.present ? html`${ageBar(p.age_bytes)}
    <dl>
      <dt>Size</dt><dd><b>${bytes(p.bytes)}</b></dd>
      ${prev ? html`<dt>Change</dt><dd>${signed(p.bytes - prev.bytes, bytes)}</dd>` : ""}
      <dt>Files</dt><dd><b>${num(p.files)}</b></dd>
      <dt>${cold}</dt><dd><b>${pct(share(p) * 100)}</b></dd>
      <dt>Average age</dt><dd><b>${ageText(p.mean_age_days)}</b></dd>
    </dl>
    <dl class="tc-keys">${buckets().map((b, j) => (p.age_bytes[j] ? html`<dt><i class="key" style="background:var(${ageColor(j)})"></i>${b.label}</dt>
      <dd>${bytes(p.age_bytes[j])} <span class="muted">${pct((p.age_bytes[j] / total) * 100)}</span></dd>` : ""))}</dl>`
    : html`<p class="small" style="margin:6px 0 0">Not in this scan.</p>`}`;
}

function table(pts, cold, marked = -1) {
  const rows = pts.map((p, i) => ({ p, i, prev: pts.slice(0, i).reverse().find((x) => x.present) })).reverse();
  return html`<div class="card flush"><div class="table-wrap"><table class="table"><thead><tr>
      <th>Scan</th><th class="num">Size</th><th class="num">Change</th><th class="num">Files</th><th class="num">Change</th>
      <th class="num">${cold}</th><th class="num">Average age</th><th style="width:26%">Age profile (size)</th></tr></thead><tbody>
    ${rows.map(({ p, i, prev }) => html`<tr class="${i === marked ? "marked" : ""}"><td class="nowrap">${date(p.taken_at)}</td>
      ${p.present ? html`<td class="num nowrap">${bytes(p.bytes)}</td><td class="num nowrap">${prev ? signed(p.bytes - prev.bytes, bytes) : "—"}</td>
        <td class="num">${num(p.files)}</td><td class="num nowrap">${prev ? signed(p.files - prev.files, num) : "—"}</td>
        <td class="num">${pct(share(p) * 100)}</td><td class="num nowrap">${ageText(p.mean_age_days)}</td><td>${ageBar(p.age_bytes, { large: true })}</td>`
        : html`<td colspan="7" class="muted">Not in this scan</td>`}</tr>`)}
    </tbody></table></div></div>`;
}

function notes(data) {
  const out = [];
  if (data.kind === "subfolder") {
    out.push(`Below the top level, history covers the scans whose index is kept (the newest ${data.keep_snapshots}).${data.older_scans
      ? ` ${num(data.older_scans)} older scan${data.older_scans === 1 ? " keeps" : "s keep"} totals only for top-level folders, labs, and filesystems.` : ""}`);
  }
  if (data.merged) {
    out.push("This covers more than one filesystem, each scanned on its own schedule: each point is a day with a scan, and each filesystem counts as of its latest scan by then.");
  }
  out.push("Ages are measured from each scan, so data that nobody changes grows older from one scan to the next.");
  return html`<p class="small muted" style="margin:14px 0 0">${out.join(" ")}</p>`;
}
