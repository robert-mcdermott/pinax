// SPDX-License-Identifier: Apache-2.0
// Storage charges: what each of the person's labs and departments is charged for a month, by filesystem and
// top-level folder, with how each charge is worked out, their past months, and the next twelve at their trend.
// Administrators pick any lab, and have Chargeback: every lab's charges for a month, for finance.
import { api, bytes, date, emptyState, fsInfo, html, icon, kindLabel, mount, num, parseQuery, pct, placeHash, raw, roleLabel, state } from "/js/core.js";
import { downloadCsv } from "/js/reports.js";
import { renderSidebar } from "/js/shell.js";

const UNIT = { GB: 1e9, GiB: 2 ** 30, TB: 1e12, TiB: 2 ** 40 };

/** A charge, already rounded to the currency's smallest unit. */
export const charge = (amount, currency = state.config?.currency || "USD") =>
  new Intl.NumberFormat(undefined, { style: "currency", currency }).format(Number(amount));
/** A price per unit, with up to six decimal places when it has them. */
const price = (amount, currency) => new Intl.NumberFormat(undefined, {
  style: "currency", currency, minimumFractionDigits: 2,
  maximumFractionDigits: Math.min(6, Math.max(2, (String(amount).split(".")[1] || "").length)),
}).format(Number(amount));
const SMALLER = { TB: "GB", TiB: "GiB" };
/** Storage in the unit it is priced in, so the arithmetic reads straight (to three places in a worked charge); a
 * sliver of a terabyte in gigabytes. */
