# IMRI

**Offline library management for school resource centres.**

IMRI is a complete, self-contained replacement for a paper borrowing register. It runs
entirely on one Windows PC with no internet connection, no installer, no database
server and no account to sign up for. Everything — the catalogue, the members, the
loans, the fines, the reports — lives in a single SQLite file you can copy onto a USB
stick.

Built for Malaysian school libraries: Dewey classification, accession numbers, Bahasa
Melayu / English / Chinese interface, `RM` currency and per-day fines.

---

## Activate it

On first run IMRI asks for an activation code. There is exactly **one** code, and it
never expires:

```
arfanayub0
```

The code is matched case-insensitively and ignores surrounding spaces. Five wrong
attempts start a short cooldown. Once activated, the licence is recorded in
`data/license.json` **and** mirrored into the database, so deleting either one does not
deactivate the copy.

> The licence is checked by the **server**, not by the page. Opening the developer tools
> and calling the API by hand returns `403 license_required` — there is no client-side
> flag to flip.

---

## Run it

### The packaged build

```
dist\IMRI.exe
```

Double-click it. It starts a private web server on a free port on `127.0.0.1` only,
opens a chromeless application window, and creates `data\` **beside the exe** — so the
whole thing is portable. Copy the folder to another PC and your library goes with it.

### From source

Requires Python 3.11 or newer. No `pip install` — IMRI uses only the standard library.

```
python main.py
```

| Flag | What it does |
|---|---|
| `--port 8080` | Pin the port instead of picking a free one |
| `--no-window` | Print the URL instead of opening the app window (useful over RDP) |
| `--data-dir D:\imri-data` | Keep the data somewhere else |
| `--check` | Start, self-test, print a JSON summary, exit — for smoke-testing a deployment |
| `--verbose` | Debug logging |

The data directory can also be set with the `IMRI_DATA_DIR` environment variable.

### First sign-in

| Username | Password |
|---|---|
| `admin` | `admin123` |

**You are forced to change this password on first login.** It is stored as a scrypt
hash; the plaintext is never written anywhere.

---

## What it does

**Catalogue** — titles, authors, publishers, ISBN-10/13 with real check-digit
validation, Dewey numbers, call numbers, subjects, languages, MARC blobs, cover images,
and per-copy records with their own barcode, accession number, location and condition.
Duplicate detection by ISBN and by title. Bulk edit and bulk withdraw.

**Members** — students and staff, class lists, IC numbers with validation, contact
details, membership and expiry dates, suspension, a 360° profile showing loans, fines
and reading list in one place, and a printable membership card with a QR code.

**Counter** — issue and return by scanning a barcode or typing an accession number.
The scan field never loses focus, so a librarian can work a whole queue without
touching the mouse. Every guard is enforced server-side: loan limits, suspended
members, copies already out, copies held for someone else, duplicate active loans.

**Fines** — per-day rates from settings, calculated from **local** dates (never UTC, or
every evening in UTC+8 would be a day early). Part payments with sequential receipt
numbers, waivers that require a written reason, and a full payment history per loan.

**Reservations** — a FIFO queue per title with gapless positions. When the last copy
comes back, the head of the queue is promoted to `ready`, the copy is set aside, and the
hold expires on its own after the configured number of days.

**Reports** — collection, circulation, membership, overdue-and-fines, and a monthly
trend, each exportable to CSV and PDF. The PDF writer is written from scratch, so there
is no reporting library to install.

**Labels** — QR codes (versions 1–10, with Reed–Solomon error correction and mask
penalty scoring) and Code 39 barcodes, rendered as SVG, printed as A4 sheets.

**Admin** — four roles (admin, librarian, assistant, viewer) with a capability matrix
enforced on every endpoint, an append-only audit log, generated settings forms, verified
backups with a SHA-256 manifest, and a guarded restore that keeps a safety snapshot
first.

**Hardware** — PC/SC smart-card readers via `ctypes` and `winscard.dll`, for tapping a
student card instead of typing a code. Optional; IMRI works fully without it.

---

## Where your data lives

```
data\
  imri.db            the whole library, one SQLite file
  license.json       the activation record
  backups\           timestamped .imribackup archives
  covers\  photos\   uploaded images
  exports\           generated CSV / PDF / label sheets
  logs\              rotating log files
