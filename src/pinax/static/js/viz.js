// SPDX-License-Identifier: Apache-2.0
// Pinax data-visualization vocabulary: age buckets and their color ramp, share bins, metrics, and the
// small charts shared by every view (age bars, histograms, legends). Colors are CSS custom properties
// (see css/pinax.css), so both themes stay validated; nothing here hard-codes a color.
import { ageText, bytes, html, num, pct, state } from "/js/core.js";

export const buckets = () => state.config.age_buckets; // [{label, short, max_days}]
export const categories = () => state.config.categories; // [{key, label, description}]
export const catLabel = (key) => (categories().find((c) => c.key === key) || {}).label || key;
// A place's bytes by category (stats.cat_bytes is keyed by category), the catalog's categories first, then any its
// scan was built with that the catalog no longer has.
export const catBytes = (stats) => {
  const known = categories().map((c) => ({ key: c.key, label: c.label, bytes: stats.cat_bytes[c.key] || 0 }));
  const extra = Object.keys(stats.cat_bytes).filter((k) => !categories().some((c) => c.key === k))
    .map((k) => ({ key: k, label: k, bytes: stats.cat_bytes[k] }));
  return [...known, ...extra];
};

export const ageColor = (i) => `--age-${Math.max(0, Math.min(6, i))}`;
export function bucketOf(days) {
  if (days == null || !isFinite(days)) return null;
  const b = buckets();
  for (let i = 0; i < b.length; i++) if (b[i].max_days == null || days < b[i].max_days) return i;
  return b.length - 1;
}
export const daysSince = (iso, asOf) => (iso && asOf ? Math.max(0, (new Date(asOf) - new Date(iso)) / 86400000) : null);

// Share bins for "what fraction of this folder is X": the cold-share view uses the age ramp,
// the category view the teal share ramp.
export const COLD_SHARE_BINS = [0.05, 0.2, 0.4, 0.6, 0.8, 0.95];
export const CAT_SHARE_BINS = [0.1, 0.3, 0.6, 0.9];
export function binOf(share, edges) {
  for (let i = 0; i < edges.length; i++) if (share < edges[i]) return i;
  return edges.length;
}
const shareLabels = (edges) => edges.map((e, i) => (i === 0 ? `Under ${pct(e * 100)}` : `${pct(edges[i - 1] * 100)} to ${pct(e * 100)}`))
  .concat(`${pct(edges[edges.length - 1] * 100)} or more`);

// ---------- Metrics (tile size) ----------
export function metricOptions() {
  const cats = categories().filter((c) => c.key !== "other");
  const b = buckets();
  return [
    { key: "bytes", label: "Size" },
    { key: "alloc", label: "Space on disk" },
    { key: "files", label: "Number of files" },
    ...b.slice(1).map((_, i) => ({ key: `older:${i + 1}`, label: `Size unchanged for ${thresholdText(i + 1)}+`, group: "age" })),
    ...cats.map((c) => ({ key: `cat:${c.key}`, label: `Size of ${c.label.toLowerCase()}`, group: "type" })),
  ];
}
export function metricValue(stats, metric) {
  if (!stats) return 0;
  if (metric === "bytes" || metric === "alloc" || metric === "files") return stats[metric] || 0;
  const [kind, arg] = metric.split(":");
  if (kind === "cat") return stats.cat_bytes[arg] || 0;
  if (kind === "older") return stats.age_bytes.slice(Number(arg)).reduce((t, v) => t + v, 0);
  return 0;
}
export const metricText = (v, metric) => (metric === "files" ? `${num(v)} files` : bytes(v));
export const metricLabel = (metric) => (metricOptions().find((m) => m.key === metric) || { label: metric }).label;

// ---------- Color modes (tile color) ----------
export function colorOptions() {
  return [
    { key: "mean", label: "Average age (by size)" },
    { key: "newest", label: "Time since last change" },
    { key: "oldest", label: "Age of oldest file" },
    { key: "access", label: "Time since last access" },
    { key: "cold", label: "Share that is cold" },
    ...categories().filter((c) => c.key !== "other").map((c) => ({ key: `share:${c.key}`, label: `Share of ${c.label.toLowerCase()}` })),
  ];
}

