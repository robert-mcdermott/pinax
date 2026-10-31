// SPDX-License-Identifier: Apache-2.0
// Captures the documentation's screenshots from the demo, in the light and dark themes, as
// docs/images/<scene>-<theme>.webp. The guides show the light ones; the website (docs/site/build.mjs copies them)
// shows the one matching its theme. It seeds a demo of its own, starts Pinax on it, and opens each scene as one of
// the demo's people, so a run never touches the demo in var/ and every run shows the same story.
//
//   node docs/capture/capture.mjs [scene …] [--theme light|dark|both] [--reuse DIR]
//
// --reuse DIR seeds a demo into DIR once and uses it on later runs (quicker while working on scenes); without it,
// the demo is seeded into a temporary folder and deleted afterwards. A scene's name is the name of its pictures.
//
// Needs uv, fs-inventory (scripts/install-fs-inventory.sh), and Chrome, Chromium, Edge, or Brave (CHROME=path).

import { spawn, spawnSync } from 'node:child_process';
import { createServer } from 'node:net';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launch, sleep } from './cdp.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const IMAGES = join(ROOT, 'docs/images');
// Two free ports: the demo, and the same catalog with local sign-in (for the sign-in page).
const freePort = () => new Promise((done) => {
  const probe = createServer().listen(0, '127.0.0.1', () => { const { port } = probe.address(); probe.close(() => done(port)); });
});
const PORT = await freePort();
const LOCAL_PORT = await freePort();
const URL = `http://127.0.0.1:${PORT}`;
const LOCAL_URL = `http://127.0.0.1:${LOCAL_PORT}`;
const WIDTH = 1600;
const HEIGHT = 1000;
const SCALE = 1.25;

const args = process.argv.slice(2);
const option = (name) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : null;
};
const themes = { light: ['light'], dark: ['dark'], both: ['light', 'dark'] }[option('theme') ?? 'both'];
const reuse = option('reuse');
const wanted = args.filter((a, i) => !a.startsWith('--') && !['--theme', '--reuse'].includes(args[i - 1]));

// --- The demo, and Pinax on it --------------------------------------------------------------------------------

// Pinax itself, run directly rather than through `uv run`, whose child would outlive it when it is stopped.
const PINAX = existsSync(join(ROOT, '.venv/bin/pinax')) ? join(ROOT, '.venv/bin/pinax') : null;
if (!PINAX) throw new Error('Run `uv sync` first: the capture runs .venv/bin/pinax.');

const run = (cmd, argv, env) => new Promise((done, fail) => spawn(cmd, argv, { cwd: ROOT, env, stdio: 'inherit' })
  .on('exit', (code) => (code ? fail(new Error(`${cmd} ${argv.join(' ')} failed (${code})`)) : done())));

function demoEnv(dir, extra = {}) {
  return { ...process.env, PINAX_ENVIRONMENT: 'demo', PINAX_AUTH_MODE: 'demo', PINAX_VAR_ROOT: join(dir, 'var'),
    PINAX_DEMO_ROOT: join(dir, 'demo'), PINAX_SCAN_ROOT: join(dir, 'scans'), PINAX_DATABASE_URL: '',
    PINAX_METRICS_TOKEN: '', PINAX_LOG_LEVEL: 'WARNING', ...extra };
}

// Started servers, stopped however the run ends.
const servers = [];
const stopServers = () => servers.forEach((s) => s.kill());
process.on('exit', stopServers);

