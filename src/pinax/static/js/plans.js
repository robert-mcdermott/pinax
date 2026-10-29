// SPDX-License-Identifier: Apache-2.0
// Cleanup plans: the folders each lab means to free space from, and how (plans.py). Pinax deletes, moves, and
// compresses nothing: a plan records what a lab means to do, and later scans show what happened. Folders are added
// from the age map's panel; the plan page lists them for each of the person's labs.
import { $, $$, api, bytes, date, displayPath, emptyState, html, icon, modal, mount, num, parseQuery, placeHash, raw, state, toast } from "/js/core.js";
import { charge } from "/js/charges.js";
import { downloadCsv } from "/js/reports.js";
import { renderSidebar } from "/js/shell.js";

const ACTIONS = { delete: "Delete", move: "Move", compress: "Compress" };
export const actionText = (item) => (item.action === "move" ? `Move to ${item.to?.name ?? "another filesystem"}` : ACTIONS[item.action]);
const planHash = (lab) => `#/plans${lab ? `?lab=${encodeURIComponent(lab)}` : ""}`;
const totalText = (t) => `${num(t.folders)} folder${t.folders === 1 ? "" : "s"} · ${bytes(t.bytes)}`;
/** What a plan would save a month, in a few words. */
const savesText = (s) => (Number(s.monthly) > 0 ? `saves ${s.estimate ? "about " : ""}${charge(s.monthly, s.currency)} a month`
  : s.filesystems.some((f) => f.priced) ? "saves nothing yet" : "no price set");

/** The form for a folder's place in a plan: what to do with it, and a note. */
function itemForm(targets, item = null) {
  const action = item?.action || "delete";
  const radio = (key, label, extra = "") => html`<label class="radio"><input type="radio" name="action" value="${key}" ${key === action ? raw("checked") : ""}>
    <span>${label}</span>${extra}</label>`;
  return html`<form class="stack plan-form" style="gap:10px">
    ${radio("delete", "Delete it")}
    ${targets.length ? radio("move", "Move it to", html` <select class="select sm" name="to" aria-label="The filesystem it moves to">
      ${targets.map((t) => html`<option value="${t.slug}" ${t.slug === item?.to?.slug ? raw("selected") : ""}>${t.name}</option>`)}</select>`) : ""}
    ${radio("compress", "Compress it")}
    <label class="field"><span class="small">Note (optional)</span><textarea class="input" name="note" rows="3" maxlength="2000"
      placeholder="e.g. Published in 2016; the raw reads are in SRA">${item?.note || ""}</textarea></label>
    <p class="small muted" style="margin:0">Pinax does not delete, move, or compress anything. The plan records what your lab means to do; do it
      yourselves or ask the storage team, and later scans show what was done.</p>
  </form>`;
}

const formValues = (wrap) => {
  const f = Object.fromEntries(new FormData($("form", wrap)));
  return { action: f.action, to: f.action === "move" ? f.to : null, note: f.note || "" };
};

/** Add a folder to its lab's plan (with an action chosen to start with); resolves to the new item, or null. */
export function addToPlan(fs, path, here, action = "delete") {
  return modal({
    title: `Add ${path.split("/").pop()} to the ${here.lab.name}'s cleanup plan`,
    body: itemForm(here.targets, { action }), confirm: "Add to plan",
    onConfirm: async (wrap) => {
      const item = await api("plans", { method: "POST", body: { fs, path, ...formValues(wrap) } });
      toast("Added to the cleanup plan");
      return item;
    },
  });
}

function changeItem(item, targets) {
  return modal({
    title: `${item.name}: what to do`, body: itemForm(targets, item), confirm: "Save",
    onConfirm: async (wrap) => {
      const changed = await api(`plans/${item.id}`, { method: "PUT", body: formValues(wrap) });
      toast("Saved");
      return changed;
    },
  });
}

async function removeItem(item) {
  const ok = await modal({ title: `Take ${item.name} out of the plan?`, body: html`<p>Its history is kept; you can add it again.</p>`,
    confirm: "Take it out", tone: "navy" });
  if (!ok) return false;
  await api(`plans/${item.id}`, { method: "DELETE" });
  toast("Taken out of the plan");
  return true;
}

