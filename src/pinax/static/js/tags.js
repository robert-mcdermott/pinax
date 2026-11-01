// SPDX-License-Identifier: Apache-2.0
// Tags, for administrators: the standard tags every lab can use (tags.py), with their types, choices, who applies
// them, and whether they protect a folder from cleanup plans; and the labs' own tags, which can be folded into a
// standard one. A tag in use is retired, not deleted.
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
    <td class="right nowrap"><span class="admin-only">${t.lab ? (t.uses ? html`<button class="btn btn-ghost btn-sm" data-fold="${t.id}">Fold into a standard tag</button>` : "")
      : html`<button class="btn btn-ghost btn-sm" data-edit="${t.id}">Change</button>`}
      <button class="btn btn-ghost btn-sm" data-active="${t.id}" data-to="${t.active ? 0 : 1}">${t.active ? "Retire" : "Bring back"}</button>
      ${t.uses ? "" : html`<button class="icon-btn" title="Delete" aria-label="Delete ${t.label}" data-delete="${t.id}">${icon("x")}</button>`}</span></td></tr>`;
  const table = (list) => html`<div class="table-wrap"><table class="table"><thead><tr><th>Tag</th><th>Value</th><th class="hide-narrow">Applied by</th>
    <th class="num">Folders</th><th></th></tr></thead><tbody>${list.map(row)}</tbody></table></div>`;
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>Tags</h1>
      <p>The standard tags every lab can put on its folders, to describe what they hold: the project, the grant, the stage and source of the data, how
        sensitive it is, and how long it must be kept. A tag on a folder applies to everything below it. A tag in use is retired, not deleted, so what
        was tagged stays tagged.</p></div>
      <div class="actions admin-only"><button class="btn btn-primary btn-sm" id="t-add">${icon("plus")}Add tag</button></div></div>
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
  $$("[data-fold]", el).forEach((b) => b.addEventListener("click", async () => {
    const t = data.custom.find((x) => String(x.id) === b.dataset.fold);
    const done = await foldTag(t, data.standard.filter((x) => x.active));
    if (done) {
      const one = done.folders === 1;
      toast(`Folded: ${num(done.folders)} folder${one ? "" : "s"} now ${one ? "has" : "have"} ${done.into}, and ${t.label} is retired`);
      refresh();
    }
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

// Folding a lab's own tag into a standard one (tags.merge): every folder with it gets the standard tag, with the value
// given, or each folder's own (a flag's: its name), and the lab's tag is retired.
function foldValue(target, t) {
  if (!target || target.type === "flag") return "";
  // Without a value, each folder keeps its own; a flag has none, so it gives its name, which only text (or a choice
  // of the same name) takes.
  const keep = t.type === "flag" ? `Its name, ${t.label}` : "Each folder's own value";
  const keeps = t.type !== "flag" || target.type === "text" || (target.type === "choice" && target.choices.includes(t.label));
  if (target.type === "choice") {
    return html`<span class="small">Value</span><select class="select" name="value">${keeps ? html`<option value="">${keep}</option>` : ""}
      ${target.choices.map((c) => html`<option>${c}</option>`)}</select>`;
  }
  if (target.type === "date") {
    return keeps ? html`<span class="small">Value (empty: ${keep.toLowerCase()})</span><input class="input" type="date" name="value">`
      : html`<span class="small">Value</span><input class="input" type="date" name="value" required>`;
  }
  return html`<span class="small">Value</span><input class="input" name="value" maxlength="200" placeholder="${keep}">`;
}

function foldNote(target) {
  if (!target) return "";
  const about = target.description && !/[.!?]$/.test(target.description) ? `${target.description}.` : target.description;
  return [about,
    target.protects ? "These folders will be kept out of cleanup plans." : "",
    target.many || target.type === "flag" ? "" : "A folder has one value of it: where a folder has it already, the folded value replaces its own."]
    .filter(Boolean).join(" ");
}

function foldTag(t, standard) {
  if (!standard.length) {
    toast("There is no standard tag in use to fold it into", "error");
    return Promise.resolve(null);
  }
  const n = `${num(t.uses)} folder${t.uses === 1 ? "" : "s"}`;
  const body = html`<form class="stack" style="gap:10px">
    <p style="margin:0">The ${t.lab.name}'s own tag <b>${t.label}</b> is on ${n}. ${t.uses === 1 ? "That folder gets" : "Each of them gets"}
      the standard tag chosen here, and ${t.label} is retired.</p>
    <label class="field"><span class="small">Standard tag</span><select class="select" name="into">
      ${standard.map((x) => html`<option value="${x.id}">${x.label} (${TYPE_TEXT[x.type].toLowerCase()})</option>`)}</select></label>
    <div class="field" id="fold-value">${foldValue(standard[0], t)}</div>
    <p class="small muted" style="margin:0" id="fold-note">${foldNote(standard[0])}</p></form>`;
  const pending = modal({ title: `Fold ${t.label} into a standard tag`, body, confirm: `Fold ${n}`,
    onConfirm: async (wrap) => {
      const f = new FormData($("form", wrap));
      const into = standard.find((x) => String(x.id) === f.get("into"));
      if ([...wrap.querySelectorAll("[required]")].some((i) => !i.value)) {
        toast(`${into.label} needs a value`, "error");
        return false;
      }
      const res = await api(`admin/tags/${t.id}/merge`, { method: "POST", body: { into: into.id, value: f.get("value") || null } });
      return { folders: res.folders, into: into.label };
    } });
  const wrap = document.querySelector(".modal-backdrop:last-of-type");
  $("select[name=into]", wrap)?.addEventListener("change", (e) => {
    const target = standard.find((x) => String(x.id) === e.target.value);
    mount($("#fold-value", wrap), foldValue(target, t));
    $("#fold-note", wrap).textContent = foldNote(target);
  });
  return pending;
}

// ---------- A folder's tags, in the age map's panel ----------
const shownValue = (t) => (t.value ? `${t.tag.label}: ${t.value}` : t.tag.label);

function valueField(tag) {
  if (!tag || tag.type === "flag") return "";
  if (tag.type === "choice") return html`<select class="select" name="value">${tag.choices.map((c) => html`<option>${c}</option>`)}</select>`;
  if (tag.type === "date") return html`<input class="input" type="date" name="value" required>`;
  return html`<input class="input" name="value" maxlength="200" required placeholder="${tag.key === "grant" ? "e.g. R01-CA123456" : ""}">`;
}

function addTag(fs, path, here) {
  const list = here.available;
  const body = html`<form class="stack" style="gap:10px">
    <label class="field"><span class="small">Tag</span><select class="select" name="tag">${list.map((t) => html`<option value="${t.id}">${t.label}${t.lab ? ` (${t.lab.name}'s own)` : ""}</option>`)}</select></label>
    <div class="field" id="tag-value">${valueField(list[0])}</div>
    <p class="small muted" style="margin:0" id="tag-about">${list[0]?.description || ""}</p>
    <p class="small muted" style="margin:0">A tag applies to this folder and everything below it.</p></form>`;
  const pending = modal({ title: `Tag ${path.split("/").pop()}`, body, confirm: "Add tag",
    onConfirm: (wrap) => {
      const f = new FormData($("form", wrap));
      return api("tags/folders", { method: "POST", body: { fs, path, tag: Number(f.get("tag")), value: f.get("value") } });
    } });
  const wrap = document.querySelector(".modal-backdrop:last-of-type");
  $("select[name=tag]", wrap)?.addEventListener("change", (e) => {
    const t = list.find((x) => String(x.id) === e.target.value);
    mount($("#tag-value", wrap), valueField(t));
    $("#tag-about", wrap).textContent = t?.description || "";
  });
  return pending;
}

function makeTag(lab) {
  return modal({ title: `A tag of the ${lab.name}'s own`,
    body: html`<form class="stack" style="gap:10px"><label class="field"><span class="small">Name</span><input class="input" name="label" required maxlength="100" placeholder="e.g. Atlas paper"></label>
      <label class="radio"><input type="radio" name="type" value="flag" checked> <span>A flag (no value)</span></label>
      <label class="radio"><input type="radio" name="type" value="text"> <span>Text (a value on each folder)</span></label>
      <p class="small muted" style="margin:0">Seen by the lab's members and administrators, who may fold it into a standard tag.</p></form>`,
    confirm: "Make tag",
    onConfirm: (wrap) => {
      const f = new FormData($("form", wrap));
      return api("tags/custom", { method: "POST", body: { lab: lab.slug, label: f.get("label"), type: f.get("type") } });
    } });
}