async function serve(dir, port, extra = {}) {
  const server = spawn(PINAX, ['serve', '--port', String(port)], { cwd: ROOT, env: demoEnv(dir, extra), stdio: ['ignore', 'ignore', 'inherit'] });
  servers.push(server);
  for (let i = 0; i < 150; i += 1) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/api/health`);
      if (r.ok && (await r.json()).version) return server;  // Pinax, not whatever else might answer there
    } catch { /* starting */ }
    if (server.exitCode !== null) break;
    await sleep(200);
  }
  throw new Error(`Pinax did not start on port ${port}.`);
}

// The API, as one of the demo's people.
async function api(user, path) {
  const r = await fetch(`${URL}/api/${path}`, { headers: { 'X-Pinax-User': user } });
  if (!r.ok) throw new Error(`/api/${path} as ${user}: ${r.status}`);
  return r.json();
}

// --- Page helpers ---------------------------------------------------------------------------------------------

let b;
let FAST;
const facts = {};
const js = (expression) => b.eval(expression);
const enc = encodeURIComponent;
const at = (rel) => enc(rel ? `${FAST}/${rel}` : FAST);

async function waitFor(expression, timeout = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await js(expression)) return;
    await sleep(200);
  }
  throw new Error(`Timed out waiting for ${expression}`);
}

// Opens a page of the app as `user` (null: nobody signed in), and waits until it has drawn.
async function open(user, hash, origin = URL) {
  await b.cookie(origin, 'pinax_user', user ?? '');
  await b.goto('about:blank', 100);
  await b.goto(`${origin}/app/${hash}`, 600);
  await waitFor(`(() => { const m = document.querySelector('main'); return m && !m.querySelector('.loading-block, .skeleton') && m.innerText.trim().length > 40; })()`);
  await sleep(1400); // charts, the treemap's layout, and boxes that load after the page
  const failed = await js(`document.querySelector('main').innerText.includes('Something went wrong')`);
  if (failed) throw new Error(`${hash}: the page says something went wrong`);
}

// Scrolls a heading (or any element) with this text to the top, below the app's bar.
async function scrollTo(text, selector = 'h2, h3') {
  const found = await js(`(() => {
    const el = [...document.querySelectorAll(${JSON.stringify(selector)})].find((e) => e.textContent.includes(${JSON.stringify(text)}));
    if (!el) return false;
    window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 90, behavior: 'instant' });
    return true;
  })()`);
  if (!found) throw new Error(`No ${selector} with "${text}"`);
  await sleep(600);
}

// An element's rectangle in the page, with room around it, to capture only it (all of it, even below the window).
async function clipOf(selector, pad = 14) {
  const rect = await js(`(() => { const r = document.querySelector(${JSON.stringify(selector)})?.getBoundingClientRect(); return r && { x: r.x, y: r.y + window.scrollY, width: r.width, height: r.height }; })()`);
  if (!rect) throw new Error(`No ${selector} to capture`);
  const x = Math.max(0, rect.x - pad);
  const y = Math.max(0, rect.y - pad);
  return { x, y, width: Math.min(WIDTH - x, rect.width + 2 * pad), height: rect.height + 2 * pad };
}

// Moves the mouse over the largest tile of the treemap whose label includes `name`, so its tooltip shows.
async function hoverTile(name) {
  const point = await js(`(() => {
    const tile = [...document.querySelectorAll('.tm-tile')].filter((t) => t.textContent.includes(${JSON.stringify(name)}))
      .sort((a, b) => b.getBoundingClientRect().width * b.getBoundingClientRect().height - a.getBoundingClientRect().width * a.getBoundingClientRect().height)[0];
    if (!tile) return null;
    const r = tile.getBoundingClientRect();
    return { x: r.x + r.width * 0.55, y: r.y + r.height * 0.6 };
  })()`);
  if (!point) throw new Error(`No tile ${name}`);
  await b.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: point.x, y: point.y });
  await sleep(700);
}

async function choose(selector, value) {
  await js(`(() => { const s = document.querySelector(${JSON.stringify(selector)}); s.value = ${JSON.stringify(value)}; s.dispatchEvent(new Event('change', { bubbles: true })); })()`);
  await sleep(1500);
}

// --- Scenes ---------------------------------------------------------------------------------------------------
// Each opens a page; one that returns { clip } is captured only there.

const scenes = {
  // Start
  async 'demo-sign-in'() { await open(null, '#/'); },
  async 'sign-in'() { await open(null, '#/', LOCAL_URL); },
  async home() { await open('hlindqvist', '#/'); },

  // The user guide
  async 'age-map'() { await open('afujimoto', `#/explore?fs=fast&path=${at('fujimoto_a/projects')}`); },
  async 'age-map-tooltip'() {
    await open('hlindqvist', `#/explore?fs=fast&path=${at('lindqvist_h')}`);
    await hoverTile('2019_tumor_normal');
  },
  async 'age-map-access'() { await open('hlindqvist', `#/explore?fs=fast&path=${at('lindqvist_h')}&color=access`); },
  async 'age-map-table'() { await open('afujimoto', `#/explore?fs=fast&path=${at('fujimoto_a/projects')}&view=table`); },
  async 'age-map-icicle'() { await open('afujimoto', `#/explore?fs=fast&path=${at('fujimoto_a')}&view=icicle`); },
  async 'age-map-sunburst'() { await open('afujimoto', `#/explore?fs=fast&path=${at('fujimoto_a')}&view=sunburst`); },
  async 'folder-panel'() {
    await open('afujimoto', `#/explore?fs=fast&path=${at('fujimoto_a/projects/2022_tumor_atlas')}`);
    return { clip: await clipOf('aside.detail') };
  },
  async history() { await open('afujimoto', `#/history?fs=fast&path=${at('fujimoto_a')}`); },
  async 'change-map'() { await open('pnatarajan', `#/changes?fs=fast&since=${facts.firstScan}`); },
  async 'what-changed'() { await open('pnatarajan', `#/changed?fs=fast&since=${facts.firstScan}`); },
  async 'age-report'() { await open('afujimoto', `#/ages?fs=fast&path=${at('fujimoto_a')}`); },
  async 'file-types'() { await open('hlindqvist', `#/types?fs=fast&path=${at('lindqvist_h')}`); },
  async 'cold-data'() { await open('afujimoto', `#/cold?fs=fast&path=${at('fujimoto_a')}`); },
  async owners() { await open('pnatarajan', `#/owners?fs=fast&path=${at('kowalski_d')}`); },
  async 'find-files'() { await open('hlindqvist', `#/files?fs=fast&path=${at('lindqvist_h')}&q=${enc('*.bam')}&older=5&sort=size`); },
  async searches() { await open('pnatarajan', `#/searches?open=${facts.searchId}`); },
  async charges() { await open('hlindqvist', `#/charges?month=${facts.lastMonth}`); },
  async 'charges-ahead'() {
    await open('hlindqvist', '#/charges');
    await scrollTo('The next 12 months');
  },
  async 'charges-split'() {
    await open('afujimoto', '#/charges');
    await scrollTo('Split by', 'label');
  },
  async 'cleanup-plan'() { await open('rdasgupta', '#/plans'); },
  async 'plan-suggestions'() {
    await open('rdasgupta', '#/plans');
    await scrollTo('Suggestions');
  },
  async collections() { await open('afujimoto', `#/collections?${facts.collection}`); },
  async 'saved-views'() {
    await open('afujimoto', '#/');
    await scrollTo('Saved views');
    return { clip: await clipOf('#home-views .card, #home-views') };
  },

  // Administration
  async 'all-data'() { await open('pnatarajan', '#/'); },
  async 'all-data-attention'() {
    await open('pnatarajan', '#/');
    await scrollTo('Needs attention');
  },
  async 'age-map-filesystem'() { await open('pnatarajan', '#/explore?fs=fast'); },
  async 'history-capacity'() { await open('pnatarajan', '#/history?fs=fast'); },
  async folders() { await open('pnatarajan', '#/folders?unmapped=1'); },
  async people() { await open('pnatarajan', '#/access'); },
  async person() { await open('pnatarajan', '#/access/people/jokafor'); },
  async labs() { await open('pnatarajan', '#/access?tab=labs'); },
  async lab() { await open('pnatarajan', '#/access/labs/fujimoto_a'); },
  async scans() { await open('pnatarajan', '#/scans'); },
  async health() { await open('pnatarajan', '#/health'); },
  async capacity() { await open('pnatarajan', '#/capacity'); },
  async 'tags-admin'() { await open('pnatarajan', '#/tags'); },
  async costs() { await open('pnatarajan', '#/costs'); },
  async chargeback() { await open('pnatarajan', `#/chargeback?month=${facts.lastMonth}`); },
  async 'every-plan'() { await open('pnatarajan', '#/plans'); },
};

