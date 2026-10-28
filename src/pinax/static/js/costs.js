// SPDX-License-Identifier: Apache-2.0
// Costs, for administrators: what each filesystem's storage costs per month, and how much of it is free. Each price
// and free tier applies from a day on, until the next; earlier ones are kept, so past months keep what they had.
import { $$, api, date, emptyState, html, icon, modal, mount, raw, state, toast } from "/js/core.js";
import { renderSidebar } from "/js/shell.js";

const KINDS = [["lab", "Every lab"], ["department", "Every department"]];

/** A price as money in the site's currency, keeping up to six decimal places when it has them. */
export function money(amount, currency = state.config?.currency || "USD") {
  const n = Number(amount);
  const places = Math.min(6, Math.max(2, (String(amount).split(".")[1] || "").length));
  return new Intl.NumberFormat(undefined, { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: places }).format(n);
}
const freeText = (t) => (t == null ? "none set" : Number(t.amount) === 0 ? "none" : `${t.amount} ${t.unit}`);

export async function viewCosts(el) {
  state.place = null;
  renderSidebar();
  const data = await api("admin/costs");
  const render = () => mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>Costs</h1>
      <p>What each filesystem's storage costs per month, in ${data.currency}, and how much of it is free. Each price and free tier applies
        from a day on, until the next; earlier ones are kept, so past months keep what they had. Charges are for the files' size.</p></div></div>
    ${data.filesystems.length ? html`<div class="stack">${data.filesystems.map((f) => card(f, data))}</div>`
      : html`<div class="card">${emptyState("database", "No filesystems", "Register a filesystem first.")}</div>`}`);

  const refresh = (fs, schedule) => {
    data.filesystems = data.filesystems.map((f) => (f.fs === fs ? schedule : f));
    render();
    bind();
  };
  const send = async (fs, path, body, done) => {
    try {
      const schedule = await api(`admin/costs/${encodeURIComponent(fs)}/${path}`, { method: "POST", body });
      toast(done);
      refresh(fs, schedule);
    } catch (e) { toast(e.message, "error"); }
  };
  const remove = async (fs, path, id, what, past) => {
    const ok = await modal({
      title: `Remove ${what}?`,
      body: past ? html`<p>It has applied since its day, so the charges since then will use what applied before it.</p>`
        : html`<p>It has not started yet.</p>`,
      confirm: "Remove", tone: "navy",
    });
    if (!ok) return;
    try {
      refresh(fs, await api(`admin/costs/${encodeURIComponent(fs)}/${path}/${id}`, { method: "DELETE" }));
      toast("Removed");
    } catch (e) { toast(e.message, "error"); }
  };

  const bind = () => {
    $$("form[data-price]", el).forEach((form) => form.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = Object.fromEntries(new FormData(form));
      send(form.dataset.price, "prices", { starts: v.starts, price: v.price.trim(), unit: v.unit }, "Price added");
    }));
    $$("form[data-free]", el).forEach((form) => form.addEventListener("submit", (e) => {
      e.preventDefault();
      const v = Object.fromEntries(new FormData(form));
      const who = v.who.startsWith("kind:") ? { kind: v.who.slice(5) } : { lab: v.who.slice(4) };
      const ending = e.submitter?.dataset.end === "1";
      send(form.dataset.free, "free-tiers", { starts: v.starts, amount: ending ? null : v.amount.trim(), unit: v.unit, ...who },
        ending ? "Free tier ended" : "Free tier set");
    }));
    $$("[data-remove]", el).forEach((b) => b.addEventListener("click", () => {
      const [fs, path, id, past] = b.dataset.remove.split("|");
      remove(fs, path, id, b.dataset.what, past === "1");
    }));
  };
  render();
  bind();
}

function card(f, data) {
  const today = data.today;
  const unitSelect = (name = "unit", current = "TB") => html`<select class="select sm" name="${name}" aria-label="Unit">
    ${data.units.map((u) => html`<option ${u === current ? raw("selected") : ""}>${u}</option>`)}</select>`;
  const removeButton = (path, row, what) => html`<button class="icon-btn" type="button" title="Remove" aria-label="Remove ${what}"
    data-what="${what}" data-remove="${f.fs}|${path}|${row.id}|${row.future ? 0 : 1}">${icon("x")}</button>`;
  const dayInput = html`<label class="ctl"><span>From</span><input class="input sm" type="date" name="starts" value="${today}" required></label>`;
  const tierRows = (rows) => rows.map((t) => html`<li><span>${t.bytes == null ? "back to its kind's" : freeText(t)} from ${date(`${t.starts}T12:00:00Z`)}${t.future ? html` <span class="chip chip-queued">scheduled</span>` : ""}</span>
    ${removeButton("free-tiers", t, `the free tier from ${t.starts}`)}</li>`);
  return html`<div class="card cost-card">
    <div class="card-head"><h2>${f.name}</h2>
      <span class="small ${f.price ? "" : "muted"}">${f.price ? html`<b>${money(f.price.price)}</b> per ${f.price.unit} per month, since ${date(`${f.price.starts}T12:00:00Z`)}` : "No price yet: its storage is not charged"}</span></div>

    <h3>Price per month</h3>
    ${f.prices.length ? html`<div class="table-wrap"><table class="table compact"><thead><tr><th>From</th><th class="num">Price</th><th></th></tr></thead><tbody>
      ${f.prices.map((p) => html`<tr><td><span class="nowrap">${date(`${p.starts}T12:00:00Z`)}</span>
          ${p.current ? html` <span class="chip chip-approved">${icon("check")}now</span>` : p.future ? html` <span class="chip chip-queued">scheduled</span>` : ""}</td>
        <td class="num nowrap">${money(p.price)} per ${p.unit}</td>
        <td class="right">${removeButton("prices", p, `the price from ${p.starts}`)}</td></tr>`)}</tbody></table></div>` : ""}
    <form class="toolbar cost-form" data-price="${f.fs}">
      ${dayInput}
      <label class="ctl"><span>Price (${state.config?.currency || "USD"})</span><input class="input sm" name="price" inputmode="decimal" placeholder="e.g. 25.50" required autocomplete="off"></label>
      <label class="ctl"><span>Per</span>${unitSelect()}</label>
      <button class="btn btn-secondary btn-sm" type="submit">${icon("plus")}Add price</button>
    </form>

    <h3>Free tier</h3>
    <div class="grid cols-2">${KINDS.map(([k, label]) => html`<div><p class="small" style="margin:0 0 4px"><b>${label}</b>: ${freeText(f.free[k].now)} free</p>
      ${f.free[k].history.length ? html`<ul class="plain-list tier-list">${tierRows(f.free[k].history)}</ul>` : ""}</div>`)}</div>
    ${f.exceptions.length ? html`<h3>Exceptions</h3><div class="grid cols-2">${f.exceptions.map((x) => html`<div>
      <p class="small" style="margin:0 0 4px"><b>${x.lab.name}</b>: ${x.now ? `${freeText(x.now)} free (its own)` : `its kind's (${freeText(f.free[x.lab.kind]?.now)})`}</p>
      <ul class="plain-list tier-list">${tierRows(x.history)}</ul></div>`)}</div>` : ""}
    <form class="toolbar cost-form" data-free="${f.fs}">
      ${dayInput}
      <label class="ctl"><span>For</span><select class="select sm" name="who" aria-label="Who the free tier is for">
        ${KINDS.map(([k, label]) => html`<option value="kind:${k}">${label}</option>`)}
        <optgroup label="One lab or department">${data.labs.filter((l) => l.active).map((l) => html`<option value="lab:${l.slug}">${l.name}</option>`)}</optgroup></select></label>
      <label class="ctl"><span>Free</span><input class="input sm" name="amount" inputmode="decimal" placeholder="e.g. 1" autocomplete="off"></label>
      <label class="ctl"><span>Unit</span>${unitSelect()}</label>
      <button class="btn btn-secondary btn-sm" type="submit">${icon("check")}Set free tier</button>
      <button class="btn btn-ghost btn-sm" type="submit" data-end="1" title="A lab's own free tier ends; its kind's applies again">End a lab's own</button>
    </form>
  </div>`;
}
