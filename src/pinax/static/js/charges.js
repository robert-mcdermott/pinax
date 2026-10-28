// SPDX-License-Identifier: Apache-2.0
// Storage charges: what each of the person's labs and departments is charged for a month, by filesystem and
// top-level folder, with how each charge is worked out, and their past months. Administrators pick any lab.
import { api, bytes, date, emptyState, fsInfo, html, icon, kindLabel, mount, parseQuery, pct, placeHash, raw, roleLabel, state } from "/js/core.js";
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
/** Storage in the unit it is priced in, so the arithmetic reads straight (to three places in a worked charge). */
const sized = (n, unit, places = 2) => (unit ? `${new Intl.NumberFormat(undefined, { maximumFractionDigits: places }).format(n / UNIT[unit])} ${unit}` : bytes(n));
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
  const admin = state.me.is_admin;
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
      </div></div>
    ${!data.priced ? html`<p class="callout">${icon("info")} <b>No storage prices are set yet</b>, so nothing is charged. The sizes below are what each lab stored.</p>` : ""}
    ${data.estimated && data.through ? html`<p class="callout">${icon("clock")} <b>${monthName(data.month)} is not over.</b>
      This is an estimate as of ${date(data.through)}: the latest scan is assumed to hold until the month's end.</p>` : ""}
    ${data.labs.length ? html`<div class="stack">${data.labs.map((l) => labCard(l, data))}</div>`
      : html`<div class="card">${emptyState("tag", "No labs", admin ? "There are no labs or departments yet." : "You are not a member of a lab or department, so nothing is charged to you.")}</div>`}`);

  el.querySelector("#c-month")?.addEventListener("change", (e) => go({ month: e.target.value }));
  el.querySelector("#c-lab")?.addEventListener("change", (e) => go({ lab: e.target.value }));
}

function labCard(l, data) {
  const cur = data.currency;
  const admin = state.me.is_admin;
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
  </section>`;
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
