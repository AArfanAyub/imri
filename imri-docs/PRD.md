# IMRI — Product Requirements Document

| | |
|---|---|
| **Product** | IMRI — offline library & resource-centre management for schools |
| **Version** | 1.0 (build-ready) |
| **Owner** | 许清楚 (Product) |
| **Language** | English (UI shipped in English / Bahasa Melayu / 中文) |
| **Stack direction** | Python 3.11 **stdlib only** + SQLite; hand-built vanilla HTML/CSS/JS UI (no build step, no CDN) served on `127.0.0.1` inside the app window |
| **Reference** | HAZWANI (Python + Tkinter + SQLite) — feature parity baseline |
| **Code name** | `imri` |

**Original request (restated).** The owner has HAZWANI, an offline library system for a school resource centre. They want a new product, IMRI: every HAZWANI feature (1:1), plus more features, plus a much nicer modern UI. 100% offline on Windows, portable folder, no installer, no third-party runtime dependencies, no network calls. Exactly one perpetual activation code: `arfanayub0`.

### Hard constraints (non-negotiable)

| Constraint | Implication for engineering |
|---|---|
| Product name **IMRI** everywhere (window title, About, PDF headers, folders) | No HAZWANI strings survive in the UI except an optional "imported from HAZWANI" note |
| Windows 10/11, single portable folder, no installer, no registry writes | Everything under `data/` next to the executable; user settings in the DB, not AppData |
| Python 3.11 **standard library only** at runtime | No pip packages; no PyInstaller-only imports; zip/csv/sqlite3/hashlib/http.server are fair game |
| UI = vanilla HTML/CSS/JS, no build step, no CDN, no framework | Served from `127.0.0.1` loopback only; all assets local; must render in the bundled WebView2/Edge runtime |
| Zero network calls, zero telemetry, zero update checks | No outbound sockets at runtime; CSP forbids external origins; verified by a firewall test |
| Exactly one perpetual code `arfanayub0` | No keygen, no second code, no trial mode beyond the gate (§7) |
| Default admin `admin` / `admin123` + forced-change nudge | Password hashed (scrypt/PBKDF2); plaintext never stored or logged |
| Target machine baseline | 4 GB RAM, dual-core, 1366×768 screen, mechanical HDD acceptable — first paint ≤2 s |


---

## 1. Product Goal & Non-Goals

### 1.1 Goal

A librarian with no IT support can run the whole resource centre from one window on one school PC: catalogue stock, lend and take back books, chase overdue items, collect fines, print labels and reports, and hand the head teacher a PDF at the end of term — with zero internet, zero installation, and zero training beyond one screen.

### 1.2 Product goals (measurable)

1. **Parity, provably.** Every one of the 10 HAZWANI capability groups is present and reachable in ≤2 clicks from the sidebar. Acceptance: parity checklist in §4 has no unmet P0 row.
2. **Speed on a school PC.** Issue a book in ≤3 keystrokes/scan events; return in ≤2. Catalogue search of 10,000 titles returns first paint in ≤200 ms. Saving a book/member commits in ≤100 ms.
3. **Trustworthy by default.** No data loss path: every destructive action is confirmed or undoable, every mutation is audited, backup/restore is verifiable, and the app runs with the network cable unplugged.

### 1.3 Non-goals (explicitly out of scope)

- No server, cloud sync, multi-PC concurrency, or shared database over a network share.
- No online enrichment (no ISBN lookups, cover downloads, OPAC web portal, email/SMS notices).
- No mobile app, no browser-hosted multi-user deployment.
- No MARC21 full editor / Z39.50 / authority-file exchange — MARC21-**lite** capture only (parity).
- No payments gateway: fines are recorded as cash received, not processed.
- No barcode/RFID hardware drivers beyond graceful NFC tag read/write and keyboard-wedge scanners.
- No key generator, trial key, licence server, or second activation code ships in the product.

---

## 2. Target Users / Personas

**Persona A — Pn. Siti, 41, Resource Centre Coordinator (primary user, non-technical).**
Runs a 6,000-title centre alone, 25 minutes before assembly. Uses the app in bursts of 2–5 minutes between classes. Types with two fingers, hates modal dialogs that lose her work, and needs the overdue list to be one click from the dashboard. Success = she can issue a stack of 12 books to a class without touching the mouse more than twice.

**Persona B — Arfan, 34, teacher-in-charge / system owner (administrator).**
Installs once, activates once, sets fine rates and loan rules, creates accounts for helpers, and takes a backup at the end of each month onto a USB stick. Semi-technical: understands folders and zips, not SQL. Success = he never has to call anyone, and a restore after a corrupted PC takes under 5 minutes.

**Persona C — Hafiz, 16, student library monitor (assistant role).**
Volunteers at the counter during recess. Must be able to issue, return and look up members — and must **not** be able to delete books, change settings, see the licence, or view other users' accounts. Success = the buttons he needs are visible, everything else is not.