/** The age map panel's part: this folder's place in its lab's plan, or the button that adds it. */
export async function planBox(box, fs, path) {
  let here;
  try {
    here = await api("plans/here", { query: { fs, path }, quiet: true });
  } catch (e) {
    if (e.name !== "AbortError") mount(box, "");
    return;
  }
  if (!here.plannable) return mount(box, "");
  const draw = () => {
    const item = here.item, inside = here.inside;
    mount(box, html`<h3>Cleanup plan</h3>
      ${item ? html`<p class="small" style="margin:0 0 8px"><span class="chip chip-review">${icon("archive")}${actionText(item)}</span>
          In the plan since ${date(item.added_at)}${item.added_by ? `, added by ${item.added_by}` : ""}.</p>
        <div class="toolbar"><button class="btn btn-secondary btn-sm" data-plan="change">Change</button>
          <button class="btn btn-ghost btn-sm" data-plan="remove">Take it out</button></div>`
        : inside ? html`<p class="small" style="margin:0 0 8px">Inside <b>${inside.name}</b>, which is in the plan (${actionText(inside).toLowerCase()}).</p>`
        : here.protected ? html`<p class="small" style="margin:0 0 8px">${icon("shield")} ${here.protected.why}.</p>`
        : html`<p class="small muted" style="margin:0 0 8px">Mark this folder for your lab to delete, move to cheaper storage, or compress, and see what it would save.</p>
          <button class="btn btn-secondary btn-sm" data-plan="add">${icon("plus")}Add to cleanup plan</button>`}
      <p class="small" style="margin:10px 0 0"><a href="${planHash(here.lab.slug)}">The ${here.lab.name}'s plan</a>: ${totalText(here.total)}${here.total.folders ? ` · ${savesText(here.savings)}` : ""}</p>`);
    $("[data-plan=add]", box)?.addEventListener("click", async () => {
      if (await addToPlan(fs, path, here)) refresh();
    });
    $("[data-plan=change]", box)?.addEventListener("click", async () => {
      if (await changeItem(here.item, here.targets)) refresh();
    });
    $("[data-plan=remove]", box)?.addEventListener("click", async () => {
      if (await removeItem(here.item)) refresh();
    });
  };
  const refresh = async () => {
    here = await api("plans/here", { query: { fs, path } });
    draw();
  };
  draw();
}

