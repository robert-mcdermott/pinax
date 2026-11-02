# SPDX-License-Identifier: Apache-2.0
"""The load test: many people using Pinax at once, as their browsers do (Q6).

Each simulated person signs in with a password, as a browser does on a first visit (the page, its styles and
scripts, then the sign-in), then opens one page after another, pausing between clicks. A page is the API requests the
interface makes for it, sent together, as a browser sends them; the pages and their requests are those of each
role's sidebar. People of labs browse their own lab's folders and go down into its projects; division heads, their
division's labs; storage administrators and auditors, every filesystem and the administration pages; finance,
charges and prices. Who does what is fixed by `--seed`, so two runs make the same requests.

Runs against an installation from `bench/load_setup.py`, starting `pinax serve` from it (or against `--url`), and
writes a report: for every page and every API route, the median, 95th and 99th percentile times, errors, and
requests a second, measured after the ramp-up; the server's memory through the run; and the budgets met or not.

    uv run python bench/load.py /scratch/load-10m --people 50 --minutes 5
    uv run python bench/load.py --compare first.json second.json
"""

import argparse
import asyncio
import json
import os
import random
import socket
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

STATIC = Path(__file__).resolve().parents[1] / "src" / "pinax" / "static"
MIX = {"pi": 0.45, "member": 0.35, "division": 0.04, "admin": 0.08, "auditor": 0.04, "finance": 0.04}
LAB_ROLES = ("pi", "member", "division")
# Budgets (roadmap, wave 14): the 95th percentile of folder-level pages and of a lab's searches, in seconds.
FOLDER_PAGE_P95, LAB_SEARCH_P95, MEMORY_GB = 1.0, 2.0, 16

# ---------- Pages ----------
# Each page: the requests the interface makes for it, given the person's place (fs and path) and lab. `{place}` is
# `fs=...&path=...` for the folder they are in, or nothing for their own data; `{lab}` their lab's slug.