/** The age map panel's part: a folder's own tags, those it inherits, and adding one. */
export async function tagBox(box, fs, path) {
  let here;
  try {
    here = await api("tags/here", { query: { fs, path }, quiet: true });
  } catch (e) {
    if (e.name !== "AbortError") mount(box, "");
    return;
  }
  if (!here.taggable) return mount(box, "");
  const draw = () => {
    const own = here.tags.filter((t) => !t.from), inherited = here.tags.filter((t) => t.from);
    mount(box, html`<h3>Tags</h3>
      ${own.length || inherited.length ? html`<div class="tag-list">
        ${own.map((t) => html`<span class="tag">${t.tag.protects ? icon("shield") : icon("tag")}${shownValue(t)}${t.can_remove
          ? html`<button class="tag-x" title="Take it off" aria-label="Take ${shownValue(t)} off" data-untag="${t.id}">${icon("x")}</button>` : ""}</span>`)}
        ${inherited.map((t) => html`<span class="tag inherited" title="From ${t.from}">${t.tag.protects ? icon("shield") : icon("tag")}${shownValue(t)}
          <small>from ${t.from.split("/").pop()}</small></span>`)}</div>`
        : html`<p class="small muted" style="margin:0 0 8px">No tags yet. Tags say what a folder holds (project, grant, stage, source) and apply to everything below it.</p>`}
      <div class="toolbar" style="margin-top:8px">${here.available.length ? html`<button class="btn btn-secondary btn-sm" data-tag="add">${icon("plus")}Add tag</button>` : ""}
        ${here.can_make && here.lab ? html`<button class="btn btn-ghost btn-sm" data-tag="make">A tag of the lab's own</button>` : ""}</div>`);
    $("[data-tag=add]", box)?.addEventListener("click", async () => {
      const next = await addTag(fs, path, here);
      if (next) { here = next; toast("Tagged"); draw(); }
    });
    $("[data-tag=make]", box)?.addEventListener("click", async () => {
      if (await makeTag(here.lab)) { here = await api("tags/here", { query: { fs, path } }); toast("Tag made"); draw(); }
    });
    $$("[data-untag]", box).forEach((b) => b.addEventListener("click", async () => {
      try { here = await api(`tags/folders/${b.dataset.untag}`, { method: "DELETE" }); toast("Taken off"); draw(); } catch (e) { toast(e.message, "error"); }
    }));
  };
  draw();
}
