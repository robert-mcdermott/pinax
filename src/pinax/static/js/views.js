// SPDX-License-Identifier: Apache-2.0
// Saved views (views.py): the page a person is on, kept under a name, to themselves or shared with one of their
// labs. A view is only the page's address, so whoever opens it sees only what they may.
import { $, $$, api, date, html, icon, modal, mount, raw, toast } from "/js/core.js";

/** A button that saves the page it is on. */
export const saveButton = () => html`<button class="btn btn-ghost btn-sm" data-save-view>${icon("plus")}Save view</button>`;

/** Save buttons anywhere inside `el`, once: pages come and go, the listener stays. */
export function bindSave(el) {
  el.addEventListener("click", (e) => { if (e.target.closest("[data-save-view]")) saveView(); });
}

async function saveView() {
  const { labs } = await api("views");
  const title = document.querySelector(".page-head h1")?.textContent.trim() || "";
  const ok = await modal({
    title: "Save this view",
    body: html`<form class="stack" style="gap:10px">
      <label class="field"><span class="small">Name</span><input class="input" name="name" value="${title}" maxlength="200" required></label>
      <label class="field"><span class="small">Who sees it</span><select class="select" name="lab"><option value="">Only me</option>
        ${labs.map((l) => html`<option value="${l.slug}">Everyone in the ${l.name}</option>`)}</select></label>
      <p class="small muted" style="margin:0">It keeps this page as it is now: the report, the folder, and the settings. Whoever opens it sees only what they may.</p></form>`,
    confirm: "Save",
    onConfirm: (wrap) => {
      const f = new FormData($("form", wrap));
      return api("views", { method: "POST", body: { name: f.get("name"), address: location.hash, lab: f.get("lab") || null } });
    },
  });
  if (ok) toast("View saved");
}

/** A card of a person's views and those shared with their labs. */
export async function viewsCard(box) {
  let data;
  try {
    data = await api("views", { quiet: true });
  } catch (e) {
    if (e.name !== "AbortError") mount(box, "");
    return;
  }
  if (!data.views.length) return mount(box, "");
  mount(box, html`<div class="card flush" style="margin-top:20px"><div class="card-head"><h2>Saved views</h2></div>
    <div class="table-wrap"><table class="table compact"><tbody>
    ${data.views.map((v) => html`<tr><td><a class="rowlink" href="${v.address}">${v.name}</a>
        <div class="sub">${v.mine ? (v.lab ? `Shared with the ${v.lab.name}` : "Only you") : `${v.by}, shared with the ${v.lab.name}`} · ${date(v.created_at)}</div></td>
      <td class="right nowrap">${v.mine ? html`<select class="select sm" data-share="${v.id}" aria-label="Who sees ${v.name}">
          <option value="" ${v.lab ? "" : raw("selected")}>Only me</option>
          ${data.labs.map((l) => html`<option value="${l.slug}" ${v.lab?.slug === l.slug ? raw("selected") : ""}>${l.name}</option>`)}</select>
        <button class="icon-btn" title="Delete" aria-label="Delete ${v.name}" data-delete-view="${v.id}">${icon("x")}</button>` : ""}</td></tr>`)}
    </tbody></table></div></div>`);
  $$("[data-share]", box).forEach((s) => s.addEventListener("change", async () => {
    try {
      await api(`views/${s.dataset.share}`, { method: "PUT", body: s.value ? { lab: s.value } : { unshare: true } });
      toast(s.value ? "Shared" : "Only you see it now");
      viewsCard(box);
    } catch (e) { toast(e.message, "error"); }
  }));
  $$("[data-delete-view]", box).forEach((b) => b.addEventListener("click", async () => {
    try { await api(`views/${b.dataset.deleteView}`, { method: "DELETE" }); toast("Deleted"); viewsCard(box); } catch (e) { toast(e.message, "error"); }
  }));
}
