// SPDX-License-Identifier: Apache-2.0
// The access review: who has access to what, with when each was given, what is worth a look, its CSV, and a record
// that it was reviewed. For administrators and auditors.
import { renderSidebar } from "/js/shell.js";
import { $, $$, api, date, html, icon, modal, mount, num, state, toast, when } from "/js/core.js";

const since = (iso) => (iso ? html`<span title="${date(iso, true)}">${date(iso)}</span>` : html`<span class="muted" title="Given before Pinax 0.12.0, which did not record it">Not recorded</span>`);
const personLink = (p) => html`<a class="rowlink" href="#/access/people/${encodeURIComponent(p.username)}">${p.display_name}</a>`;
const text = (...parts) => parts.flat().join(" ").toLowerCase();

function sees(p) {
  if (p.everything) return html`<span class="small">Everything</span>`;
  const folders = p.folders.length ? html`<details class="audit-detail"><summary class="small">${num(p.folders.length)} folder${p.folders.length === 1 ? "" : "s"}</summary>
    <ul class="small" style="list-style:none; padding:0; margin:6px 0 0">${p.folders.map((f) => html`<li><span class="mono">${f.path}</span> <span class="tiny muted">via ${f.via}</span></li>`)}</ul></details>` : "";
  return html`${p.charges ? html`<div class="small">Every lab's charges</div>` : ""}${folders}${!p.charges && !p.folders.length ? html`<span class="muted small">Nothing</span>` : ""}`;
}

