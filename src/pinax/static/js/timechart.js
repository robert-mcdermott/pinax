// SPDX-License-Identifier: Apache-2.0
// Charts over time: stacked columns (one per scan) and lines, on a shared time axis. Scans are events, so
// columns and markers sit at each scan's date; nothing is drawn between scans a folder was not in. Marks follow
// the dataviz specs: columns at most 24px wide with a 4px rounded cap and square base, 2px surface gaps between
// stacked segments, 2px lines, 8px markers with a 2px surface ring, hairline solid gridlines. Colors are CSS
// custom properties, so a theme change needs no redraw. Several charts can share one hover: `link(charts)`.
const NS = "http://www.w3.org/2000/svg";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
let ctx;
const measure = (text, font) => {
  ctx = ctx || document.createElement("canvas").getContext("2d");
  ctx.font = font;
  return ctx.measureText(text).width;
};

/** Ticks from 0 to a clean top above `max`, about `target` of them, as multiples of `unit`. */
export function niceTicks(max, { target = 4, unit = 1 } = {}) {
  const v = max / unit;
  if (!(v > 0)) return [0, unit];
  const raw = v / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const top = Math.ceil(v / step - 1e-9) * step;
  const out = [];
  for (let t = 0; t <= top + step / 2; t += step) out.push(Math.round(t / step) * step * unit);
  return out;
}

/**
 * Draw a chart in `el` (sized by CSS height) and return { highlight(i) }.
 *   times: Date per point; kind: "stack" or "line";
 *   series: [{ token, label, values }] (values null where nothing is drawn; a stack lists its bottom first);
 *   ticks(max) -> y tick values; tick(v) -> tick text; value(i) -> the label at the last point (or null);
 *   tip(i) -> trusted HTML for the readout; label: the chart's accessible summary;
 *   onHover(i | null): called as the pointer or keyboard moves; mark: a point to mark for good (a chosen scan).
 */