*(Secondary, served but not designed for: teachers as borrowers with higher loan limits; the head teacher as a read-only report consumer via exported PDF.)*

---

## 3. User Stories (by module, testable)

**Activation & access**
- As Arfan, I want to paste the one activation code on first launch so that the app unlocks forever without internet.
- As Arfan, I want a wrong code to tell me it is wrong and let me retry, so that a typo is not fatal.
- As Hafiz, I want to log in with my own account so that the audit log records who did what.
- As Arfan, I want to be nudged to change `admin123` so that the default password cannot stay in production.

**Dashboard**
- As Siti, I want six KPI cards at login so that I know today's workload before I open anything.
- As Siti, I want today's due and overdue items listed with the borrower's name so that I can chase them at the counter.
- As Arfan, I want a 12-month loans chart and a collection-mix chart so that I can show usage in a staff meeting.

**Catalogue**
- As Siti, I want the catalogue to open on a search box, not a form, so that I can find a book by typing three letters of the title.
- As Siti, I want to scan an ISBN and have it validated before saving so that bad records never enter the catalogue.
- As Siti, I want each physical copy to have its own barcode so that I can lend copy 2 while copy 1 is out.
- As Arfan, I want to import a 2,000-row CSV, preview the first rows and see exactly which rows will fail, so that a bad file never half-loads.
- As Arfan, I want to export whatever I am currently filtering to CSV/XLSX so that I can hand a list to a teacher.

**Members**
- As Siti, I want to find a member by name, member code or IC number so that a student without a card can still borrow.
- As Siti, I want to see a member's current loans, history and outstanding fine in one panel so that I can answer "what do I owe?" instantly.
- As Hafiz, I want the issue button to refuse a suspended member with a clear reason so that I do not break the rules by accident.

**Circulation**
- As Siti, I want one scan field that accepts a book barcode then a member code, keeping focus, so that issuing is a rhythm not a form.
- As Siti, I want to renew a loan in one click when nobody is waiting so that a good borrower is not penalised.
- As Siti, I want the fine calculated automatically on return and recorded against the loan so that cash and records always agree.
- As Arfan, I want to record a partial fine payment and see the balance so that I can take RM1 now and RM1 next week.
- As Siti, I want reservations to show queue position and to be offered the book when it is returned so that holds are honoured in order.

**Reports**
- As Arfan, I want collection, circulation and membership reports over a date range, exportable to CSV and PDF, so that termly reporting takes minutes.

**Labels**
- As Siti, I want a QR code and a Code 39 barcode on a book label, previewed before printing, so that labels are right the first time.

**Administration**
- As Arfan, I want to create a librarian and an assistant account with different permissions so that helpers cannot delete stock.
- As Arfan, I want to back up to a zip with an integrity manifest and restore it safely so that a dead PC is not a dead library.
- As Arfan, I want to see who changed what and when so that stock discrepancies can be traced.

**Experience**
- As Siti, I want Ctrl+K to jump anywhere or run "Issue book" without hunting the menu so that I stay fast.
- As Siti, I want dark mode in the evening stocktake and my choice remembered so that the screen is comfortable.
- As any user, I want a toast confirming every save so that I know it worked without a dialog to dismiss.

---

## 4. Requirement Pool

Priority: **P0** = must ship (parity or safety-critical) · **P1** = should ship in 1.0 if capacity allows · **P2** = post-1.0.

### 4.1 Activation & licensing

| ID | Module | Requirement | Pri | Acceptance criteria |
|---|---|---|---|---|
| LIC-01 | Licensing | Activation gate blocks the entire app (before login) until activated | P0 | Unactivated launch shows only the activation screen; no route, API call or keyboard shortcut reaches app data |
| LIC-02 | Licensing | Accept exactly `arfanayub0` — case-insensitive, whitespace-trimmed | P0 | `arfanayub0`, `ARFANAYUB0`, ` arfanayub0 ` all unlock; every other string fails |
| LIC-03 | Licensing | Activation is offline, one-time, perpetual; stored locally in `data/license.json` and mirrored to `settings` | P0 | Restart, reboot, folder move/copy all stay activated; no expiry check anywhere in code |
| LIC-04 | Licensing | No other code exists; no key generator, no trial timer, no hidden bypass in the shipped build | P0 | `keygen.py` and any multi-code table absent from the package; grep finds only one literal code constant |
| LIC-05 | Licensing | Wrong code shows inline error and rate-limits repeated attempts | P0 | 5 wrong attempts → 30 s in-session cooldown with countdown; no crash, no data written |
| LIC-06 | Licensing | Licence status visible (licensed to `<school name>`, "Perpetual", activation date) in About + Administration | P0 | Admin → Licence shows the record; code itself is never displayed in full or logged |
| LIC-07 | Licensing | If both licence stores are missing/corrupt, app returns to the gate without data loss | P0 | Deleting `license.json` while DB mirror intact re-activates silently from the mirror |

