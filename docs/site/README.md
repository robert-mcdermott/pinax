# The Pinax website

A product site with the whole documentation, built from the guides in `docs/` (the same Markdown GitHub shows) and
screenshots of the demo in both themes. It is plain HTML, CSS, and a little JavaScript: any web server, or GitHub
Pages later, can serve the built folder.

```text
docs/site/
  build.mjs          the generator: pages from the guides, the menu, links, screenshots, link check
  pages/index.html   the home page
  assets/            site.css and site.js
docs/*.md            the guides: every page of the documentation is one of them, or some of its sections
docs/images/         the screenshots, <scene>-light.webp and <scene>-dark.webp, and their sizes (shots.json)
docs/capture/        the script that takes the screenshots from the demo
site/                the built site (not committed)
```

## Building it

Needs Node 22 or later, and nothing else:

```bash
node docs/site/build.mjs
```

It builds into `site/` (or a folder given after it), and checks every local link, anchor, and screenshot; it exits
with an error and lists the problems if any are broken. To look at it:

```bash
python3 -m http.server 8810 --directory site
```

Then open <http://127.0.0.1:8810>.

## Writing the documentation

Edit the guides in `docs/`, not the site: the build makes the site's pages from them.

- **Pages.** `DOCS` at the top of `build.mjs` lists the pages in reading order, in groups (the menu): each is a
  whole guide, or some of a guide's `##` sections (`intro: true` adds the text before its first section). A page of
  one section takes the section's heading as its title. Every section of a guide must be on a page; the build says
  so if one is not. A new guide needs a line there.
- **Links.** Write links as GitHub reads them (`[Sizing](DEPLOYMENT.md#sizing)`, `[x](#same-file-anchor)`): the
  build sends each to the page that holds its anchor. Links to other files in the repository go to GitHub.
- **Screenshots.** A paragraph that is only a picture of a scene,
  `![What it shows](images/<scene>-light.webp "The caption")`, becomes a figure with both themes' pictures; GitHub
  shows the light one. A narrower crop (a panel) is shown at its own size.
- **The home page** is `pages/index.html`, HTML with `{{shot <scene> "alt" "caption" [window] [eager]}}` for
  screenshots and `{{version}}` for Pinax's version.

The Markdown the build understands is what the guides use: headings, paragraphs, lists (nested, loose or tight,
with code inside), fenced code, tables, block quotes, and inline code, emphasis, links, and autolinks.

## Screenshots

The screenshots are captured from the demo, in both themes, by driving headless Chrome (or Chromium, Edge, or Brave;
`CHROME` chooses). The script seeds a demo of its own and starts Pinax on it (it never touches the demo in `var/`),
then opens each scene as one of the demo's people:

```bash
node docs/capture/capture.mjs
```

```bash
node docs/capture/capture.mjs age-map history --theme dark
```

Each scene is a function in `docs/capture/capture.mjs`: a person and a page, and sometimes a scroll, a hover, or a
crop to one part of the page. To add one, write its function, run it, and refer to its pictures from a guide.
`--reuse DIR` seeds the demo into `DIR` once and keeps it, which is quicker while working on scenes; the pictures of
one run share one seed, so re-capture them all (without `--reuse`, or with a new folder) once the demo or the
interface changes. Needs uv, fs-inventory (`scripts/install-fs-inventory.sh`), and Chrome.

## For each release

1. Re-capture the screenshots if the interface or the demo changed, and commit them.
2. Check the guides against what changed, and the release highlight on the home page.
3. Build, and look at the pages that changed in both themes and at a phone's width.
