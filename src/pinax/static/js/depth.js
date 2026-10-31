// SPDX-License-Identifier: Apache-2.0
// The icicle and the sunburst: a folder's subfolders several levels deep, each spanning its share of the folder it
// is in, so deep, narrow trees show where a treemap hides them. Both draw the same partition: the icicle as columns
// (one per level, the tiles of treemap.js), the sunburst as rings (SVG arcs). A folder's subfolders start at its
// own start, the largest first; the space after them is its own files and the folders too small to draw.
import { esc, visible } from "/js/core.js";
import { fitText, fonts, resolver, tileElement, tooltip } from "/js/treemap.js";

// ---------- Layout ----------
/** Every tile with a value, as { t, depth, x0, x1 } in [0, 1] of `total`, and how many levels are drawn. */
export function partition(tiles, value, total) {
  const nodes = [];
  let levels = 0;
  const walk = (list, start, span, whole, depth) => {
    const items = (list || []).map((t) => ({ t, v: Math.max(0, value(t) || 0) })).filter((d) => d.v > 0).sort((a, b) => b.v - a.v);
    let x = start;
    for (const { t, v } of items) {
      const w = Math.min(v / whole, 1) * span;
      nodes.push({ t, depth, x0: x, x1: x + w });
      levels = Math.max(levels, depth + 1);
      walk(t.children, x, w, v, depth + 1);
      x += w;
    }
  };
  if (total > 0) walk(tiles, 0, 1, total, 0);
  return { nodes, levels };
}

const empty = (el, opts) => { el.innerHTML = `<div class="tm-empty">${opts.onEmpty ? opts.onEmpty() : "Nothing to show"}</div>`; };

// ---------- Icicle ----------
/**
 * Draw `tiles` (nested by `children`) into `el` as columns, one per level, left to right.
 * opts: as for renderTreemap (treemap.js), plus total: what the place holds by `value`.
 * The first level's tiles are in the tab order; deeper ones are reached by opening a folder.
 */
export function renderIcicle(el, tiles, opts) {
  el.innerHTML = "";
  const { nodes, levels } = partition(tiles, opts.value, opts.total);
  if (!nodes.length) return empty(el, opts);
  const W = el.clientWidth, H = el.clientHeight;
  const ctx = { col: resolver(), fonts: fonts(), tip: tooltip(el) };
  const column = W / levels;
  const frag = document.createDocumentFragment();
  for (const n of nodes) {
    const r = { x: n.depth * column, y: n.x0 * H, w: column, h: (n.x1 - n.x0) * H };
    if (r.h < 1) continue;
    frag.append(tileElement(n.t, r, opts, ctx, { focusable: n.depth === 0 }).a);
  }
  el.append(frag, ctx.tip.el);
}

// ---------- Sunburst ----------
const TAU = 2 * Math.PI;
const point = (cx, cy, r, a) => [cx + r * Math.sin(a), cy - r * Math.cos(a)]; // a: clockwise from 12 o'clock

function arc(cx, cy, ri, ro, a0, a1) {
  if (a1 - a0 >= TAU - 1e-6) a1 = a0 + TAU - 1e-4; // a whole ring: an arc can't end where it starts
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const f = (n) => n.toFixed(2);
  const [x0, y0] = point(cx, cy, ro, a0), [x1, y1] = point(cx, cy, ro, a1);
  const [x2, y2] = point(cx, cy, ri, a1), [x3, y3] = point(cx, cy, ri, a0);
  return `M${f(x0)},${f(y0)}A${f(ro)},${f(ro)} 0 ${large} 1 ${f(x1)},${f(y1)}L${f(x2)},${f(y2)}`
    + `A${f(ri)},${f(ri)} 0 ${large} 0 ${f(x3)},${f(y3)}Z`;
}

const NS = "http://www.w3.org/2000/svg";
const svgEl = (name, attrs = {}) => {
  const e = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
};

/**
 * Draw `tiles` (nested by `children`) into `el` as rings around the place, one per level, clockwise from the top.
 * opts: as for renderIcicle, plus center: { name, sub, href }, the place itself, drawn in the middle (its link
 * goes up one level). A ring's outer arcs are longer than its inner ones, so compare folders by angle, not area.
 */
