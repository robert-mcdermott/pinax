// SPDX-License-Identifier: Apache-2.0
// API tokens: a person's own, for scripts; service tokens, for administrators to make; every token, for
// administrators and auditors to see.
import { renderSidebar } from "/js/shell.js";
import { $, $$, api, date, emptyState, html, icon, modal, mount, raw, state, toast, when } from "/js/core.js";

const LASTS = [7, 30, 90, 180, 365];
const STATE_CHIP = {
  active: html`<span class="chip chip-approved">${icon("check")}Active</span>`,
  expired: html`<span class="chip chip-muted">${icon("clock")}Expired</span>`,
  revoked: html`<span class="chip chip-muted">${icon("x")}Revoked</span>`,
};
const scriptsUrl = () => `${location.origin}/script/api`;

function lasts(max, chosen) {
  const days = LASTS.filter((d) => d <= max);
  if (!days.includes(max)) days.push(max);
  return days.map((d) => html`<option value="${d}" ${d === chosen ? raw("selected") : ""}>${d === 365 ? "A year" : `${d} days`}</option>`);
}

function tokenRows(tokens, { person = false, mine = false, canRevoke }) {
  return html`<div class="table-wrap"><table class="table compact"><thead><tr>
    ${person ? html`<th>Person</th>` : ""}<th>Token</th><th>Access</th><th class="hide-narrow">Expires</th><th class="hide-narrow">Last used</th><th></th></tr></thead><tbody>
    ${tokens.map((t) => html`<tr>
      ${person ? html`<td>${t.person ? html`<a class="rowlink" href="#/access/people/${encodeURIComponent(t.person.username)}">${t.person.display_name}</a>` : html`<span class="muted">Service</span>`}</td>` : ""}
      <td><b>${t.name}</b><div class="sub"><span class="mono">${t.prefix}…</span> · made ${date(t.created_at)}${t.created_by && t.kind === "service" ? ` by ${t.created_by}` : ""}</div></td>
      <td><span class="small">${mine ? (t.read_only ? "Reads only" : "Your access") : t.access}</span></td>
      <td class="hide-narrow nowrap">${t.state === "active" ? date(t.expires_at) : STATE_CHIP[t.state]}${t.state === "revoked" && t.revoked_by ? html`<div class="sub">by ${t.revoked_by}</div>` : ""}</td>
      <td class="hide-narrow nowrap">${t.last_used_at ? html`${when(t.last_used_at)}<div class="sub mono">${t.last_used_address}</div>` : html`<span class="muted small">Never</span>`}</td>
      <td class="right">${t.state === "active" && canRevoke(t) ? html`<button class="btn btn-ghost btn-sm" data-revoke="${t.id}" data-name="${t.name}">Revoke</button>` : ""}</td></tr>`)}
    </tbody></table></div>`;
}

async function showSecret(made) {
  const t = made.token;
  await modal({
    title: `Your token “${t.name}”`,
    body: html`<p class="small">Copy it now: Pinax keeps only a fingerprint of it, so it is not shown again. Keep it where your script
      can read it and others cannot, as a password is kept.</p>
      <div class="secret" style="font-size:15px">${made.secret}</div>
      <p class="small muted">${t.access}, until ${date(t.expires_at)}. A script sends it in a header:</p>
      <pre class="json">curl -H "Authorization: Bearer $PINAX_TOKEN" \\
  ${scriptsUrl()}/me</pre>`,
    confirm: "Copy and close", cancel: "Close",
    onConfirm: async () => { await navigator.clipboard?.writeText(made.secret).catch(() => {}); toast("Copied"); },
  });
}