// ---------- The plan page ----------
export async function viewPlans(el, query) {
  state.place = null;
  renderSidebar();
  const q = parseQuery(query);
  const admin = state.me.is_admin;
  if (admin && !q.lab) return everyPlan(el);
  const data = await api("plans", { query: { lab: q.lab } });
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Overview</div><h1>Cleanup plans</h1>
      <p>The folders ${admin ? "a lab or department" : "your labs"} mean${admin ? "s" : ""} to delete, move to cheaper storage, or compress. Pinax does none of
        it: each plan records what the lab means to do, and later scans show what was done. Add folders from the age map's panel.</p></div>
      ${data.choices.length > 1 || admin ? html`<div class="actions toolbar"><label class="ctl"><span>Lab or department</span><select class="select sm" id="p-lab">
        ${!q.lab && !data.labs.length ? html`<option value="">Choose</option>` : ""}
        ${data.choices.map((c) => html`<option value="${c.slug}" ${data.labs.some((l) => l.lab.slug === c.slug) && data.labs.length === 1 ? raw("selected") : ""}>${c.name}</option>`)}</select></label></div>` : ""}
    </div>
    ${data.labs.length ? html`<div class="stack">${data.labs.map((p) => planCard(p))}</div>`
      : html`<div class="card">${emptyState("archive", "No plan to show", admin ? "Choose a lab or department." : "You are not a member of a lab or department.")}</div>`}`);
  $("#p-lab", el)?.addEventListener("change", (e) => { location.hash = planHash(e.target.value); });
  $$("[data-item]", el).forEach((b) => b.addEventListener("click", async () => {
    const plan = data.labs.find((p) => p.items.some((i) => String(i.id) === b.dataset.item));
    const item = plan.items.find((i) => String(i.id) === b.dataset.item);
    const targets = (await api("plans/here", { query: { fs: item.fs, path: item.path } })).targets;
    const done = b.dataset.do === "remove" ? await removeItem(item) : await changeItem(item, targets);
    if (done) viewPlans(el, query);
  }));
  $$("[data-csv]", el).forEach((b) => b.addEventListener("click", () => planCsv(data.labs.find((p) => p.lab.slug === b.dataset.csv))));
  // Suggestions read each lab's latest scans, so they follow the plans.
  data.labs.forEach(async (p) => {
    const box = $(`[data-suggest="${p.lab.slug}"]`, el);
    if (!box) return;
    let got;
    try {
      got = (await api("plans/suggestions", { query: { lab: p.lab.slug }, quiet: true })).suggestions;
    } catch (e) {
      if (e.name !== "AbortError") mount(box, "");
      return;
    }
    mount(box, suggestionsTable(got, p.savings.currency));
    $$("[data-suggestion]", box).forEach((b) => b.addEventListener("click", async () => {
      const s = got[Number(b.dataset.suggestion)];
      const here = await api("plans/here", { query: { fs: s.fs, path: s.path } });
      if (await addToPlan(s.fs, s.path, here, s.action)) viewPlans(el, query);
    }));
  });
  $$("[data-moved]", el).forEach((b) => b.addEventListener("click", async () => {
    try {
      await api(`plans/${b.dataset.moved}/moved`, { method: "POST", body: { accept: b.dataset.accept === "1" } });
      toast(b.dataset.accept === "1" ? "The plan follows it" : "Marked as gone");
      viewPlans(el, query);
    } catch (e) { toast(e.message, "error"); }
  }));
}

/** What the plan saves on each filesystem: the lab's monthly charge now and after. */
function savingsNote(s) {
  const cur = s.currency;
  const lines = s.filesystems.map((f) => (!f.priced ? `${f.fs_name} has no price, so nothing there is charged.`
    : f.added && !f.freed ? `${f.fs_name}: ${bytes(f.added)} moved in, ${charge(f.now, cur)} a month now, ${charge(f.after, cur)} after.`
    : `${f.fs_name}: ${bytes(f.freed)} freed${f.added ? ` and ${bytes(f.added)} moved in` : ""}, the lab's charge ${charge(f.now, cur)} a month now and ${charge(f.after, cur)} after${f.within_free ? " (the lab is within its free tier there, so freeing storage saves nothing)" : ""}.`));
  if (s.estimate) lines.push("Compression is an estimate, from what compressing each type of file typically frees.");
  lines.push("At today's prices and free tiers; folders inside another planned folder are counted with it.");
  return html`<p class="small muted" style="margin:0;padding:12px 22px 16px">${lines.join(" ")}</p>`;
}