export function renderSunburst(el, tiles, opts) {
  el.innerHTML = "";
  const { nodes, levels } = partition(tiles, opts.value, opts.total);
  if (!nodes.length) return empty(el, opts);
  const W = el.clientWidth, H = el.clientHeight;
  const col = resolver();
  const { font, small } = fonts();
  const tip = tooltip(el);
  const cx = W / 2, cy = H / 2;
  const R = Math.min(W, H) / 2 - 3;
  const r0 = Math.max(R * 0.2, 40);
  const ring = (R - r0) / levels;
  const svg = svgEl("svg", { class: "sb", width: W, height: H, viewBox: `0 0 ${W} ${H}` });
  const hot = svgEl("path", { class: "sb-hot" });

  const center = opts.center || {};
  const middle = svgEl(center.href ? "a" : "g", center.href ? { href: center.href, class: "sb-up", "aria-label": `Up one level from ${visible(center.name || "")}` } : {});
  middle.append(svgEl("circle", { class: "sb-center", cx, cy, r: r0 - 2 }));
  const title = fitText(visible(center.name || ""), r0 * 1.6, font);
  if (title) middle.append(Object.assign(svgEl("text", { class: "sb-name", x: cx, y: cy - (center.sub ? 7 : 0) }), { textContent: title }));
  const sub = center.sub && fitText(center.sub, r0 * 1.6, small);
  if (sub) middle.append(Object.assign(svgEl("text", { class: "sb-sub", x: cx, y: cy + 11 }), { textContent: sub }));
  if (center.href) {
    const up = () => tip.show(`<div class="t">${esc(visible(center.name || ""))}</div><div class="p">Open the folder it is in</div>`, null, { x: cx, y: cy, w: 0, h: 0 });
    middle.addEventListener("mouseenter", up);
    middle.addEventListener("focus", up);
    middle.addEventListener("mouseleave", tip.hide);
    middle.addEventListener("blur", tip.hide);
  }
  svg.append(middle);

  for (const n of nodes) {
    const a0 = n.x0 * TAU, a1 = n.x1 * TAU;
    const ri = r0 + n.depth * ring, ro = ri + ring;
    if ((a1 - a0) * ro < 1) continue; // thinner than a pixel at its widest
    const t = n.t;
    const url = opts.href(t);
    const name = visible(t.name || "");
    const link = svgEl(url ? "a" : "g", url ? { href: url, class: "sb-link" } : {});
    if (url && n.depth > 0) link.setAttribute("tabindex", "-1");
    link.setAttribute("aria-label", `${name}: ${opts.label(t)}`);
    const d = arc(cx, cy, ri, ro, a0, a1);
    const c = col(opts.color(t));
    link.append(svgEl("path", { class: `sb-arc${url ? "" : " static"}`, d, fill: c.bg }));
    // A label across the ring (along the radius) or along it (on the tangent at the middle of the arc), whichever
    // has more room: across, the ring's depth, where the arc is wide enough for a line of text; along, the chord,
    // kept to where the straight line stays inside the ring.
    const am = (a0 + a1) / 2, rm = (ri + ro) / 2;
    const across = (a1 - a0) * rm >= 15 ? ring - 10 : 0;
    const room = ring / 2 - 8;
    const along = room > 0 ? Math.min(2 * rm * Math.sin(Math.min(a1 - a0, Math.PI) / 2) - 10, 2 * Math.sqrt(rm * rm - (rm - room) ** 2)) : 0;
    const text = Math.max(across, along) >= 24 ? fitText(name, Math.max(across, along), font) : null;
    if (text && (text === name || text.length > 5)) { // a whole name, or enough of one to recognize
      const deg = (am * 180) / Math.PI;
      const turn = along > across
        ? `rotate(${deg.toFixed(2)}) translate(0,${(-rm).toFixed(2)})${am > Math.PI / 2 && am < 1.5 * Math.PI ? " rotate(180)" : ""}`
        : `rotate(${(deg - 90).toFixed(2)}) translate(${rm.toFixed(2)},0)${am > Math.PI ? " rotate(180)" : ""}`;
      const label = svgEl("text", { class: "sb-label", fill: c.fg, transform: `translate(${cx.toFixed(2)},${cy.toFixed(2)}) ${turn}` });
      label.textContent = text;
      link.append(label);
    }
    const [mx, my] = point(cx, cy, rm, am);
    const show = (e) => { hot.setAttribute("d", d); hot.classList.remove("focus"); tip.show(opts.tip(t), e); };
    link.addEventListener("mouseenter", show);
    link.addEventListener("mousemove", show);
    link.addEventListener("mouseleave", () => { hot.removeAttribute("d"); tip.hide(); });
    link.addEventListener("focus", () => { hot.setAttribute("d", d); hot.classList.add("focus"); tip.show(opts.tip(t), null, { x: mx, y: my, w: 0, h: 0 }); });
    link.addEventListener("blur", () => { hot.removeAttribute("d"); tip.hide(); });
    svg.append(link);
  }
  svg.append(hot);
  el.append(svg, tip.el);
}