/** Color token and a short description for a tile's stats under a color mode. */
export function colorFor(stats, mode, asOf, coldBucket) {
  if (!stats || !stats.files) return { token: "--tile-none", text: "No files" };
  if (mode === "mean" || mode === "newest" || mode === "oldest" || mode === "access") {
    const days = mode === "mean" ? stats.mean_age_days
      : daysSince(mode === "newest" ? stats.newest : mode === "oldest" ? stats.oldest : stats.last_access, asOf);
    const b = bucketOf(days);
    return b == null ? { token: "--tile-none", text: "Unknown" } : { token: ageColor(b), text: ageText(days) };
  }
  if (mode === "cold") {
    const share = stats.bytes ? stats.age_bytes.slice(coldBucket).reduce((t, v) => t + v, 0) / stats.bytes : 0;
    return { token: ageColor(binOf(share, COLD_SHARE_BINS)), text: `${pct(share * 100)} cold` };
  }
  if (mode.startsWith("share:")) {
    const k = mode.slice(6);
    const share = stats.bytes ? (stats.cat_bytes[k] || 0) / stats.bytes : 0;
    if (!share) return { token: "--tile-none", text: `No ${catLabel(k).toLowerCase()}` };
    return { token: `--share-${binOf(share, CAT_SHARE_BINS)}`, text: `${pct(share * 100)} ${catLabel(k).toLowerCase()}` };
  }
  return { token: "--tile-none", text: "" };
}

export function legend(mode, coldBucket) {
  const b = buckets();
  let title, items;
  if (mode === "cold") {
    title = `Share unchanged for ${thresholdText(coldBucket)}+`;
    items = shareLabels(COLD_SHARE_BINS).map((l, i) => [ageColor(i), l]);
  } else if (mode.startsWith("share:")) {
    title = `Share of ${catLabel(mode.slice(6)).toLowerCase()}`;
    items = [["--tile-none", "None"], ...shareLabels(CAT_SHARE_BINS).map((l, i) => [`--share-${i}`, l])];
  } else {
    title = { mean: "Average age", newest: "Last change", oldest: "Oldest file", access: "Last access" }[mode];
    items = b.map((x, i) => [ageColor(i), x.label]);
    items.push(["--tile-none", "No files"]);
  }
  return html`<div class="ramp-legend" aria-label="Color legend"><span class="lt">${title}</span>
    ${items.map(([t, l]) => html`<span><i style="background:var(${t})"></i>${l}</span>`)}</div>`;
}

// ---------- Small charts ----------
/** One stacked bar of bytes by age bucket (youngest left), with an accessible summary. */
export function ageBar(ageBytes, { large = false } = {}) {
  const total = (ageBytes || []).reduce((t, v) => t + v, 0);
  if (!total) return html`<div class="agebar empty ${large ? "lg" : ""}" role="img" aria-label="No files"></div>`;
  const b = buckets();
  const parts = ageBytes.map((v, i) => [v, i]).filter(([v]) => v > 0);
  const summary = parts.map(([v, i]) => `${b[i].label}: ${pct((v / total) * 100)}`).join(", ");
  return html`<div class="agebar ${large ? "lg" : ""}" role="img" aria-label="${summary}" title="${summary}">${parts.map(([v, i]) =>
    html`<span style="flex:${v / total} 1 0;background:var(${ageColor(i)})"></span>`)}</div>`;
}

/** Horizontal bars, one per age bucket, colored by the age ramp (an ordinal series). */
export function ageHistogram(values, { unit = "bytes", total } = {}) {
  const b = buckets();
  const max = Math.max(1, ...values);
  const sum = total ?? values.reduce((t, v) => t + v, 0);
  return html`<div class="bars">${values.map((v, i) => html`<div class="bar-row compact">
      <span class="lab" title="${b[i].label}">${b[i].label}</span>
      <div class="track"><div class="fill age" style="--c:var(${ageColor(i)});width:${(v / max) * 100}%"></div></div>
      <span class="val">${unit === "files" ? num(v) : bytes(v)}${sum ? html` <span class="tiny">${pct((v / sum) * 100)}</span>` : ""}</span></div>`)}
    </div>`;
}

/** Plain single-series bars (one hue; the label names each bar). */
export function bars(rows, { value = (r) => r.bytes, text = (r) => bytes(value(r)), label = (r) => r.label, href } = {}) {
  const max = Math.max(1, ...rows.map(value));
  return html`<div class="bars">${rows.map((r) => html`<div class="bar-row compact wide">
      <span class="lab" title="${label(r)}">${href ? html`<a href="${href(r)}">${label(r)}</a>` : label(r)}</span>
      <div class="track"><div class="fill" style="width:${(value(r) / max) * 100}%"></div></div>
      <span class="val">${text(r)}</span></div>`)}</div>`;
}

export const coldBytes = (stats, coldBucket) => (stats?.age_bytes || []).slice(coldBucket).reduce((t, v) => t + v, 0);
/** "2 years" for the bucket that starts at two years: the age threshold a bucket index stands for. */
export function thresholdText(i) {
  const d = buckets()[i - 1]?.max_days;
  if (!d) return "any time";
  if (d < 365) return `${Math.round(d)} days`;
  const y = Math.round(d / 365.25);
  return `${y} year${y === 1 ? "" : "s"}`;
}
