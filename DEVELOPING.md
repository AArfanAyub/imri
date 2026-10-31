# Developing IMRI

Everything you need to pick this up cold and ship a second version. Written to be
read in order the first time, and used as a lookup afterwards.

If you only read one section, read [Where to change things](#where-to-change-things)
— it is the difference between a day and an hour.

---

## Requirements

| To do this | You need |
|---|---|
| Run from source | **Python 3.11 or newer.** Nothing else. No `pip install`. |
| Run the tests | The same. Standard library only. |
| Build `IMRI.exe` | Python 3.11 + `pip install pyinstaller` (6.x). |
| Run the browser proofs | Python 3.11 + Edge or Chrome installed. |

There is no `requirements.txt` because there are no requirements. That is a
deliberate constraint, not an accident: a school library may have no internet, no
administrator rights, and a machine from 2014. Every dependency is a reason the
software stops working for them.

The one exception is PyInstaller, which is only needed to *build*, never to *run*.
It is not bundled and not required by the tests.

**Verify your interpreter can do what IMRI needs.** One thing bites people: FTS5.
The search index is an FTS5 virtual table, and some Python builds ship a SQLite
without it.

```bat
python -c "import sqlite3; print(sqlite3.sqlite_version); sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(x)'); print('FTS5 ok')"
```

If that fails, use the python.org build.

---

## Repo map

```
main.py              Entry point. Single-instance lock, database prep, server, window, wait loop.
build_exe.py         Builds dist/IMRI.exe with PyInstaller. Verifies the result.
release.py           Cuts a clean source release folder and zip. See "Cutting a release".
run.bat              Double-click launcher for a source run. Finds Python for you.
build.bat            Double-click wrapper around build_exe.py.

imri/                Backend. Standard library only.
  config.py          Paths, defaults, validation rules, formatting. APP_VERSION and SCHEMA_VERSION live here.
  db.py              Schema DDL, migrations, transactions, audit, the rollup invariant.
  errors.py          Error codes and their HTTP status. One list, used by the API and the tests.
  security.py        Password hashing, sessions, RBAC matrix, the licence gate.
  catalog.py         Books, copies, barcodes, accession numbers, subjects.
  members.py         Members, profiles, reading lists.
  circulation.py     Issue, return, renew, fines, reservations, holds.
  reporting.py       Five reports, dashboard, and a PDF 1.4 writer.
  transfer.py        Import, export, backup, restore.
  labels.py          QR and Code 39 encoders, emitted as SVG.
  nfc.py             PC/SC smart-card reader.
  server.py          HTTP server, middleware chain, the app window, the shutdown protocol.
  api/               Route handlers, split by area: core, library, circulation, insights.

web/                 Frontend. No framework, no bundler, no build step.
  index.html         The shell. Inlines the favicon and the pre-JS splash mark.
  css/app.css        One stylesheet, organised in numbered sections. Search for "=== 0".
  js/app.js          Boot sequence, liveness ping, global error handlers.
  js/api.js          The only module that calls fetch. Everything else calls this.
  js/store.js        Client state, settings, theme, session.
  js/router.js       Route table and guards.
  js/shell.js        Sidebar, topbar, command palette, shortcut sheet.
  js/ui.js           Element builders, icons, dialogs, toasts.
  js/brand.js        The logo, drawn once and used in five places.
  js/charts.js       Charts, drawn as SVG.
  js/i18n.js         Translation lookup, interpolation, date formatting.
  js/pages/          20 screens. Files starting with _ are shared partials, not routes.
  js/lang/           en.js, ms.js, zh.js — 913 keys each.

tests/               180 tests. Standard library only. Run with python tests/run_all.py.
tools/               Proof harnesses, the load test, and a stdlib CDP browser driver.
docs/                Design notes, load-test results, screenshots.
imri-docs/           PRD, architecture, task breakdown, class and sequence diagrams.
```

---

## Run it from source

```bat
run.bat
```

or directly:

```bat
python main.py                :: opens an app window
python main.py --no-window    :: prints the URL and stays in the console
python main.py --check        :: verify the build and exit, without serving
python main.py --port 8800    :: different port
python main.py --data-dir D:\libdata
```

`run.bat` prefers `pythonw.exe` so no console flashes up, and keeps `python.exe`
for `--check` / `--no-window` where you want to see output. It looks for Python in
this order: `%IMRI_PYTHON%`, `.venv\Scripts\python.exe`, the `py` launcher, then
whatever is on `PATH`.

The activation code is `arfanayub0`. It is checked server-side and cannot be
skipped from the browser.

---

## Run the tests

```bat
python tests\run_all.py                    :: everything, ~2 minutes
python tests\run_all.py --fast             :: skip the socket suite
python tests\run_all.py --only test_catalog
python tests\run_all.py --quiet
```

| Module | What it pins down |
|---|---|
| `test_db` | Schema shape, migration idempotence, audit triggers, the rollup invariant |
| `test_security` | scrypt round-trip, PBKDF2 fallback, sessions, the RBAC matrix, the licence gate |
| `test_catalog` | ISBN and Dewey validation, copies, rollup discipline, duplicate detection |
| `test_circulation` | The full counter story: issue, late return, part payment, reservation, promotion |
| `test_transfer` | Import dry-run and commit, CSV injection, backup verify, zip-slip, restore |
| `test_api` | Real sockets: licence gate, auth, CSRF, RBAC per role, 413/415, path traversal |

**One thing to know before you add a test.** `tests/helpers.py` sets
`IMRI_DATA_DIR` to a fresh temporary directory for every test, because
`data/license.json` is install-global and would otherwise leak one test's
activation into the next. Use the helper; do not write to the real `data/`.

### Browser proofs

Some bugs only exist in the paint, or in the shutdown protocol. These drive a real
headless Edge over the DevTools protocol and assert on the live DOM:

```bat
python tools\smoke_pages.py        :: all 14 screens paint, zero console errors
python tools\proof_brand.py        :: the logo, in all five places it appears
python tools\proof_liveness.py     :: the server outlives an idle activation screen
```

`tools\cdp.py` is a small stdlib CDP client — no Selenium, no Playwright. If you
extend a proof, `CDP.evaluate()` awaits promises and returns plain JSON.

Two visual aids that assert nothing, because the decision is made by looking:

```bat
python tools\proof_brand_sizes.py  :: renders the mark from 192 px down to 16 px
python tools\brand_candidates.py   :: renders alternative drawings side by side
```

---

## Build the exe

```bat
build.bat
build.bat --portable
build.bat --clean
build.bat --no-verify
```

or `python build_exe.py`. Output is a single `dist\IMRI.exe`, about 9.9 MB.

The build ends with a self-check that runs the *built* exe against a temporary data
directory and asserts the schema version, table/index/trigger counts, the seeded
admin, and that it starts unlicensed. If the self-check fails the build fails —
which is the point, because the alternative is discovering it at the school.

**PyInstaller must be installed in the interpreter you build with.** A common
trap on a machine with several Pythons: the one on `PATH` may not be the one with
PyInstaller. Pass it explicitly:

```bat
set IMRI_PYTHON=C:\Python311\python.exe
build.bat
```

---

## Cutting a release

```bat
python release.py                  :: stage + zip + verify
python release.py --no-verify      :: stage + zip only
python release.py --no-exe         :: source only, no IMRI.exe
python release.py --out D:\builds
```

`release.py` is the only supported way to produce a distributable. It does five
things and refuses to do them quietly:

1. Reads the version from `imri/config.py:APP_VERSION`. There is no second copy of
   the version number to fall out of step.
2. Stages a **clean** tree into `release/IMRI-<version>/`. Build junk, caches,
   `build/`, `dist/`, `data/`, `.tmpdata/`, `reference/` and `.workbuddy-ai/` are
   excluded by rule, not by hand — so the next release cannot accidentally ship
   someone's test database.
3. Copies `dist/IMRI.exe` in as `IMRI.exe`, unless `--no-exe`.
4. Writes `MANIFEST.txt`: every file with its size and SHA-256, so you can prove
   two copies are the same.
5. **Verifies the staged copy, not the working tree**: runs the full test suite
   from inside the staged folder and runs the bundled exe's `--check`. A release
   that cannot pass its own tests is not produced.

Then zips to `release/IMRI-<version>.zip`.

Re-running over an existing folder updates it in place and removes only files
that are no longer part of the release, so a re-release cannot delete anything it
did not write. `--clean` wipes the folder first if that is genuinely what you
want.

### Verify what you are about to hand over

```bat
python tools\verify_release.py release\IMRI-1.0.0.zip
```

This is deliberately a separate program from `release.py` and shares no code with
it. It extracts the archive to a fresh directory and tests what came out:

- every file matches the SHA-256 in `MANIFEST.txt`, and nothing on disk is
  missing from the manifest — so the archive is the archive that was tested;
- the tree contains no `data/`, `build/`, `dist/`, `reference/` or bytecode;
- the full suite passes **from the extracted copy**;
- `main.py` boots and serves the shell, the stylesheet, the brand module and a
  page module over HTTP, and the licence gate is still in force;
- the bundled `IMRI.exe` passes its `--check`.

A packer bug that also existed in its own checker would go unnoticed by
`release.py` alone. That is the whole reason this second program exists.

To bump the version for v2: edit `APP_VERSION` in `imri/config.py`, add a
`CHANGELOG.md` entry, and re-run `release.py`.

---

## Architecture in ten lines

1. `main.py` takes a single-instance lock, prepares the database, binds an HTTP
   server on loopback, opens a chromeless Edge/Chrome window pointed at it, then
   waits for a shutdown reason.
2. Every request walks a fixed middleware chain in `imri/server.py`: static →
   health → licence gate → session → CSRF → RBAC → handler. Order is the security
   model; do not reorder it.
3. Handlers are plain functions registered with `@route(...)` and collected into
   one route table. They return data; the server wraps it in the response envelope.
4. All data access goes through a service class in `imri/`. Services own the SQL;
   handlers own the HTTP. Nothing else touches `sqlite3`.
5. `book_copies` is the truth about availability. `books.copies` is a rollup that
   exactly one function (`refresh_book_rollup`) may write.
6. The audit log is append-only, enforced by two triggers that raise on `UPDATE`
   and `DELETE`. Not by discipline.
7. The frontend is ES modules loaded directly by the browser. `js/api.js` is the
   only module that calls `fetch`; `js/store.js` is the only module that holds
   shared state.
8. A page is a module exporting `render(ctx)` that returns `{element, destroy}`.
   The router owns mounting, teardown and guards.
9. The page tells the server it is alive every 20 s. If it stops, the server exits
   — that is how a closed window stops the process. **Do not shorten that
   watchdog.** It is a backstop for a crashed renderer, not a liveness check, and
   it was once 120 s, which killed the app underneath the activation screen.
10. Dates that matter for fines are **local**, never UTC. `toISODate()` on the
    client and the local-date helpers on the server exist for that reason. Using
    `toISOString()` anywhere near a fine is a bug in UTC+8.

---

## Where to change things

### Add an API endpoint

1. Write a handler in the right module under `imri/api/`, decorated:

   ```python
   @route("GET", "/api/books/{book_id}/history", cap="book.read")
   def book_history(ctx, request, book_id: str) -> dict:
       return ctx.catalog.history(int(book_id))
   ```

   `cap=` is the RBAC capability required. Omit it only if the path is in
   `PUBLIC_PATHS` or `LICENSE_EXEMPT` in `imri/api/__init__.py` — and if you do,
   say why in a comment.

2. If it is public or pre-activation, add the path to `PUBLIC_PATHS` and/or
   `LICENSE_EXEMPT` in `imri/api/__init__.py`.
3. Add the client wrapper to `web/js/api.js`. Nothing else should call `fetch`.
4. Add a test in `tests/test_api.py`.

### Add a screen

1. Create `web/js/pages/thing.js`:

   ```js
   import * as ui from '../ui.js';
   import { t } from '../i18n.js';

   export function render(ctx) {
     const element = ui.el('div', { class: 'page' },
       ui.el('h1', { class: 'page__title', text: t('thing.title') }));
     return { element, destroy() {} };
   }
   export default { render };
   ```

2. Register it in `ROUTES` in `web/js/router.js` — `{ name, pattern, titleKey,
   chrome?, load, cap? }`. Put fixed paths before parameterised ones, or
   `/thing/new` will be swallowed by `/thing/:id`.
3. Add a sidebar entry to `NAV_GROUPS` in `web/js/shell.js` with a `cap`. A user
   without the capability never sees a link that would 403.
4. Add the keys to **all three** of `web/js/lang/{en,ms,zh}.js`, then check:

   ```bat
   python -c "print(open('web/js/lang/en.js',encoding='utf-8').read().count(chr(39)+':'))"
   ```

   or in the browser console: `__IMRI__.checkCompleteness()`. It reports missing
   and extra keys per language. A screen with a missing key renders the raw key
   name, which is how this gets caught late if you skip it.

### Add a setting

1. `DEFAULT_SETTINGS` in `imri/config.py` — the server's value.
2. `SETTING_DEFAULTS` in `web/js/store.js` — the client's fallback for first paint.
3. A control in `web/js/pages/settings.js`.

**These two tables must agree.** They did not once: the client defaulted the
currency to `MYR` and the fine rate to `0.10` while the server used `RM` and
`0.20`, so the first render of a report disagreed with the second.

### Change the schema

1. Add a `_step_<name>(conn)` function in `imri/db.py`. It must be safe to re-run.
2. Add it to `Migrator.STEPS` keyed by the new version number.
3. Bump `SCHEMA_VERSION` in `imri/config.py`.
4. If it is a new table or index, also add it to `DDL` so a fresh database gets it
   without running the step. The step exists for databases that already have data.

Migrations are forward-only and idempotent. A database written by a *newer* build
is refused with a clear message rather than half-read — do not weaken that.

### Change the logo

It lives in `web/js/brand.js`, drawn once and injected in five places. Each
instance gets its own gradient id, because two marks sharing an id makes the
second render black. After editing, run `tools\proof_brand.py`, then
`tools\proof_brand_sizes.py` and look at the result at 16 px before you keep it.

`web/index.html` carries a hand-written copy for the splash and the favicon. It is
replaced at boot, so it only has to be close — but keep it close or you will see a
flicker.

---

## Conventions

- **Standard library only.** A new dependency needs a reason that survives the
  question "what happens when this school has no internet?".
- **No build step on the frontend.** If you find yourself wanting a bundler, the
  answer is usually a smaller module.
- **Comments explain why, not what.** The code says what it does. A comment earns
  its place by recording the thing that is not visible: the ordering constraint,
  the bug that was fixed, the reason the obvious approach was rejected.
- **Errors are codes, not sentences.** `imri/errors.py` owns the codes and their
  HTTP status; the client translates them. That is why the UI is bilingual.
- **Money and dates.** Use the helpers. `Decimal` for money, local dates for
  fines.

---

## v2 backlog

In the order I would do them, with the reasoning, so the next person can disagree
with me on purpose rather than by accident.

1. **Stream the full-collection report.** It is 2.9 s at 200,000 books and the
   cost is the payload, not the query — the screen already renders a 500-row
   preview. A streaming export endpoint is the durable fix. It breaks the frozen
   response contract, which is why it was not done in 1.0.
2. **One implementation of the loan-balance rule.** There are 13. They agree
   today, which is exactly why it is dangerous: 13 copies of a rule is 13 places
   to fix the next bug. `docs/CODE-OVERLAP.md` lists all of them.
3. **Prune the 141 unreferenced translation keys.** Careful: some are reached by
   interpolation (`t(\`circ.status.${x}\`)`), so a naive "not found in source"
   scan will delete live keys. Build the scan, review the list, then delete.
4. **Surface `POST /api/loans/{id}/lost`.** The endpoint is implemented and
   tested; no screen calls it. It needs a confirm dialog and a fine decision.
5. **Concurrent write headroom.** p95 is 229 ms against a 250 ms budget, with one
   connection behind one lock. If a school ever runs a counter and an office
   terminal at once, this is the ceiling to raise.

---

## Troubleshooting the development environment

**"The IMRI service is not responding" in a dev run.**
You are looking at a stopped server. Check the console running `main.py`, or
`data/logs/imri.log`. If the page had been idle for a very long time, the
watchdog fired; that is by design, and 1800 s is already generous.

**`--check` says "expected schema version N, found M".**
`build_exe.py` reads the version from `config.SCHEMA_VERSION`. A mismatch means
the exe was built before the schema change — rebuild.

**Tests fail with a licence error on the first run.**
Something wrote to the real `data/`. `data/license.json` is install-global. Use
`tests/helpers.py`, which redirects `IMRI_DATA_DIR` per test.

**The browser proofs cannot find a browser.**
They look for Edge then Chrome in the standard install locations. Install one, or
edit the `BROWSERS` list at the top of the proof script.

**A screenshot looks doubled or ghosted.**
That is a `Page.captureScreenshot` flake on a long-lived page, not a product bug.
The assertions are the real gate; screenshots are for looking at.
