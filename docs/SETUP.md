# Running Pinax on your own data

How to point Pinax at a real filesystem on a laptop or workstation: settings, the first administrator, a scan,
labs and people, and prices. It takes a few minutes plus the scan. To install Pinax on a server, with services,
scheduled scans, and single sign-on, follow [DEPLOYMENT.md](DEPLOYMENT.md); every setting is in
[CONFIGURATION.md](CONFIGURATION.md).

## What you need

- [uv](https://docs.astral.sh/uv/), and this repository with its packages (`uv sync`).
- fs-inventory: `scripts/install-fs-inventory.sh` installs the pinned release into `bin/` and checks its SHA-256.
- Read access to the filesystem's metadata (directory listings and file attributes) from where you scan it.
  Folders you cannot read are recorded as scan errors and shown under *Storage health*; the rest is scanned.
- Disk for the snapshot indexes, about 56 bytes per file (0.6 GB for ten million files), and for the scans
  themselves. Pinax keeps the six newest indexes of each filesystem by default.

## 1. Settings

Settings are `PINAX_*` environment variables, or a `.env` file in the folder you run Pinax from. Keep your data
apart from the demo's, and sign in with passwords Pinax keeps:

```bash
cat > .env <<'EOF'
PINAX_ENVIRONMENT=dev
PINAX_AUTH_MODE=local
PINAX_VAR_ROOT=data/var
PINAX_SCAN_ROOT=data/scans
EOF
```

- `PINAX_ENVIRONMENT=dev`: this catalog holds real data, so `pinax seed`, which deletes the catalog to write the
  demo, refuses to run (it runs only in `demo`). To go back to the demo, move `.env` aside, or keep the demo in a
  separate checkout.
- `PINAX_AUTH_MODE=local`: people sign in with a username and a password. (The demo's persona switcher lets
  anyone be anyone; single sign-on needs a proxy, set up in [DEPLOYMENT.md](DEPLOYMENT.md).)
- `PINAX_VAR_ROOT` holds the catalog (`pinax.db`) and the snapshot indexes; `PINAX_SCAN_ROOT` is where
  `pinax scan` writes scans. `data/` is ignored by git, so no scan, path, or name is committed by accident.
- This replaces any `.env` you have; [.env.example](../.env.example) lists every setting with its default.

## 2. The first administrator

Nobody can sign in until someone has a password. Add yourself as a storage administrator; Pinax prints a
temporary password, which you change when you first sign in:

```bash
uv run pinax user add jdoe@example.org --name "Jane Doe" --email jdoe@example.org --admin
```

If you may move to single sign-on later, use the usernames it will send (with Entra ID, usually the user
principal name, such as `jdoe@example.org`), so people keep their access.

## 3. A filesystem and its first scan

Register the filesystem by the root its scans record, with the name people know it by:

```bash
uv run pinax filesystem add fast /fh/fast --name Fast
```

If this machine mounts it elsewhere than where people know it (say `/mnt/fast` for `/fh/fast`), register the
mount and add `--display-root /fh/fast`. Then scan it and ingest the scan in one step:

```bash
uv run pinax scan fast
```

A large filesystem takes a while. To choose how many threads to crawl with, sample it first, and set the result as
`PINAX_SCAN_THREADS`:

```bash
bin/fs-inventory probe /fh/fast
```

Scans made elsewhere, for example by a job on a storage node, are ingested the same way; Pinax finds the
filesystem by the scan's root:

```bash
uv run pinax ingest /path/to/scan
```

Or let Pinax ingest each new scan written under a folder, checking every five minutes:

```bash
uv run pinax ingest --watch /path/to/scans
```

## 4. Check the numbers

Each snapshot can be checked directory by directory against `fs-inventory rollup`, an independent computation.
The snapshot's ID is in `pinax snapshots`:

```bash
uv run pinax check 1
```

## 5. Sign in

```bash
uv run pinax serve
```

Open <http://127.0.0.1:8001>, sign in with the temporary password, and choose your own. As a storage
administrator you see every filesystem and folder: *All data*, the age map, *Storage health*, and *Scans*.

## 6. Labs, people, and folders

Each top-level folder of a filesystem belongs to one lab or department, and its members see it; nobody else
does, except storage administrators. Set this up in the app:

1. *People and access*, *Labs and departments* tab: **Add lab or department** for each one (a slug, a name, and
   whether it is a lab or a department).
2. *People and access*, *People* tab: **Add person** for each person, with their labs and their role in each
   (PI or owner, or delegate), then **Set a temporary password** for them to sign in with.
3. *Folders and labs*: give each top-level folder its owner. Tick *Unmapped only* to see the ones left.

For many labs and people at once, `pinax import` reads CSV files and shows every change before making it
([IMPORT.md](IMPORT.md)):

```bash
uv run pinax import path/to/csv-folder
```

```bash
uv run pinax import path/to/csv-folder --apply
```

## 7. Prices (optional)

To show labs what their storage costs, set a price for each filesystem on *Costs*, per GB, GiB, TB, or TiB per
month, and free tiers for labs and for departments, with exceptions for particular labs. Charges start from the
first scan and the first price; labs see theirs under *Storage charges*, and *Chargeback* has every lab's, with a
CSV statement. The currency is `PINAX_CURRENCY` (USD by default).

## Next

- **A server:** [DEPLOYMENT.md](DEPLOYMENT.md) installs Pinax with systemd services, scheduled scans and automatic
  ingest, an HTTPS proxy for local sign-in or single sign-on with oauth2-proxy, upgrades, sizing, and backups.
- **Every setting:** [CONFIGURATION.md](CONFIGURATION.md).
- **How it works, and how fast:** [ARCHITECTURE.md](ARCHITECTURE.md).
