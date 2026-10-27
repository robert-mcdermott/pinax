// SPDX-License-Identifier: Apache-2.0
// Squarified treemap (Bruls, Huizing and van Wijk, 2000), drawn as absolutely positioned links so
// every tile is a real, keyboard-reachable navigation target. Tiles are separated by a 2px surface
// gap (never a stroke), labels are measured and only drawn when they fit, and nested tiles sit in a
// neutral frame whose header names the parent folder.
import { esc } from "/js/core.js";

const GAP = 1; // inset per side: 2px between neighbors
const HEADER = 19; // frame header height when drawing two levels
const MIN_FRAME = { w: 110, h: 64 };

// ---------- Layout ----------
function worst(row, side) {
  let s = 0, max = -Infinity, min = Infinity;
  for (const a of row) { s += a; if (a > max) max = a; if (a < min) min = a; }
  const s2 = s * s, side2 = side * side;
  return Math.max((side2 * max) / s2, s2 / (side2 * min));
}

export function squarify(values, x, y, w, h) {
  const total = values.reduce((t, v) => t + v, 0);
  if (!total || w <= 0 || h <= 0) return values.map(() => null);
  const scale = (w * h) / total;
  const areas = values.map((v) => v * scale);
  const out = new Array(values.length);
  let rect = { x, y, w, h };
  let row = [], rowIdx = [], i = 0;
  const place = () => {
    const s = row.reduce((t, a) => t + a, 0);
    if (rect.w >= rect.h) {
      const cw = s / rect.h;
      let cy = rect.y;
      row.forEach((a, k) => { const ch = a / cw; out[rowIdx[k]] = { x: rect.x, y: cy, w: cw, h: ch }; cy += ch; });
      rect = { x: rect.x + cw, y: rect.y, w: rect.w - cw, h: rect.h };
    } else {
      const rh = s / rect.w;
      let cx = rect.x;
      row.forEach((a, k) => { const cw = a / rh; out[rowIdx[k]] = { x: cx, y: rect.y, w: cw, h: rh }; cx += cw; });
      rect = { x: rect.x, y: rect.y + rh, w: rect.w, h: rect.h - rh };
    }
    row = []; rowIdx = [];
  };
  while (i < areas.length) {
    const side = Math.min(rect.w, rect.h);
    const next = areas[i];
    if (!row.length || worst([...row, next], side) <= worst(row, side)) { row.push(next); rowIdx.push(i); i++; }
    else place();
  }
  if (row.length) place();
  return out;
}

