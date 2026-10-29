// SPDX-License-Identifier: Apache-2.0
// Builds the Pinax website from the guides in docs/ (Markdown, the same files GitHub shows) and a home page.
//
//   node docs/site/build.mjs [FOLDER]      build into FOLDER (default: site/ in the repository)
//
// The documentation is the Markdown in docs/: each page of the site is a whole guide, or some of a guide's sections
// (DOCS below), so the guides and the site never say different things. Links between guides become links to the
// page that holds their anchor; links to other files in the repository go to GitHub. A picture written as
// ![alt](images/<scene>-light.webp "caption") becomes a figure with both themes' screenshot (docs/capture writes
// them), and the page shows the one matching its theme. The build then checks every local link, anchor, and image,
// and fails if one is broken. No dependencies beyond Node 22.

import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, posix, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, '../..');
const docs = join(repo, 'docs');
const out = resolve(process.argv[2] ?? join(repo, 'site'));
const GITHUB = 'https://github.com/robert-mcdermott/pinax';
const VERSION = /__version__ = "([^"]+)"/.exec(readFileSync(join(repo, 'src/pinax/__init__.py'), 'utf8'))[1];
const SHOTS = JSON.parse(readFileSync(join(docs, 'images/shots.json'), 'utf8'));

// The documentation, in reading order. Each page is a guide, or the sections of one (with `intro`, also the text
// before its first section). A page of one section takes that section's heading as its own.
const DOCS = [
  { group: 'Start', pages: [
    { slug: 'index', title: 'Documentation', file: 'README.md', lede: 'What Pinax is for, and where everything is.' },
    { slug: 'concepts', title: 'How Pinax sees storage', file: 'CONCEPTS.md', lede: 'Scans, snapshots, folder ownership, sizes, ages, change, costs, plans, tags, and the catalog: the ideas every other page relies on.' },
    { slug: 'demo', title: 'The demo', file: 'DEMO.md', lede: 'Pinax with fictional labs and six months of scans, in one command, and a 20-minute walk-through.' },
  ] },
  { group: 'User guide', pages: [
    { slug: 'my-data', title: 'Signing in and My data', file: 'USER-GUIDE.md', intro: true, sections: ['Signing in and My data'], lede: 'For PIs, lab managers, and lab members: what your labs store, as of the latest scan.' },
    { slug: 'age-map', title: 'The age map', file: 'USER-GUIDE.md', sections: ['The age map'], lede: 'A treemap of any folder, sized by what it holds and colored by how old it is.' },
    { slug: 'history', title: 'History and forecasts', file: 'USER-GUIDE.md', sections: ['History and forecasts'], lede: 'How a folder grew and aged over its scans, and where it is heading.' },
    { slug: 'changes', title: 'Change map and what changed', file: 'USER-GUIDE.md', sections: ['Change map and what changed'], lede: 'Where a folder grew and shrank between two scans, and the files behind it.' },
    { slug: 'reports', title: 'Reports', file: 'USER-GUIDE.md', sections: ['Reports'], lede: 'Ages, file types, cold folders, and owners for any folder.' },
    { slug: 'find-files', title: 'Finding files', file: 'USER-GUIDE.md', sections: ['Finding files'], lede: 'Search by name, type, size, and age, and take the results away as CSV.' },
    { slug: 'charges', title: 'Storage charges', file: 'USER-GUIDE.md', sections: ['Storage charges'], lede: 'What each of your labs pays each month, how it is worked out, and what is ahead.' },
    { slug: 'cleanup-plans', title: 'Cleanup plans', file: 'USER-GUIDE.md', sections: ['Cleanup plans'], lede: 'What a lab means to delete, move, or compress, what it would save, and what later scans show was done.' },
    { slug: 'tags', title: 'Tags and collections', file: 'USER-GUIDE.md', sections: ['Tags and collections'], lede: 'Folders described in terms a lab and IT share, and data found by them.' },
    { slug: 'saved-views', title: 'Saved views and earlier scans', file: 'USER-GUIDE.md', sections: ['Saved views and earlier scans'], lede: 'Pages kept under a name and shared with a lab, and every report on an earlier scan.' },
  ] },
  { group: 'Administration', pages: [
    { slug: 'administration', title: 'Administering Pinax', file: 'ADMINISTRATION.md', intro: true, sections: ['All data'], lede: 'For storage administrators: the pages only they see, and a first week with them.' },
    { slug: 'folders-and-labs', title: 'Folders and labs', file: 'ADMINISTRATION.md', sections: ['Folders and labs'], lede: 'Every top-level folder, who owns it, and how to change that.' },
    { slug: 'people', title: 'People and access', file: 'ADMINISTRATION.md', sections: ['People and access'], lede: 'Who can sign in, what each person sees, and who administers Pinax.' },
    { slug: 'scans', title: 'Scans and storage health', file: 'ADMINISTRATION.md', sections: ['Scans', 'Storage health'], lede: 'Every snapshot and its state, automatic ingest, and what the scans say is wrong with the storage.' },
    { slug: 'capacity', title: 'Capacity', file: 'ADMINISTRATION.md', sections: ['Capacity'], lede: 'How much each filesystem holds, how fast it fills, and when.' },
    { slug: 'costs', title: 'Costs and chargeback', file: 'ADMINISTRATION.md', sections: ['Costs', 'Chargeback'], lede: 'Prices and free tiers from a day on, and every lab\'s charges with a statement for finance.' },
    { slug: 'tags-admin', title: 'Tags, plans, and the audit log', file: 'ADMINISTRATION.md', sections: ['Tags', "Every lab's cleanup plans", 'The audit log'], lede: 'The standard tags, every lab\'s cleanup plans, and the record of every change.' },
    { slug: 'import', title: 'Importing labs and people', file: 'IMPORT.md', lede: 'Labs, people, memberships, and folder ownership from CSV files, with a dry run first.' },
  ] },
  { group: 'Installation and operations', pages: [
    { slug: 'setup', title: 'Trying Pinax on your own data', file: 'SETUP.md', lede: 'One machine, one filesystem, a first scan, and your first people, in a few minutes.' },
    { slug: 'install', title: 'Installing on a server', file: 'DEPLOYMENT.md', intro: true, sections: ['Shape of a deployment', 'What you need', 'Install'], lede: 'Services, single sign-on or passwords, and a check that it all works.' },
    { slug: 'scanning', title: 'Scans and ingest', file: 'DEPLOYMENT.md', sections: ['Scans', "Checking a filesystem's numbers"], lede: 'Scanning on a schedule, automatic ingest, what to keep, and checking the numbers.' },
    { slug: 'database', title: 'The catalog and backups', file: 'DEPLOYMENT.md', sections: ['Catalog database', 'Backups'], lede: 'SQLite or PostgreSQL, moving between them, and backing up what cannot be made again.' },
    { slug: 'upgrading', title: 'Upgrading', file: 'DEPLOYMENT.md', sections: ['Upgrading'], lede: 'A new release, its migrations, and rebuilding indexes.' },
    { slug: 'sizing', title: 'Sizing', file: 'DEPLOYMENT.md', sections: ['Sizing'], lede: 'The server, disk, and memory a filesystem of a billion files needs.' },
    { slug: 'statements', title: 'Charges and statements', file: 'DEPLOYMENT.md', sections: ['Charges and statements'], lede: 'How charges are worked out, and when a past month can change.' },
    { slug: 'operations', title: 'Logs, metrics, and operations', file: 'DEPLOYMENT.md', sections: ['Monitoring and operations'], lede: 'Health, logs and the audit trail, Prometheus metrics and alerts, and day-to-day tasks.' },
    { slug: 'configuration', title: 'Configuration', file: 'CONFIGURATION.md', lede: 'Every setting, with its default.' },
  ] },
  { group: 'Reference', pages: [
    { slug: 'cli', title: 'Command line', file: 'CLI.md', lede: 'Every pinax command and option.' },
    { slug: 'troubleshooting', title: 'Troubleshooting', file: 'TROUBLESHOOTING.md', lede: 'What a problem looks like, why it happens, and what to do.' },
    { slug: 'architecture', title: 'Architecture', file: 'ARCHITECTURE.md', lede: 'How Pinax is built: the snapshot index, checking it, querying it, the catalog, security, and measured performance.' },
    { slug: 'development', title: 'Development', file: 'DEVELOPMENT.md', lede: 'Working on Pinax: setup, conventions, tests, migrations, the HTTP API, and benchmarks.' },
  ] },
];
const PAGES = DOCS.flatMap((g) => g.pages);

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// --- Markdown ---------------------------------------------------------------------------------------------------
// The subset the guides use: headings, paragraphs, lists (nested, with code inside), fenced code, tables, block
// quotes, and inline code, emphasis, links, images, and autolinks.

