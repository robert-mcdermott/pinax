// SPDX-License-Identifier: Apache-2.0
// Capacity, for administrators: how much each filesystem holds, where that figure comes from, how much of it is
// used, and when it fills at its trend (capacity.py). A capacity set here applies from a day on, in place of what
// the storage reports; earlier ones are kept, and a later one can be scheduled.
import { $$, api, bytes, date, emptyState, html, icon, modal, mount, pct, raw, toast } from "/js/core.js";
import { renderSidebar } from "/js/shell.js";

const month = (iso) => new Date(iso).toLocaleDateString(undefined, { month: "short", year: "numeric" });
const signed = (v) => (v ? `${v > 0 ? "+" : "−"}${bytes(Math.abs(v))}` : "No change");

/** What a filesystem's use is measured by. */
export function measureText(c) {
  return { storage: "space used, as the storage reports it", alloc: "space on disk, from the scans",
    size: "the files' size, since these scans record no space on disk" }[c.measure] || "";
}

/** When it fills, in a few words: the month, "Full", or why there is no date. */
export function fillShort(c) {
  if (!c.capacity.bytes) return "No capacity known";
  if (c.full) return "Full";
  if (!c.forecast?.ok) {
    if (c.forecast?.why === "step") return "Trend starting again after a step";
    return c.forecast?.need ? `Needs ${c.forecast.need.scans} scans over ${Math.round(c.forecast.need.days / 7)} weeks` : "No scans";
  }
  return c.fills?.at ? month(c.fills.at) : "Not within three years";
}

/** The range around the month it fills. */
export function fillRange(c) {
  const f = c.fills;
  if (!f?.at) return "";
  return f.latest ? `likely ${month(f.earliest)} to ${month(f.latest)}` : `likely from ${month(f.earliest)}, perhaps not within three years`;
}

/** A chip for a filesystem that fills within the warning time, or is full. */
export function fillChip(c) {
  if (c.full) return html`<span class="chip chip-failed">${icon("alert")}Full</span>`;
  return c.soon ? html`<span class="chip chip-failed">${icon("alert")}Fills within ${c.warning_months} months</span>` : "";
}

export async function viewCapacity(el) {
  renderSidebar();
  const data = await api("admin/capacity");
  const render = () => mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>Capacity</h1>
      <p>How much each filesystem holds, how much of it is used, and when it fills if it keeps growing as it has over the last six months
        of scans. <code>pinax scan</code> records what the storage reports with each scan; a capacity set here takes its place from its day on,
        and a later one can be scheduled for an expansion.</p></div></div>
    ${data.filesystems.length ? html`<div class="stack">${data.filesystems.map((c) => card(c, data))}</div>`
      : html`<div class="card">${emptyState("server", "No filesystems", "Register a filesystem first.")}</div>`}`);

  const refresh = (summary) => {
    data.filesystems = data.filesystems.map((c) => (c.fs === summary.fs ? summary : c));
    render();
    bind();
  };
  const bind = () => {
    $$("form[data-capacity]", el).forEach((form) => form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const v = Object.fromEntries(new FormData(form));
      try {
        refresh(await api(`admin/capacity/${encodeURIComponent(form.dataset.capacity)}`, { method: "POST",
          body: { starts: v.starts, amount: v.amount.trim(), unit: v.unit } }));
        toast("Capacity set");
      } catch (err) { toast(err.message, "error"); }
    }));
    $$("[data-remove]", el).forEach((b) => b.addEventListener("click", async () => {
      const [fs, id] = b.dataset.remove.split("|");
      const ok = await modal({ title: `Remove the capacity from ${b.dataset.day}?`,
        body: html`<p>Until the next one, the capacity is what applied before it, or what the storage reports.</p>`, confirm: "Remove", tone: "navy" });
      if (!ok) return;
      try {
        refresh(await api(`admin/capacity/${encodeURIComponent(fs)}/${id}`, { method: "DELETE" }));
        toast("Removed");
      } catch (err) { toast(err.message, "error"); }
    }));
  };
  render();
  bind();
}

function card(c, data) {
  const cap = c.capacity;
  const st = c.storage;
  const share = c.share == null ? null : Math.min(1, c.share);
  return html`<div class="card cost-card">
    <div class="card-head"><h2>${c.name}</h2>
      <span class="small ${cap.bytes ? "" : "muted"}">${cap.bytes ? html`<b>${bytes(cap.bytes)}</b> ${cap.source === "set"
        ? `as set, since ${date(`${cap.since}T12:00:00Z`)}` : "as the storage reports it"}` : "No capacity known: set one below"}</span></div>
    ${c.used == null ? html`<p class="muted">Not scanned yet.</p>` : html`
      <div class="stat-row" style="margin-bottom:12px">
        <div class="stat"><div class="n">${bytes(c.used)}</div><div class="l">used${share == null ? "" : ` · ${pct(c.share * 100)} of ${bytes(cap.bytes)}`}</div></div>
        <div class="stat"><div class="n">${c.forecast?.ok ? signed(c.forecast.per_month) : "—"}</div><div class="l">a month, at its trend</div></div>
        <div class="stat"><div class="n">${fillShort(c)}</div><div class="l">${c.fills?.at ? `fills, ${fillRange(c)}` : cap.bytes ? "when it fills" : "set a capacity to see when it fills"}</div></div>
      </div>
      ${share == null ? "" : html`<div class="progress" role="img" aria-label="${pct(c.share * 100)} of the capacity used"><span style="width:${(share * 100).toFixed(1)}%"></span></div>`}
      <p class="small muted" style="margin:12px 0 0">${fillChip(c)} Use is measured by ${measureText(c)}.
        ${st ? `With the scan of ${date(st.at)}, the storage reported ${bytes(st.used)} used of ${bytes(st.total)}, ${bytes(st.available)} available.`
          : "The scans of this filesystem record nothing about the storage (scans made with pinax scan do)."}</p>`}

    <h3>Capacity from a day on</h3>
    ${c.schedule.length ? html`<div class="table-wrap"><table class="table compact"><thead><tr><th>From</th><th class="num">Capacity</th><th></th></tr></thead><tbody>
      ${c.schedule.map((r) => html`<tr><td><span class="nowrap">${date(`${r.starts}T12:00:00Z`)}</span>
          ${r.current ? html` <span class="chip chip-approved">${icon("check")}now</span>` : r.future ? html` <span class="chip chip-queued">scheduled</span>` : ""}</td>
        <td class="num nowrap">${r.amount} ${r.unit}</td>
        <td class="right"><button class="icon-btn" type="button" title="Remove" aria-label="Remove the capacity from ${r.starts}"
          data-day="${r.starts}" data-remove="${c.fs}|${r.id}">${icon("x")}</button></td></tr>`)}</tbody></table></div>`
      : html`<p class="small muted">None set${st ? ": the storage's own figure is used" : ""}.</p>`}
    <form class="toolbar cost-form" data-capacity="${c.fs}">
      <label class="ctl"><span>From</span><input class="input sm" type="date" name="starts" value="${data.today}" required></label>
      <label class="ctl"><span>Capacity</span><input class="input sm" name="amount" inputmode="decimal" placeholder="e.g. 500" required autocomplete="off"></label>
      <label class="ctl"><span>Unit</span><select class="select sm" name="unit" aria-label="Unit">
        ${data.units.map((u) => html`<option ${u === "TB" ? raw("selected") : ""}>${u}</option>`)}</select></label>
      <button class="btn btn-secondary btn-sm" type="submit">${icon("plus")}Set capacity</button>
    </form>
  </div>`;
}