```

**To back up:** Admin → Backup → Create. To move the library to another PC, copy the
entire `data\` folder.

**To restore:** Admin → Backup → Restore. You must type the backup's date to confirm.
IMRI verifies the archive's SHA-256 manifest, refuses anything that would write outside
the data folder, snapshots the current database, and re-runs an integrity check
afterwards.

---

## Security

- **Passwords** — scrypt (`ln=14, r=8, p=1`) with a per-password salt, verified in
  constant time. A PBKDF2 hash from an older runtime still verifies and is upgraded on
  the next successful login.
- **Sessions** — the cookie holds a random token; the database stores only its SHA-256.
  `HttpOnly`, `SameSite=Strict`, and revoked on logout, on password change and on
  expiry.
- **CSRF** — every mutating request needs `X-IMRI-CSRF: 1`, and a cross-site `Origin`
  is refused.
- **SQL** — every query is parameterised. There is no string concatenation of user
  input anywhere, and search terms are wildcard-escaped so `%` cannot turn a search
  into a full table scan.
- **Headers** — a strict Content-Security-Policy, `nosniff`, `X-Frame-Options: DENY`.
- **Files** — uploads and backups are confined to the data directory; `..` in a backup
  name or a zip member is refused outright.
- **Spreadsheets** — exported CSV and XLSX cells are prefixed so a title beginning with
  `=` cannot become a live Excel formula.

IMRI binds to `127.0.0.1` only. It is not reachable from the network, and it does not
phone home — there is no telemetry, no update check and no external request of any kind.

---

## Tests

```
python tests\run_all.py           # everything, ~4 minutes
python tests\run_all.py --fast    # skip the HTTP suite
python tests\run_all.py --only test_circulation
```

**180 tests** across six modules:

| Module | Covers |
|---|---|
| `test_db` | Schema shape, migration, idempotence, audit triggers, rollup invariant |
| `test_security` | scrypt round-trip, PBKDF2 fallback, sessions, RBAC matrix, the licence gate |
| `test_catalog` | ISBN/Dewey validation, copies, rollup discipline, duplicate detection |
| `test_circulation` | The full counter story: issue, late return, part payment, reservation, promotion |
| `test_transfer` | Import dry-run/commit, CSV injection, backup verify, zip-slip, restore |
| `test_api` | Real sockets: licence gate, auth, CSRF, RBAC per role, 413/415, traversal |

Scale was measured separately at **10,000 members and 200,000 books** — see
`docs/LOAD-TEST.md` for the numbers and `tools/load_test.py` to reproduce them.

Three browser proofs cover things a unit test cannot see, because the bug lives in the
paint or in the shutdown protocol. They drive a real headless Edge over the DevTools
protocol and assert on the live DOM:

| Harness | Proves |
|---|---|
| `tools\smoke_pages.py` | All 14 screens paint a title and log no console error |
| `tools\proof_brand.py` | The mark renders at the right size in all five places, and two marks never collide on a gradient id |
| `tools\proof_liveness.py` | The server outlives an activation screen left untouched for 150 s, and the form still submits afterwards |
| `tools\verify_release.py` | A release archive extracts into something that works — manifest hashes, tests, a served frontend, and the bundled exe |

`tools\proof_brand_sizes.py` renders the mark from 192 px to 16 px into
`docs\brand-sizes.png`; `tools\brand_candidates.py` renders alternative drawings side by
side when the mark is being redesigned. Neither asserts anything — they exist so a visual
decision is made by looking.

---

## Releasing

```bat
python release.py                        :: stage, verify, zip
python tools\verify_release.py release\IMRI-1.0.0.zip
```

`release.py` stages a clean tree into `release\IMRI-<version>\`, excludes build junk and
runtime data by rule, writes `MANIFEST.txt` with a SHA-256 per file, and **verifies the
staged copy rather than the working tree** — the full test suite plus the bundled exe's
self-check. Then it zips.

`verify_release.py` is the independent half: it extracts the archive to a fresh directory
and tests what came out, sharing no code with the packer. It checks every hash against the
manifest, runs the suite from the extracted copy, boots `main.py` and fetches the shell,
the stylesheet and the brand module over HTTP, and runs the bundled exe's `--check`.

Re-running `release.py` over an existing folder updates it in place and removes only files
that are no longer part of the release, so a re-release cannot delete anything it did not
write. `--clean` wipes the folder first if that is what you want.

Full instructions for changing the code, including where to add a screen, an endpoint, a
setting or a migration, are in `DEVELOPING.md`.

---

## Troubleshooting

**"Another copy of IMRI is already running."**
There is a single-instance lock at `data\.lock`. If IMRI crashed, delete that file.

**"The IMRI service is not responding. Restart the application."**
IMRI never touches the internet, so this is not a network problem — it means the page
cannot reach the local server on `127.0.0.1`. Two causes:

1. A second copy of IMRI is already running and holds the single-instance lock (above).
2. The server shut itself down. It exits when the page stops checking in, because a
   crashed window cannot say goodbye. On builds before this one the check-in only began
   *after* sign-in, so the activation and login screens were silent and the server
   killed itself after two minutes — leaving the form you were reading attached to
   nothing. That is fixed: the page now checks in from the moment it loads, including
   while minimised, and the server waits thirty minutes before treating silence as death.

**The window is blank or won't open.**
Run `python main.py --no-window` and open the printed URL in any browser. IMRI is a
normal web app; the app window is only a convenience wrapper around Edge or Chrome.

**"This database was created by a newer version of IMRI."**
Your `data\imri.db` is from a later build than the exe you are running. Use the newer
exe, or point `--data-dir` at a different folder.

**I forgot the admin password.**
Create an empty file named `RESET-ADMIN` in the `data\` folder and start IMRI. The
admin password is reset to `admin123` and you are forced to change it again. Delete the
marker afterwards.

**Antivirus flags the exe.**
The build is a PyInstaller one-file bundle, which some scanners treat as suspicious
because it unpacks itself at launch. It is built from the source in this folder with
`python build_exe.py`.

---

## Project layout

```
main.py              entry point: lock, server, window
run.bat              double-click launcher for source runs
build_exe.py         builds dist\IMRI.exe (PyInstaller one-file)
release.py           cuts a clean source release folder + zip, and verifies it
CHANGELOG.md         what shipped, what was fixed, what is still open
DEVELOPING.md        how to build, how to test, how to start version two
imri\                backend — stdlib only
  config.py          paths, defaults, validation rules, formatting
  db.py              schema, migrations, transactions, audit
  security.py        hashing, sessions, RBAC, the licence gate
  catalog.py         books, copies, barcodes, accession numbers
  members.py         members, profiles, reading lists
  circulation.py     issue, return, renew, fines, reservations
  reporting.py       five reports, dashboard, PDF writer
  transfer.py        import, export, backup, restore
  labels.py          QR and Code 39 encoders
  nfc.py             PC/SC smart-card reader
  server.py          HTTP server, middleware chain, app window
  api\               route handlers (core, library, circulation, insights)