// GitHub's anchor for a heading, with -1, -2... for repeats in one file.
function slugger() {
  const seen = new Map();
  return (text) => {
    const base = text.toLowerCase().replace(/<[^>]+>/g, '').replace(/[^\p{L}\p{N}\s_-]/gu, '').trim().replace(/\s/g, '-');
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return n ? `${base}-${n}` : base;
  };
}
const plain = (md) => md.replace(/`([^`]+)`/g, '$1').replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*]+)\*/g, '$1')
  .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1');

// Inline Markdown; `link` turns a link's target into the site's. A link's text shares its page's code spans.
function inline(text, link, codes = []) {
  let s = text.replace(/(`+)([\s\S]*?[^`])\1(?!`)/g, (_, _ticks, code) => `\u0000${codes.push(esc(code.trim())) - 1}\u0000`);
  const keep = [];
  const stash = (html) => `\u0001${keep.push(html) - 1}\u0001`;
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g, (_, alt, src) => stash(`<img src="${esc(link(src))}" alt="${esc(alt)}" />`));
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+"([^"]*)")?\)/g, (_, label, href) => stash(`<a href="${esc(link(href))}">${inline(label, link, codes)}</a>`));
  s = s.replace(/<(https?:\/\/[^>\s]+)>/g, (_, url) => stash(`<a href="${esc(url)}">${esc(url)}</a>`));
  s = esc(s.replace(/\\([\\`*_[\]|<>#])/g, (_, c) => stash(esc(c))));
  s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>').replace(/(^|[^\w*])\*(?=\S)([\s\S]*?\S)\*(?!\w)/g, '$1<em>$2</em>');
  s = s.replace(/\u0001(\d+)\u0001/g, (_, i) => keep[i]).replace(/\u0001(\d+)\u0001/g, (_, i) => keep[i]);
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
}

const cells = (row) => row.trim().replace(/^\|/, '').replace(/(?<!\\)\|$/, '').split(/(?<!\\)\|/).map((c) => c.trim());
const indentOf = (line) => /^ */.exec(line)[0].length;
const LIST = /^( *)([-*]|\d+\.) +(.*)$/;

// Blocks of Markdown to HTML. `ctx` has link (targets), slug (anchors), shot (figures), and headings (collected).
function blocks(lines, ctx) {
  const html = [];
  let i = 0;
  const para = [];
  const flush = () => {
    if (!para.length) return;
    const text = para.join(' ').trim();
    para.length = 0;
    const figure = /^!\[([^\]]*)\]\(images\/([\w-]+)-light\.webp(?:\s+"([^"]*)")?\)$/.exec(text);
    html.push(figure ? ctx.shot(figure[2], figure[1], figure[3] ? inline(figure[3], ctx.link) : '') : `<p>${inline(text, ctx.link)}</p>`);
  };
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { flush(); i += 1; continue; }
    const fence = /^ *(```+|~~~+)\s*([\w+-]*)/.exec(line);
    if (fence) {
      flush();
      const pad = indentOf(line);
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].trimStart().startsWith(fence[1])) body.push(lines[i].slice(Math.min(pad, indentOf(lines[i])))), i += 1;
      i += 1;
      html.push(`<div class="code"><pre><code${fence[2] ? ` class="language-${fence[2]}"` : ''}>${esc(body.join('\n'))}</code></pre></div>`);
      continue;
    }
    const heading = /^(#{1,6})\s+(.*?)\s*#*$/.exec(line);
    if (heading) {
      flush();
      const level = heading[1].length;
      const id = ctx.slug(plain(heading[2]));
      ctx.headings.push({ level, id, text: heading[2] });
      html.push(`<h${level} id="${id}">${inline(heading[2], ctx.link)}</h${level}>`);
      i += 1;
      continue;
    }
    if (line.trimStart().startsWith('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1])) {
      flush();
      const head = cells(line);
      const align = cells(lines[i + 1]).map((c) => (c.endsWith(':') ? (c.startsWith(':') ? 'center' : 'right') : ''));
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].trimStart().startsWith('|')) rows.push(cells(lines[i])), i += 1;
      const cell = (tag, c, j) => `<${tag}${align[j] ? ` style="text-align:${align[j]}"` : ''}>${inline(c, ctx.link)}</${tag}>`;
      html.push(`<div class="table-wrap"><table><thead><tr>${head.map((c, j) => cell('th', c, j)).join('')}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, j) => cell('td', c, j)).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    if (/^ *>/.test(line)) {
      flush();
      const quoted = [];
      while (i < lines.length && /^ *>/.test(lines[i])) quoted.push(lines[i].replace(/^ *> ?/, '')), i += 1;
      html.push(`<blockquote>${blocks(quoted, ctx)}</blockquote>`);
      continue;
    }
    const item = LIST.exec(line);
    if (item && (!para.length || (item[1].length === 0 && /^([-*]|1\.)$/.test(item[2])))) {
      flush();
      const base = item[1].length;
      const ordered = /\d/.test(item[2]);
      const items = [];
      let loose = false;
      while (i < lines.length) {
        let j = i;  // items may be apart, a blank line between them (a loose list)
        while (j < lines.length && !lines[j].trim()) j += 1;
        const m = LIST.exec(lines[j] ?? '');
        if (!m || m[1].length !== base || /\d/.test(m[2]) !== ordered) break;
        if (j > i) loose = true;
        i = j;
        const contentIndent = base + m[2].length + 1;
        const body = [m[3]];
        i += 1;
        while (i < lines.length) {
          const next = lines[i];
          if (!next.trim()) {
            const after = lines.slice(i + 1).find((l) => l.trim());
            if (after === undefined || indentOf(after) < contentIndent) break;
            body.push('');
            i += 1;
            continue;
          }
          if (indentOf(next) < contentIndent && (LIST.test(next) || !body.at(-1)?.trim() || indentOf(next) <= base)) break;
          body.push(next.slice(Math.min(contentIndent, indentOf(next))));
          i += 1;
        }
        items.push(body);
      }
      const start = ordered ? Number(/^\s*(\d+)/.exec(line)[1]) : 1;
      const rendered = items.map((body) => {
        const inner = blocks(body, ctx);
        const tight = !loose && !body.includes('') && /^<p>[\s\S]*?<\/p>(?=<|$)/.test(inner);
        return `<li>${tight ? inner.replace(/^<p>([\s\S]*?)<\/p>/, '$1') : inner}</li>`;
      });
      html.push(ordered ? `<ol${start !== 1 ? ` start="${start}"` : ''}>${rendered.join('')}</ol>` : `<ul>${rendered.join('')}</ul>`);
      continue;
    }
    if (/^ *(-{3,}|\*{3,})\s*$/.test(line) && !para.length) { flush(); html.push('<hr />'); i += 1; continue; }
    para.push(line.trim());
    i += 1;
  }
  flush();
  return html.join('\n');
}

// --- The guides, split into pages -------------------------------------------------------------------------------

// A guide's parts: its title, the text before its first section, and each section (## heading) with every anchor
// in it, as GitHub makes them for the whole file.
function readGuide(file) {
  const text = readFileSync(join(docs, file), 'utf8');
  const lines = text.split('\n');
  const slug = slugger();
  const parts = { title: '', intro: [], sections: [] };
  let current = null;
  let fenced = false;
  for (const line of lines) {
    if (/^ *(```|~~~)/.test(line)) fenced = !fenced;
    const h = fenced ? null : /^(#{1,6})\s+(.*?)\s*#*$/.exec(line);
    if (h && h[1].length === 1 && !parts.title) { parts.title = h[2]; slug(plain(h[2])); continue; }
    if (h && h[1].length === 2) {
      current = { title: plain(h[2]), lines: [line], anchors: [slug(plain(h[2]))] };
      parts.sections.push(current);
      continue;
    }
    if (h) {
      const anchor = slug(plain(h[2]));
      if (current) current.anchors.push(anchor);
      else (parts.introAnchors ??= []).push(anchor);
    }
    (current ? current.lines : parts.intro).push(line);
  }
  return parts;
}