function planCard(p) {
  const s = p.savings;
  const todo = p.items.filter((i) => i.progress.status !== "done");
  const done = p.items.filter((i) => i.progress.status === "done");
  const progressText = (i) => (i.progress.status === "partly done" ? `${bytes(i.progress.freed)} freed${i.progress.since ? ` by ${date(i.progress.since)}` : ""}` : "");
  return html`<section class="card flush">
    <div class="card-head"><h2>${p.lab.name}</h2><span class="small">${totalText(p.total)}${todo.length
      ? html` · <b>${savesText(s)}</b>${Number(s.monthly) > 0 ? html` <span class="muted">(${charge(s.yearly, s.currency)} a year)</span>` : ""}` : ""}</span></div>
    ${s.done.freed ? html`<p class="callout" style="margin:0 22px 14px">${icon("check")} <b>Done so far: ${bytes(s.done.freed)} freed</b>,
      saving ${charge(s.done.monthly, s.currency)} a month, as later scans show.</p>` : ""}
    ${todo.length ? html`<h3 style="margin:0;padding:0 22px 6px">To do</h3><div class="table-wrap"><table class="table"><thead><tr><th>Folder</th><th>What to do</th>
        <th class="num hide-narrow">Size when added</th><th class="num">Now</th><th class="num">Saves a month</th><th class="hide-narrow">Added</th><th></th></tr></thead><tbody>
      ${todo.map((i) => html`<tr>
        <td><a class="rowlink" href="${placeHash("explore", { fs: i.fs, path: i.path })}">${i.name}</a>
          <div class="sub">${displayPath(i.fs, i.path)}</div>
          ${i.inside ? html`<div class="sub">Inside another planned folder: counted with it</div>` : ""}
          ${i.note ? html`<div class="sub">${i.note}</div>` : ""}
          ${i.progress.status === "partly done" ? html`<div class="sub"><span class="chip chip-queued">partly done</span> ${progressText(i)}</div>` : ""}
          ${i.protected ? html`<div class="alert alert-warn" style="margin-top:8px">${icon("shield")}<div>Protected since it was planned: ${i.protected.why.replace(", so it cannot go in a cleanup plan", "")}. Take it out of the plan, or ask for the tag to be taken off.</div></div>` : ""}
          ${i.mark.status === "moved" ? html`<div class="alert alert-info" style="margin-top:8px">${icon("info")}<div>
            Not at its path in the latest scan; a folder with its inode is at <b>${displayPath(i.fs, i.mark.moved_to)}</b>. If it moved there, nothing was freed.
            <div class="toolbar" style="margin-top:6px"><button class="btn btn-secondary btn-sm" data-moved="${i.id}" data-accept="1">It moved there</button>
            <button class="btn btn-ghost btn-sm" data-moved="${i.id}" data-accept="0">It is not the same folder</button></div></div></div>` : ""}</td>
        <td class="nowrap">${actionText(i)}</td>
        <td class="num nowrap hide-narrow">${bytes(i.baseline.bytes)}</td>
        <td class="num nowrap">${i.mark.status === "here" && i.now ? bytes(i.now.bytes) : html`<span class="muted">Moved?</span>`}</td>
        <td class="num nowrap">${i.action === "compress" && i.saving && !i.saving.freed ? html`<span class="muted">—</span><div class="sub">nothing in it typically compresses</div>`
          : i.saving?.saves != null ? html`${i.saving.estimate ? "about " : ""}${charge(i.saving.saves, s.currency)}${i.saving.estimate
          ? html`<div class="sub">${bytes(i.saving.freed)} freed, estimated</div>` : ""}` : html`<span class="muted">${i.inside ? "with the folder around it" : "—"}</span>`}</td>
        <td class="hide-narrow nowrap">${date(i.added_at)}<div class="sub">${i.added_by || ""}</div></td>
        <td class="right nowrap"><button class="btn btn-ghost btn-sm" data-item="${i.id}" data-do="change">Change</button>
          <button class="icon-btn" title="Take it out" aria-label="Take ${i.name} out of the plan" data-item="${i.id}" data-do="remove">${icon("x")}</button></td></tr>`)}
      </tbody></table></div>${savingsNote(s)}` : ""}
    ${done.length ? html`<h3 style="margin:0;padding:${todo.length ? "4px" : "0"} 22px 6px">Done</h3><div class="table-wrap"><table class="table"><thead><tr><th>Folder</th><th>What was done</th>
        <th class="num">Freed</th><th>By the scan of</th><th class="hide-narrow">Added</th><th></th></tr></thead><tbody>
      ${done.map((i) => html`<tr><td><span class="title-cell">${i.name}</span><div class="sub">${displayPath(i.fs, i.path)}</div></td>
        <td class="nowrap">${actionText(i)}</td><td class="num nowrap">${bytes(i.progress.freed)}</td>
        <td class="nowrap">${i.progress.since ? date(i.progress.since) : "—"}</td>
        <td class="hide-narrow nowrap">${date(i.added_at)}<div class="sub">${i.added_by || ""}</div></td>
        <td class="right"><button class="icon-btn" title="Take it out" aria-label="Take ${i.name} out of the plan" data-item="${i.id}" data-do="remove">${icon("x")}</button></td></tr>`)}
      </tbody></table></div>` : ""}
    ${!p.items.length ? html`<div style="padding:0 22px 18px">${emptyState("archive", "Nothing planned yet", "Open a folder on the age map and add it from its panel, or add a suggestion below.")}</div>`
      : html`<div class="toolbar" style="padding:0 22px 16px"><button class="btn btn-ghost btn-sm" data-csv="${p.lab.slug}">${icon("download")}Download CSV</button></div>`}
    <div data-suggest="${p.lab.slug}"><p class="small muted" style="padding:0 22px 16px;margin:0">Looking for folders to suggest…</p></div>
  </section>`;
}

