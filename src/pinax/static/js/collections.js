// SPDX-License-Identifier: Apache-2.0
// Collections: data found by its tags, across filesystems (tags.collection). Each condition is a tag and a value (or
// any value); a folder matches when it has every one, on its own or from a folder above, and the outermost such
// folders are the collection, with their totals, cost, history, and forecast. Conditions are kept in the address.
import { $, $$, api, bytes, displayPath, emptyState, html, icon, mount, num, parseQuery, pct, placeHash, raw, state } from "/js/core.js";
import { charge } from "/js/charges.js";
import { downloadCsv } from "/js/reports.js";
import { renderSidebar } from "/js/shell.js";
import { niceTicks, renderTimeChart } from "/js/timechart.js";

const wantedOf = (query) => new URLSearchParams(query || "").getAll("want");

export async function viewCollections(el, query) {
  state.place = null;
  renderSidebar();
  const wanted = wantedOf(query);
  // Each condition is its own `want`, so the query string is built here rather than by api().
  const qs = new URLSearchParams(wanted.map((w) => ["want", w]));
  const [{ tags }, data] = await Promise.all([api("tags/filters"), wanted.length ? api(`tags/collection?${qs}`) : Promise.resolve(null)]);
  const conditions = wanted.length ? wanted.map((w) => { const [id, ...v] = w.split(":"); return { id, value: v.join(":") }; }) : [{ id: "", value: "" }];
  const row = (c, i) => {
    const tag = tags.find((t) => String(t.id) === c.id);
    return html`<div class="toolbar coll-row" data-row="${i}">
      <label class="ctl"><span>Tag</span><select class="select sm" data-field="id"><option value="">Choose a tag</option>
        ${tags.map((t) => html`<option value="${t.id}" ${String(t.id) === c.id ? raw("selected") : ""}>${t.label}${t.lab ? ` (${t.lab.name})` : ""}</option>`)}</select></label>
      ${tag && tag.type !== "flag" ? html`<label class="ctl"><span>Value</span><select class="select sm" data-field="value"><option value="">Any value</option>
        ${(tag.type === "choice" ? tag.choices : tag.values).map((v) => html`<option ${v === c.value ? raw("selected") : ""}>${v}</option>`)}</select></label>` : ""}
      ${i ? html`<button class="icon-btn" title="Take this condition away" aria-label="Take this condition away" data-drop="${i}">${icon("x")}</button>` : ""}</div>`;
  };
  const t = data?.total;
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Overview</div><h1>Collections</h1>
      <p>Data found by its tags, wherever it is: every folder that has all of the tags below, on its own or from a folder above it${state.me.is_admin ? "" : ", among your labs' folders"}.
        A folder inside another that matches is counted with it.</p></div></div>
    <div class="card"><div id="coll-rows">${conditions.map(row)}</div>
      <div class="toolbar" style="margin-top:8px"><button class="btn btn-ghost btn-sm" id="coll-more">${icon("plus")}And another tag</button>
        <button class="btn btn-primary btn-sm" id="coll-find">${icon("search")}Find</button></div></div>
    ${!data ? html`<div class="card" style="margin-top:20px">${emptyState("tag", "Choose a tag", "Folders are tagged from their panel on the age map.")}</div>`
    : !data.folders.length ? html`<div class="card" style="margin-top:20px">${emptyState("tag", "Nothing has those tags", "No folder you can see has every one of them.")}</div>`
    : html`
      <div class="kpis" style="margin-top:20px">
        <div class="kpi"><div class="label">Folders</div><div class="value">${num(t.folders)}</div><div class="sub">${num(t.files)} files</div></div>
        <div class="kpi" style="--kpi-accent:var(--fh-teal)"><div class="label">Size</div><div class="value">${bytes(t.bytes)}</div>
          <div class="sub">${data.forecast?.ok ? `${bytes(data.forecast.points.at(-1).value)} in a year at its trend` : "At the latest scans"}</div></div>
        <div class="kpi" style="--kpi-accent:var(--age-5)"><div class="label">Cold</div><div class="value">${pct(t.bytes ? (t.cold_bytes / t.bytes) * 100 : 0)}</div><div class="sub">${bytes(t.cold_bytes)}</div></div>
        <div class="kpi" style="--kpi-accent:var(--strong)"><div class="label">A month</div><div class="value">${t.monthly == null ? "—" : charge(t.monthly, t.currency)}</div>
          <div class="sub">Its share of its labs' charges</div></div>
      </div>
      ${data.points.length > 1 ? html`<div class="card"><div class="card-head"><h2>Size over time</h2><span class="small muted">From when its folders were tagged</span></div>
        <div class="tc" id="coll-chart" style="height:220px"></div></div>` : ""}
      <div class="card flush" style="margin-top:20px"><div class="card-head"><h2>Folders</h2><button class="btn btn-ghost btn-sm" id="coll-csv">${icon("download")}Download CSV</button></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Folder</th><th class="hide-narrow">Owned by</th><th class="num">Size</th>
          <th class="num hide-narrow">Files</th><th class="num">Cold</th><th class="num">A month</th></tr></thead><tbody>
        ${data.folders.map((f) => html`<tr><td><a class="rowlink" href="${placeHash("explore", { fs: f.fs, path: f.path })}">${f.name}</a>
            <div class="sub">${displayPath(f.fs, f.path)}</div><div class="sub">${icon("tag")} ${f.tags.join(", ")}</div>
            ${f.status !== "here" ? html`<div class="sub">Not at its path in the latest scan</div>` : ""}</td>
          <td class="hide-narrow">${f.lab ? f.lab.name : "No one"}</td><td class="num nowrap">${bytes(f.bytes)}</td>
          <td class="num hide-narrow">${num(f.files)}</td><td class="num">${pct(f.bytes ? (f.cold_bytes / f.bytes) * 100 : 0)}</td>
          <td class="num nowrap">${f.monthly == null ? "—" : charge(f.monthly, t.currency)}</td></tr>`)}</tbody></table></div></div>`}`);

  const read = () => $$(".coll-row", el).map((r) => ({ id: $("[data-field=id]", r).value, value: $("[data-field=value]", r)?.value || "" }));
  const go = (list) => {
    const q = new URLSearchParams();
    list.filter((c) => c.id).forEach((c) => q.append("want", c.value ? `${c.id}:${c.value}` : c.id));
    location.hash = `#/collections${q.toString() ? `?${q}` : ""}`;
  };
  const bindRows = () => {
    $$("[data-drop]", el).forEach((b) => b.addEventListener("click", () => go(read().filter((_, i) => i !== Number(b.dataset.drop)))));
    // A condition's tag changed: its value starts again from any.
    $$("[data-field=id]", el).forEach((s) => s.addEventListener("change", () => {
      const list = read();
      list[Number(s.closest(".coll-row").dataset.row)].value = "";
      go(list);
    }));
  };
  bindRows();
  $("#coll-find", el).addEventListener("click", () => go(read()));
  $("#coll-more", el).addEventListener("click", () => {  // a new condition, filled in before anything is found
    mount($("#coll-rows", el), html`${[...read(), { id: "", value: "" }].map(row)}`);
    bindRows();
  });
  $("#coll-csv", el)?.addEventListener("click", () => downloadCsv("pinax-collection.csv",
    ["filesystem", "path", "owner", "bytes", "files", "cold_bytes", "mean_age_days", "monthly", "currency", "tags"],
    data.folders.map((f) => [f.fs, f.path, f.lab?.slug ?? "", f.bytes, f.files, f.cold_bytes, f.mean_age_days ?? "", f.monthly ?? "", t.currency, f.tags.join("; ")])));
  if (data?.points.length > 1) {
    const pts = data.points;
    const ahead = data.forecast?.ok ? data.forecast : null;
    renderTimeChart($("#coll-chart", el), {
      times: [...pts.map((p) => new Date(p.taken_at)), ...(ahead ? ahead.points.map((p) => new Date(p.at)) : [])], kind: "line",
      series: [{ token: "--series-1", label: "Size", values: pts.map((p) => (p.present ? p.bytes : null)) }],
      ticks: (m) => niceTicks(m, { target: 3 }), tick: (v) => bytes(v, 1).replace(".0 ", " "), value: (i) => bytes(pts[i].bytes),
      ahead: ahead && { from: pts.length - 1, anchor: pts.at(-1).bytes, values: ahead.points.map((p) => p.value),
        low: ahead.points.map((p) => p.low), high: ahead.points.map((p) => p.high), label: bytes(ahead.points.at(-1).value) },
      tip: (i) => (i < pts.length ? `<div class="t">${new Date(pts[i].taken_at).toLocaleDateString()}</div><dl><dt>Size</dt><dd><b>${bytes(pts[i].bytes)}</b></dd></dl>`
        : `<div class="t">Forecast</div><dl><dt>Size</dt><dd><b>${bytes(ahead.points[i - pts.length].value)}</b></dd></dl>`),
      label: `Size of the collection over ${pts.length} scans` });
  }
}