const guides = new Map();
const guide = (file) => {
  if (!guides.has(file)) guides.set(file, readGuide(file));
  return guides.get(file);
};

// Where each guide's anchors are on the site: `file#anchor` → page slug, and `file` → its first page.
const where = new Map();
for (const page of PAGES) {
  const g = guide(page.file);
  const sections = page.sections ? page.sections.map((t) => {
    const s = g.sections.find((x) => x.title === t);
    if (!s) throw new Error(`${page.file} has no section "${t}" (for ${page.slug})`);
    return s;
  }) : g.sections;
  page.parts = { intro: page.intro || !page.sections ? g.intro : [], sections };
  if (!where.has(page.file) && (page.intro || !page.sections)) where.set(page.file, page.slug);
  for (const s of sections) for (const a of s.anchors) where.set(`${page.file}#${a}`, page.slug);
  for (const a of (page.intro || !page.sections) ? g.introAnchors ?? [] : []) where.set(`${page.file}#${a}`, page.slug);
}
for (const page of PAGES) if (!where.has(page.file)) where.set(page.file, page.slug);
for (const [file, g] of guides) {
  const unplaced = g.sections.filter((s) => !where.has(`${file}#${s.anchors[0]}`)).map((s) => s.title);
  if (unplaced.length) throw new Error(`${file}: sections on no page: ${unplaced.join(', ')} (add them to DOCS in build.mjs)`);
}