### 4.2 Dashboard

| ID | Module | Requirement | Pri | Acceptance criteria |
|---|---|---|---|---|
| DSH-01 | Dashboard | Six KPI cards: titles, copies, active members, on loan, overdue, fines outstanding | P0 | Each value correct against DB; card shows a 30-day delta or secondary line; loads ≤400 ms at 10k titles |
| DSH-02 | Dashboard | Today's due + overdue lists (top 5 each) with borrower, days late, fine accrued; click-through to Circulation | P0 | Row opens the matching loan detail; empty state shown when zero |
| DSH-03 | Dashboard | Two charts: loans per month (last 12 months, bar) and collection by material type (donut), inline SVG | P0 | Renders with zero external assets; recolours correctly on theme switch |
| DSH-04 | Dashboard | Quick actions: Issue, Return, Add book, Add member | P0 | Each opens the target screen with focus in the first field |
| DSH-05 | Dashboard | Alerts strip: titles with 0 available copies but open reservations; members over loan limit | P0 | Alert count matches Circulation data; dismiss per item for the session |

### 4.3 Catalogue

| ID | Module | Requirement | Pri | Acceptance criteria |
|---|---|---|---|---|
| CAT-01 | Catalogue | Search-first list: instant filter on title/author/accession/ISBN/subject/call no | P0 | Debounced 150 ms; 200 ms first paint at 10k rows; matches are highlighted |
| CAT-02 | Catalogue | Book detail panel: full metadata, copies, current loans, loan history | P0 | Opens without leaving the list; shows all parity fields including MARC blob viewer |
| CAT-03 | Catalogue | Add/edit modal with field-level validation and inline errors | P0 | Invalid ISBN blocks save with message on the field; unsaved-changes guard on Esc |
| CAT-04 | Catalogue | ISBN-10/13 validation, normalisation, and 10↔13 conversion | P0 | Check-digit correct per ISO 2108; `isbn10` derived and stored on save |
| CAT-05 | Catalogue | DDC and LCC classification capture + call-number validation | P0 | DDC pattern `###(.###)?`; LCC/local call numbers accepted up to 64 chars; both searchable |
| CAT-06 | Catalogue | Subjects and languages as multi-value, reusable controlled vocabulary | P0 | Re-typing an existing subject reuses the row; chips removable before save |
| CAT-07 | Catalogue | Material types: book, textbook, reference, magazine, AV, kit, thesis, other | P0 | Enum enforced; filter chips by type on the list |
| CAT-08 | Catalogue | Per-copy records: copy no, own barcode, status, location, condition | P0 | Copies derived from count on import (N copies auto-created); issue targets a specific copy |
| CAT-09 | Catalogue | CSV/XLSX import with dry-run preview and per-row error report | P0 | Preview shows first 20 rows + counts; committing imports valid rows and lists rejected rows with reason |
| CAT-10 | Catalogue | CSV/XLSX export of the current filtered, sorted view | P0 | Export equals what is on screen (same filters/order), UTF-8 BOM for Excel |
| CAT-11 | Catalogue | MARC21-lite field capture stored as JSON blob, viewable and editable | P0 | Round-trips without data loss; unknown tags preserved |
| CAT-12 | Catalogue | Accession number and barcode auto-generation when blank | P0 | Generated value is Code-39 safe (A–Z, 0–9 only) and unique |
| CAT-13 | Catalogue | Cover image attach from local file, copied into `data/covers/` | P1 | Path traversal/remote URL rejected; missing file falls back to generated placeholder |
| CAT-14 | Catalogue | Duplicate detection on add/import (same ISBN, or title+author similarity) | P1 | Warns with the candidate record; "add anyway" available to admins |
| CAT-15 | Catalogue | Bulk actions: set material type / location / status, delete with confirm | P1 | Applies to selection only; one audit entry per affected record |
| CAT-16 | Catalogue | Saved filter views ("Overdue textbooks", "Malay language") | P2 | Persisted per user; appears in sidebar flyout |

### 4.4 Members