export function renderTimeChart(el, opts) {
  const { times, kind, series, ticks, tick, value, tip, label, onHover = () => {}, mark = null } = opts;
  el.innerHTML = "";
  const W = el.clientWidth, H = el.clientHeight, n = times.length;
  const body = getComputedStyle(document.body).fontFamily;
  const axisFont = `400 11.5px ${body}`, labelFont = `700 12.5px ${body}`;
  const totals = times.map((_, i) => (kind === "stack"
    ? series.reduce((t, s) => t + (s.values[i] || 0), 0) : Math.max(...series.map((s) => s.values[i] ?? 0))));
  const ys = ticks(Math.max(0, ...totals));
  const top = ys[ys.length - 1] || 1;
  const last = [...Array(n).keys()].reverse().find((i) => series.some((s) => s.values[i] != null));
  const endText = last != null && value ? value(last) : null;
  const left = Math.ceil(Math.max(...ys.map((v) => measure(tick(v), axisFont)))) + 10;
  const right = kind === "line" && endText ? Math.ceil(measure(endText, labelFont)) + 14 : 10;
  const T = kind === "stack" && endText ? 22 : 10, B = 26;
  const pw = Math.max(10, W - left - right), ph = Math.max(10, H - T - B);
  const t0 = +times[0], span = +times[n - 1] - t0;
  const pad = Math.min(40, pw / (2 * Math.max(1, n)));
  const x = (i) => (n === 1 || !span ? left + pw / 2 : left + pad + ((+times[i] - t0) / span) * (pw - 2 * pad));
  const y = (v) => T + ph - (v / top) * ph;
  const xs = times.map((_, i) => x(i));
  const gap = n > 1 ? Math.min(...xs.slice(1).map((v, i) => v - xs[i])) : pw;
  const colW = Math.max(3, Math.min(24, gap * 0.6));

  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);
  svg.setAttribute("class", "tc-svg");
  svg.setAttribute("role", "img");
  svg.setAttribute("tabindex", "0");
  svg.setAttribute("aria-label", label);
  const parts = [];
  // Hover band and crosshair sit under the marks.
  parts.push(`<rect class="tc-band" x="0" y="${T}" width="0" height="${ph}" hidden/>`);
  if (mark != null && mark >= 0 && mark < n) parts.push(`<line class="tc-mark" x1="${xs[mark]}" x2="${xs[mark]}" y1="${T}" y2="${T + ph}"/>`);
  for (const v of ys) {
    parts.push(`<line class="tc-grid" x1="${left}" x2="${left + pw}" y1="${y(v)}" y2="${y(v)}"/>`,
      `<text class="tc-axis" x="${left - 8}" y="${y(v) + 4}" text-anchor="end">${esc(tick(v))}</text>`);
  }
  // Date labels: every scan when they fit, else evenly spaced scans.
  const yearly = span > 300 * 86400000;
  const fmt = (d) => d.toLocaleDateString(undefined, yearly ? { month: "short", year: "numeric" } : { month: "short", day: "numeric" });
  const widest = Math.max(...times.map((d) => measure(fmt(d), axisFont))) + 12;
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(pw / widest))));
  for (let i = n - 1; i >= 0; i -= every) {
    parts.push(`<text class="tc-axis" x="${xs[i]}" y="${T + ph + 18}" text-anchor="middle">${esc(fmt(times[i]))}</text>`);
  }
  if (kind === "stack") {
    for (let i = 0; i < n; i++) {
      if (!totals[i]) continue;
      const h = (totals[i] / top) * ph, x0 = xs[i] - colW / 2, base = T + ph, r = Math.min(4, colW / 2, h);
      parts.push(`<clipPath id="tc-c${el.id}${i}"><path d="M${x0},${base}V${base - h + r}Q${x0},${base - h} ${x0 + r},${base - h}H${x0 + colW - r}Q${x0 + colW},${base - h} ${x0 + colW},${base - h + r}V${base}Z"/></clipPath>`);
      let acc = 0;
      const segs = [];
      series.forEach((s) => {
        const v = s.values[i] || 0;
        if (!v) return;
        const y1 = y(acc + v), y0 = y(acc);
        acc += v;
        // Each segment gives up 1px at each side it shares: a 2px surface gap between neighbors.
        const above = acc < totals[i] ? 1 : 0, below = acc - v > 0 ? 1 : 0;
        const hh = Math.max(0, y0 - y1 - above - below);
        if (hh > 0) segs.push(`<rect x="${x0}" y="${y1 + above}" width="${colW}" height="${hh}" style="fill:var(${s.token})"/>`);
      });
      parts.push(`<g clip-path="url(#tc-c${el.id}${i})">${segs.join("")}</g>`);
    }
    if (endText) parts.push(`<text class="tc-label" x="${xs[last]}" y="${y(totals[last]) - 7}" text-anchor="middle">${esc(endText)}</text>`);
  } else {
    for (const s of series) {
      let d = "";
      s.values.forEach((v, i) => {
        if (v == null) return;
        d += `${i && s.values[i - 1] != null ? "L" : "M"}${xs[i]},${y(v)}`;
      });
      parts.push(`<path class="tc-line" d="${d}" style="stroke:var(${s.token})"/>`);
      s.values.forEach((v, i) => {
        if (v != null) parts.push(`<circle class="tc-dot" cx="${xs[i]}" cy="${y(v)}" r="5" style="fill:var(${s.token})"/>`);
      });
    }
    if (endText) {
      const v = series[0].values[last];
      parts.push(`<text class="tc-label" x="${xs[last] + 10}" y="${y(v) + 4}">${esc(endText)}</text>`);
    }
    parts.push(`<line class="tc-cross" x1="0" x2="0" y1="${T}" y2="${T + ph}" hidden/>`);
  }
  parts.push(`<line class="tc-base" x1="${left}" x2="${left + pw}" y1="${T + ph}" y2="${T + ph}"/>`);
  // Hit targets: each scan's whole slot, from halfway to the scan before to halfway to the one after.
  const edges = xs.map((v, i) => [i ? (xs[i - 1] + v) / 2 : left, i < n - 1 ? (v + xs[i + 1]) / 2 : left + pw]);
  edges.forEach(([a, b], i) => parts.push(`<rect class="tc-hit" data-i="${i}" x="${a}" y="${T}" width="${Math.max(1, b - a)}" height="${ph}"/>`));
  svg.innerHTML = parts.join("");
  const tipEl = document.createElement("div");
  tipEl.className = "tm-tip tc-tip";
  tipEl.hidden = true;
  tipEl.setAttribute("role", "tooltip");
  el.append(svg, tipEl);

  const band = svg.querySelector(".tc-band"), cross = svg.querySelector(".tc-cross");
  const highlight = (i) => {
    const on = i != null;
    band.toggleAttribute("hidden", !on || kind !== "stack");
    cross?.toggleAttribute("hidden", !on);
    if (!on) return;
    if (kind === "stack") {
      band.setAttribute("x", xs[i] - colW / 2 - 6);
      band.setAttribute("width", colW + 12);
    } else {
      cross.setAttribute("x1", xs[i]);
      cross.setAttribute("x2", xs[i]);
    }
  };
  const show = (i, px, py) => {
    tipEl.innerHTML = tip(i);
    tipEl.hidden = false;
    const tw = tipEl.offsetWidth, th = tipEl.offsetHeight;
    let lx = px + 14, ly = py + 14;
    if (lx + tw > W) lx = Math.max(0, px - tw - 14);
    if (ly + th > H) ly = Math.max(0, H - th);
    tipEl.style.left = `${lx}px`;
    tipEl.style.top = `${ly}px`;
  };
  let current = null;
  const move = (i, px, py) => { current = i; onHover(i); show(i, px, py); };
  const leave = () => { current = null; tipEl.hidden = true; onHover(null); };
  const point = (e) => {
    const hit = e.target.closest(".tc-hit");
    if (!hit) return leave();
    const box = el.getBoundingClientRect();
    move(Number(hit.dataset.i), e.clientX - box.left, e.clientY - box.top);
  };
  svg.addEventListener("pointermove", point);
  svg.addEventListener("pointerdown", point); // a tap on a touch screen
  svg.addEventListener("pointerleave", leave);
  svg.addEventListener("blur", leave);
  svg.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const i = e.key === "Home" ? 0 : e.key === "End" ? n - 1
      : Math.max(0, Math.min(n - 1, (current ?? (e.key === "ArrowLeft" ? n : -1)) + (e.key === "ArrowLeft" ? -1 : 1)));
    move(i, xs[i], T + 8);
  });
  return { highlight };
}

/** Hovering any of `charts` highlights the same scan in all of them. */
export function link(charts) {
  return (i) => charts.forEach((c) => c && c.highlight(i));
}