// ---------- Colors and labels ----------
const lum = (hex) => {
  const m = hex.replace("#", "").match(/.{2}/g);
  if (!m) return 0;
  const [r, g, b] = m.map((c) => { const v = parseInt(c, 16) / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

function resolver() {
  const cs = getComputedStyle(document.documentElement);
  const ink = cs.getPropertyValue("--tile-ink").trim() || "#0B1022";
  const cache = new Map();
  return (token) => {
    if (cache.has(token)) return cache.get(token);
    const hex = cs.getPropertyValue(token).trim() || "#888888";
    const fg = contrast(hex, "#FFFFFF") >= contrast(hex, ink) ? "#FFFFFF" : ink;
    const v = { bg: hex, fg };
    cache.set(token, v);
    return v;
  };
}

let measureCtx = null;
function measure(text, font) {
  measureCtx = measureCtx || document.createElement("canvas").getContext("2d");
  measureCtx.font = font;
  return measureCtx.measureText(text).width;
}
function fitText(text, maxW, font) {
  if (measure(text, font) <= maxW) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (measure(text.slice(0, mid) + "…", font) <= maxW) lo = mid; else hi = mid - 1;
  }
  return lo >= 3 ? text.slice(0, lo) + "…" : null;
}

// ---------- Rendering ----------
/**
 * Draw `tiles` into `el`.
 * opts: value(tile) -> number; color(tile) -> CSS custom property name (e.g. "--age-3");
 *       href(tile) -> URL or null; label(tile) -> secondary text; tip(tile) -> trusted HTML string;
 *       levels: 1 or 2; onEmpty() -> HTML for when nothing has a value.
 */
export function renderTreemap(el, tiles, opts) {
  const { value, color, href, label, tip, levels = 1 } = opts;
  el.innerHTML = "";
  const W = el.clientWidth, H = el.clientHeight;
  const font = `700 12.5px ${getComputedStyle(document.body).fontFamily}`;
  const small = `400 11.5px ${getComputedStyle(document.body).fontFamily}`;
  const col = resolver();
  const tipEl = document.createElement("div");
  tipEl.className = "tm-tip";
  tipEl.hidden = true;
  tipEl.setAttribute("role", "tooltip");

  const items = tiles.map((t) => ({ t, v: Math.max(0, value(t) || 0) })).filter((d) => d.v > 0).sort((a, b) => b.v - a.v);
  if (!items.length) {
    el.innerHTML = `<div class="tm-empty">${opts.onEmpty ? opts.onEmpty() : "Nothing to show"}</div>`;
    return;
  }
  const frag = document.createDocumentFragment();
  const rects = squarify(items.map((d) => d.v), 0, 0, W, H);

  const makeTile = (t, r, { frame = false, focusable = true } = {}) => {
    const url = href(t);
    const a = document.createElement(url ? "a" : "div");
    if (url) a.href = url;
    if (!focusable) a.tabIndex = -1;
    a.className = `tm-tile ${frame ? "frame" : "leaf"} ${url ? "" : "static"}`;
    const x = r.x + GAP, y = r.y + GAP, w = Math.max(0, r.w - 2 * GAP), h = Math.max(0, r.h - 2 * GAP);
    Object.assign(a.style, { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` });
    if (!frame) {
      const c = col(color(t));
      a.style.background = c.bg;
      a.style.color = c.fg;
    }
    const name = t.name || "";
    a.setAttribute("aria-label", `${name}: ${label(t)}`);
    const room = w - 12;
    if (room > 18 && h >= 18) {
      const n = fitText(name, room, font);
      if (n) {
        const sub = !frame && h >= 36 ? fitText(label(t), room, small) : null;
        a.insertAdjacentHTML("beforeend", `<span class="tm-l">${esc(n)}${sub ? `<small>${esc(sub)}</small>` : ""}</span>`);
      }
    }
    const onPointer = (e) => { if (e.target.closest(".tm-tile") === a) showTip(t, e); };
    a.addEventListener("mouseenter", onPointer);
    a.addEventListener("mousemove", onPointer);
    a.addEventListener("mouseleave", () => { tipEl.hidden = true; });
    a.addEventListener("focus", () => showTip(t, null, { x, y, w, h }));
    a.addEventListener("blur", () => { tipEl.hidden = true; });
    frag.append(a);
    return { x, y, w, h };
  };

  const showTip = (t, e, box) => {
    tipEl.innerHTML = tip(t);
    tipEl.hidden = false;
    const bounds = el.getBoundingClientRect();
    let px, py;
    if (e) { px = e.clientX - bounds.left + 14; py = e.clientY - bounds.top + 14; }
    else { px = box.x + Math.min(box.w, 40); py = box.y + Math.min(box.h, 30); }
    const tw = tipEl.offsetWidth, th = tipEl.offsetHeight;
    if (px + tw > W) px = Math.max(0, px - tw - 28);
    if (py + th > H) py = Math.max(0, H - th - 4);
    tipEl.style.left = `${px}px`;
    tipEl.style.top = `${py}px`;
  };

  items.forEach((d, i) => {
    const r = rects[i];
    if (!r) return;
    const kids = levels > 1 ? (d.t.children || []).map((c) => ({ t: c, v: Math.max(0, value(c) || 0) })).filter((k) => k.v > 0) : [];
    const nest = kids.length > 0 && r.w >= MIN_FRAME.w && r.h >= MIN_FRAME.h && !(kids.length === 1 && kids[0].t.kind === "files");
    if (!nest) { makeTile(d.t, r); return; }
    const box = makeTile(d.t, r, { frame: true });
    kids.sort((a, b) => b.v - a.v);
    const inner = { x: box.x + 2, y: box.y + HEADER, w: box.w - 4, h: box.h - HEADER - 2 };
    // Children are scaled to the parent's own total, so a parent with files of its own keeps the right proportions.
    const kr = squarify(kids.map((k) => k.v), inner.x, inner.y, inner.w, inner.h);
    kids.forEach((k, j) => { if (kr[j]) makeTile(k.t, kr[j], { focusable: false }); });
  });
  el.append(frag, tipEl);
}