| ID | Module | Requirement | Pri | Acceptance criteria |
|---|---|---|---|---|
| MEM-01 | Members | CRUD for students, teachers, patrons | P0 | Create/edit/delete with confirm; delete blocked when active loans exist |
| MEM-02 | Members | Unique member code; IC/MyKad-aware ID with format validation | P0 | Duplicate code rejected; 12-digit IC accepted (with/without dashes), non-numeric IDs allowed and flagged |
| MEM-03 | Members | Profile fields: form, class, address, phone, email, membership date | P0 | Persisted; empty optional fields allowed |
| MEM-04 | Members | Membership status active / suspended / expired; status gates issuing | P0 | Issue attempt on non-active member returns a specific, translated reason |
| MEM-05 | Members | Member detail: current loans, full borrowing history, fines, reservations, reading list | P0 | Counts reconcile with Circulation; history paginated and sortable |
| MEM-06 | Members | Search by name, member code or IC number | P0 | Partial match, case-insensitive; 200 ms at 5k members |
| MEM-07 | Members | Printable member card with photo placeholder, code, QR and barcode | P1 | Print stylesheet produces card at 85×54 mm; preview matches print |
| MEM-08 | Members | CSV bulk import of members with dry-run preview | P1 | Same contract as CAT-09 |
| MEM-09 | Members | Member photo attach | P2 | Stored under `data/photos/`, path validated |
| MEM-10 | Members | Membership expiry date with automatic status transition | P2 | Expired members blocked at issue with the expiry date in the message |

### 4.5 Circulation & fines

| ID | Module | Requirement | Pri | Acceptance criteria |
|---|---|---|---|---|
| CIR-01 | Circulation | Scan-first issue: barcode → member code, focus retained, Enter advances | P0 | 3 events per loan; unknown barcode shows "not found" without clearing the field |
| CIR-02 | Circulation | Return by barcode/copy, fine auto-calculated from days late × rate | P0 | Fine = `max(0, days_late) × fine_per_day`, rounded to 2 dp; correct across month/year boundaries |
| CIR-03 | Circulation | Renewal extends due date when no open reservation and renewals < max | P0 | Blocked renewals explain why; renewal count increments and is auditable |
| CIR-04 | Circulation | Overdue list with days late, accrued fine, borrower contact; CSV export | P0 | Matches dashboard count exactly; sorted by most overdue |
| CIR-05 | Circulation | Configurable loan_days, max_loans, max_renewals, fine_per_day, hold_days | P0 | Change applies to new loans only; existing loans keep their dates |
| CIR-06 | Circulation | Fine ledger: per-loan fine, payment rows (date, amount, receipt no, actor), outstanding balance | P0 | Partial payments accumulate; balance = fine − paid, never negative; overpayment rejected |
| CIR-07 | Circulation | Fine waiver with mandatory reason, admin-only | P1 | Waived amount leaves the outstanding total; reason stored in audit log |
| CIR-08 | Circulation | Mark copy lost/damaged with replacement charge | P1 | Copy status changes; charge appears in fines; loan closed |
| CIR-09 | Circulation | Reservations with FIFO queue position, auto-promoted on return | P0 | Position recalculates on cancel/fulfil; return of a reserved copy flags it for the first in queue |
| CIR-10 | Circulation | Hold shelf: reservation expires after hold_days and moves to next in queue | P1 | Expiry job runs on app start and on screen open; expired rows visible with reason |
| CIR-11 | Circulation | Keyboard-only counter mode (no mouse needed for issue/return) | P1 | Tab order linear; Ctrl+Enter submits; documented in shortcuts sheet |

### 4.6 Reports

| ID | Module | Requirement | Pri | Acceptance criteria |
|---|---|---|---|---|
| REP-01 | Reports | Collection statistics: titles/copies by material, language, DDC range, status, value | P0 | Totals equal catalogue counts; value sums price × copies |
| REP-02 | Reports | Circulation statistics: issues/returns in range, top 10 titles, top 10 borrowers, avg loan length | P0 | Range filter inclusive; numbers match loan rows |
| REP-03 | Reports | Membership statistics: by form/class/status, new members per month | P0 | Counts match Members module |
| REP-04 | Reports | CSV export of any report | P0 | Header row matches displayed columns; UTF-8 BOM |
| REP-05 | Reports | PDF export of any report, generated by built-in PDF writer (no dependency) | P0 | Valid PDF opens in a standard reader; school name + date + page numbers in header/footer |
| REP-06 | Reports | Monthly trend series with date-range picker and comparison to previous period | P1 | Chart and table agree; range persists per session |
| REP-07 | Reports | Overdue & fines report (outstanding by member) | P0 | Sum equals dashboard fines KPI |
| REP-08 | Reports | Print-optimised report layout | P1 | Print preview shows no navigation, white background, readable at A4 |

### 4.7 Administration