export async function viewTokens(el) {
  state.place = null;
  renderSidebar();
  const sees = state.me.sees_all, admin = state.me.is_admin;
  const [mine, every] = await Promise.all([api("tokens"), sees ? api("admin/tokens") : null]);
  const services = every ? every.tokens.filter((t) => t.kind === "service") : [];
  const others = every ? every.tokens.filter((t) => t.kind === "personal" && t.person.username !== state.me.username) : [];
  const own = (t) => t.person && t.person.username === state.me.username;
  mount(el, html`
    <div class="page-head"><div><div class="eyebrow">Your account</div><h1>API tokens</h1>
      <p>A token lets a script read Pinax, or act for you, without signing in: a nightly report, a chargeback import, a dashboard.</p></div></div>
    <div class="grid split">
      <div class="stack">
        <div class="card flush"><div class="card-head"><h2>Your tokens</h2></div>
          ${mine.tokens.length ? tokenRows(mine.tokens, { mine: true, canRevoke: () => true })
            : html`<div style="padding:0 22px 18px">${emptyState("key", "No tokens yet", "Make one below for each script, named for what it does.")}</div>`}
          <form class="card-foot" id="make-token" novalidate>
            <h3 style="margin:4px 0 12px">Make a token</h3>
            <div class="grid cols-2">
              <div class="field"><label for="t-name">Name</label><input class="input" id="t-name" maxlength="100" placeholder="e.g. Monthly chargeback" autocomplete="off" required>
                <span class="hint">What will use it, so you know which to revoke.</span></div>
              <div class="field"><label for="t-days">Lasts</label><select class="select" id="t-days">${lasts(mine.max_days, 90)}</select></div>
            </div>
            <div class="radio-cards" role="radiogroup" aria-label="What the token may do" style="margin:4px 0 14px">
              <label class="radio-card checked"><input type="radio" name="t-access" value="read" checked><span><span class="t">Reads only</span><br>
                <span class="small muted">Sees what you see, and changes nothing. Best for reports.</span></span></label>
              <label class="radio-card"><input type="radio" name="t-access" value="all"><span><span class="t">Your access</span><br>
                <span class="small muted">Also changes what you may change, such as your lab's tags and plans.</span></span></label>
            </div>
            <button class="btn btn-primary" type="submit">${icon("key")}Make token</button>
          </form></div>
        ${sees ? html`<div class="card flush"><div class="card-head"><h2>Service tokens</h2></div>
          <p class="small muted" style="padding:0 22px">For a service rather than a person, such as a finance system or a dashboard: it belongs to no one, so it outlives whoever made it, and it only reads.</p>
          ${services.length ? tokenRows(services, { canRevoke: () => admin }) : html`<p class="small muted" style="padding:0 22px 18px">No service tokens.</p>`}
          ${admin ? html`<form class="card-foot" id="make-service" novalidate>
            <h3 style="margin:4px 0 12px">Make a service token</h3>
            <div class="grid cols-2">
              <div class="field"><label for="s-name">Service</label><input class="input" id="s-name" maxlength="100" placeholder="e.g. Finance ledger" autocomplete="off" required></div>
              <div class="field"><label for="s-days">Lasts</label><select class="select" id="s-days">${lasts(every.max_days, 365)}</select></div>
            </div>
            <div class="radio-cards" role="radiogroup" aria-label="What the service reads" style="margin:4px 0 14px">
              <label class="radio-card checked"><input type="radio" name="s-role" value="finance" checked><span><span class="t">As finance</span><br>
                <span class="small muted">Every lab's charges, the chargeback statement, and the prices behind them.</span></span></label>
              <label class="radio-card"><input type="radio" name="s-role" value="auditor"><span><span class="t">As an auditor</span><br>
                <span class="small muted">Everything, people and folders included.</span></span></label>
            </div>
            <button class="btn btn-primary" type="submit">${icon("key")}Make service token</button>
          </form>` : ""}</div>
        <div class="card flush"><div class="card-head"><h2>Everyone else's tokens</h2></div>
          ${others.length ? tokenRows(others, { person: true, canRevoke: () => admin }) : html`<p class="small muted" style="padding:0 22px 18px">No one else has a token.</p>`}</div>` : ""}
      </div>
      <aside class="stack">
        <div class="card"><h3>Using a token</h3>
          <p class="small" style="margin-top:8px">A script sends the token in an <span class="mono">Authorization</span> header, to Pinax's API under
            <span class="mono">/script/api/</span>, which works however people sign in:</p>
          <pre class="json" style="white-space:pre-wrap; overflow-wrap:anywhere">curl -H "Authorization: Bearer $PINAX_TOKEN" \\
  "${scriptsUrl()}/charges?month=${new Date().toISOString().slice(0, 7)}"</pre>
          <p class="small" style="margin-top:10px">Every page's data has an address under <span class="mono">/api/</span>: <a href="/api/docs" target="_blank" rel="noopener">the API's reference</a> lists them.</p></div>
        <div class="card"><h3>Keeping tokens safe</h3>
          <ul class="small" style="margin:8px 0 0 18px; padding:0">
            <li>One token for each script, so revoking one stops only it.</li>
            <li>Reading only, unless the script must change something.</li>
            <li>A token stops when it expires, when it is revoked, and when its person is deactivated.</li>
            <li>Every change made with a token is in the audit log, with the token's name.</li>
            <li>No token makes tokens or changes a password.</li>
          </ul></div>
      </aside>
    </div>`);

  $$(".radio-card input", el).forEach((r) => r.addEventListener("change", () => {
    $$(`input[name="${r.name}"]`, el).forEach((o) => o.closest(".radio-card").classList.toggle("checked", o.checked));
  }));
  const submit = (form, path, body) => form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const made = await api(path, { method: "POST", body: body() });
      await showSecret(made);
      viewTokens(el);
    } catch (err) { toast(err.message, "error"); }
  });
  submit($("#make-token", el), "tokens", () => ({ name: $("#t-name", el).value, days: Number($("#t-days", el).value),
    read_only: $('input[name="t-access"]:checked', el).value === "read" }));
  submit($("#make-service", el), "admin/tokens", () => ({ name: $("#s-name", el).value, days: Number($("#s-days", el).value),
    role: $('input[name="s-role"]:checked', el).value }));
  $$("[data-revoke]", el).forEach((b) => b.addEventListener("click", async () => {
    const t = [...mine.tokens, ...(every ? every.tokens : [])].find((x) => String(x.id) === b.dataset.revoke);
    const ok = await modal({
      title: `Revoke “${b.dataset.name}”?`,
      body: html`<p>${own(t) ? "Scripts using it" : t.person ? `${t.person.display_name}'s scripts using it` : "The service using it"} will be refused from now on. This cannot be undone; ${t.person && !own(t) ? "they can" : "you can"} make a new token at any time.</p>`,
      confirm: "Revoke", tone: "warn",
    });
    if (!ok) return;
    try {
      await api(`tokens/${b.dataset.revoke}`, { method: "DELETE" });
      toast("Token revoked");
      viewTokens(el);
    } catch (err) { toast(err.message, "error"); }
  }));
}