function render(el, r) {
  const local = r.local;
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>Access review</h1>
      <p>Who has access to what, as of ${date(r.as_of, true)}: for a periodic review, and a record that it was done.</p></div>
      <div class="actions"><a class="btn btn-ghost btn-sm" href="/api/admin/access-review?fmt=csv">${icon("download")}Download CSV</a>
        <button class="btn btn-primary btn-sm" id="sign-off">${icon("check")}Record a review</button></div></div>
    <div class="kpis">
      <div class="kpi"><div class="label">People with access</div><div class="value">${num(r.counts.people)}</div>
        <div class="sub">${r.counts.no_access ? `${num(r.counts.no_access)} more who ${r.counts.no_access === 1 ? "sees" : "see"} nothing · ` : ""}${num(r.counts.inactive_people)} deactivated</div></div>
      <div class="kpi"><div class="label">Labs and departments</div><div class="value">${num(r.counts.labs)}</div>
        <div class="sub">${num(r.counts.inactive_labs)} deactivated</div></div>
      <div class="kpi"><div class="label">Site roles</div><div class="value">${num(r.counts.roles)}</div><div class="sub">Administrators, auditors, finance, division heads</div></div>
      <div class="kpi" style="--kpi-accent:var(--fh-yellow)"><div class="label">Worth a look</div><div class="value">${num(r.counts.attention)}</div><div class="sub">Below</div></div>
    </div>
    <div class="toolbar"><label class="ctl grow"><span>Find</span><div class="search-input">${icon("search")}<input class="input" id="review-find" placeholder="A person, lab, division, role, or folder" autocomplete="off"></div></label></div>
    <div class="grid split">
      <div class="stack">
        ${r.attention.length ? html`<div class="card"><div class="card-head"><h2>Worth a look</h2></div>
          <ul class="feed">${r.attention.map((a) => html`<li data-find="${text(a.text)}">${icon("alert")}<a href="${a.href}">${a.text}</a></li>`)}</ul></div>` : ""}
        <div class="card flush"><div class="card-head"><h2>Site roles</h2></div>
          <div class="table-wrap"><table class="table compact"><thead><tr><th>Role</th><th>Person</th><th>Since</th></tr></thead><tbody>
          ${r.roles.map((x) => html`<tr data-find="${text(x.label, x.division, x.person.display_name, x.person.username)}">
            <td>${x.label}${x.division ? html`<div class="sub">${x.division}</div>` : ""}</td><td>${personLink(x.person)}<div class="sub">${x.person.title}</div></td>
            <td class="nowrap">${since(x.since)}</td></tr>`)}</tbody></table></div></div>
        <div class="card flush"><div class="card-head"><h2>Labs and departments</h2></div>
          <div class="table-wrap"><table class="table compact review"><thead><tr><th>Lab or department</th><th>Members</th><th>Folders</th></tr></thead><tbody>
          ${r.labs.map((l) => html`<tr data-find="${text(l.name, l.slug, l.division, l.heads, l.members.map((m) => [m.display_name, m.username]), l.folders.map((f) => f.path))}">
            <td><a class="rowlink" href="#/access/labs/${encodeURIComponent(l.slug)}">${l.name}</a>
              <div class="sub">${l.division || "No division"}${l.heads.length ? ` · head: ${l.heads.join(", ")}` : ""}</div></td>
            <td>${l.members.length ? l.members.map((m) => html`<div class="small">${m.display_name} <span class="muted">· ${m.label} since ${m.since ? date(m.since) : "not recorded"}</span></div>`)
              : html`<span class="small muted">No members</span>`}</td>
            <td>${l.folders.length ? l.folders.map((f) => html`<div class="small"><span class="mono">${f.path}</span>${f.since ? html` <span class="muted">· since ${date(f.since)}</span>` : ""}</div>`)
              : html`<span class="small muted">No folders</span>`}</td></tr>`)}</tbody></table></div></div>
        <div class="card flush"><div class="card-head"><h2>People</h2></div>
          <div class="table-wrap"><table class="table compact review"><thead><tr><th>Person</th><th>Access</th><th>Sees</th>
            ${local ? html`<th class="hide-narrow">Last signed in</th>` : ""}<th class="hide-narrow num">Tokens</th></tr></thead><tbody>
          ${r.people.map((p) => html`<tr data-find="${text(p.display_name, p.username, p.title, p.access, p.folders.map((f) => f.path))}">
            <td>${personLink(p)}<div class="sub">${p.title}</div></td>
            <td>${p.access.length ? p.access.map((a) => html`<div class="small">${a}</div>`) : html`<span class="small muted">None</span>`}</td>
            <td>${sees(p)}</td>
            ${local ? html`<td class="hide-narrow nowrap">${p.last_login_at ? when(p.last_login_at) : html`<span class="muted small">Never</span>`}</td>` : ""}
            <td class="hide-narrow num">${p.tokens ? html`${num(p.tokens)}${p.tokens_with_access ? html`<div class="sub">${num(p.tokens_with_access)} with access</div>` : ""}` : html`<span class="muted">—</span>`}</td></tr>`)}</tbody></table></div></div>
      </div>
      <aside class="stack">
        <div class="card"><h3>Reviews</h3>
          ${r.reviews.length ? html`<ul class="feed" style="margin-top:6px">${r.reviews.map((v) => html`<li style="padding:8px 0">${icon("check")}<span>
            <span class="small"><b>${v.by || "Someone"}</b>, ${date(v.ts, true)}</span>${v.note ? html`<br><span class="small">${v.note}</span>` : ""}
            <br><span class="tiny muted">${v.summary.replace(/^.*? reviewed access: /, "")}</span></span></li>`)}</ul>`
            : html`<p class="small muted" style="margin-top:8px">Not reviewed yet. When you have gone through it, <b>Record a review</b>: it is kept in the audit log, with what there was.</p>`}</div>
        <div class="card"><h3>Reading it</h3>
          <ul class="small" style="margin:8px 0 0 18px; padding:0">
            <li>Only active people and labs: deactivated ones give no access, and are counted above.</li>
            <li><b>Since</b>: when the role or membership was given. Pinax began recording it in 0.12.0; earlier ones say <i>Not recorded</i>.</li>
            <li>A folder's date is when its lab came to own it, where that was changed in Pinax.</li>
            <li>A division head sees the folders of their division's labs, to read.</li>
            ${local ? html`<li>Worth a look includes people with access who have not signed in for ${r.idle_days} days.</li>` : ""}
          </ul>
          <p class="small" style="margin-top:10px">The <b>CSV</b> has a row for each piece of access each person has, for a spreadsheet or a sign-off record.</p></div>
      </aside>
    </div>`);

  $("#review-find", el).addEventListener("input", (e) => {
    const want = e.target.value.trim().toLowerCase();
    $$("[data-find]", el).forEach((row) => { row.hidden = Boolean(want) && !row.dataset.find.includes(want); });
  });
  $("#sign-off", el).addEventListener("click", async () => {
    const done = await modal({
      title: "Record a review",
      body: html`<p>Record that you have reviewed who has access to what, as it is now: ${num(r.counts.people)} people with access,
        ${num(r.counts.labs)} labs and departments, and ${num(r.counts.roles)} site roles. It changes no one's access.</p>
        <div class="field"><label for="review-note">Note</label><textarea class="input" id="review-note" rows="3" maxlength="2000"
          placeholder="e.g. Quarterly review; asked the Bennett Lab to remove a former student"></textarea></div>`,
      confirm: "Record review",
      onConfirm: async (wrap) => api("admin/access-review/sign-off", { method: "POST", body: { note: $("#review-note", wrap).value } }),
    });
    if (!done) return;
    toast("Review recorded");
    render(el, done);
  });
}

export async function viewAccessReview(el) {
  state.place = null;
  renderSidebar();
  render(el, await api("admin/access-review"));
}