| ID | Module | Requirement | Pri | Acceptance criteria |
|---|---|---|---|---|
| ADM-01 | Admin | Settings: school name, currency, fine/day, loan days, max loans, max renewals, hold days, label prefix | P0 | Validated ranges; saved to `settings`; affects new transactions immediately |
| ADM-02 | Admin | User management with roles admin / librarian / assistant / viewer and enforced permission matrix | P0 | Assistant cannot delete stock, change settings or see licence; enforcement is server-side, not CSS |
| ADM-03 | Admin | Change own password; admin can reset another user's password | P0 | Password policy ≥8 chars; scrypt/PBKDF2 hash only; no plaintext stored or logged |
| ADM-04 | Admin | Forced-password-change nudge while the account still uses `admin123` | P0 | Banner on every login until changed; dismissible per session with "remind me later" |
| ADM-05 | Admin | Offline recovery: a `RESET-ADMIN` file next to the DB resets `admin` to `admin123` and is consumed | P0 | Marker removed after use; audit entry written; works with no network and no other admin |
| ADM-06 | Admin | Audit log viewer: filter by actor/action/date, search detail, CSV export | P0 | Every create/update/delete/issue/return/payment/login writes one row; viewer read-only |
| ADM-07 | Admin | Diagnostics: schema version, row counts, `PRAGMA integrity_check`, orphan scan | P1 | Runs in <2 s on 10k titles; result is plain-language pass/fail |
| ADM-08 | Admin | Auto-lock session after N minutes idle | P2 | Configurable 5–60 min; unlocks with password only, not a new session |

### 4.8 Backup & restore

| ID | Module | Requirement | Pri | Acceptance criteria |
|---|---|---|---|---|
| BAK-01 | Backup | Backup zips the data directory with `MANIFEST.json` (path, size, sha256) and verifies after writing | P0 | Restore of the produced zip succeeds; tampered zip is refused |
| BAK-02 | Backup | Restore validates manifest, takes a pre-restore snapshot, then swaps data | P0 | Failed restore leaves live data untouched; snapshot path shown to user |
| BAK-03 | Backup | Backup list with date, size, label; delete old backups | P1 | Deletion requires confirm; last backup cannot be deleted without warning |
| BAK-04 | Backup | Scheduled local backup on exit and daily, keeping the last 10 | P1 | Runs silently; failures surface as a toast + audit entry, never a crash |

### 4.9 Internationalisation

| ID | Module | Requirement | Pri | Acceptance criteria |
|---|---|---|---|---|
| I18N-01 | i18n | English, Bahasa Melayu, 中文 with full key coverage | P0 | Zero missing keys; completeness check passes for all three |
| I18N-02 | i18n | Live language switch without restart; choice persisted per user | P0 | Switch repaints current screen; no reload of data, no lost form input |
| I18N-03 | i18n | Locale-aware date, number and currency formatting | P0 | `RM 12.50` / `RM12.50` per setting; dates render per locale |
| I18N-04 | i18n | No hard-coded UI strings outside the translation catalogue | P0 | Grep check in CI/test finds no literal UI text in view templates |

### 4.10 Labels, NFC, and experience shell

| ID | Module | Requirement | Pri | Acceptance criteria |
|---|---|---|---|---|
| LBL-01 | Labels | Offline QR code generation for any book copy or member | P0 | Generated matrix scans correctly with a phone camera at 8 px/module |
| LBL-02 | Labels | Offline Code 39 barcode rendering with human-readable caption | P0 | Pattern matches Code 39 spec; characters outside the set are rejected with a message |
| LBL-03 | Labels | Label preview with size/scale/quiet-zone options before export | P0 | Preview equals exported PNG; options persisted |
| LBL-04 | Labels | Batch label export for a selection, plus A4 sheet layout (e.g. 3×8 grid) for printing | P1 | Sheet paginates correctly; cut guides optional |
| NFC-01 | NFC | Detect reader presence; degrade gracefully with an explanatory "NFC unavailable" state | P0 | With no reader, every screen works fully by keyboard; no error dialogs, no crash |
| NFC-02 | NFC | Read tag UID/text into the focused member or book field | P0 | Tag read fills the field and submits the same way a scan does |
| NFC-03 | NFC | Write a short text/UID payload to a blank tag | P1 | Written tag reads back identical; >48 bytes rejected with a clear message |
| UX-01 | Shell | Sidebar + topbar application shell with brand, nav groups, user menu, licence badge | P0 | All modules reachable in ≤2 clicks; current section always indicated |
| UX-02 | Shell | Command palette on Ctrl+K: navigate, run actions, search books and members | P0 | Fuzzy match on commands; Enter executes; Esc closes; works from every screen |
| UX-03 | Shell | Global search in the topbar across books, members, loans, reservations with grouped results | P0 | Results grouped by type with counts; keyboard-navigable; ≤200 ms |
| UX-04 | Shell | Toast notifications for every mutation (success/error) with aria-live | P0 | Toast appears ≤150 ms after commit, auto-dismisses in 4 s, stackable |
| UX-05 | Shell | Dark and light theme, persisted per user, default follows OS preference | P0 | All surfaces, charts, tables and print previews readable in both |
| UX-06 | Shell | Keyboard shortcuts: Ctrl+K, `/` focus search, Alt+1…7 nav, Ctrl+S save, Esc close, `?` shortcut sheet | P0 | Shortcut sheet lists all bindings and is reachable from Help |
| UX-07 | Shell | Pagination (25/50/100/200), column sorting, and filter chips on every list | P0 | State survives navigation within the session; filters compose |
| UX-08 | Shell | Designed empty, loading (skeleton) and error states on every screen | P0 | No blank white screens; every empty state offers the action that fills it |
| UX-09 | Shell | Layout usable at 1024×768 (school projectors/laptops), sidebar collapses to icons | P1 | No horizontal scroll at 1024 px; all actions still reachable |
| UX-10 | Shell | Undo for destructive deletes (5-second soft-delete window) | P2 | Undo restores the record with the same id; expiry commits the delete |