// A link in a guide, as the site has it: a page and anchor for another guide, GitHub for other files.
function linker(file, pageSlug) {
  return (href) => {
    if (/^(https?:|mailto:)/.test(href)) return href;
    const [path, anchor] = href.split('#');
    if (!path) {
      const target = where.get(`${file}#${anchor}`);
      return target && target !== pageSlug ? `${target}.html#${anchor}` : `#${anchor}`;
    }
    const resolved = posix.normalize(posix.join('docs', posix.dirname(file), path));
    if (resolved.startsWith('docs/images/')) return `../assets/img/${posix.basename(resolved)}`;
    const docFile = resolved.startsWith('docs/') ? resolved.slice(5) : null;
    if (docFile && where.has(docFile)) {
      const target = anchor ? where.get(`${docFile}#${anchor}`) : where.get(docFile);
      if (!target) return `#missing-${docFile}-${anchor}`;  // reported by the link check
      return `${target === pageSlug ? '' : `${target === 'index' ? './' : `${target}.html`}`}${anchor ? `#${anchor}` : ''}` || './';
    }
    const kind = existsSync(join(repo, resolved)) && statSync(join(repo, resolved)).isDirectory() ? 'tree' : 'blob';
    return `${GITHUB}/${kind}/main/${resolved}${anchor ? `#${anchor}` : ''}`;
  };
}

