# Changelog

All notable changes to IMRI. The version here is the one in
`imri/config.py:APP_VERSION`; there is no second copy to fall out of step.

---

## [1.0.0] — 2026-10-25

First release. Feature parity with the reference HAZWANI desktop application, plus
a browser front end, a licence gate, an audit trail and scale to 200,000 books.

### Added

- **Everything the reference application did**, re-implemented rather than wrapped:
  catalogue with copies and accession numbers, members, issue/return/renew, fines
  with part payment, reservations with a promotion queue, reading lists, five
  reports, label printing, import/export, backup/restore, and a NFC reader.
- **Web front end** — 20 screens, hand-written HTML/CSS/ES modules. No framework,
  no bundler, no `node_modules`, no CDN, no network access at any point.
- **Perpetual activation** — one code, checked server-side before the session
  middleware so it cannot be bypassed from DevTools. `arfanayub0`.
- **Role-based access** — admin, librarian, assistant, viewer, enforced by a
  capability matrix in one place rather than scattered `if role ==` checks.
- **Audit log** — append-only, enforced by two database triggers that refuse
  `UPDATE` and `DELETE`, not by application politeness.
- **Bilingual UI** — English, Bahasa Melayu, Chinese; 913 keys each, switchable
  without a page reload.
- **Barcode and QR labels** — pure-Python encoders (QR v1–10 with GF(256) Reed–
  Solomon and mask penalty scoring, and Code 39), emitted as SVG.
- **PDF reports** — a PDF 1.4 writer built from scratch, so no dependency.
- **Three languages of documentation** in `imri-docs/`: PRD, architecture, task
  breakdown, plus class and sequence diagrams.
- **180 automated tests** across six modules, standard library only.
- **Load harness** — `tools/load_test.py`, measured at 10,000 members and
  200,000 books. Results in `docs/LOAD-TEST.md`.
- **Browser proofs** — a stdlib Chrome DevTools Protocol driver (`tools/cdp.py`)
  used to assert on the live DOM, because some bugs only exist in the paint.

### Changed from the reference implementation

- SQLite in WAL mode with `foreign_keys=ON`, one long-lived connection behind a
  re-entrant lock, `BEGIN IMMEDIATE` for every write.
- Availability is **derived**: `book_copies` is the truth, `books.copies` is a
  rollup that exactly one helper may write.
- Fines are computed from **local** dates. The reference used UTC, which shifted
  every fine by a day in UTC+8.
- Deep pagination actually works — services derive `offset` from `page` instead of
  silently returning page 1.
- `LIKE` wildcards in search terms are escaped, so searching `%` no longer returns
  the whole catalogue.
- Restore **fails closed** on a zip member that escapes the target directory,
  instead of skipping it.
- Import preview detects duplicates *within* the file being imported, not only
  against the database.

### Performance

Measured at 200,000 books / 10,000 members, all seven budgets met:

| Operation | Before | After |
|---|---|---|
| Catalogue search | 1390 ms | 4.4 ms (FTS5 external-content index) |
| Dashboard | 2678 ms | 117 ms |
| Barcode lookup (miss) | 538 ms | 0.1 ms |
| Overdue list | 239 ms | 1.9 ms |
| Member profile | 143 ms | 0.3 ms |

Schema v2: 18 tables, 48 indexes, 2 triggers. Database grew 219 → 351 MB (+60%)
in exchange for the index coverage; the index audit reports zero missing indexes.

### Fixed

- **The application shut itself down while you were reading it.** The server exits
  when the page stops checking in, and the check-in only began after sign-in — so
  the activation and login screens were silent and the server exited at t+120 s,
  underneath the form. Reported as *"network error, when it is supposed to be
  offline"*; it was never the network. The watchdog is now 1800 s, silence before
  the page's first ping is not treated as death, and the ping starts at boot.
  It also no longer stops when the window is minimised.
- `const brand` in `shell.js` shadowed the imported `brand` module and threw
  *"Cannot access 'brand' before initialization"*, which killed the whole boot.
- Import preview accepted a duplicate ISBN that appeared twice in the same file.
- `backup.list()` returned the older of two archives created in the same second.
- The build self-check hardcoded `schema version 1` and failed once the schema
  reached 2.

### Known limitations

Documented rather than hidden, with the reasoning, in `docs/CODE-OVERLAP.md`
and `DEVELOPING.md`:

- 141 of 913 translation keys are unreferenced (15.5%).
- The loan-balance rule has 13 separate implementations. They agree today; that
  is a maintenance risk, not a bug.
- The full-collection report is 2.9 s at 200,000 books. It is not a slow query —
  the payload is the floor. The screen renders a 500-row preview and points at
  the export. A streaming endpoint is the durable fix and is the first v2 item.
- `POST /api/loans/{id}/lost` is implemented and tested but no screen calls it.
- Concurrent write p95 is 229 ms against a 250 ms budget. One connection behind
  one lock is the ceiling.

---

## Version scheme

`MAJOR.MINOR.PATCH`. A schema change bumps `config.SCHEMA_VERSION` and must add a
step to `imri.db.Migrator.STEPS`; a database written by a newer build is refused
with a clear message rather than half-read.