### 4.11 Reading lists (new)

| ID | Module | Requirement | Pri | Acceptance criteria |
|---|---|---|---|---|
| RDL-01 | Reading lists | Per-member wishlist / reading list with add, remove, reorder, note | P1 | Persists per member; visible on member detail |
| RDL-02 | Reading lists | Convert a reading-list item into a reservation in one click | P1 | Reservation appears in the queue at the correct position |
| RDL-03 | Reading lists | Class reading list curated by a teacher, visible to the class | P2 | Read-only for students; exportable to CSV |

**P0 count: 68 · P1: 19 · P2: 6 · Total: 93.** Rationale for a high P0: 1:1 HAZWANI parity plus data-safety rules already consume ~55 P0 rows; the remaining ~13 are the few modern-UI features the owner explicitly asked for (dashboard KPIs/charts, Ctrl+K, global search, renewals, fine payments, queue positions, per-copy, theme, toasts). Everything else — covers, member cards, reading lists, bulk actions, scheduled backups, label sheets — is deliberately P1.

---

## 5. Information Architecture / Navigation

```
┌ Sidebar (240px, collapsible to 64px) ┐
  IMRI  ·  <school name>
  ── OVERVIEW ───────────────
   ◧ Dashboard                Alt+1
  ── LIBRARY ────────────────
   ▤ Catalogue                Alt+2
   ◉ Members                  Alt+3
   ▦ Reading Lists            Alt+4   (P1)
  ── CIRCULATION ────────────
   ⇄ Issue / Return           Alt+5
   ⏱ Overdue & Fines
   ◷ Reservations
  ── INSIGHTS ───────────────
   ▥ Reports                  Alt+6
   ▣ Labels
  ── SYSTEM ─────────────────
   ⚙ Administration           Alt+7
     ├ Licence & About
     ├ Settings
     ├ Users & Roles
     ├ Backup / Restore
     └ Audit Log
  ── footer ─────────────────
   ◐ Theme   ⌘ Language   ◔ Hafiz (assistant)
```

Top bar (56 px, every screen): `Page title / breadcrumb` · `Search  ⌘K` · `Overdue bell (count)` · `Theme toggle` · `Language chip EN|MS|中文` · `Avatar menu`.

Screen inventory (16): Activation gate · Login · Dashboard · Catalogue list · Book detail (drawer) · Book editor (modal) · Import wizard (3 steps) · Members list · Member detail (drawer) · Member editor (modal) · Circulation counter · Overdue & fines · Reservations queue · Reports · Labels · Administration (5 tabs) · Command palette (overlay) · Shortcuts sheet (overlay).

Routing: hash-based (`#/catalogue?q=...&page=2`) so refresh/back preserves state; modals and drawers are not routes but do restore from URL query where cheap.

---

## 6. UI/UX Direction — "modern, slick, quiet"

Design intent: a **calm productivity tool**, not a colourful school app. High information density with generous breathing room, one accent colour, no decoration that does not carry meaning. Every screen answers "what do I do next?" in its top-left region.

### 6.1 Layout & density

- Three-zone shell: fixed sidebar (240 px / 64 px collapsed) · fixed topbar (56 px) · scrollable content.
- Content: max-width 1440 px, centred, 24 px gutters, 16 px grid gap, 8 px base spacing scale (`4/8/12/16/24/32/48`).
- Cards: 20 px padding, 12 px radius, 1 px border, **no** drop shadow at rest; shadow only on hover/drag (0 4px 12px rgba(16,24,40,.08)).
- Tables: 44 px row height (36 px compact toggle), sticky header, no zebra stripes, hover tint, 1 px row divider, right-aligned numerics with tabular figures.
- Modals: 560 px (forms) / 720 px (import, detail), 16 px radius, backdrop `rgba(16,24,40,.45)` with 2 px blur, footer actions right-aligned, primary action rightmost.
- Drawers: 480 px from the right for record detail, 220 ms slide, Esc closes.

### 6.2 Colour (semantic tokens only — no raw hex in components)

| Token | Light | Dark |
|---|---|---|
| `--bg` | `#F6F7F9` | `#0B0F17` |
| `--surface` | `#FFFFFF` | `#121826` |
| `--surface-2` | `#F9FAFB` | `#1A2233` |
| `--border` | `#E4E7EC` | `#243049` |
| `--text` | `#101828` | `#E6EAF2` |
| `--muted` | `#667085` | `#94A3B8` |
| `--primary` | `#4F46E5` (hover `#4338CA`) | `#818CF8` |
| `--success` | `#0E9384` | `#2DD4BF` |
| `--warning` | `#DC6803` | `#FBBF24` |
| `--danger` | `#D92D20` | `#F87171` |