// A screenshot in both themes.
const shotsUsed = new Set();
function shot(root) {
  return (name, alt, caption, { window = false, eager = false } = {}) => {
    shotsUsed.add(name);
    const size = SHOTS[name] ?? { width: 2000, height: 1250 };
    const loading = eager ? 'fetchpriority="high"' : 'loading="lazy"';
    const img = (theme) => `<img class="shot-${theme}" src="${root}assets/img/${name}-${theme}.webp" width="${size.width}" height="${size.height}" ${loading} alt="${esc(alt)}" />`;
    // A crop of one part of a page is shown at its own size (the capture's 1.25 device pixels per CSS pixel).
    const crop = size.width < 1500 ? ` shot-crop" style="max-width:${Math.round(size.width / 1.25)}px` : '';
    return `<figure${window ? ' class="hero-shot"' : ''}><div class="shot${window ? ' shot-window' : ''}${crop}">${img('light')}${img('dark')}</div>${caption ? `<figcaption>${caption}</figcaption>` : ''}</figure>`;
  };
}

// --- Layout -----------------------------------------------------------------------------------------------------

const ICON = {
  github: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 .5a11.5 11.5 0 0 0-3.64 22.41c.58.1.79-.25.79-.56v-2c-3.2.7-3.88-1.37-3.88-1.37-.53-1.34-1.29-1.7-1.29-1.7-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.77 2.71 1.26 3.37.96.1-.75.4-1.26.73-1.55-2.56-.29-5.25-1.28-5.25-5.69 0-1.26.45-2.29 1.19-3.1-.12-.29-.52-1.46.11-3.05 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.77 0c2.2-1.49 3.17-1.18 3.17-1.18.63 1.59.23 2.76.11 3.05.74.81 1.19 1.84 1.19 3.1 0 4.42-2.7 5.4-5.26 5.68.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.56A11.5 11.5 0 0 0 12 .5Z"/></svg>',
  moon: '<svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/></svg>',
  sun: '<svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  menu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>',
};

