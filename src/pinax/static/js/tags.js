// SPDX-License-Identifier: Apache-2.0
// Tags, for administrators: the standard tags every lab can use (tags.py), with their types, choices, who applies
// them, and whether they protect a folder from cleanup plans; and the labs' own tags. A tag in use is retired, not
// deleted.
import { $, $$, api, emptyState, html, icon, modal, mount, num, raw, toast } from "/js/core.js";
import { renderSidebar } from "/js/shell.js";

export const TYPE_TEXT = { flag: "A flag", text: "Text", date: "A date", choice: "A choice" };
const BY_TEXT = { owners: "Lab PIs and delegates", admins: "Administrators only" };

function tagForm(t, types) {
  const v = t || { type: "flag", choices: [], many: false, applied_by: "owners", protects: false, label: "", description: "" };
  const opt = (value, label, current) => html`<option value="${value}" ${value === current ? raw("selected") : ""}>${label}</option>`;
  return html`<form class="stack" style="gap:10px">
    <label class="field"><span class="small">Name</span><input class="input" name="label" value="${v.label}" required maxlength="100"></label>
    <label class="field"><span class="small">What it is for</span><input class="input" name="description" value="${v.description}" maxlength="300"></label>
    ${t ? html`<p class="small muted" style="margin:0">${TYPE_TEXT[t.type]}; its type cannot change.</p>`
      : html`<label class="field"><span class="small">Type</span><select class="select" name="type">${types.map((k) => opt(k, TYPE_TEXT[k], v.type))}</select></label>`}
    <label class="field"><span class="small">Choices, one per line (for a choice)</span><textarea class="input" name="choices" rows="4">${v.choices.join("\n")}</textarea></label>
    <label class="field"><span class="small">Applied by</span><select class="select" name="applied_by">${Object.entries(BY_TEXT).map(([k, l]) => opt(k, l, v.applied_by))}</select></label>
    <label class="radio"><input type="checkbox" name="many" ${v.many ? raw("checked") : ""}> <span>A folder can have more than one value</span></label>
    <label class="radio"><input type="checkbox" name="protects" ${v.protects ? raw("checked") : ""}> <span>Protects the folder from cleanup plans (a flag, or a date until it)</span></label>
  </form>`;
}

const formBody = (wrap, isNew) => {
  const f = new FormData($("form", wrap));
  const body = { label: f.get("label"), description: f.get("description"), applied_by: f.get("applied_by"),
    choices: String(f.get("choices") || "").split("\n").map((c) => c.trim()).filter(Boolean),
    many: f.get("many") === "on", protects: f.get("protects") === "on" };
  if (isNew) body.type = f.get("type");
  return body;
};

export async function viewTags(el) {
  renderSidebar();
  const data = await api("admin/tags");
  const row = (t) => html`<tr class="${t.active ? "" : "muted"}">
    <td><b>${t.label}</b>${t.active ? "" : html` <span class="chip chip-muted">retired</span>`}${t.protects ? html` <span class="chip chip-review">${icon("shield")}protects</span>` : ""}
      <div class="sub">${t.description}</div></td>
    <td>${TYPE_TEXT[t.type]}${t.many ? ", more than one" : ""}${t.choices.length ? html`<div class="sub">${t.choices.join(", ")}</div>` : ""}</td>
    <td class="hide-narrow">${t.lab ? t.lab.name : BY_TEXT[t.applied_by]}</td><td class="num">${num(t.uses)}</td>
    <td class="right nowrap">${t.lab ? "" : html`<button class="btn btn-ghost btn-sm" data-edit="${t.id}">Change</button>`}
      <button class="btn btn-ghost btn-sm" data-active="${t.id}" data-to="${t.active ? 0 : 1}">${t.active ? "Retire" : "Bring back"}</button>
      ${t.uses ? "" : html`<button class="icon-btn" title="Delete" aria-label="Delete ${t.label}" data-delete="${t.id}">${icon("x")}</button>`}</td></tr>`;
  const table = (list) => html`<div class="table-wrap"><table class="table"><thead><tr><th>Tag</th><th>Value</th><th class="hide-narrow">Applied by</th>
    <th class="num">Folders</th><th></th></tr></thead><tbody>${list.map(row)}</tbody></table></div>`;
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>Tags</h1>
      <p>The standard tags every lab can put on its folders, to describe what they hold: the project, the grant, the stage and source of the data, how
        sensitive it is, and how long it must be kept. A tag on a folder applies to everything below it. A tag in use is retired, not deleted, so what
        was tagged stays tagged.</p></div>
      <div class="actions"><button class="btn btn-primary btn-sm" id="t-add">${icon("plus")}Add tag</button></div></div>
    <div class="card flush"><div class="card-head"><h2>Standard tags</h2></div>${table(data.standard)}</div>
    <div class="card flush" style="margin-top:20px"><div class="card-head"><h2>Labs' own tags</h2></div>
      ${data.custom.length ? table(data.custom) : html`<div style="padding:0 22px 18px">${emptyState("tag", "None yet", "Labs make tags of their own from a folder's panel on the age map.")}</div>`}</div>`);

  const refresh = () => viewTags(el);
  $("#t-add", el).addEventListener("click", async () => {
    const ok = await modal({ title: "Add a standard tag", body: tagForm(null, data.types), confirm: "Add tag",
      onConfirm: (wrap) => api("admin/tags", { method: "POST", body: formBody(wrap, true) }) });
    if (ok) { toast("Tag added"); refresh(); }
  });
  $$("[data-edit]", el).forEach((b) => b.addEventListener("click", async () => {
    const t = data.standard.find((x) => String(x.id) === b.dataset.edit);
    const ok = await modal({ title: `Change ${t.label}`, body: tagForm(t, data.types), confirm: "Save",
      onConfirm: (wrap) => api(`admin/tags/${t.id}`, { method: "PUT", body: formBody(wrap, false) }) });
    if (ok) { toast("Saved"); refresh(); }
  }));
  $$("[data-active]", el).forEach((b) => b.addEventListener("click", async () => {
    try {
      await api(`admin/tags/${b.dataset.active}/active`, { method: "POST", body: { active: b.dataset.to === "1" } });
      toast(b.dataset.to === "1" ? "Brought back" : "Retired: folders keep it, and it can no longer be applied");
      refresh();
    } catch (e) { toast(e.message, "error"); }
  }));
  $$("[data-delete]", el).forEach((b) => b.addEventListener("click", async () => {
    try {
      await api(`admin/tags/${b.dataset.delete}`, { method: "DELETE" });
      toast("Deleted");
      refresh();
    } catch (e) { toast(e.message, "error"); }
  }));
}