Status pills: available (success 12% bg / success text), on loan (primary 12%), overdue (danger 12%), reserved (warning 12%), lost (muted). Pills: 22 px height, 999 px radius, 12 px text, 6 px horizontal padding, no border.

### 6.3 Typography

- Stack: `"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif` — **no webfont download** (offline rule).
- Scale: 12 (meta/labels) · 13 (table body) · 14 (body/base) · 16 (card title) · 20 (section) · 24 (page title) · 32 (KPI value).
- Weights 400 / 500 / 600 only. Headings 600 with `letter-spacing:-0.01em`; KPI values 600 with `font-variant-numeric: tabular-nums`.
- Line-height 1.5 body, 1.25 headings. Never centre body text; labels above inputs, 12 px, `--muted`.

### 6.4 Motion

- Hover/focus: 120 ms `ease-out`, colour/opacity only.
- Drawer/panel: 220 ms `cubic-bezier(.2,.8,.2,1)` transform + fade.
- Toasts: 160 ms fade + 8 px rise; exit 120 ms.
- Route change: 120 ms content fade, no slide (keeps it feeling instant).
- Skeletons: 1.2 s shimmer in `--surface-2`; no spinners over 300 ms of work — use skeleton then content.
- Respect `prefers-reduced-motion: reduce` → all durations 0 except opacity 80 ms.
- Charts animate once on mount (600 ms draw), never on hover.

### 6.5 Components & interaction rules

- Buttons: 36 px (32 px compact), 8 px radius, 12 px/16 px padding, 13–14 px text. Primary = filled accent; secondary = surface + border; ghost = text only; danger = red outline until confirmed. Icon+label, never icon-only for destructive actions.
- Inputs: 36 px, 1 px `--border`, 8 px radius; focus = 3 px `--primary` at 20% alpha ring + border accent. Errors: red border + 12 px message below, never a tooltip only.
- KPI cards: label (12 px muted) · value (32 px) · delta chip (▲/▼ + %) · optional 40 px sparkline.
- Charts: hand-rolled inline SVG, 2 px lines, 8 px rounded bars, 4-colour categorical palette derived from tokens, axis labels 11 px muted, no gridlines except horizontal 1 px at 8% opacity, tooltip on hover only.
- Tables: sort caret in header, filter chips row above, "N selected" sticky bar with bulk actions, pagination footer "Showing 1–50 of 1,284".
- Toasts: bottom-right, 360 px, 4 s, max 3 stacked, close button, icon by type.
- Command palette: 640 px centred, 12 px radius, blurred backdrop, grouped results (Actions / Books / Members / Go to), `↑↓` navigate, `↵` run, `esc` close, hint footer with key caps.

### 6.6 Empty, loading, error states

- Empty: 24 px line icon in `--muted`, 16 px title, one 13 px explanation, one primary action. Example: *"No books yet — Add your first book, or import a CSV."*
- Loading: skeleton rows matching final layout (never a full-page spinner).
- Error: inline card with what failed, the likely cause, and a retry button; technical detail behind "Details".
- No state may render a blank region larger than 200 px without content.

### 6.7 Print

Dedicated print stylesheet: white background, 12 mm margins, no sidebar/topbar/toasts, school name + report title + generated date header, page numbers footer. Used by Reports, member cards and label sheets.

### 6.8 Accessibility

Contrast ≥4.5:1 for text, ≥3:1 for UI borders; visible focus ring on every interactive element; full keyboard operability; `aria-live="polite"` for toasts and result counts; form labels bound to inputs; no colour-only meaning (status pills carry text).

---

## 7. Activation & Licensing Behaviour (exact)

**Single perpetual code: `arfanayub0`. No other code exists in the product.**

1. **Launch.** App starts a local server on `127.0.0.1` (ephemeral port, bound to loopback only) and opens the app window.
2. **Gate check.** Read activation state: `data/license.json` first, then `settings.license_key`. If neither holds a valid record → activation gate. The gate renders **instead of** the app shell: brand, one-line explanation, a single code field, `Activate` button, language switch, and "Where do I find my code?" help text. No navigation, no API route other than activation, no data queries.
3. **Entry.** User types the code. Input is trimmed, case-folded, and compared in constant time against the single embedded constant. No prefix, no dashes, no variants are accepted.
4. **Success.** Write `data/license.json`:
   `{ "product":"IMRI", "edition":"perpetual", "code_fingerprint": "<sha256 of code, first 16 hex>", "activated_at": "<ISO timestamp>", "school_name": "<from settings>", "schema": 1 }`
   Mirror `license_key = "PERPETUAL:<fingerprint>"` and `license_edition = "perpetual"` into `settings`. Audit: `license_activated`. Then reveal the login screen. Total time from Enter to login ≤300 ms.