const SUGGESTED = { workflow: "Made again by running the workflow", compress: "Compresses well", cold: "Could go to cheaper storage" };

/** Folders worth adding to the plan, each with what it would free and save on its own. */
function suggestionsTable(list, currency) {
  if (!list.length) return html`<p class="small muted" style="padding:0 22px 16px;margin:0">No suggestions: no workflow working folders, uncompressed sequencing data, or large cold folders outside the plan.</p>`;
  return html`<h3 style="margin:0;padding:4px 22px 6px">Suggestions</h3><div class="table-wrap"><table class="table"><thead><tr><th>Folder</th><th>Why</th>
      <th class="num">Size</th><th class="num">Frees</th><th class="num">Saves a month</th><th></th></tr></thead><tbody>
    ${list.map((s, i) => html`<tr><td><a class="rowlink" href="${placeHash("explore", { fs: s.fs, path: s.path })}">${s.name}</a><div class="sub">${displayPath(s.fs, s.path)}</div></td>
      <td>${s.what}<div class="sub">${SUGGESTED[s.kind]}</div></td><td class="num nowrap">${bytes(s.bytes)}</td>
      <td class="num nowrap">${s.estimate ? "about " : ""}${bytes(s.freed)}</td>
      <td class="num nowrap">${s.saves == null ? html`<span class="muted">no price</span>` : `${s.estimate ? "about " : s.action === "move" ? "up to " : ""}${charge(s.saves, currency)}`}</td>
      <td class="right"><button class="btn btn-secondary btn-sm" data-suggestion="${i}">${icon("plus")}Add</button></td></tr>`)}
    </tbody></table></div>
    <p class="small muted" style="margin:0;padding:8px 22px 16px">Each saving is the folder's on its own, at today's prices; moving cold data saves less than deleting it, by what it costs where it goes.</p>`;
}

/** A plan as CSV, for the lab or the storage team. */
export function planCsv(p) {
  downloadCsv(`pinax-cleanup-plan-${p.lab.slug}.csv`,
    ["lab", "filesystem", "path", "action", "to", "note", "added_by", "added_at", "bytes_when_added", "bytes_now", "status", "freed",
      "freed_by", "saves_a_month", "estimate", "currency"],
    p.items.map((i) => [p.lab.slug, i.fs, i.path, i.action, i.to?.slug ?? "", i.note, i.added_by ?? "", i.added_at, i.baseline.bytes,
      i.now?.present ? i.now.bytes : "", i.progress.status, i.progress.freed, i.progress.since ?? "", i.saving?.saves ?? "",
      i.saving?.estimate ? "yes" : "", p.savings.currency]));
}