web\                 frontend — no framework, no build step
  index.html
  css\app.css
  js\                api, store, ui, charts, router, shell, i18n, brand
  js\pages\          20 screens + 7 shared partials
  js\lang\           en, ms, zh — 913 keys each
tests\               180 tests, standard library only
tools\               proof harnesses, load test, CDP browser driver
docs\                design notes, screenshots, load-test results
imri-docs\           PRD, architecture, task breakdown
release\             output of release.py — the folder and zip you hand over
```

**If you are picking this up to change it, read `DEVELOPING.md`.** It has the repo
map, the build and test commands, the architecture in ten lines, and recipes for
adding a screen, an endpoint, a setting or a migration.

---

## Design notes

The backend is Python's standard library: `http.server`, `sqlite3`, `hashlib`, `hmac`,
`ctypes`, `zipfile`, `csv`, `xml.etree`. The frontend is hand-written HTML, CSS and ES
modules. There is no framework, no bundler, no `node_modules`, no CDN and no network
dependency — a school can run this on a ten-year-old PC that has never been online.

The database runs in WAL mode with `foreign_keys=ON`, one long-lived connection guarded
by a re-entrant lock, and `BEGIN IMMEDIATE` for every write. Availability is derived:
`book_copies` is the truth and `books.copies` is a rollup that only one helper function
may write. The audit log is append-only, enforced by two triggers that refuse `UPDATE`
and `DELETE` at the database level rather than trusting the application.

The mark is drawn once, in `web\js\brand.js`, and injected into five places — the
sidebar, the boot splash, the sign-in and activation screens, and the tab icon. It is an
open book with a bookmark, in a violet squircle: four masses, so it still reads at 16 px
in a browser tab. Each instance carries its own gradient id, because two marks on one
page sharing an id would make the second render black. `tools\proof_brand.py` checks
that in a real browser, and `tools\proof_brand_sizes.py` renders it from 192 px down to
16 px so the result can be looked at rather than assumed.