5. **Failure.** Inline message *"That activation code is not valid."* Nothing is written. Attempt counter increments; after 5 failures a 30-second cooldown with a live countdown. Counter resets on success or app restart.
6. **Persistence.** No expiry, no phone-home, no hardware binding, no re-activation. Moving or copying the portable folder carries the activation. Deleting `data/` loses activation (accepted; documented in Help).
7. **Login.** Default account `admin` / `admin123`. If the password is still the default, a non-blocking banner appears: *"You are using the default password. Change it now / Remind me later."* Reappears each login until changed.
8. **Recovery.** Placing a file named `RESET-ADMIN` next to the database file resets `admin` to `admin123` on next start (creating the account if missing), writes an audit row, and deletes the marker. This is the only password escape hatch; there is no email reset.
9. **What must not exist in the build:** key generator, multi-code table, trial countdown, "activate later" bypass, licence server URL, telemetry beacon, or the code rendered in plain text anywhere in the UI or logs.
10. **Licence screen.** Administration → Licence & About shows: product, edition **Perpetual**, activation date, school name, app version, schema version, and a `Copy diagnostics` button (no secrets included).

---

## 8. Data Model Summary

SQLite, single file `data/imri.db`, `PRAGMA foreign_keys=ON`, WAL mode, versioned schema with forward migrations. All access through parameterised statements.

| Entity | Key fields | Notes |
|---|---|---|
| `books` | id, accession_no (unique), isbn, isbn10, title, subtitle, authors, publisher, pub_year, edition, language, dewey, classification (`ddc`/`lcc`), call_no, subjects (denorm display), material_type, location, copies (denorm count), price, currency, synopsis, marc_blob (JSON), cover_path, barcode, status, source, created_at, updated_at | Parity field set preserved 1:1 |
| `book_copies` | id, book_id → books, copy_no, barcode (unique), status (available/on_loan/reserved/lost/damaged), location, condition, acquired_at | **New.** Authoritative per-copy state; `books.copies` = count of rows |
| `members` | id, member_code (unique), name, ic_number, form, class_name, address, phone, email, photo_path, membership_date, expiry_date, status, created_at | +expiry_date (P2) |
| `users` | id, username (unique), password_hash (scrypt/PBKDF2), display_name, role, access_level, created_at, must_change_password | +must_change_password |
| `loans` | id, book_id, copy_id, accession_no, member_code, issue_date, due_date, return_date, renewal_count, status (on_loan/returned/lost), fine_amount, paid_amount, created_at | +copy_id, renewal_count |
| `fine_payments` | id, loan_id → loans, amount, paid_at, receipt_no, actor, note | **New.** Partial payments |
| `reservations` | id, book_id, accession_no, member_code, reserved_date, queue_position, status (open/ready/fulfilled/expired/cancelled), ready_at, expires_at | +queue_position, ready/expiry |
| `reading_lists` | id, member_code, book_id, note, sort_order, created_at | **New** (P1) |
| `subjects`, `book_subjects`, `book_languages`, `authorities` | heading/name, type; M2M links | Parity controlled vocabulary |
| `settings` | key, value | school_name, currency, fine_per_day, loan_days, max_loans, max_renewals, hold_days, language, license_key, license_edition, theme |
| `audit_log` | id, ts, actor, action, target, detail | Append-only; viewer reads it |
| `import_batches`, `import_batch_items` | filename, row_count, status; per-row status/message | Parity import audit |
| `schema_meta` | version | Migration gate |

Derived, never stored: available copies, outstanding fine balance, queue position ordering, overdue days. Money stored as REAL rounded to 2 dp (school-scale values; no currency arithmetic risk).

---

## 9. Open Questions

1. **Loan policy defaults** — HAZWANI ships 14 days / 3 books / RM0.20 per day. Should IMRI keep these as the factory defaults, or does this school use different rules? (Proposed: keep, all configurable in Settings.)
2. **Fine cap** — should a fine stop at the item's price, or accrue without limit? (Proposed: accrue without limit, with a manual waiver; flagging because schools differ.)
3. **Per-copy migration** — for existing HAZWANI data, should `copies = N` expand into N copies with auto-generated barcodes (`<accession>-1..N`), or should only one copy be created and the rest be added by hand? (Proposed: auto-expand.)
4. **Class/Form vocabulary** — is the Malaysian `Form 1–6` + class naming fixed, or should form/class be free text? (Proposed: free text with a suggested list.)
5. **Student self-service** — is there any expectation that students see their own loans (e.g. a kiosk mode on the same PC)? Currently out of scope; confirm.
6. **Printers** — are label sheets printed on A4 sticker sheets, or on a dedicated label printer? Affects LBL-04 layout defaults.
