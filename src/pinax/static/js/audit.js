// SPDX-License-Identifier: Apache-2.0
// The audit log: every audited change and sign-in, for administrators and auditors, found by person, kind of change,
// lab, folder, and day, a page at a time, with its CSV.
import { renderSidebar } from "/js/shell.js";
import { $, api, date, emptyState, fsInfo, html, icon, mount, num, parseQuery, raw, state } from "/js/core.js";

const PAGE = 50;
const KEYS = ["person", "action", "lab", "fs", "path", "since", "until"];
const SHOWN_ELSEWHERE = new Set(["token", "source", "by"]);

const opt = (value, label, chosen) => html`<option value="${value}" ${value === (chosen || "") ? raw("selected") : ""}>${label}</option>`;
const hashOf = (q) => {
  const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== undefined && v !== null && v !== ""));
  return `#/audit${String(qs) ? `?${qs}` : ""}`;
};

function who(e) {
  const token = e.token ? html`<div class="sub">with the token “${e.token}”</div>` : "";
  if (e.who) return html`<a class="rowlink" href="#/access/people/${encodeURIComponent(e.who.username)}">${e.who.display_name}</a>${token}`;
  if (e.source === "cli") return html`Command line${e.by ? html`<div class="sub mono">${e.by}</div>` : ""}`;
  return html`<span class="muted">Pinax</span>`;
}

function details(e) {
  const rows = Object.entries(e.detail).filter(([k, v]) => !SHOWN_ELSEWHERE.has(k) && v !== null && v !== "");
  if (!rows.length) return "";
  return html`<details class="audit-detail"><summary class="tiny">Details</summary><dl class="kv">
    ${rows.map(([k, v]) => html`<dt>${k.replace(/_/g, " ")}</dt><dd class="${typeof v === "string" ? "" : "mono"}">${typeof v === "string" ? v : JSON.stringify(v)}</dd>`)}</dl></details>`;
}

export async function viewAudit(el, query) {
  state.place = null;
  renderSidebar();
  const q = parseQuery(query);
  const pageNo = Math.max(1, Number(q.page) || 1);
  const filters = Object.fromEntries(KEYS.map((k) => [k, q[k] || undefined]));
  const log = await api("admin/audit", { query: { ...filters, offset: (pageNo - 1) * PAGE, limit: PAGE } });
  const fs = fsInfo(filters.fs);
  const folder = fs && filters.path && filters.path !== fs.root ? filters.path.slice(fs.root.length + 1) : "";
  const kinds = [...new Set(log.actions.map((a) => a.kind))];
  const csv = `/api/admin/audit?${new URLSearchParams(Object.entries({ ...filters, fmt: "csv" }).filter(([, v]) => v))}`;
  const filtered = KEYS.some((k) => filters[k]);
  const first = log.offset + 1, last = log.offset + log.events.length;
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Administration</div><h1>Audit log</h1>
      <p>Every change made in Pinax, by import, and from the command line, and every sign-in with local sign-in: who, when, from where, and what changed.</p></div>
      <div class="actions"><a class="btn btn-ghost btn-sm" href="${csv}">${icon("download")}Download CSV</a></div></div>
    <form class="card" id="audit-filters" style="margin-bottom:20px">
      <div class="toolbar" style="margin:0">
        <label class="ctl"><span>Person</span><select class="select sm" name="person">${opt("", "Anyone", filters.person)}
          ${log.people.map((p) => opt(p.username, p.display_name, filters.person))}</select></label>
        <label class="ctl"><span>Change</span><select class="select sm" name="action">${opt("", "Any", filters.action)}
          ${kinds.map((k) => html`<optgroup label="${log.kinds[k] || k}">${opt(k, `All: ${(log.kinds[k] || k).toLowerCase()}`, filters.action)}
            ${log.actions.filter((a) => a.kind === k).map((a) => opt(a.action, `${a.action} (${num(a.count)})`, filters.action))}</optgroup>`)}</select></label>
        <label class="ctl"><span>Lab or department</span><select class="select sm" name="lab">${opt("", "Any", filters.lab)}
          ${log.labs.map((l) => opt(l.slug, l.name, filters.lab))}</select></label>
        <label class="ctl"><span>Filesystem</span><select class="select sm" name="fs">${opt("", "Any", filters.fs)}
          ${(state.config.filesystems || []).map((f) => opt(f.slug, f.name, filters.fs))}</select></label>
        <label class="ctl grow"><span>Folder, and the folders in it</span><input class="input sm" name="folder" value="${folder}" placeholder="e.g. dasgupta_r/sequencing" autocomplete="off" spellcheck="false"></label>
        <label class="ctl"><span>From</span><input class="input sm" type="date" name="since" value="${filters.since || ""}"></label>
        <label class="ctl"><span>To</span><input class="input sm" type="date" name="until" value="${filters.until || ""}"></label>
        <div class="ctl"><button class="btn btn-primary btn-sm" type="submit">${icon("search")}Show</button></div>
        ${filtered ? html`<div class="ctl"><a class="btn btn-ghost btn-sm" href="#/audit">Clear</a></div>` : ""}
      </div>
      <p class="hint" style="margin:8px 0 0">A person's changes include those made to them; a lab's, those to its folders; a folder's, those to who owns it.</p>
    </form>
    <div class="card flush"><div class="card-head"><h2>${log.total ? `${num(log.total)} ${log.total === 1 ? "change" : "changes"}` : "No changes"}</h2>
      ${log.total > PAGE ? html`<span class="small muted">${num(first)}–${num(last)}, newest first</span>` : ""}</div>
      ${log.events.length ? html`<div class="table-wrap"><table class="table compact audit"><thead><tr>
        <th style="width:150px">When</th><th style="width:190px">Who</th><th>What</th><th class="hide-narrow" style="width:130px">From</th></tr></thead><tbody>
        ${log.events.map((e) => html`<tr>
          <td class="nowrap"><span title="${date(e.ts, true)}">${date(e.ts)}</span><div class="sub">${new Date(e.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div></td>
          <td>${who(e)}</td>
          <td><span class="small">${e.summary}</span><div class="sub mono">${e.action}</div>${details(e)}</td>
          <td class="hide-narrow mono small">${e.address || html`<span class="muted">—</span>`}</td></tr>`)}</tbody></table></div>`
        : html`<div style="padding:0 22px 18px">${emptyState("search", "Nothing matches", filtered ? "Try fewer filters, or a wider range of days." : "Nothing has been changed yet.")}</div>`}
      ${log.total > PAGE ? html`<div class="card-foot row between">
        ${pageNo > 1 ? html`<a class="btn btn-ghost btn-sm" href="${hashOf({ ...filters, page: pageNo - 1 })}">${icon("arrowLeft")}Newer</a>` : html`<span></span>`}
        <span class="small muted">Page ${num(pageNo)} of ${num(Math.ceil(log.total / PAGE))}</span>
        ${last < log.total ? html`<a class="btn btn-ghost btn-sm" href="${hashOf({ ...filters, page: pageNo + 1 })}">Older${icon("arrowRight")}</a>` : html`<span></span>`}</div>` : ""}
    </div>`);

  $("#audit-filters", el).addEventListener("submit", (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const chosen = fsInfo(f.get("fs"));
    const rel = String(f.get("folder") || "").trim().replace(/^\/+|\/+$/g, "");
    location.hash = hashOf({ person: f.get("person"), action: f.get("action"), lab: f.get("lab"), fs: f.get("fs"),
      path: chosen && rel ? `${chosen.root.replace(/\/$/, "")}/${rel}` : undefined, since: f.get("since"), until: f.get("until") });
  });
}