function head({ title, description }, root) {
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${esc(title === 'Pinax' ? 'Pinax: explore your data' : `${title} · Pinax`)}</title>
  <meta name="description" content="${esc(description)}" />
  <meta property="og:title" content="${esc(title)}" />
  <meta property="og:description" content="${esc(description)}" />
  <meta name="theme-color" content="#18365D" />
  <link rel="icon" href="${root}assets/img/favicon.svg" type="image/svg+xml" />
  <link rel="stylesheet" href="${root}assets/css/site.css" />
  <script>try { const t = localStorage.getItem('pinax-site-theme'); if (t) document.documentElement.dataset.theme = t; } catch (e) {}</script>
  <script src="${root}assets/js/site.js" defer></script>
</head>
<body>
  <a class="skip-link" href="#main">Skip to content</a>
`;
}

const home = (root) => root || './';

function header(root, current) {
  const link = (href, label, key) => `<a href="${href.startsWith('#') ? home(root) : root}${href}"${current === key ? ' aria-current="page"' : ''}>${label}</a>`;
  return `
  <header class="site-header">
    <div class="container">
      <a class="brand" href="${home(root)}"><img src="${root}assets/img/pinax-mark.svg" alt="" width="34" height="34" /><span class="brand-name">Pinax<small>Explore your data</small></span></a>
      <nav class="site-nav" id="site-nav" aria-label="Main">
        ${link('#features', 'Features', 'features')}
        ${link('docs/my-data.html', 'User guide', 'user')}
        ${link('docs/administration.html', 'Administration', 'admin')}
        ${link('docs/install.html', 'Install', 'install')}
        ${link('docs/', 'Documentation', 'docs')}
      </nav>
      <div class="header-actions">
        <a class="icon-button" href="${GITHUB}" aria-label="Pinax on GitHub">${ICON.github}</a>
        <button class="icon-button theme-toggle" type="button" aria-label="Switch between light and dark">${ICON.moon}${ICON.sun}</button>
        <button class="icon-button nav-toggle" type="button" aria-label="Menu" aria-controls="site-nav" aria-expanded="false">${ICON.menu}</button>
      </div>
    </div>
  </header>
`;
}

function footer(root) {
  return `
  <footer class="site-footer">
    <div class="container">
      <div class="footer-grid">
        <div class="footer-about">
          <a class="brand" href="${home(root)}"><img src="${root}assets/img/pinax-mark.svg" alt="" width="30" height="30" /><span class="brand-name">Pinax</span></a>
          <p>File system reporting for research storage, built on fs-inventory scans. Free and open source under the Apache License 2.0.</p>
        </div>
        <div>
          <h4>Use</h4>
          <ul>
            <li><a href="${root}docs/my-data.html">User guide</a></li>
            <li><a href="${root}docs/age-map.html">The age map</a></li>
            <li><a href="${root}docs/charges.html">Storage charges</a></li>
            <li><a href="${root}docs/demo.html">The demo</a></li>
          </ul>
        </div>
        <div>
          <h4>Run</h4>
          <ul>
            <li><a href="${root}docs/install.html">Installing</a></li>
            <li><a href="${root}docs/administration.html">Administration</a></li>
            <li><a href="${root}docs/configuration.html">Configuration</a></li>
            <li><a href="${root}docs/troubleshooting.html">Troubleshooting</a></li>
          </ul>
        </div>
        <div>
          <h4>Project</h4>
          <ul>
            <li><a href="${GITHUB}">GitHub</a></li>
            <li><a href="${GITHUB}/releases">Releases</a></li>
            <li><a href="${GITHUB}/blob/main/CHANGELOG.md">Changelog</a></li>
            <li><a href="${GITHUB}/issues">Issues</a></li>
          </ul>
        </div>
      </div>
      <div class="footer-bottom">Pinax ${esc(VERSION)} · Apache License 2.0 · The screenshots are of the demo, whose labs, people, and data are fictional.</div>
    </div>
  </footer>
</body>
</html>
`;
}

function sidebar(current) {
  const groups = DOCS.map((g) => `        <h4>${g.group}</h4>
        <ul>
${g.pages.map((p) => `          <li><a href="${p.slug === 'index' ? './' : `${p.slug}.html`}"${p.slug === current ? ' aria-current="page"' : ''}>${esc(p.title)}</a></li>`).join('\n')}
        </ul>`).join('\n');
  return `
    <aside class="docs-sidebar" aria-label="Documentation">
      <button class="btn docs-menu-toggle" type="button" aria-expanded="false">Documentation menu <span aria-hidden="true">▾</span></button>
      <nav>
${groups}
      </nav>
    </aside>
`;
}

function pager(current) {
  const i = PAGES.findIndex((p) => p.slug === current);
  const href = (p) => (p.slug === 'index' ? './' : `${p.slug}.html`);
  const prev = PAGES[i - 1];
  const next = PAGES[i + 1];
  return `
      <nav class="doc-pager" aria-label="Pages">
        ${prev ? `<a class="prev" href="${href(prev)}"><span>Previous</span>${esc(prev.title)}</a>` : '<span></span>'}
        ${next ? `<a class="next" href="${href(next)}"><span>Next</span>${esc(next.title)}</a>` : ''}
      </nav>`;
}

function write(path, html) {
  const file = join(out, path);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, html);
}

// --- Build ------------------------------------------------------------------------------------------------------

rmSync(out, { recursive: true, force: true });

for (const page of PAGES) {
  const root = '../';
  const slug = slugger();
  // Anchors as GitHub makes them for the whole guide, so links from elsewhere find them on this page.
  const g = guide(page.file);
  const anchors = [...(page.parts.intro.length ? g.introAnchors ?? [] : []), ...page.parts.sections.flatMap((s) => s.anchors)];
  let next = 0;
  const ctx = { link: linker(page.file, page.slug), shot: shot(root), headings: [], slug: () => anchors[next++] ?? slug('heading') };
  let lines = [...page.parts.intro, ...page.parts.sections.flatMap((s) => s.lines)];
  // A page of one section, without the guide's introduction, takes the section's heading as its own: its h3s become
  // h2s, and its anchor is the page's top.
  let single = '';
  if (page.sections?.length === 1 && !page.intro) {
    single = page.parts.sections[0].anchors[0];
    next = 1;
    lines = lines.slice(1).map((l) => l.replace(/^###(?!#)/, '##').replace(/^####(?!#)/, '###'));
  }
  const body = blocks(lines, ctx);
  const description = page.lede;
  const section = { 'USER-GUIDE.md': 'user', 'ADMINISTRATION.md': 'admin' }[page.file] ?? (page.slug === 'install' ? 'install' : 'docs');
  write(`docs/${page.slug}.html`, head({ title: page.title, description }, root) + header(root, section) + `
  <div class="docs-layout">${sidebar(page.slug)}
    <main id="main" class="doc"${single ? ` data-anchor="${single}"` : ''}>
      ${single ? `<span id="${single}" class="anchor-target"></span>` : ''}<h1>${esc(page.title)}</h1>
      <p class="doc-lede">${esc(page.lede)}</p>
${body}
${pager(page.slug)}
    </main>

    <aside class="toc" aria-label="On this page">
      <h4>On this page</h4>
      <ul></ul>
    </aside>
  </div>
` + footer(root));
}

// The home page: pages/index.html, with {{shot name "alt" "caption"}} for screenshots and {{version}}.
{
  const text = readFileSync(join(here, 'pages/index.html'), 'utf8');
  const take = shot('');
  const body = text
    .replace(/\{\{shot\s+([\w-]+)\s+"([^"]*)"(?:\s+"([^"]*)")?((?:\s+\w+)*)\s*\}\}/g, (_, name, alt, caption, flags) =>
      take(name, alt, caption ?? '', { window: /\bwindow\b/.test(flags), eager: /\beager\b/.test(flags) }))
    .replaceAll('{{version}}', esc(VERSION)).replaceAll('{{github}}', GITHUB);
  write('index.html', head({ title: 'Pinax', description: 'See what is stored on research filesystems, who owns it, how old it is, how it is changing, and what it costs.' }, '') + header('', 'home') + `
  <main id="main">
${body.trimEnd()}
  </main>
` + footer(''));
}

// Assets.
for (const dir of ['assets/css', 'assets/js', 'assets/img']) mkdirSync(join(out, dir), { recursive: true });
copyFileSync(join(here, 'assets/site.css'), join(out, 'assets/css/site.css'));
copyFileSync(join(here, 'assets/site.js'), join(out, 'assets/js/site.js'));
copyFileSync(join(repo, 'src/pinax/static/img/favicon.svg'), join(out, 'assets/img/favicon.svg'));
copyFileSync(join(repo, 'src/pinax/static/img/pinax-mark.svg'), join(out, 'assets/img/pinax-mark.svg'));
for (const name of shotsUsed) for (const theme of ['light', 'dark']) {
  const image = join(docs, 'images', `${name}-${theme}.webp`);
  if (existsSync(image)) copyFileSync(image, join(out, 'assets/img', `${name}-${theme}.webp`));
}

// Check every local link, anchor, and image.
const problems = [];
const ids = new Map();
const idsOf = (file) => {
  if (!ids.has(file)) ids.set(file, new Set([...readFileSync(file, 'utf8').matchAll(/\sid="([^"]+)"/g)].map((m) => m[1])));
  return ids.get(file);
};
const built = [];
const walk = (dir) => {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (entry.name.endsWith('.html')) built.push(full);
  }
};
walk(out);
for (const file of built) {
  const html = readFileSync(file, 'utf8');
  for (const [, url] of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
    if (/^(https?:|mailto:|data:)/.test(url)) continue;
    const [path, anchor] = url.split('#');
    let target = path ? resolve(dirname(file), path) : file;
    if (path.endsWith('/') || (existsSync(target) && !/\.\w+$/.test(target))) target = join(target, 'index.html');
    const at = relative(out, file);
    if (!existsSync(target)) problems.push(`${at}: ${url} does not exist`);
    else if (anchor && target.endsWith('.html') && !idsOf(target).has(anchor)) problems.push(`${at}: ${url} has no #${anchor}`);
  }
}
for (const name of shotsUsed) for (const theme of ['light', 'dark']) {
  if (!existsSync(join(docs, 'images', `${name}-${theme}.webp`))) problems.push(`docs/images/${name}-${theme}.webp is missing (node docs/capture/capture.mjs ${name})`);
}
console.log(`Built ${built.length} pages into ${relative(process.cwd(), out) || '.'} (Pinax ${VERSION}, ${shotsUsed.size} screenshots).`);
if (problems.length) {
  console.error(`${problems.length} problem${problems.length === 1 ? '' : 's'}:\n  ${[...new Set(problems)].join('\n  ')}`);
  process.exitCode = 1;
}