// --- Run ------------------------------------------------------------------------------------------------------

const names = wanted.length ? wanted : Object.keys(scenes);
for (const name of names) if (!scenes[name]) throw new Error(`Unknown scene ${name}. Scenes: ${Object.keys(scenes).join(', ')}`);
mkdirSync(IMAGES, { recursive: true });

const dir = reuse ? resolve(reuse) : mkdtempSync(join(tmpdir(), 'pinax-capture-'));
if (!existsSync(join(dir, 'var', 'pinax.db'))) {
  console.log(`Seeding a demo into ${dir}`);
  mkdirSync(dir, { recursive: true });
  await run(PINAX, ['seed'], demoEnv(dir));
}
FAST = realpathSync(join(dir, 'demo', 'fs', 'fast'));
try {
  await serve(dir, PORT);
  await serve(dir, LOCAL_PORT, { PINAX_AUTH_MODE: 'local' });
  // What the scenes need to know about this seed: the first scan's day, last month, the demo's background search,
  // and the tags to find the sequencing collection with.
  facts.firstScan = (await api('pnatarajan', 'changes?fs=fast')).choices.at(-1);
  const now = new Date();
  facts.lastMonth = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1)).toISOString().slice(0, 7);
  facts.searchId = (await api('pnatarajan', 'files/jobs')).searches[0]?.id;
  const filters = await api('afujimoto', 'tags/filters');
  const tagId = (key) => (filters.tags ?? filters).find((t) => t.key === key)?.id;
  facts.collection = `want=${tagId('source')}:sequencing&want=${tagId('stage')}:raw`;

  const sizes = existsSync(join(IMAGES, 'shots.json')) ? JSON.parse(readFileSync(join(IMAGES, 'shots.json'), 'utf8')) : {};
  for (const theme of themes) {
    b = await launch({ width: WIDTH, height: HEIGHT, scale: SCALE });
    try {
      for (const origin of [URL, LOCAL_URL]) {  // each theme's choice, kept by the browser for every page after
        await b.goto(`${origin}/app/`, 800);
        await js(`localStorage.setItem('pinax-theme', ${JSON.stringify(theme)})`);
      }
      for (const name of names) {
        try {
          const shot = (await scenes[name]()) ?? {};
          await js(`document.querySelectorAll('.toast').forEach((t) => t.remove())`);
          await b.capture(join(IMAGES, `${name}-${theme}.webp`), { clip: shot.clip });
          const box = shot.clip ?? { width: WIDTH, height: HEIGHT };
          sizes[name] = { width: Math.round(box.width * SCALE), height: Math.round(box.height * SCALE) };
          console.log(`${name} (${theme})`);
        } catch (error) {
          console.error(`${name} (${theme}) failed: ${error.message}`);
          process.exitCode = 1;
        }
      }
    } finally {
      await b.close();
    }
  }
  const ordered = Object.fromEntries(Object.keys(sizes).sort().map((k) => [k, sizes[k]]));
  writeFileSync(join(IMAGES, 'shots.json'), `${JSON.stringify(ordered, null, 1)}\n`);
} finally {
  stopServers();
  if (!reuse) {
    // The demo has a folder no one may read (for Storage health), which must be opened before it can be deleted.
    spawnSync('chmod', ['-R', 'u+rwx', dir]);
    rmSync(dir, { recursive: true, force: true });
  }
}
