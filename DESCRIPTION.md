# About IMRI

Ready-made descriptions of this repository, for the places that need one. Copy the
block that fits; do not paraphrase the constraints, because the constraints are the
product.

---

## One line

Offline library management for school resource centres — one Windows PC, one SQLite
file, no internet.

## One paragraph

IMRI is a complete, self-contained replacement for a paper borrowing register. It runs
on a single Windows PC with no internet connection, no installer, no database server and
no account to sign up for. The catalogue, members, loans, fines, reservations and reports
all live in one SQLite file you can copy onto a USB stick. It is built for Malaysian
school libraries — Dewey classification, accession numbers, Bahasa Melayu / English /
Chinese interface, `RM` currency and per-day fines.

## For the GitHub About panel

Offline school-library system for Windows. Python standard library only, no installer,
no network, no database server. Catalogue, circulation, fines, reservations, reports and
labels in one SQLite file you can carry on a USB stick.

## Longer, for a flyer or a handover note

IMRI replaces the paper borrowing register in a school resource centre. There is nothing
to install and nothing to configure: copy `IMRI.exe` into a folder, double-click it, and
the library lives in the `data\` folder beside it. Move that folder to another PC and the
whole library moves with it.

It covers what a librarian actually does in a day — catalogue books with real ISBN and
Dewey validation, register students and staff, issue and return at the counter by
scanning a barcode, collect fines with sequential receipts, hold reservations in a FIFO
queue, and print the monthly report for the principal. Labels come out as A4 sheets of
QR codes and Code 39 barcodes. Four roles decide who may do what, every change is written
to an append-only audit log, and backups are verified against a SHA-256 manifest before
anyone is allowed to restore one.

The design constraint is the school, not the demo. A resource centre may have no internet,
no administrator rights and a machine from 2014. So the backend is Python's standard
library with no `pip install`, the frontend is hand-written ES modules with no bundler and
no CDN, and the PDF and QR writers are written from scratch rather than installed. The
server binds to `127.0.0.1` only and never makes an external request — there is no
telemetry and no update check. It works on a PC that has never been online.

180 tests, all standard library. Plus browser proofs that drive a real headless Edge over
the DevTools protocol, because some bugs only exist in the paint or in the shutdown
protocol.

---

## Topics

```
library-management  school-library  offline-first  sqlite  python
vanilla-javascript  no-dependencies  education  dewey-decimal
circulation  pyinstaller  windows  single-file-database
```

## Facts worth quoting correctly

| Claim | The number |
|---|---|
| Tests | 180, across six modules, standard library only |
| Dependencies | Zero at runtime. PyInstaller to build, nothing else. |
| Runtime binary | `IMRI.exe`, ~9.9 MB, PyInstaller one-file |
| Measured scale | 10,000 members, 200,000 books — see `docs/LOAD-TEST.md` |
| Network access | None. Binds `127.0.0.1`, no outbound requests. |
| Languages | English, Bahasa Melayu, Chinese — 913 keys each |
| Platforms | Windows. Source runs anywhere Python 3.11+ runs. |

## What not to say

- **Not "cloud-based", "syncs", or "multi-user".** It is one PC, one file, one
  connection. There is no sync and no remote access, by design.
- **Not "free" or "open source" without checking the licence first.** The repository is
  private and carries a single activation code; neither is a public distribution promise.
- **Not "web app".** It serves HTTP, but only to `127.0.0.1`, to its own window. It is a
  desktop application that happens to speak HTTP.
