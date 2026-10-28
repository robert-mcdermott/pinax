# Importing labs, people, and folder ownership

`pinax import` sets up or updates, in bulk, who is who in Pinax: labs and departments, people, who belongs to
which lab, and which lab owns each top-level folder. It reads up to four CSV files, shows every change it would
make, and makes them only when asked:

```bash
uv run pinax import examples/import
```

```bash
uv run pinax import examples/import --apply
```

For one person at a time, use *People and access* in the app: it follows the same rules for usernames and emails,
and audits each change the same way (with `"source": "app"` instead of `"import"`).

The folder holds any of `labs.csv`, `people.csv`, `memberships.csv`, and `folders.csv`; or name the files with
`--labs`, `--people`, `--memberships`, and `--folders`. The examples in `examples/import/` fit the demo: they add
two departments and a lab, three people, and owners for the demo's three unmapped folders.

This is a stop-gap until the authoritative source of folder ownership is chosen (wave 7 in
`pinax-spec/roadmap.md`). That source, or a script reading it, can write these files.

## How an import works

- **A dry run first.** Without `--apply`, Pinax lists every change, file by file, and changes nothing:
  `+` creates, `~` updates (with each field's value before and after), `-` removes. Rows that would change
  nothing are counted as unchanged.
- **All or nothing.** If any row is refused (below), nothing is applied, and every refused row is listed with its
  file and line. Fix the files and run again. With `--apply`, all the changes are made in one transaction.
- **Matched by key.** A row updates what already has its key, or creates it. Rows never delete labs or people;
  set `active` to `false` instead. Memberships and folder ownership are removed with `none` (below).
- **Columns set fields.** A column that is present sets its field on every row, an empty text value included. A
  column that is absent leaves that field as it is, and new rows get its default. So a file with only `username`,
  `display_name`, and `email` never changes anyone's administrator flag.
- **Audited.** Every applied change is an audit event with the same action as the same change made in the
  interface (`folder.mapped`, `lab.member`, and `lab.created`, `lab.updated`, `person.created`, `person.updated`),
  marked `"source": "import"` with the operating-system user who ran it. A final `import.applied` event records
  the number of changes and each file's SHA-256.
- **Running it again changes nothing.** An applied import's dry run lists no changes.

Files are UTF-8 (a byte-order mark from Excel is fine), with a header row; column names are not case-sensitive,
and spaces around values are ignored. Empty lines are skipped.

## The files

### labs.csv: labs and departments

| Column | Required | Values |
|---|---|---|
| `slug` | yes, the key | Lowercase letters, digits, `-` and `_`, starting with a letter or digit; at most 64 characters; not `none` |
| `name` | yes | The name people see |
| `kind` | no (default `lab`) | `lab` or `department` |
| `division` | no | Free text |
| `research_area` | no | Free text |
| `active` | no (default `true`) | `true` or `false` (also `yes`/`no`, `1`/`0`). An inactive lab gives its members no access |

### people.csv: people

| Column | Required | Values |
|---|---|---|
| `username` | yes, the key | The sign-on name the SSO proxy sends (`X-Remote-User`); no spaces |
| `display_name` | yes | |
| `email` | yes | |
| `title` | no | Free text |
| `is_admin` | no (default `false`) | `true` gives the person the storage administrator role: every filesystem and folder |
| `active` | no (default `true`) | `false` stops the person signing in |

### memberships.csv: who belongs to which lab

| Column | Required | Values |
|---|---|---|
| `lab` | yes, the key with `username` | A lab's slug, from the catalog or `labs.csv` |
| `username` | yes | A person's username, from the catalog or `people.csv` |
| `role` | yes | `pi` (the PI of a lab, or a department's owner), `delegate` (a member who sees the lab's folders), or `none` to remove the membership |

### folders.csv: which lab owns each top-level folder

| Column | Required | Values |
|---|---|---|
| `filesystem` | yes, the key with `folder` | A registered filesystem's slug (`pinax filesystem list`) |
| `folder` | yes | One folder name directly under the filesystem's root, such as `alvarez_m` |
| `lab` | yes | The owning lab's slug, from the catalog or `labs.csv`, or `none` to unmap the folder |

A folder can be mapped before it exists on the filesystem (it appears with the next scan). Each folder has one
owner, so mapping it to another lab moves it.

## Refused rows

Any of these refuses the import:

- a file that cannot be read, or is missing a required column, has an unknown column (a misspelled `emial`
  would otherwise be ignored), or repeats one;
- a row with the wrong number of values, or an empty required value;
- a key listed twice in one file, since which row is meant would be ambiguous;
- a username that differs only in case from another (`Ada` and `ada`);
- a reference to a lab, person, or filesystem that is neither in the catalog nor in the import;
- a value Pinax does not accept: a slug it cannot use, a kind, role, or true/false value it does not know, an
  email without `@`, a username with a space, or a folder name with `/` or that is `.` or `..`.
