// SPDX-License-Identifier: Apache-2.0
// Pinax app shell: sign-in, sidebar, routes, and the home pages.
import {
  $, ageText, api, bytes, date, emptyState, html, icon, loadIdentity, mount, num, pct, placeHash, renderTopbar,
  setActiveNav, showPasswordChange, showPersonaPicker, showSignIn, startRouter, state,
} from "/js/core.js";
import { folderCard, renderSidebar } from "/js/shell.js";
import { viewChanged } from "/js/changed.js";
import { charge, viewChargeback, viewCharges } from "/js/charges.js";
import { viewCapacity } from "/js/capacity.js";
import { viewCosts } from "/js/costs.js";
import { viewPlans } from "/js/plans.js";
import { viewTags } from "/js/tags.js";
import { viewCollections } from "/js/collections.js";
import { viewChanges } from "/js/changes.js";
import { viewExplore } from "/js/explore.js";
import { viewHistory } from "/js/history.js";
import { viewAges, viewCold, viewFiles, viewOwners, viewTypes } from "/js/reports.js";
import { viewAccess, viewFolders, viewHealth, viewLab, viewOverview, viewPerson, viewScans } from "/js/admin.js";
import { coldBytes, thresholdText } from "/js/viz.js";

const main = $("#main");

// ---------- Home ----------
async function viewHome(el) {
  state.place = null;
  renderSidebar();
  if (state.me.is_admin) return viewOverview(el);
  const [home, tree, charges] = await Promise.all([api("home"), api("tree"),
    api("charges", { query: { history: false }, quiet: true }).catch(() => null)]);
  const folders = home.folders;
  const cb = tree.cold_bucket;
  const s = tree.node.stats;
  const change = folders.reduce((t, f) => t + (f.change ? f.change.bytes : 0), 0);
  const hasChange = folders.some((f) => f.change);
  if (!folders.length) {
    mount(el, html`<div class="page-head"><div><div class="eyebrow">Home</div><h1>My data</h1></div></div>
      <div class="card">${emptyState("folder", "No folders yet",
        "None of your labs owns a top-level folder that has been scanned. Ask Research Data Services to map your lab's folders.")}</div>`);
    return;
  }
  const cold = coldBytes(s, cb);
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Home</div><h1>My data</h1>
      <p>What your labs store, as of the scan on ${date(tree.as_of, true)}.</p></div>
      <div class="actions"><a class="btn btn-primary" href="${folders.length === 1 ? placeHash("explore", { fs: folders[0].fs, path: folders[0].path }) : "#/explore"}">${icon("treemap")}Open the age map</a>
        <a class="btn btn-secondary" href="#/files">${icon("search")}Find files</a></div></div>
    <div class="kpis">
      <div class="kpi"><div class="label">Total size</div><div class="value">${bytes(s.bytes)}</div><div class="sub">${num(s.files)} files in ${num(folders.length)} folder${folders.length === 1 ? "" : "s"}</div></div>
      <div class="kpi" style="--kpi-accent:var(--age-5)"><div class="label">Unchanged for ${thresholdText(cb)}+</div><div class="value">${bytes(cold)}</div><div class="sub">${pct(s.bytes ? (cold / s.bytes) * 100 : 0)} of your data</div></div>
      <div class="kpi" style="--kpi-accent:var(--age-4)"><div class="label">Average age</div><div class="value">${ageText(s.mean_age_days)}</div><div class="sub">Weighted by size</div></div>
      <a class="kpi" href="#/history" style="--kpi-accent:var(--fh-bright-blue)"><div class="label">Change since the previous scan</div><div class="value">${!hasChange ? "—" : change ? `${change > 0 ? "+" : "−"}${bytes(Math.abs(change))}` : "No change"}</div><div class="sub">${hasChange ? "Across your folders · history" : "Only one scan so far"}</div></a>
      ${charges?.priced && charges.labs.some((l) => l.filesystems.length) ? html`<a class="kpi" href="#/charges" style="--kpi-accent:var(--fh-teal)">
        <div class="label">Storage charges this month</div><div class="value">${charge(charges.total, charges.currency)}</div>
        <div class="sub">${charges.estimated ? "Estimated to the month's end" : "This month"} · ${charges.labs.length === 1 ? charges.labs[0].lab.name : `${charges.labs.length} labs`}</div></a>` : ""}
    </div>
    <h2 style="margin:8px 0 14px">Your folders</h2>
    <div class="folder-cards">${folders.map((f) => folderCard(f, cb))}</div>
    ${home.labs.length > 1 ? html`<div class="card flush" style="margin-top:24px"><div class="card-head"><h2>Your labs</h2></div>
      <div class="table-wrap"><table class="table"><thead><tr><th>Lab or department</th><th class="num">Folders</th><th class="num">Size</th><th class="num">Cold</th><th class="num">Change</th></tr></thead><tbody>
      ${home.labs.map((l) => html`<tr><td><a class="rowlink" href="#/explore?lab=${encodeURIComponent(l.slug)}">${l.name}</a><div class="sub">${l.research_area}</div></td>
        <td class="num">${l.folders}</td><td class="num">${bytes(l.bytes)}</td><td class="num">${pct(l.bytes ? (l.cold_bytes / l.bytes) * 100 : 0)}</td>
        <td class="num">${l.change ? `${l.change > 0 ? "+" : "−"}${bytes(Math.abs(l.change))}` : "—"}</td></tr>`)}</tbody></table></div></div>` : ""}`);
}

// ---------- Boot ----------
const routes = [
  [/^#\/?$/, viewHome],
  [/^#\/explore(\?.*)?$/, viewExplore],
  [/^#\/changes(\?.*)?$/, viewChanges],
  [/^#\/changed(\?.*)?$/, viewChanged],
  [/^#\/ages(\?.*)?$/, viewAges],
  [/^#\/history(\?.*)?$/, viewHistory],
  [/^#\/types(\?.*)?$/, viewTypes],
  [/^#\/cold(\?.*)?$/, viewCold],
  [/^#\/owners(\?.*)?$/, viewOwners],
  [/^#\/files(\?.*)?$/, viewFiles],
  [/^#\/folders(\?.*)?$/, viewFolders],
  [/^#\/health(\?.*)?$/, viewHealth],
  [/^#\/scans$/, viewScans],
  [/^#\/costs$/, viewCosts],
  [/^#\/capacity$/, viewCapacity],
  [/^#\/plans(\?.*)?$/, viewPlans],
  [/^#\/tags$/, viewTags],
  [/^#\/collections(\?.*)?$/, viewCollections],
  [/^#\/charges(\?.*)?$/, viewCharges],
  [/^#\/chargeback(\?.*)?$/, viewChargeback],
  [/^#\/access(\?.*)?$/, viewAccess],
  [/^#\/access\/people\/([^?]+)$/, viewPerson],
  [/^#\/access\/labs\/([^?]+)$/, viewLab],
];

await loadIdentity();
renderTopbar({ mode: "app" });
if (!state.me && state.config.auth_mode === "local") {
  mount(main, html`<div class="card">${emptyState("lock", "Sign in to Pinax", "Sign in with the username and password your Pinax administrator gave you.")}</div>`);
  showSignIn();
} else if (!state.me) {
  mount(main, html`<div class="card">${emptyState("user", "Choose who you are", "Pick a demo identity to explore Pinax.")}</div>`);
  showPersonaPicker(true);
} else if (state.me.must_change_password) {
  mount(main, html`<div class="card">${emptyState("lock", "Choose your password", "You signed in with a temporary password.")}</div>`);
  showPasswordChange({ forced: true });
} else {
  renderSidebar();
  startRouter(routes, main, { onRoute: () => setActiveNav((location.hash || "#/").split("?")[0], ".side-link") });
  window.addEventListener("hashchange", () => renderSidebar());
}