/** A person's tokens on their page: what they have, for administrators to revoke. */
export function personTokens(person) {
  const live = person.tokens.filter((t) => t.state === "active");
  return html`<div class="card"><h3>API tokens</h3>
    ${person.tokens.length ? html`<ul class="feed" style="margin-top:6px">${person.tokens.map((t) => html`<li style="padding:8px 0">${icon("key")}<span style="flex:1">
      <span class="small"><b>${t.name}</b> <span class="mono tiny muted">${t.prefix}…</span></span><br>
      <span class="tiny muted">${t.access} · ${t.state === "active" ? `until ${date(t.expires_at)}` : t.state}${t.last_used_at ? ` · used ${date(t.last_used_at)}` : " · never used"}</span></span>
      ${t.state === "active" && state.me.is_admin ? html`<button class="btn btn-ghost btn-sm" style="flex:none; align-self:center" data-revoke-token="${t.id}" data-name="${t.name}">Revoke</button>` : ""}</li>`)}</ul>`
      : html`<p class="small muted" style="margin-top:8px">None. People make their own, from the menu under their name.</p>`}
    ${live.length && person.active ? html`<p class="tiny muted" style="margin-top:6px">Deactivating ${person.display_name} revokes ${live.length === 1 ? "it" : "them"}.</p>` : ""}</div>`;
}

/** Bind the person page's revoke buttons. */
export function bindPersonTokens(el, person, onChange) {
  $$("[data-revoke-token]", el).forEach((b) => b.addEventListener("click", async () => {
    const ok = await modal({
      title: `Revoke ${person.display_name}'s token “${b.dataset.name}”?`,
      body: html`<p>Their scripts using it will be refused from now on. They can make a new one.</p>`,
      confirm: "Revoke", tone: "warn",
    });
    if (!ok) return;
    try {
      await api(`tokens/${b.dataset.revokeToken}`, { method: "DELETE" });
      toast("Token revoked");
      onChange();
    } catch (err) { toast(err.message, "error"); }
  }));
}