// ---------- Every lab's plan, for administrators ----------
async function everyPlan(el) {
  const [all, choices] = await Promise.all([api("plans/all"), api("plans")]);
  const cur = all.currency;
  const toFree = all.filesystems.reduce((t, f) => t + f.to_free, 0);
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Overview</div><h1>Cleanup plans</h1>
      <p>Every lab's and department's cleanup plan: what they mean to delete, move, or compress, what it would free and save, and what later
        scans show is done. Pinax does none of it.</p></div>
      <div class="actions toolbar"><label class="ctl"><span>Lab or department</span><select class="select sm" id="p-lab">
        <option value="">Every lab</option>${choices.choices.map((c) => html`<option value="${c.slug}">${c.name}</option>`)}</select></label>
        ${all.labs.length ? html`<button class="btn btn-primary btn-sm" id="p-csv">${icon("download")}Download CSV</button>` : ""}</div></div>
    <div class="kpis">
      <div class="kpi"><div class="label">Planned to free</div><div class="value">${bytes(toFree)}</div><div class="sub">${num(all.labs.length)} lab${all.labs.length === 1 ? "" : "s"} with a plan</div></div>
      <div class="kpi" style="--kpi-accent:var(--fh-teal)"><div class="label">Would save a month</div><div class="value">${charge(all.monthly, cur)}</div><div class="sub">At today's prices and free tiers</div></div>
      <div class="kpi" style="--kpi-accent:var(--strong)"><div class="label">Done so far</div><div class="value">${bytes(all.done.freed)}</div><div class="sub">Saving ${charge(all.done.monthly, cur)} a month</div></div>
    </div>
    ${all.labs.length ? html`<div class="stack">
      <div class="card flush"><div class="card-head"><h2>By filesystem</h2></div><div class="table-wrap"><table class="table"><thead><tr><th>Filesystem</th>
        <th class="num">To free</th><th class="num">Moved in</th><th class="num">Freed so far</th></tr></thead><tbody>
        ${all.filesystems.map((f) => html`<tr><td><b>${f.fs_name}</b></td><td class="num">${bytes(f.to_free)}</td><td class="num">${f.moved_in ? bytes(f.moved_in) : "—"}</td>
          <td class="num">${f.freed ? bytes(f.freed) : "—"}</td></tr>`)}</tbody></table></div></div>
      <div class="card flush"><div class="card-head"><h2>Largest folders planned</h2><span class="small muted">Still to do, by what each would free</span></div><div class="table-wrap"><table class="table"><thead><tr><th>Folder</th>
        <th class="hide-narrow">Lab or department</th><th>What to do</th><th class="num">Frees</th><th class="num">Saves a month</th></tr></thead><tbody>
        ${all.largest.map((i) => html`<tr><td><a class="rowlink" href="${placeHash("explore", { fs: i.fs, path: i.path })}">${i.name}</a><div class="sub">${displayPath(i.fs, i.path)}</div></td>
          <td class="hide-narrow">${i.lab_name}</td><td class="nowrap">${actionText(i)}</td>
          <td class="num nowrap">${i.saving?.estimate ? "about " : ""}${bytes(i.saving?.freed || 0)}</td><td class="num nowrap">${i.saving?.saves != null ? charge(i.saving.saves, cur) : "—"}</td></tr>`)}</tbody></table></div></div>
      <div class="card flush"><div class="card-head"><h2>Labs and departments</h2></div><div class="table-wrap"><table class="table"><thead><tr><th>Lab or department</th>
        <th class="num">Folders</th><th class="num">Saves a month</th><th class="num">Freed so far</th></tr></thead><tbody>
        ${all.labs.map((p) => html`<tr><td><a class="rowlink" href="${planHash(p.lab.slug)}">${p.lab.name}</a></td><td class="num">${num(p.total.folders)}</td>
          <td class="num nowrap">${charge(p.savings.monthly, cur)}</td><td class="num">${p.savings.done.freed ? bytes(p.savings.done.freed) : "—"}</td></tr>`)}</tbody></table></div></div>
    </div>` : html`<div class="card">${emptyState("archive", "No plans yet", "No lab or department has added a folder to a cleanup plan.")}</div>`}`);
  $("#p-lab", el)?.addEventListener("change", (e) => { location.hash = planHash(e.target.value); });
  $("#p-csv", el)?.addEventListener("click", () => {
    const rows = all.labs.flatMap((p) => p.items.map((i) => ({ p, i })));
    downloadCsv("pinax-cleanup-plans.csv",
      ["lab", "filesystem", "path", "action", "to", "note", "added_by", "added_at", "bytes_when_added", "bytes_now", "status", "freed", "freed_by", "saves_a_month", "estimate", "currency"],
      rows.map(({ p, i }) => [p.lab.slug, i.fs, i.path, i.action, i.to?.slug ?? "", i.note, i.added_by ?? "", i.added_at, i.baseline.bytes,
        i.now?.present ? i.now.bytes : "", i.progress.status, i.progress.freed, i.progress.since ?? "", i.saving?.saves ?? "", i.saving?.estimate ? "yes" : "", cur]));
  });
}
