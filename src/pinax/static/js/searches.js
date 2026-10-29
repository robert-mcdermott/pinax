// SPDX-License-Identifier: Apache-2.0
// Searches: file searches run in the background (jobs.py), past a page's time limit, with what each found, kept for
// a while. Started from Find files, for a whole filesystem; this page follows them while they run.
import { $$, api, bytes, date, displayPath, emptyState, html, icon, mount, num, poll, toast } from "/js/core.js";
import { renderSidebar } from "/js/shell.js";

const STATUS = { queued: ["chip-queued", "Waiting for a turn"], running: ["chip-active", "Running"], done: ["chip-approved", "Done"],
  failed: ["chip-failed", "Failed"], stopped: ["chip-muted", "Stopped"] };

function asked(s) {
  const f = s.filter || {};
  const parts = [f.q && `named ${f.q}`, f.category && `${f.category} data`, f.ext && `.${f.ext} files`, f.min_size && `at least ${bytes(f.min_size)}`,
    f.older && "older", f.newer && "newer", f.uid != null && `owned by uid ${f.uid}`].filter(Boolean);
  return parts.length ? parts.join(", ") : "every file";
}

export async function viewSearches(el, query) {
  renderSidebar();
  const open = new URLSearchParams(query || "").get("open");
  const data = await api("files/jobs");
  const shown = open ? await api(`files/jobs/${open}`).catch(() => null) : null;
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Explore</div><h1>Searches</h1>
      <p>File searches run in the background, past a page's time limit, from <a href="#/files">Find files</a> for a whole filesystem. What each finds is
        kept for ${num(data.days)} days.</p></div></div>
    ${data.searches.length ? html`<div class="card flush"><div class="table-wrap"><table class="table"><thead><tr><th>Search</th><th>Started</th>
        <th>State</th><th class="num">Found</th><th></th></tr></thead><tbody>
      ${data.searches.map((s) => html`<tr><td><b>${asked(s)}</b><div class="sub">${s.fs_name ? displayPath(s.fs, s.path) : "Every filesystem"} · ${s.by}</div>
          ${s.message ? html`<div class="sub">${s.message}</div>` : ""}</td>
        <td class="nowrap">${date(s.created_at, true)}</td><td><span class="chip ${STATUS[s.status][0]}">${STATUS[s.status][1]}</span></td>
        <td class="num nowrap">${s.files == null ? "—" : html`${num(s.files)} files<div class="sub">${bytes(s.bytes)}</div>`}</td>
        <td class="right nowrap">${s.status === "done" ? html`<a class="btn btn-ghost btn-sm" href="#/searches?open=${s.id}">Open</a>
            <a class="btn btn-ghost btn-sm" href="/api/files/jobs/${s.id}/csv">${icon("download")}CSV</a>` : ""}
          <button class="icon-btn" title="${s.status === "running" ? "Stop" : "Delete"}" aria-label="${s.status === "running" ? "Stop" : "Delete"} this search" data-delete="${s.id}">${icon("x")}</button></td></tr>`)}
      </tbody></table></div></div>`
      : html`<div class="card">${emptyState("search", "No searches yet", "Search a whole filesystem on Find files, then run it in the background.")}</div>`}
    ${shown ? html`<div class="card flush" style="margin-top:20px"><div class="card-head"><h2>${asked(shown)}</h2><span class="small muted">The first ${num(shown.rows.length)} of ${num(shown.files)}</span></div>
      <div class="table-wrap"><table class="table compact"><thead><tr><th>File</th><th class="num">Size</th><th>Last change</th></tr></thead><tbody>
      ${shown.rows.map((f) => html`<tr><td><span class="title-cell">${f.name}</span><div class="sub path">${displayPath(f.fs, f.path)}</div></td>
        <td class="num nowrap">${bytes(f.size)}</td><td class="nowrap">${f.mtime ? date(f.mtime) : "—"}</td></tr>`)}</tbody></table></div></div>` : ""}`);
  $$("[data-delete]", el).forEach((b) => b.addEventListener("click", async () => {
    try { await api(`files/jobs/${b.dataset.delete}`, { method: "DELETE" }); toast("Deleted"); viewSearches(el, ""); } catch (e) { toast(e.message, "error"); }
  }));
  // While any search waits or runs, look again every few seconds.
  if (data.searches.some((s) => s.status === "queued" || s.status === "running")) {
    const watch = poll(async () => {
      const now = await api("files/jobs", { quiet: true });
      if (!now.searches.some((s) => s.status === "queued" || s.status === "running")) {
        watch.stop();
        viewSearches(el, query);
      }
    }, 3000);
  }
}