FOLDER_PAGES = {  # name: (weight, requests)
    "Age map": (25, ["/api/tree?{place}&metric=bytes&depth=2", "/api/scan-days?{fs}"]),
    "Age report": (5, ["/api/tree?{place}&depth=1"]),
    "History": (10, ["/api/history?{place}"]),
    "Change map": (8, ["/api/changes?{place}&metric=max&depth=1"]),
    "What changed": (6, ["/api/changed?{place}"]),
    "File types": (8, ["/api/types?{place}"]),
    "Cold data": (6, ["/api/cold?{place}&older=3&min_bytes=0"]),
    "Owners": (6, ["/api/owners?{place}"]),
    "Find files": (8, ["/api/files?{place}&sort=size&limit=500&total=false"]),
}
LAB_PAGES = FOLDER_PAGES | {
    "My folders": (6, ["/api/home"]),
    "Storage charges": (6, ["/api/charges", "/api/charges/split?lab={lab}&month={month}"]),
    "Cleanup plans": (4, ["/api/plans", "/api/plans/suggestions?lab={lab}"]),
    "Collections": (2, ["/api/tags/filters"]),
}
ADMIN_PAGES = {name: (w // 2 or 1, reqs) for name, (w, reqs) in FOLDER_PAGES.items() if name != "Find files"} | {
    "All data": (10, ["/api/admin/overview", "/api/tree", "/api/home", "/api/views"]),
    "Find files, everywhere": (2, ["/api/files?sort=size&limit=500&total=false", "/api/files/count?sort=size"]),
    "Searches": (1, ["/api/files/jobs"]),
    "Storage health": (6, ["/api/admin/health?fs={fs_slug}&huge_entries=10000&many_files=5000"]),
    "Scans": (5, ["/api/admin/snapshots", "/api/admin/ingest"]),
    "Capacity": (4, ["/api/admin/capacity"]),
    "Tags": (2, ["/api/admin/tags"]),
    "Costs": (2, ["/api/admin/costs"]),
    "Chargeback": (4, ["/api/admin/costs/charges", "/api/admin/costs/ahead"]),
    "People and access": (6, ["/api/admin/people"]),
    "API tokens": (2, ["/api/tokens", "/api/admin/tokens"]),
    "Audit log": (5, ["/api/admin/audit?offset=0&limit=50"]),
    "Access review": (4, ["/api/admin/access-review"]),
    "Cleanup plans, everyone's": (2, ["/api/plans", "/api/plans/all"]),
}
AUDITOR_PAGES = ADMIN_PAGES | {"Audit log": (12, ADMIN_PAGES["Audit log"][1]),
                               "Access review": (10, ADMIN_PAGES["Access review"][1])}
FINANCE_PAGES = {
    "Chargeback": (40, ["/api/admin/costs/charges", "/api/admin/costs/ahead"]),
    "Storage charges": (30, ["/api/charges", "/api/charges/split?lab={lab}&month={month}"]),
    "Costs": (15, ["/api/admin/costs"]),
    "Capacity": (15, ["/api/admin/capacity"]),
}
PAGES = {"pi": LAB_PAGES, "member": LAB_PAGES, "division": LAB_PAGES, "admin": ADMIN_PAGES, "auditor": AUDITOR_PAGES,
         "finance": FINANCE_PAGES}
START = {  # the requests after signing in, for the page each role lands on
    "lab": ["/api/home", "/api/tree", "/api/charges?history=false", "/api/views"],
    "admin": ["/api/admin/overview", "/api/tree", "/api/home", "/api/views"],
    "finance": ["/api/admin/costs/charges", "/api/admin/costs/ahead"],
}
FOLDER_PAGE_NAMES = set(FOLDER_PAGES) - {"Find files"}


# ---------- Simulated people ----------

@dataclass
class Person:
    username: str
    role: str
    labs: list[dict]  # the labs whose folders they browse (their own; a division's; any, for administrators)
    rng: random.Random
    lab: dict | None = None
    path: str | None = None  # where they are; None: their own data, or the filesystem's root

    def place(self, fs: str) -> dict:
        return {"place": f"fs={fs}&path={self.path}" if self.path else (f"fs={fs}" if self.role in ("admin", "auditor") else ""),
                "fs": f"fs={fs}" if self.path else ""}

    def wander(self) -> None:
        """Before an explore page: stay, go into one of the lab's projects, or back up to a lab's folder."""
        if self.role == "finance":
            return
        r = self.rng.random()
        if self.path is None or r < 0.2:
            self.lab = self.rng.choice(self.labs)
            self.path = self.lab["folder"] if self.role in LAB_ROLES or r < 0.1 else None
        elif r < 0.5 and self.lab["projects"]:
            self.path = self.rng.choice(self.lab["projects"])


@dataclass
class Recorder:
    measure_from: float
    pages: dict = field(default_factory=lambda: defaultdict(list))
    routes: dict = field(default_factory=lambda: defaultdict(list))
    errors: list = field(default_factory=list)
    counts: dict = field(default_factory=lambda: defaultdict(int))

    def page(self, name: str, started: float, seconds: float, ok: bool) -> None:
        if started >= self.measure_from:
            self.pages[name].append(seconds)
            self.counts["pages"] += 1
            self.counts["page_errors"] += not ok

    def request(self, route: str, started: float, seconds: float, status: int, detail: str = "") -> None:
        if started < self.measure_from:
            return
        self.routes[route].append(seconds)
        self.counts["requests"] += 1
        if status >= 400 or status == 0:
            self.counts["errors"] += 1
            if len(self.errors) < 50:
                self.errors.append({"route": route, "status": status, "detail": detail[:200]})


async def _get(client: httpx.AsyncClient, rec: Recorder, url: str) -> int:
    """A request; `!401` after the address means that answer is expected (`/api/me` before signing in)."""
    url, _, expect = url.partition("!")
    route = url.split("?")[0]
    t = time.monotonic()
    try:
        r = await client.get(url)
        status, detail = r.status_code, (r.text if r.status_code >= 400 else "")
    except httpx.HTTPError as e:
        status, detail = 0, f"{type(e).__name__}: {e}"
    if expect and status == int(expect):
        status = 200
    rec.request(route, t, time.monotonic() - t, status, detail)
    return status


async def _page(client, rec: Recorder, name: str, urls: list[str]) -> None:
    t = time.monotonic()
    statuses = await asyncio.gather(*(_get(client, rec, u) for u in urls))
    rec.page(name, t, time.monotonic() - t, all(0 < s < 400 for s in statuses))


async def _session(base: str, person: Person, password: str, fs: str, rec: Recorder, start_at: float, end_at: float,
                   think: tuple[float, float], assets: list[str]) -> None:
    await asyncio.sleep(max(0.0, start_at - time.monotonic()))
    month = datetime.now(UTC).strftime("%Y-%m")
    limits = httpx.Limits(max_connections=6)  # as a browser, per site
    async with httpx.AsyncClient(base_url=base, limits=limits, timeout=300, headers={"Origin": base}) as client:
        # A first visit: the page and its files, then signing in.
        await _page(client, rec, "Sign-in page", assets + ["/api/config", "/api/me!401"])
        t = time.monotonic()
        try:
            r = await client.post("/api/auth/login", json={"username": person.username, "password": password})
            status = r.status_code
        except httpx.HTTPError as e:
            status, r = 0, None
            rec.request("/api/auth/login", t, time.monotonic() - t, 0, str(e))
        else:
            rec.request("/api/auth/login", t, time.monotonic() - t, status, r.text if status >= 400 else "")
        rec.page("Signing in", t, time.monotonic() - t, status == 200)
        if status != 200:
            return
        kind = "lab" if person.role in LAB_ROLES else ("finance" if person.role == "finance" else "admin")
        await _page(client, rec, "Landing", ["/api/config", "/api/me"] + START[kind])
        pages = PAGES[person.role]
        names, weights = list(pages), [w for w, _ in pages.values()]
        while time.monotonic() < end_at:
            await asyncio.sleep(person.rng.uniform(*think))
            if time.monotonic() >= end_at:
                break
            name = person.rng.choices(names, weights)[0]
            if name in FOLDER_PAGES:
                person.wander()
            lab = (person.lab or person.labs[0])["slug"] if person.labs else ""
            values = person.place(fs) | {"lab": lab, "month": month, "fs_slug": fs}
            urls = [u.format(**values).replace("?&", "?").rstrip("?&") for u in pages[name][1]]
            await _page(client, rec, name, urls)


def _people(spec: dict, count: int, mix: dict, seed: int) -> list[Person]:
    """`count` people in the proportions of `mix`, taken in order from the installation's, so a run is repeatable."""
    by_role = defaultdict(list)
    for p in spec["people"]:
        by_role[p["role"]].append(p)
    shares = {r: s for r, s in mix.items() if s > 0 and by_role[r]}
    total = sum(shares.values())
    exact = {r: count * s / total for r, s in shares.items()}
    n = {r: int(v) for r, v in exact.items()}
    for r in sorted(exact, key=lambda r: exact[r] - n[r], reverse=True)[:count - sum(n.values())]:
        n[r] += 1
    labs = spec["labs"]
    out = []
    for role, k in n.items():
        for i in range(k):
            p = by_role[role][i % len(by_role[role])]
            mine = ([lab for lab in labs if lab["slug"] == p.get("lab")] if role in ("pi", "member")
                    else [lab for lab in labs if lab["division"] == p.get("division")] if role == "division"
                    else labs if role in ("admin", "auditor") else [lab for lab in labs[:20]])
            out.append(Person(p["username"], role, mine, random.Random(seed * 1_000_003 + len(out))))
    random.Random(seed).shuffle(out)
    return out


# ---------- The server ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(folder: Path) -> dict:
    env = dict(os.environ)
    for line in (folder / "pinax.env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _start_server(folder: Path, port: int, log: Path) -> subprocess.Popen:
    pinax = str(Path(sys.executable).with_name("pinax"))
    proc = subprocess.Popen([pinax, "serve", "--port", str(port)], env=_env(folder), stdout=log.open("w"),
                            stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"error: pinax serve stopped ({proc.returncode}); see {log}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    proc.terminate()
    sys.exit(f"error: pinax serve did not answer within 90 s; see {log}")


def _usage(pid: int) -> tuple[float, float] | None:
    """The process's memory (MB) and the CPU time it has used (seconds), from `ps`."""
    out = subprocess.run(["ps", "-o", "rss=,time=", "-p", str(pid)], capture_output=True, text=True,
                         check=False).stdout.split()
    if len(out) != 2:
        return None
    days, _, clock = out[1].rpartition("-")  # [DD-][HH:]MM:SS[.ss]
    seconds = 0.0
    for part in clock.split(":"):
        seconds = seconds * 60 + float(part)
    return int(out[0]) / 1024, seconds + 86400 * int(days or 0)


async def _sample_server(pid: int, memory: list, cpu: list, stop: asyncio.Event, t0: float) -> None:
    """Every second: the server's memory, and how many cores it kept busy since the last sample."""
    last = None
    while not stop.is_set():
        got = await asyncio.to_thread(_usage, pid)
        now = round(time.monotonic() - t0, 1)
        if got is not None:
            memory.append((now, round(got[0], 1)))
            if last is not None and now > last[0]:
                cpu.append((now, round((got[1] - last[1]) / (now - last[0]), 2)))
            last = (now, got[1])
        try:
            await asyncio.wait_for(stop.wait(), 1.0)
        except TimeoutError:
            pass


# ---------- Running ----------

def _stats(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)
    q = statistics.quantiles(v, n=100, method="inclusive") if len(v) > 1 else [v[0]] * 99
    return {"n": len(v), "p50": round(q[49], 4), "p95": round(q[94], 4), "p99": round(q[98], 4), "max": round(v[-1], 4)}


async def _run(base: str, folder: Path, people: list[Person], password: str, fs: str, *, seconds: float, ramp: float,
               think: tuple[float, float], pid: int | None) -> dict:
    t0 = time.monotonic()
    rec = Recorder(measure_from=t0 + ramp)
    assets = ["/app/", "/css/pinax.css", "/img/favicon.svg"] + [f"/js/{p.name}" for p in sorted((STATIC / "js").glob("*.js"))]
    memory, cpu, stop = [], [], asyncio.Event()
    sampler = asyncio.create_task(_sample_server(pid, memory, cpu, stop, t0)) if pid else None
    end_at = t0 + ramp + seconds
    await asyncio.gather(*(_session(base, p, password, fs, rec, t0 + ramp * i / max(1, len(people)), end_at, think,
                                    assets) for i, p in enumerate(people)))
    stop.set()
    if sampler:
        await sampler
    elapsed = time.monotonic() - rec.measure_from
    steady = [mb for t, mb in memory if t >= ramp]
    cores = [c for t, c in cpu if t >= ramp]
    pages = {name: _stats(v) for name, v in sorted(rec.pages.items())}
    folder_p95 = max((pages[n]["p95"] for n in FOLDER_PAGE_NAMES if pages.get(n, {}).get("n")), default=None)
    search_p95 = pages.get("Find files", {}).get("p95")
    peak = max((mb for _, mb in memory), default=None)
    return {
        "measured_seconds": round(elapsed, 1),
        "totals": {"pages": rec.counts["pages"], "requests": rec.counts["requests"], "errors": rec.counts["errors"],
                   "pages_per_s": round(rec.counts["pages"] / elapsed, 2) if elapsed > 0 else 0,
                   "requests_per_s": round(rec.counts["requests"] / elapsed, 2) if elapsed > 0 else 0},
        "all_pages": _stats([s for name, v in rec.pages.items() if name not in ("Sign-in page", "Signing in") for s in v]),
        "pages": pages,
        "routes": {route: _stats(v) for route, v in sorted(rec.routes.items())},
        "memory_mb": {"peak": peak, "steady_median": round(statistics.median(steady), 1) if steady else None,
                      "samples": memory} if pid else None,
        "cpu_cores": {"steady_median": round(statistics.median(cores), 2), "peak": max(cores),
                      "samples": cpu} if cores else None,
        "budgets": {
            "folder pages, p95 within 1 s": None if folder_p95 is None else folder_p95 <= FOLDER_PAGE_P95,
            "a lab's searches, p95 within 2 s": None if search_p95 is None else search_p95 <= LAB_SEARCH_P95,
            "no errors": rec.counts["errors"] == 0,
            "memory within 16 GB": None if peak is None else peak <= MEMORY_GB * 1024,
        },
        "errors": rec.errors,
    }


def run(folder: Path, *, people: int, seconds: float, ramp: float = 60, think: tuple[float, float] = (5, 15),
        seed: int = 1, mix: dict | None = None, url: str | None = None, pid: int | None = None,
        name: str | None = None) -> tuple[dict, Path]:
    """Run the load test against the installation in `folder`; returns the report and where it was written."""
    folder = folder.resolve()
    spec = json.loads((folder / "people.json").read_text())
    password = (folder / "password").read_text().strip()
    crowd = _people(spec, people, mix or MIX, seed)
    reports = folder / "reports"
    reports.mkdir(exist_ok=True)
    name = name or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{people}p"
    proc = None
    if url is None:
        port = _free_port()
        proc = _start_server(folder, port, reports / f"{name}.server.log")
        url, pid = f"http://127.0.0.1:{port}", proc.pid
    try:
        result = asyncio.run(_run(url.rstrip("/"), folder, crowd, password, spec["filesystem"], seconds=seconds,
                                  ramp=ramp, think=think, pid=pid))
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                proc.kill()
    env = _env(folder)
    report = {"name": name, "when": datetime.now(UTC).isoformat(timespec="seconds"),
              "settings": {"people": people, "seconds": seconds, "ramp": ramp, "think": list(think), "seed": seed,
                           "mix": mix or MIX, "roles": {r: sum(p.role == r for p in crowd) for r in MIX},
                           "catalog": "postgresql" if env.get("PINAX_DATABASE_URL") else "sqlite",
                           "server": "started here" if proc else url},
              **result}
    (reports / f"{name}.json").write_text(json.dumps(report, indent=1))
    (reports / f"{name}.md").write_text(markdown(report))
    return report, reports / f"{name}.md"


# ---------- Reports ----------

def _ms(s: float | None) -> str:
    return "" if s is None else f"{s * 1000:,.0f}"


def markdown(r: dict) -> str:
    s, t = r["settings"], r["totals"]
    lines = [f"# Load test {r['name']}", "",
             (f"{s['people']} people ({', '.join(f'{n} {role}' for role, n in s['roles'].items() if n)}) on "
              f"{s['catalog']}, each clicking every {s['think'][0]:g} to {s['think'][1]:g} s, for "
              f"{r['measured_seconds']:g} s after a ramp-up of {s['ramp']:g} s (seed {s['seed']})."), "",
             (f"{t['pages']:,} pages and {t['requests']:,} requests ({t['pages_per_s']} pages and "
              f"{t['requests_per_s']} requests a second), {t['errors']} errors. All pages: median "
              f"{_ms(r['all_pages'].get('p50'))} ms, 95th percentile {_ms(r['all_pages'].get('p95'))} ms."), ""]
    if r.get("memory_mb"):
        m = r["memory_mb"]
        lines += [f"Server memory: {m['steady_median']:,.0f} MB (median after the ramp-up), {m['peak']:,.0f} MB at most.", ""]
    if r.get("cpu_cores"):
        c = r["cpu_cores"]
        lines += [f"Server CPU: {c['steady_median']:g} cores busy (median after the ramp-up), {c['peak']:g} at most.", ""]
    lines += ["| Budget | Met |", "|---|---|"]
    lines += [f"| {k} | {'yes' if v else 'no' if v is False else 'not measured'} |" for k, v in r["budgets"].items()]
    for title, rows in (("Pages", r["pages"]), ("API routes", r["routes"])):
        lines += ["", f"## {title}", "", "| | Count | Median ms | p95 ms | p99 ms | Max ms |", "|---|---:|---:|---:|---:|---:|"]
        lines += [f"| {k} | {v['n']:,} | {_ms(v.get('p50'))} | {_ms(v.get('p95'))} | {_ms(v.get('p99'))} | {_ms(v.get('max'))} |"
                  for k, v in rows.items() if v["n"]]
    if r["errors"]:
        lines += ["", "## Errors (first 50)", ""] + [f"- `{e['route']}` {e['status']}: {e['detail']}" for e in r["errors"]]
    return "\n".join(lines) + "\n"


def compare(a: dict, b: dict, tolerance: float = 0.10, min_count: int = 30) -> tuple[bool, str]:
    """Whether two runs agree: each page's median (with `min_count` samples in both) and the pages a second within
    `tolerance` of each other. The 95th percentiles are shown, not judged: a few slow requests move them."""
    lines = ["| Page | Median ms | | Change | p95 ms | | Change |", "|---|---:|---:|---:|---:|---:|---:|"]
    ok = True
    rows = [("All pages", a["all_pages"], b["all_pages"])] + [(n, a["pages"][n], b["pages"].get(n, {"n": 0}))
                                                            for n in a["pages"]]
    for name, x, y in rows:
        if not x.get("n") or not y.get("n"):
            continue
        d50, d95 = y["p50"] / x["p50"] - 1, y["p95"] / x["p95"] - 1
        judged = x["n"] >= min_count and y["n"] >= min_count
        if judged and abs(d50) > tolerance:
            ok = False
        lines.append(f"| {name} | {_ms(x['p50'])} | {_ms(y['p50'])} | {d50:+.0%}{'' if judged else ' (few)'} | "
                     f"{_ms(x['p95'])} | {_ms(y['p95'])} | {d95:+.0%} |")
    pa, pb = a["totals"]["pages_per_s"], b["totals"]["pages_per_s"]
    if pa and abs(pb / pa - 1) > tolerance:
        ok = False
    lines.append(f"\nPages a second: {pa} and {pb}. {'They agree' if ok else 'They do not agree'} within {tolerance:.0%}.")
    return ok, "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", type=Path, nargs="?", help="An installation from bench/load_setup.py")
    ap.add_argument("--people", type=int, default=25, help="People at once (default 25)")
    ap.add_argument("--minutes", type=float, default=5, help="Minutes measured, after the ramp-up (default 5)")
    ap.add_argument("--ramp", type=float, default=60, help="Seconds over which people arrive (default 60)")
    ap.add_argument("--think", default="5,15", help="Seconds between a person's clicks, from,to (default 5,15)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--mix", help="Shares of roles, such as pi=0.5,admin=0.5 (default: " +
                    ",".join(f"{k}={v}" for k, v in MIX.items()) + ")")
    ap.add_argument("--url", help="A Pinax already running from the installation, instead of starting one")
    ap.add_argument("--pid", type=int, help="With --url: its process, to sample its memory")
    ap.add_argument("--name", help="The report's name (default: the time and the number of people)")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("A", "B"), help="Compare two reports' JSON")
    args = ap.parse_args()
    if args.compare:
        ok, text = compare(*(json.loads(p.read_text()) for p in args.compare))
        print(text)
        sys.exit(0 if ok else 1)
    if args.folder is None:
        ap.error("name an installation folder, or --compare two reports")
    mix = {k: float(v) for k, v in (kv.split("=") for kv in args.mix.split(","))} if args.mix else None
    think = tuple(float(x) for x in args.think.split(","))
    report, path = run(args.folder, people=args.people, seconds=args.minutes * 60, ramp=args.ramp, think=think,
                       seed=args.seed, mix=mix, url=args.url, pid=args.pid, name=args.name)
    print(markdown(report).split("\n## API routes")[0])
    print(f"Report: {path} (and .json)")


if __name__ == "__main__":
    main()