function sized(n, unit, places = 2) {
  if (!unit) return bytes(n);
  if (n && n < UNIT[unit] / 100 && SMALLER[unit]) unit = SMALLER[unit];
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: places }).format(n / UNIT[unit])} ${unit}`;
}
const monthName = (m) => new Date(`${m}-01T12:00:00Z`).toLocaleDateString(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
const dayCount = (d) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(d);
// Months and their days are in UTC, as charges are worked out.
const dayText = (iso) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
// A period's last day: its end is the start of the next.
const lastDay = (iso) => new Date(new Date(iso).getTime() - 1).toISOString();

export async function viewCharges(el, query) {
  state.place = null;
  renderSidebar();
  const q = parseQuery(query);
  const data = await api("charges", { query: { month: q.month, lab: q.lab } });
  const go = (changes) => {
    const next = Object.entries({ month: q.month, lab: q.lab, ...changes }).filter(([, v]) => v);
    location.hash = `#/charges${next.length ? `?${new URLSearchParams(next)}` : ""}`;
  };
  const admin = state.me.sees_charges || state.me.divisions?.length > 0;  // chooses among labs
  const months = data.months.includes(data.month) ? data.months : [data.month, ...data.months];

  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Overview</div><h1>Storage charges</h1>
      <p>What ${admin ? "a lab or department" : "your labs"} pay${admin ? "s" : ""} for storage each month: the average size of the folders over the month,
        less the free tier, at each filesystem's price. Charges are for the files' size.</p></div>
      <div class="actions toolbar">
        ${admin ? html`<label class="ctl"><span>Lab or department</span><select class="select sm" id="c-lab">
          ${data.choices.map((c) => html`<option value="${c.slug}" ${data.labs.some((l) => l.lab.slug === c.slug) ? raw("selected") : ""}>${c.name}</option>`)}</select></label>` : ""}
        <label class="ctl"><span>Month</span><select class="select sm" id="c-month">
          ${months.map((m) => html`<option value="${m}" ${m === data.month ? raw("selected") : ""}>${monthName(m)}</option>`)}</select></label>
        ${state.me.sees_charges ? html`<a class="btn btn-secondary btn-sm" href="#/chargeback?month=${data.month}">${icon("table")}Every lab</a>` : ""}
      </div></div>
    ${!data.priced ? html`<p class="callout">${icon("info")} <b>No storage prices are set yet</b>, so nothing is charged. The sizes below are what each lab stored.</p>` : ""}
    ${data.estimated && data.through ? html`<p class="callout">${icon("clock")} <b>${monthName(data.month)} is not over.</b>
      This is an estimate as of ${date(data.through)}: the latest scan is assumed to hold until the month's end.</p>` : ""}
    ${data.labs.length ? html`<div class="stack">${data.labs.map((l) => labCard(l, data))}</div>`
      : html`<div class="card">${emptyState("tag", "No labs", admin ? "There are no labs or departments yet." : "You are not a member of a lab or department, so nothing is charged to you.")}</div>`}`);

  el.querySelector("#c-month")?.addEventListener("change", (e) => go({ month: e.target.value }));
  el.querySelector("#c-lab")?.addEventListener("change", (e) => go({ lab: e.target.value }));
  // Each lab's month split by a tag, once the month itself is shown.
  data.labs.forEach((l) => splitBox(el.querySelector(`[data-split="${l.lab.slug}"]`), l.lab, data.month, null));
}

/** A lab's month split by a tag's values (a grant, a project), with the tags its folders have to choose from. */
async function splitBox(box, lab, month, tag) {
  if (!box) return;
  let got;
  try {
    got = await api("charges/split", { query: { lab: lab.slug, month, tag }, quiet: true });
  } catch (e) {
    if (e.name !== "AbortError") mount(box, "");
    return;
  }
  const s = got.split;
  if (!s) return mount(box, "");
  mount(box, html`<h3>By tag</h3>
    <div class="toolbar"><label class="ctl"><span>Split by</span><select class="select sm" data-split-tag>
      ${got.options.map((o) => html`<option value="${o.id}" ${o.id === s.tag.id ? raw("selected") : ""}>${o.label}</option>`)}</select></label>
      <button class="btn btn-ghost btn-sm" data-split-csv>${icon("download")}Download CSV</button></div>
    <div class="table-wrap"><table class="table compact"><thead><tr><th>${s.tag.label}</th><th class="num">Average size</th><th class="num">Charge</th></tr></thead><tbody>
      ${s.values.map((v) => html`<tr><td>${v.value}</td><td class="num">${bytes(v.size)}</td><td class="num nowrap">${charge(v.charge, s.currency)}</td></tr>`)}
      <tr><td class="muted">Not tagged</td><td></td><td class="num nowrap">${charge(s.untagged, s.currency)}</td></tr></tbody></table></div>
    <p class="tiny muted">Each value's share of the lab's charge on each filesystem is its folders' share of the lab's average size there; a folder
      inside another tagged one counts for its own value, and a folder with several values splits evenly.</p>`);
  box.querySelector("[data-split-tag]").addEventListener("change", (e) => splitBox(box, lab, month, e.target.value));
  box.querySelector("[data-split-csv]").addEventListener("click", () => downloadCsv(`pinax-charges-${lab.slug}-${month}-by-${s.tag.label.toLowerCase().replace(/\W+/g, "-")}.csv`,
    ["lab", "month", "tag", "value", "average_bytes", "charge", "currency"],
    [...s.values.map((v) => [lab.slug, month, s.tag.label, v.value, v.size, v.charge, s.currency]), [lab.slug, month, s.tag.label, "", "", s.untagged, s.currency]]));
}

function labCard(l, data) {
  const cur = data.currency;
  const admin = state.me.sees_charges || state.me.divisions?.length > 0;
  const link = (m) => `#/charges?${new URLSearchParams(Object.entries({ month: m, lab: admin ? l.lab.slug : undefined }).filter(([, v]) => v))}`;
  const role = l.role ? roleLabel(l.role, l.lab.kind) : null;
  const unit = l.filesystems.flatMap((f) => f.periods).findLast((p) => p.unit)?.unit;  // as the month above
  return html`<section class="card charge-card">
    <div class="card-head"><div><h2>${l.lab.name}</h2>
      <div class="small muted">${kindLabel(l.lab.kind)}${role ? ` · you are its ${role === "PI" ? role : role.toLowerCase()}` : ""}</div></div>
      <div class="charge-total"><span class="small muted">${monthName(data.month)}${data.estimated ? " (estimate)" : ""}</span><b>${charge(l.total, cur)}</b></div></div>
    ${l.filesystems.length ? l.filesystems.map((f) => fsLine(f, data))
      : html`<p class="muted small">Nothing of ${l.lab.name}'s was on a scanned filesystem in ${monthName(data.month)}.</p>`}
    ${l.history.length ? html`<h3>Past months</h3>
      <div class="table-wrap"><table class="table compact"><thead><tr><th>Month</th><th class="num">Average size</th><th class="num">Charge</th></tr></thead><tbody>
      ${l.history.map((h) => html`<tr class="${h.month === data.month ? "current" : ""}"><td>${h.month === data.month ? html`<b aria-current="page">${monthName(h.month)}</b>`
        : html`<a class="rowlink" href="${link(h.month)}">${monthName(h.month)}</a>`}${h.estimated ? html` <span class="chip chip-queued">estimate</span>` : ""}</td>
        <td class="num">${sized(h.size, unit)}</td><td class="num nowrap">${charge(h.total, cur)}</td></tr>`)}</tbody></table></div>` : ""}
    <div data-split="${l.lab.slug}"></div>
    ${l.ahead ? ahead(l.ahead, cur) : ""}
  </section>`;
}

const between = (r, cur) => `${charge(r.low, cur)} – ${charge(r.high, cur)}`;

/** Why a filesystem's charges ahead are at its latest size rather than a trend. */
function noTrend(f) {
  return f.why === "step" ? `its size changed more than usual by ${date(f.step.at)}, so its trend starts again then`
    : `a trend needs 6 scans over 8 weeks, and there ${f.scans === 1 ? "is 1" : `are ${num(f.scans)}`}`;
}

/** A lab's charges for the next twelve months, at its trend on each filesystem. */
function ahead(a, cur) {
  const flat = a.filesystems.filter((f) => !f.trend);
  return html`<h3>The next 12 months</h3>
    <p class="small muted" style="margin-top:0">At the trend of the folders it owns now (from the last six months of scans), with the prices and free
      tiers as scheduled: about <b>${charge(a.total.charge, cur)}</b> in all, likely ${between(a.total, cur)}.
      ${flat.map((f) => `On ${f.fs_name}, at its size in the latest scan: ${noTrend(f)}.`).join(" ")}
      ${a.filesystems.some((f) => !f.priced) ? "Storage on a filesystem with no price is not charged." : ""}</p>
    <div class="table-wrap"><table class="table compact"><thead><tr><th>Month</th><th class="num">Estimate</th><th class="num">Likely between</th></tr></thead><tbody>
    ${a.months.map((m) => html`<tr><td>${monthName(m.month)}</td><td class="num nowrap">${charge(m.charge, cur)}</td><td class="num nowrap">${between(m, cur)}</td></tr>`)}
    </tbody></table></div>`;
}

function fsLine(f, data) {
  const cur = data.currency;
  const unit = f.periods.findLast((p) => p.unit)?.unit;
  const root = fsInfo(f.fs)?.root;
  return html`<div class="charge-line">
    <div class="charge-line-head"><h3>${f.fs_name}</h3><b class="nowrap">${f.unpriced && Number(f.charge) === 0 ? "Not charged" : charge(f.charge, cur)}</b></div>
    <p class="small muted">${sized(f.size, unit)} on average over the month${unit ? html`, ${sized(f.billable, unit)} of it charged` : ""}.</p>
    <ol class="plain-list periods">${f.periods.map((p) => period(p, data))}</ol>
    ${f.periods.length > 1 && !f.unpriced ? html`<p class="tiny muted">Each part is shown to the cent; the charge is worked out exactly and rounded once.</p>` : ""}
    <ul class="plain-list charge-folders" aria-label="Folders on ${f.fs_name}">${f.folders.map((d) => html`<li>
      ${d.open && root ? html`<a href="${placeHash("explore", { fs: f.fs, path: `${root}/${d.folder}` })}">${icon("folder")}${d.folder}</a>` : html`<span>${icon("folder")}${d.folder}</span>`}
      <span class="muted nowrap">${sized(d.size, unit)} · ${pct(f.size ? (d.size / f.size) * 100 : 0)}</span></li>`)}</ul>
  </div>`;
}

/** One period's charge, worked out in words: its average size, less its free tier, at its price, for its days. */
function period(p, data) {
  const cur = data.currency;
  const first = dayText(p.from);
  const last = dayText(lastDay(p.to));
  const span = html`<b class="nowrap">${first}${last !== first ? ` – ${last}` : ""}</b>`;
  const part = p.share < 0.9995 ? `, for ${dayCount(p.days)} of ${data.days} days` : "";
  if (!p.price) return html`<li>${span}: ${bytes(p.size)} on average${part}. No price yet, so not charged.</li>`;
  const free = p.free ? `, less ${p.free_amount} ${p.free_unit} free` : ", with no free tier";
  return html`<li>${span}: ${sized(p.size, p.unit, 3)} on average${free}, so ${sized(p.billable, p.unit, 3)}
    at ${price(p.price, cur)} per ${p.unit} per month${part}: <b class="nowrap">${charge(p.charge, cur)}</b></li>`;
}

// ---------- Chargeback, for administrators ----------
/** Every lab's and department's charges for a month, totals by filesystem, the storage no one owns, and the CSV. */
export async function viewChargeback(el, query) {
  state.place = null;
  renderSidebar();
  const q = parseQuery(query);
  const data = await api("admin/costs/charges", { query: { month: q.month } });
  const cur = data.currency;
  const months = data.months.includes(data.month) ? data.months : [data.month, ...data.months];
  const unitOf = (f) => f.prices.at(-1)?.unit;
  const unit = data.by_filesystem.map(unitOf).find(Boolean);  // for sizes across filesystems
  const charged = data.labs.filter((l) => Number(l.total) > 0).length;
  const unbilled = data.unbilled.reduce((t, u) => t + u.size, 0);
  const csv = `/api/admin/costs/charges?${new URLSearchParams({ month: data.month, fmt: "csv" })}`;
  const labLink = (l) => `#/charges?${new URLSearchParams({ lab: l.lab.slug, month: data.month })}`;
  const priceList = (f) => (f.prices.length ? f.prices.map((p, i) => html`${i ? html`<br>` : ""}<span class="nowrap">${price(p.price, cur)} per ${p.unit}</span>${f.prices.length > 1 ? html` <span class="muted nowrap">from ${dayText(`${p.from}T00:00:00Z`)}</span>` : ""}`)
    : html`<span class="muted">No price</span>`);
  const size = (l) => l.filesystems.reduce((t, f) => t + f.size, 0);
  const billable = (l) => l.filesystems.reduce((t, f) => t + f.billable, 0);

  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>Chargeback</h1>
      <p>Every lab's and department's storage charges for a month, as each sees them on <a href="#/charges">Storage charges</a>, with totals by
        filesystem and the storage no one owns. The statement downloads as CSV: a row for each lab or department on each filesystem.</p></div>
      <div class="actions toolbar">
        <label class="ctl"><span>Month</span><select class="select sm" id="c-month">
          ${months.map((m) => html`<option value="${m}" ${m === data.month ? raw("selected") : ""}>${monthName(m)}</option>`)}</select></label>
        <a class="btn btn-primary btn-sm" href="${csv}" download>${icon("download")}Download CSV</a>
      </div></div>
    ${data.estimated && data.through ? html`<p class="callout">${icon("clock")} <b>${monthName(data.month)} is not over.</b>
      This is an estimate as of ${date(data.through)}: the latest scan is assumed to hold until the month's end. Its statement is marked as an estimate.</p>` : ""}
    ${data.by_filesystem.some((f) => f.unpriced) ? html`<p class="callout">${icon("info")} <b>Some storage has no price</b>
      for part or all of the month, so it is not charged. Set prices on <a href="#/costs">Costs</a>.</p>` : ""}
    <div class="kpis">
      <div class="kpi" style="--kpi-accent:var(--fh-teal)"><div class="label">Total charges</div><div class="value">${charge(data.total, cur)}</div><div class="sub">${monthName(data.month)}${data.estimated ? " · estimate" : ""}</div></div>
      <div class="kpi"><div class="label">Labs and departments charged</div><div class="value">${num(charged)}</div><div class="sub">of ${num(data.labs.length)} with storage</div></div>
      ${state.me.sees_all ? html`<a class="kpi" href="#/folders?unmapped=1" style="--kpi-accent:var(--fh-yellow)"><div class="label">Unbilled storage</div><div class="value">${sized(unbilled, unit)}</div><div class="sub">On average, in folders no one owns · assign owners</div></a>`
        : html`<div class="kpi" style="--kpi-accent:var(--fh-yellow)"><div class="label">Unbilled storage</div><div class="value">${sized(unbilled, unit)}</div><div class="sub">On average, in folders no one owns</div></div>`}
    </div>
    <div class="stack">
      <div class="card flush"><div class="card-head"><h2>By filesystem</h2></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Filesystem</th><th>Price per month</th><th class="num hide-narrow">Labs' storage</th><th class="num hide-narrow">Unbilled</th><th class="num">Charge</th></tr></thead><tbody>
        ${data.by_filesystem.map((f) => html`<tr><td><b>${f.fs_name}</b></td><td>${priceList(f)}</td>
          <td class="num hide-narrow">${sized(f.size, unitOf(f))}</td><td class="num hide-narrow">${f.unbilled ? sized(f.unbilled, unitOf(f)) : "—"}</td>
          <td class="num nowrap">${charge(f.charge, cur)}</td></tr>`)}
        <tr class="total"><td><b>Total</b></td><td></td><td class="hide-narrow"></td><td class="hide-narrow"></td><td class="num nowrap"><b>${charge(data.total, cur)}</b></td></tr></tbody></table></div></div>
      <div class="card flush"><div class="card-head"><h2>Labs and departments</h2><span class="small muted">${num(data.labs.length)}, the most first</span></div>
        ${data.labs.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Lab or department</th><th class="hide-narrow">Kind</th><th class="num hide-narrow">Average size</th><th class="num hide-narrow">Charged on</th><th class="num">Charge</th></tr></thead><tbody>
        ${data.labs.map((l) => html`<tr><td><a class="rowlink" href="${labLink(l)}">${l.lab.name}</a>${l.lab.active ? "" : html` <span class="chip chip-muted">inactive</span>`}
            <div class="sub">${l.filesystems.map((f) => f.fs_name).join(", ")}</div></td>
          <td class="hide-narrow">${kindLabel(l.lab.kind)}</td><td class="num hide-narrow">${sized(size(l), unit)}</td><td class="num hide-narrow">${sized(billable(l), unit)}</td>
          <td class="num nowrap">${charge(l.total, cur)}</td></tr>`)}</tbody></table></div>`
          : emptyState("tag", "No charges", `No lab or department had storage on a scanned filesystem in ${monthName(data.month)}.`)}</div>
      <div id="c-ahead"></div>
      ${data.unbilled.length ? html`<div class="card flush"><div class="card-head"><h2>Unbilled</h2><a class="small" href="#/folders?unmapped=1">Assign owners</a></div>
        <div class="table-wrap"><table class="table"><thead><tr><th>Folder</th><th class="hide-narrow">Filesystem</th><th class="num">Average size</th></tr></thead><tbody>
        ${data.unbilled.flatMap((u) => u.folders.map((f) => html`<tr><td>${f.folder ? html`${icon("folder")} ${f.folder}` : html`<span class="muted">(files in the root)</span>`}
            <div class="sub hide-wide">${u.fs_name}</div></td><td class="hide-narrow">${u.fs_name}</td><td class="num nowrap">${sized(f.size, unitOf(u))}</td></tr>`))}</tbody></table></div></div>` : ""}
    </div>`);

  el.querySelector("#c-month")?.addEventListener("change", (e) => { location.hash = `#/chargeback?month=${e.target.value}`; });
  // The next twelve months take longer to work out, so they follow the month's charges.
  const box = el.querySelector("#c-ahead");
  mount(box, html`<div class="card"><div class="card-head"><h2>The next 12 months</h2><span class="small muted">Working out…</span></div></div>`);
  api("admin/costs/ahead", { quiet: true }).then((a) => mount(box, a.labs ? aheadTotals(a, cur) : ""))
    .catch((e) => { if (e.name !== "AbortError") mount(box, ""); });
}

/** Every lab's charges for the next twelve months, added up. */
function aheadTotals(a, cur) {
  return html`<div class="card flush"><div class="card-head"><h2>The next 12 months</h2><span class="small muted">About ${charge(a.total.charge, cur)}, likely ${between(a.total, cur)}</span></div>
    <p class="small muted" style="margin:0;padding:0 22px 12px">Every lab's and department's charges at the trend of the folders it owns now, with the prices and free tiers as scheduled, added up.${a.without_trend
      ? ` ${num(a.without_trend)} of the ${num(a.labs)} have no trend yet on some filesystem and are counted there at their size in the latest scan.` : ""} Storage no one owns is not counted.</p>
    <div class="table-wrap"><table class="table"><thead><tr><th>Month</th><th class="num">Estimate</th><th class="num">Likely between</th></tr></thead><tbody>
    ${a.months.map((m) => html`<tr><td>${monthName(m.month)}</td><td class="num nowrap">${charge(m.charge, cur)}</td><td class="num nowrap">${between(m, cur)}</td></tr>`)}</tbody></table></div></div>`;
}
