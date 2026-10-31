# IMRI — Code Overlap Audit

**Scope:** `imri/` (13,074 lines incl. `main.py`), `web/js/` (17,232 lines), `web/css/app.css` (4,095 lines).
**Method:** every claim below is machine-counted (grep with `file:line`, plus throwaway Python parsers for the CSS rule set and the i18n key set). Nothing was eyeballed. Read-only audit; no repository file was modified except the creation of this document.

---

## Verdict

There is real, load-bearing duplication in this codebase — 13 sites that each re-derive a loan's money balance, 6 that each count overdue days, 20 that restate a policy default, a 145-line import validator that shadows the form validator it was supposed to reuse, and a 55-line fine-payment dialog that exists twice with three broken reads between the copies. The CSS, by contrast, is genuinely well factored: only **one** selector is declared twice in 4,095 lines, and there is a complete token layer for colour, radius, space and shadow. So the duplication is **concentrated in business logic and JS plumbing, not in styling**. Two of the findings are not maintenance risk but live defects (a client fallback of `0.1` where the server default is `0.20`; a report date range computed with a UTC slice the codebase's own i18n module warns against). Everything else is drift waiting to happen: the copies agree today, and the cost is that any change to a fine rule, a policy default or a route is an N-place edit with no compiler to catch the place you forgot.

---

## Summary table

| # | Category | Occurrences | Worst offender | Severity |
|---|---|---|---|---|
| F1 | Duplicated business logic — loan balance `max(0, fine−paid−waived)` | **13** (4 Python, 3 JS, 6 SQL) | `imri/reporting.py:787` | **High** |
| F2 | Duplicated business logic — overdue day counting | **6** (2 SQL, 3 Python, 1 JS) | `imri/reporting.py:557` | **High** |
| F3 | Duplicated policy defaults (settings fallbacks) | **20** (14 Python, 6 JS) | `web/js/store.js:280` | **High** |
| F4 | Duplicated validation — import vs form | **2 functions / 145 lines** | `imri/transfer.py:431` | **High** |
| F5 | Duplicated fine roll-up in reports | **3 blocks** | `imri/reporting.py:553` | **High** |
| F12 | Duplicated frontend component — fine payment dialog | **2 copies / 55 lines** | `web/js/pages/counter.js:334` | **High** |
| F6 | Two `due_today` queries, the service copy dead | **2** | `imri/reporting.py:801` | Medium |
| F7 | Duplicated data access — paged-list boilerplate | **7** | `imri/circulation.py:904` | Medium |
| F8 | `_export_path` implemented twice, one sanitises | **2** | `imri/transfer.py:752` | Medium |
| F10 | Duplicated page lifecycle scaffolding | **17 modules** | `web/js/pages/members.js:149` | Medium |
| F11 | Duplicated error toast ×45; the hook that replaces it is unregistered | **45** | `web/js/api.js:90` | Medium |
| F13 | Two inline-field-error widgets | **2 / 69 lines** | `web/js/pages/_member-form.js:151` | Medium |
| F14 | Three parallel route/nav/shortcut tables | **3** | `web/js/shell.js:39` | Medium |
| F15 | Dead i18n keys | **141 keys × 3 files = 423** | `web/js/lang/en.js` | Medium |
| F18 | Design tokens re-typed as magic numbers in JS | **51** | `web/js/pages/_book-drawer.js:109` | Medium |
| F26 | UTC date arithmetic contradicting `i18n.js`'s own rule | **2** | `web/js/pages/reports.js:42` | Medium |
| F29 | Duplicated sort-key allow-lists | **4 pairs / 35 keys** | `imri/api/library.py:21` | Medium |
| F9 | `_where_from_filters` twins | 2 | `imri/members.py:224` | Low |
| F16 | Dead CSS classes | 21 | `web/css/app.css:3584` | Low |
| F17 | Repeated CSS declaration blocks | 32 groups / 351 near-dup pairs | `web/css/app.css:304` | Low |
| F19 | Two byte formatters | 2 | `web/js/pages/backup.js:25` | Low |
| F20 | Two XML escapers | 2 | `imri/transfer.py:334` | Low |
| F21 | `dewey_bucket()` dead while reports inline it | 2 | `imri/catalog.py:209` | Low |
| F22 | Dead Python functions | 16 (15 to delete) | `imri/catalog.py:209` | Low |
| F23 | API endpoints never called by the client | 14 (+4 to keep) | `imri/api/library.py:426` | Low |
| F24 | `ui.js` exports used only inside `ui.js` | 12 | `web/js/ui.js:149` | Low |
| F25 | `debounce` re-implemented inline | 6 | `web/js/pages/catalogue.js:169` | Low |
| F27 | `statusPill` re-implements `pill`'s element | 2 | `web/js/ui.js:912` | Low |
| F28 | Hand-built `cell-stack` / `stat-mini` markup | 6 + 12 | `web/js/pages/overdue.js:241` | Low |

**Totals: 28 findings — 6 High, 10 Medium, 12 Low.**

---

# Part 1 — Findings

## F1 · Loan balance `max(0, fine − paid − waived)` — 13 implementations

**What is duplicated.** One arithmetic rule, expressed three ways in three layers.

Python value form (canonical is `CirculationService.balance`):
- `imri/circulation.py:114-121` — `balance()`, the documented owner
- `imri/circulation.py:1018` — the same expression inlined in `fines()`, on the normalised `row["fine"] / row["paid"] / row["waived"]` keys, right after those keys were themselves derived at `:1016-1017`
- `imri/members.py:476` — `balance = config.money(max(0.0, fine - paid - waived))` inside `member_fines`
- `imri/reporting.py:576` — identical line inside `overdue_fines`

SQL form:
- `imri/circulation.py:992` and `:994` — `(l.fine_amount - l.paid_amount - l.waived_amount) > 0.004` / `<= 0.004`
- `imri/circulation.py:1024-1025` — the four-line `CASE WHEN … > 0 THEN … ELSE 0 END` in `fines()` totals
- `imri/members.py:319-320` — same `CASE` in `_aggregates`
- `imri/members.py:452` — the `> 0.004` clause again
- `imri/reporting.py:787-788` — same `CASE` in `dashboard()`

JavaScript:
- `web/js/pages/counter.js:335`
- `web/js/pages/overdue.js:113`
- `web/js/pages/overdue.js:185`

**Why it matters.** `CirculationService.balance()` clamps at zero and rounds with `config.money`; the SQL forms use `0.004` as the epsilon; the JS forms use `Math.max(0, …)` with no rounding and, in `overdue.js`, prefer `accrued_fine` over `fine_amount` as the minuend. Change the rounding rule (say, bank rounding, or a different epsilon for "paid off") and you must find all 13. Miss one and the fine ledger total on `/overdue` disagrees with the receipt the counter just printed — the failure the module docstring at `circulation.py:8` explicitly says a school "will not forgive". `overdue.js:113` and `:185` are also pure client-side arithmetic on a value the server already sends as `balance` (`circulation.py:923`, `:962`, `:1018`), so they are redundant as well as divergent.

**Recommendation — merge.** Make `CirculationService.balance()` the only Python implementation; replace `circulation.py:1018`, `members.py:476` and `reporting.py:576` with calls to it (all three operate on a loan-shaped row, which is what `balance()` takes). Add one module constant next to it:

```python
BALANCE_SQL = "(CASE WHEN {p}fine_amount - {p}paid_amount - {p}waived_amount > 0.004 " \
              "THEN {p}fine_amount - {p}paid_amount - {p}waived_amount ELSE 0 END)"
```

and use it at the six SQL sites. **Delete** `counter.js:335`, `overdue.js:113`, `overdue.js:185` — read `row.balance` (the server sends it at `circulation.py:923`, `:962`, `:1018`) and fall back to `0` rather than recomputing.

---

## F2 · Overdue day counting — 6 implementations

**What is duplicated.**
- `imri/circulation.py:98-106` — `overdue_days()`, the documented owner (`config.days_between`, local dates, clamped at 0)
- `imri/circulation.py:470` — `days_late = max(0, config.days_between(loan["due_date"], reference))`, i.e. `return_copy` re-derives what its own `overdue_days()` already does
- `imri/reporting.py:557` — `MAX(0, julianday(?) - julianday(l.due_date))` in SQL
- `imri/reporting.py:813` — `CAST(julianday(?) - julianday(l.due_date) AS INTEGER)`, then re-clamped at `:821`
- `imri/reporting.py:822` — `accrued_fine = config.money(overdue_days * fine_per_day)`, duplicating `accrued_fine()` at `circulation.py:108-112`
- `web/js/pages/member.js:144-157` — `dueCell()` re-derives days-until-due with `Math.round((due - today) / 86400000)` instead of `i18n.daysFromToday` (`web/js/i18n.js:522`)

**Why it matters.** `julianday()` operates on the stored ISO string as a UTC instant; `config.days_between` (`imri/config.py:270`) parses local dates. In UTC+8 the two agree only because due dates are date-only strings — the moment anyone stores a timestamp, `reporting.py:557` and `circulation.py:106` start disagreeing by one day, and the dashboard's "overdue" KPI stops matching the `/overdue` list. `member.js:144` additionally ignores `i18n.daysFromToday`'s midnight-normalisation, so a loan due today can render "overdue by 1 day" depending on the hour.

**Recommendation — merge.** Keep `circulation.overdue_days()` and `accrued_fine()`. Replace `circulation.py:470` with `days_late = self.overdue_days(loan, reference)`. Replace the SQL at `reporting.py:557` and `:813` with the same expression constant used elsewhere, or — better — have `overdue_fines()` and `dashboard()` call `CirculationService.overdue_list()` and aggregate in Python; both are already bounded queries. Replace `member.js:144-157` with `i18n.daysFromToday(row.due_date)`.

---

## F3 · Policy defaults restated 20 times, two of them wrong

**What is duplicated.** `config.DEFAULT_SETTINGS` (`imri/config.py:50-70`) is the single source of truth. It is bypassed at 20 sites — 14 in Python, 6 in `store.js`:

| Setting | Canonical | Restated |
|---|---|---|
| `fine_per_day` = `0.20` | `config.py:54` | `circulation.py:64` (`0.20`), `reporting.py:551` (`0.20`), `reporting.py:730` (`0.20`), **`web/js/store.js:280` (`0.1`)** |
| `loan_days` = `14` | `config.py:55` | `circulation.py:65`, `web/js/store.js:257` |
| `max_loans` = `3` | `config.py:56` | `circulation.py:66`, `reporting.py:731`, `web/js/store.js:259` |
| `max_renewals` = `1` | `config.py:57` | `circulation.py:67`, `web/js/store.js:263` |
| `hold_days` = `3` | `config.py:58` | `circulation.py:68`, `circulation.py:1139`, `web/js/store.js:271` |
| `page_size_default` = `50` | `config.py:68` | **`web/js/store.js:251` (`25`)** |
| `currency` = `RM` | `config.py:52` | `catalog.py:977`, `circulation.py:69`, `reporting.py:283` — all spell `"RM"` inline |
| `school_name` | `config.py:51` | `reporting.py:280` and `security.py:913` use `DEFAULT_SETTINGS["school_name"]`; `security.py:767` and `api/library.py:407` use `""` |

**Why it matters.** Two of these are not drift risk but live disagreement: `store.js:280` falls back to `0.1` and `store.js:251` to `25`, while the server seeds `0.20` and `50`. The fallback only fires when the bootstrap payload lacks the key, so today the UI shows the right number — but any screen that renders before `applyBootstrap` (`web/js/store.js:327`) completes, or any test that stubs a partial bootstrap, silently shows a fine of 10 sen a day and 25 rows a page. Separately, four different spellings of the `school_name` default mean a member card printed by `api/library.py:407` shows a blank school name where the PDF from `reporting.py:280` shows "My School Resource Centre".

**Recommendation — merge.** Add `Database.setting(key)` that resolves through `config.DEFAULT_SETTINGS` and replace all 10 Python call sites. In `store.js`, delete the numeric literals: `numericSetting('fine_per_day', 0)` — the server always sends the value, so the fallback should be neutral, not a second guess. If a fallback is genuinely needed, derive it from `state.settings` once in `applyBootstrap` rather than per accessor.

---

## F4 · Import validation re-implements form validation — 145 lines, divergent behaviour

**What is duplicated.**
- `imri/transfer.py:431-517` `_validate_book_row` (87 lines) vs `imri/catalog.py:323-464` `_clean_book_fields` (142 lines). Same rules, restated: title required (`transfer.py:445` / `catalog.py:335`), ISBN validate → normalise → derive ISBN-10 (`:449-457` / `:344-358`), Dewey (`:465` / `:360`), call number (`:470` / `:370`), year (`:475` / `:380`), material type (`:480` / `:390`), price ≥ 0 (`:504` / `:424`), language default `en` (`:495` / `:434`), copies 0–500 (`:498` / `:946`).
- `imri/transfer.py:519-564` `_validate_member_row` (46 lines) vs `imri/members.py:138-221` `_clean_member_fields` (84 lines): name, member type, status, IC, email, membership/expiry dates.

**Why it matters.** The two copies have already diverged in four measurable ways:

1. `transfer.py:516` silently coerces an unknown `status` to `"available"`; `catalog.py:413-422` **rejects** it with `validation_failed`. Same input, opposite outcome.
2. `transfer.py:537` coerces an unknown `member_type` to `"student"`; `members.py:152-161` rejects.
3. `transfer.py:459-463` checks accession uniqueness on the **raw** string; `catalog.py:928` runs it through `sanitize_barcode()` first. So an import row with accession `"imri 000001"` passes preview (no row matches that literal) and is then rejected at commit, because `create_book` sanitises to `IMRI-000001` and finds the existing title.
4. `transfer.py:431-517` validates none of `classification`, `currency`, `barcode`, `cover_path`, `source`, `marc_blob`, and `transfer.py:480` never lowercases before the membership test that `catalog.py:392` performs.

And the two are not alternatives: `commit()` at `transfer.py:690` calls `self.catalog.create_book(...)`, which runs `_clean_book_fields` again. So every imported row is validated **twice**, by two different rule sets. The wizard's promise ("N rows ready to import", `_import-wizard.js:57`) is therefore not a promise the commit path honours.

**Recommendation — merge, with one deliberate seam.** Extract the field rules from `_clean_book_fields` / `_clean_member_fields` into module-level `validate_book_fields(fields)` / `validate_member_fields(fields)` that return `(clean, field_errors)` instead of raising. Then:
- `_clean_book_fields` raises `ImriError` when `field_errors` is non-empty (form behaviour).
- `_validate_book_row` maps the first error to a code (import behaviour).

Keep the one genuine difference — import may *default* missing values where the form *requires* them — as an explicit `defaults=True` argument, not as 87 lines of parallel code. This also fixes divergence 3 automatically, because the accession is sanitised in one place.

---

## F5 · `reporting.overdue_fines` re-derives the fine roll-up

**What is duplicated.**
- `imri/reporting.py:553-566` aggregates `days_late`, `fine`, `paid`, `waived` per member in SQL, then `:575` recomputes `accrued` and `:576` recomputes `balance` — all three of which `CirculationService` already computes (`circulation.py:108`, `:114`, and the `fines()` totals at `:1020-1028`).
- `imri/reporting.py:785-791` `fines_outstanding` restates the `CASE WHEN fine−paid−waived > 0` aggregate that `circulation.fines()` returns as `totals.outstanding` (`circulation.py:1020-1046`).

**Why it matters.** `reporting.py:577` skips a member when `balance <= 0.004 and loans == 0`; `circulation.py:984` filters on `l.fine_amount > 0`. Those are different populations, so "Overdue and fines report → members: 41" and the `/overdue` fines tab count can differ for the same day without either being obviously wrong. `reporting.py:575` also multiplies `days_late` (an integer from SQL) by `fine_per_day` without the `config.money` clamp `accrued_fine()` applies.

**Recommendation — merge.** Have `ReportingService.__init__` take a `CirculationService` (it already takes `db`; `circulation.py:49` shows the pattern) and implement `overdue_fines()` as a projection of `circulation.fines(status="outstanding")` plus `circulation.overdue_list()`. Delete the SQL at `:553-566` and `:785-791`.

---

## F12 · Fine payment dialog exists twice — 55 lines, three broken reads

**What is duplicated.** `web/js/pages/overdue.js:112-169` `payDialog(row)` and `web/js/pages/counter.js:334-389` `openFineDialog(loan, record)` are the same widget: amount input prefilled with the balance, cash/other select, POST to `/api/loans/{id}/payments`, success toast, disable-while-pending, `requestAnimationFrame(() => amountInput.select())`.

**Why it matters — and this one is worse than cosmetic.** The two copies have drifted in three ways that are actual defects:

1. `counter.js:284` reads `result.promoted_reservation`. The server returns `{queue: {promoted, next_member}}` (`circulation.py:543-546`). So the "your reservation is ready" toast at `counter.js:298-300` **never fires**.
2. `counter.js:274` reads `record.copy.current_loan_id`. `CatalogService.list_copies` returns `current_loan: {id, member_id, …}` (`catalog.py:731-738`) and `lookup_barcode` returns the raw copy row (`catalog.py:1311`) — neither has `current_loan_id`. So `loanId` is always `undefined`.
3. Because of (2), `counter.js:278-280` falls through to `{copy_id: copyId}`, but `api/circulation.py:55-56` only reads `loan_id` and `copy_barcode`. `copy_id` is ignored, so the request hits the `loan_or_copy_required` guard at `api/circulation.py:57-63`. The return-by-copy-id path is dead code.

Additionally the two copies disagree on the cancel button label (`common.cancel` at `overdue.js:165` vs `common.close` at `counter.js:384`) and `overdue.js` calls `load()` after payment while `counter.js` does not, so the counter's recent-activity panel keeps a stale fine.

**Recommendation — merge.** One `openPayDialog({loanId, title, amount, onPaid})` in `web/js/pages/_pay-dialog.js` (the `_`-prefixed shared-module convention already used for `_book-form.js`, `_label.js`, `_gate.js`). Both call sites pass `loanId`. Separately fix the server contract: either return `promoted_reservation` from `return_copy` or fix `counter.js:284` to read `result.queue.next_member` — **keep whichever the API documents, but make it one name**.

---

## F6 · `due_today` exists twice; the service copy is dead

**What is duplicated.**
- `imri/circulation.py:1401-1411` — `CirculationService.due_today()`, complete with a `limit` parameter. **Zero callers** (verified repo-wide, including `tests/` and `tools/`).
- `imri/reporting.py:801-809` — the same eight-line SQL, differing only in `LIMIT ?` vs `LIMIT 5`, inlined into `dashboard()`.

**Why it matters.** Two copies of a dashboard panel query, one of which is unreachable. Whoever next changes the due-today panel (add `m.phone`, filter withdrawn copies) will edit the dead one — it is the one with the docstring, the one that looks canonical, and the one an IDE will offer first.

**Recommendation — delete the inline copy.** `dashboard()` calls `self.circulation.due_today(reference, limit=5)`. If `ReportingService` does not hold a circulation service, add one (see F5).

---

## F7 · Paged-list boilerplate repeated 7 times

**What is duplicated.** The same 12-line shape — extract `limit`/`offset`, `SELECT COUNT(*)` + base + where, `SELECT … LIMIT ? OFFSET ?`, `pages = (total + limit - 1) // limit`, and a 7-key `meta` dict — at:
- `imri/catalog.py:838-873` (`list_books`)
- `imri/circulation.py:904-935` (`active_loans`)
- `imri/circulation.py:941-977` (`overdue_list`)
- `imri/circulation.py:997-1040` (`fines`)
- `imri/circulation.py:1364-1398` (`list_reservations`)
- `imri/members.py:267-302` (`list_members`)
- `imri/members.py:416-445` (`member_loans`)

The `pages = (total + limit - 1) // limit if limit else 0` line appears verbatim at `catalog.py:864`, `circulation.py:925`, `:966`, `:1031`, `:1388`, `members.py:292`, `:435`.

**Why it matters.** The `meta` block is the wire contract `parse_page`/`ui.pagination` rely on. `overdue_list` (`circulation.py:969-976`) and `fines` (`:1033-1040`) hardcode `sort`/`order` rather than echoing the request, so a client that sorts the overdue list gets a `meta.sort` that lies. Fixing that in seven places is how it stays unfixed.

**Recommendation — merge into a helper**, not a full query builder:

```python
def paginate(self, select_sql, count_sql, params, page, sort_sql, order, meta_extra=None) -> dict
```

on `Database` (it already owns `scalar`/`query`/`one`). It owns the limit/offset parse, the two queries, the `pages` formula and the `meta` dict — including echoing `sort`/`order` correctly. Each caller then supplies only its SQL and its row post-processing. This is the largest refactor in this list; if only one thing is done, do the `meta` echo, since that is a correctness bug rather than a tidiness one.

---

## F8 · `_export_path` implemented twice; only one sanitises

**What is duplicated.**
- `imri/reporting.py:284-292` — sanitises the stem: `"".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:60]`
- `imri/transfer.py:752-757` — no sanitisation, otherwise identical (`now_utc()` reformatted, `exports_dir()`, `mkdir`, `"%s-%s.%s"`)

**Why it matters.** Export filenames land in `data/exports` and are then fetched by `GET /api/exports/{filename}` (`api/core.py:510`). A stem containing `/`, `:` or a space produces a different — and on Windows, invalid — filename from the catalogue export than from the report export. The download endpoint has its own guard (`api/core.py:513-525`), so this is a filename-mangling bug rather than a traversal, but the two paths disagree about what a legal filename is.

**Recommendation — merge.** Move the sanitising version to `config.export_path(stem, suffix)` (it already lives in `config` for `exports_dir()` at `imri/config.py`) and have both services call it.

---

## F9 · `_where_from_filters` twins

**What is duplicated.** `imri/catalog.py:787-831` and `imri/members.py:224-257`. Same structure: `q` → `LIKE` across N columns with `params.extend([like] * N)`, then a `for column, key in (...)` loop appending `"%s = ?"`, then optional boolean clauses.

**Why it matters.** Little. The column lists genuinely differ (11 book columns vs 6 member columns) and the boolean clauses differ (`available_only` vs `has_overdue`). The only shared bug surface is the `if value and value.lower() != "all"` idiom, which is correct in both.

**Recommendation — keep**, with one optional extraction: the `for column, key in (...)` equality loop is 6 lines and identical in intent; pulling it into a `_eq_clauses(pairs, filters)` helper would remove ~10 lines with no behavioural risk. Not worth a dedicated refactor on its own.

---

## F10 · 17 page modules re-implement the same lifecycle

**What is duplicated.** Counted exactly:

| Pattern | Sites |
|---|---|
| `let destroyed = false;` | 16 — `audit.js:30`, `backup.js:43`, `catalogue.js:123`, `counter.js:35`, `dashboard.js:125`, `labels.js:30`, `license.js:26`, `member.js:30`, `members.js:39`, `overdue.js:35`, `reading-lists.js:26`, `reports.js:60`, `reservations.js:26`, `settings.js:45`, `users.js:29`, `_book-drawer.js:107` |
| `async function load() {` | 13 — `audit.js:87`, `backup.js:64`, `catalogue.js:703`, `dashboard.js:387`, `labels.js:124`, `license.js:37`, `member.js:49`, `members.js:149`, `overdue.js:75`, `reports.js:127`, `reservations.js:63`, `settings.js:81`, `users.js:49` |
| `let loading = true` + `loading = true` in `load()` | 13 |
| `document.addEventListener('imri:lang', onLang)` + matching `removeEventListener` in `destroy` | 17 — including `_gate.js:57` and `_stub.js:64` |
| sequence guard `const mine = ++seq` | 3 only (`members.js:150`, `catalogue.js:704`, `_book-drawer.js`) — the other 10 can race |

**Why it matters.** The three modules that added a `seq` guard did so because the unguarded ten can render a stale response over a fresh one when a filter is changed quickly. That is the concrete cost: a 20-line pattern copied 13 times, with the correctness fix applied in 3 of them. `overdue.js:75-98` is the clearest example — no sequence guard, so switching tabs mid-flight can paint the fines table with overdue rows.

**Recommendation — merge into `ui.listPage()`.** Something like:

```js
ui.mountPage({ ctx, key, load, paint })
```

owning `destroyed`, `loading`, `error`, `seq`, the `imri:lang` listener, the `destroy()` teardown, and the debounce (see F25). Honest caveat: this touches all 13 list modules and is the second-largest item here. It is still worth doing, because the alternative is remembering to re-add a sequence guard by hand every time a screen gains a filter — which is precisely what did not happen.

---

## F11 · Error toast written 45 times; the hook that would replace it is never wired

**What is duplicated.** `ui.toast.error(t('toast.error'), ui.formatError(failure).message)` appears **45 times** across `web/js/pages/` and `web/js/shell.js` (highest: `_book-drawer.js` 4, `backup.js` 4, `counter.js` 6, `member.js` 6, `reading-lists.js` 6).

Meanwhile `web/js/api.js` already defines the hook for exactly this: `handlers.onError` (`api.js:90`), settable through `setHandlers` (`api.js:113`), dispatched on every rejection (`api.js:273`). `web/js/app.js:135-146` registers `onUnauthorized` and `onLicenseRequired` and **not** `onError`. So `api.js:273` dispatches to `null` on every failed request.

**Why it matters.** 45 copies of a two-line error policy, and a purpose-built single point of change sitting unused. Changing the error surface (add a "copy details" action, throttle duplicate toasts, log to a ring buffer) is a 45-file edit today and a 1-line edit after `onError` is registered. The 45 sites also disagree with each other: several toast *and* render an inline `ui.errorState`, producing two messages for one failure (`overdue.js:108` and `:326`; `counter.js:266-267` toasts twice for one exception).

**Recommendation — merge.** Register `onError` in `app.js:135` to show the toast, then delete the duplicated `toast.error(...)` line at the 45 sites. Where a screen wants an inline error instead of a toast, it already has `ui.errorState` (`ui.js:1887`) and should return `true` from the handler to claim the error. If a middle ground is preferred, add `ui.fail(error)` as a one-line alias and mechanically replace the 45 sites — but the handler is the design that is already half-built.

---

## F13 · Two inline-field-error widgets

**What is duplicated.** `web/js/pages/_book-form.js:355-392` (`setFieldError` + `clearErrors` + `applyServerErrors`) and `web/js/pages/_member-form.js:151-181` (the same three, minus `clearErrors`). Both create a `p.field__error[role=alert]` with an `alertCircle` icon, toggle `field--error` on the wrapper, set `aria-invalid`, and focus the first offending control.

**Why it matters.** The accessibility behaviour has already diverged:
- `_book-form.js:361` **removes** `aria-invalid`; `_member-form.js:156` sets it to the string `"false"`. Screen readers treat those differently.
- `_book-form.js:375` reuses the node's existing `id`; `_member-form.js:162` invents `mem-${key}-error`. Two id conventions for one pattern.
- `_book-form.js:379-382` has `clearErrors()`; `_member-form.js` has no equivalent, so a member form that fails twice can leave a stale message on a field the user has since fixed.

**Recommendation — merge.** `ui.fieldError(controls, { idPrefix })` in `ui.js` returning `{ set, clear, apply }`. `ui.field()` (`ui.js:556`) already owns the wrapper markup, so the helper belongs beside it. Both forms then shrink by ~40 lines and inherit the same a11y behaviour by construction.

---

## F14 · Three parallel route / nav / shortcut tables

**What is duplicated.** One page list, three times:
- `web/js/router.js:31-51` — `ROUTES`, 20 entries, each `{name, pattern, titleKey, cap, load}`
- `web/js/shell.js:39-77` — `NAV_GROUPS`, 18 items, each `{path, name, labelKey, icon, cap}`
- `web/js/shell.js:910-924` — `GO_MAP`, 13 entries, `{key: path}`

**Why it matters.** Adding a screen is a 3-file edit, and the two tables have already drifted on the *title*:
- catalogue: `titleKey: 'cat.title'` (`router.js:39`) vs `labelKey: 'nav.catalogue'` (`shell.js:44`)
- admin: `titleKey: 'adm.users.title'` (`router.js:49`) vs `labelKey: 'nav.users'` (`shell.js:58`)

So the browser tab title and the sidebar label for the same screen come from different i18n keys — which is why `nav.admin` exists in all three language files and is referenced nowhere (F15). `GO_MAP` additionally omits `/reading-lists` and `/reservations` is mapped to `v`, an arbitrary mnemonic with no nav entry to derive it from.

**Recommendation — merge into `ROUTES`.** Add `nav: { group, icon, labelKey, goKey }` to each route and derive both `NAV_GROUPS` and `GO_MAP` at module load in `shell.js`. `ROUTES` is already `Object.freeze`d and already exported (`router.js:419`). This removes two hand-maintained tables and makes the catalogue/admin title inconsistency impossible.

---

## F15 · 141 dead i18n keys (423 definitions across three languages)

**What is duplicated.** `web/js/lang/en.js` defines 912 keys; `ms.js` and `zh.js` define the same 912 (parity is perfect — no missing translations). Of those 912, **141 are never referenced** by any string in `web/js/` outside `lang/` and are not reachable through a dynamic prefix.

Measured by namespace: `common` 23, `cat` 21, `circ` 16, `shell` 15, `adm` 10, `dash` 9, `mem` 8, `activate` 7, `auth` 5, `validate` 5, `print` 5, `app` 3, `rep` 3, `label` 3, `toast` 3, `confirm` 2, `nav` 1, `res` 1, `palette` 1.

Representative dead keys (all verified absent from every `.js`/`.html` outside `lang/`): `common.ok`, `common.today`, `common.yesterday`, `common.enabled`, `common.disabled`, `shell.quickAdd`, `shell.readOnly`, `shell.saved`, `dash.title`, `dash.subtitle`, `dash.alert.overdue` … `dash.alert.rollup` (6), `cat.copies.add`, `cat.copies.barcode`, `cat.duplicates.title`, `circ.lost`, `circ.receipt.title`, `circ.heldFor`, `label.qr`, `adm.tab.users`, `confirm.deleteTitle`, `validate.email`, `nav.admin`, and all five `print.*` keys.

Dynamic prefixes correctly excluded from the count: `status.`, `err.`, `type.`, `role.`, `memberType.`, `condition.`, `theme.`, `lang.`, `set.` — those keys are reachable through template-literal lookups such as `` t(`status.${x}`) `` and friends.

**Why it matters.** 423 lines to maintain across three files, and `i18n.js:562 checkCompleteness()` — the module's own guard — only checks that the three files agree with each other, not that any key is used. So the drift is invisible to the existing tooling: every key is "complete" and 141 of them are dead. The `print.*` group is a coherent example — a whole server-side-print-header vocabulary (`print.school`, `print.generated`, `print.page`, `print.of`, `print.report`) whose CSS counterpart is also dead (F16).

**Recommendation — delete**, and extend `checkCompleteness()` (`i18n.js:562`) to also report keys with no reference in the source tree. Deleting 141 keys is mechanical and safe precisely because nothing reads them; the risk is zero and the payoff is that the next "is this key used?" question takes one grep instead of a script.

---

## F16 · 21 dead CSS classes

**What is duplicated.** Selectors defined in `web/css/app.css` and emitted by no `.js`/`.html` file (verified per class; dynamic families such as `btn--${variant}`, `pill--${tone}`, `toast--${tone}`, `stat__delta--${dir}` were excluded because they *are* reachable):

`grid--3` `:423`, `sb__name` `:468`, `sb__count` `:572`, `tb__crumb` `:671`, `tbl__nowrap` `:1006`, `tbl__primary` `:1010`, `tbl__sub` `:1014`, `tbl__mono` `:1021`, `tbl__actions` `:1027`, `tbl__selbar` `:1048`, `tbl__selbar .btn` `:1060`, `chip--clear` `:1175`, `modal__foot--split` `:1275`, `sk--title` `:1503`, `u-right` `:2664`, `u-hidden` `:2686`, `is-busy` `:2690`, `gate__row` `:2813`, `print-only` `:3580`, `print-head` `:3584`, `print-head__school` `:3594`, `print-head__title` `:3599`.

**Why it matters.** `.tbl__actions` is referenced a second time at `app.css:3527` inside `@media print` (`display: none !important`) — so a future reader will reasonably conclude it is live, add the class to a table row, and get an invisible column in print. `.print-head*` and `.print-only` are a complete print-header design that the browser print path (`reports.js:79 window.print()`) never emits, because the real header is drawn server-side by `reporting.PdfWriter` (`reporting.py:178-200`). `.u-hidden` and `.u-right` are utilities nobody adopted — `ui.js` sets `hidden` and alignment via props instead.

**Recommendation — delete.** Removing them also removes the false lead in the print media query. Keep `.sb__count` only if the collapsed-sidebar rule at `app.css:614` is meant to be extended — as written it selects an element that does not exist.

---

## F17 · Repeated CSS declaration blocks — real but mostly cosmetic

**What is duplicated.** The stylesheet is 4,095 lines / 552 rules and is **well factored**: exactly **one** selector is declared twice (`.u-grow` at `:2639` and `:3610`, both `flex`, benign). The duplication is at the *pattern* level: 32 groups of byte-identical declaration blocks and 351 pairs with Jaccard ≥ 0.8.

The genuinely mergeable identical groups:
- `color: var(--muted); font-size: var(--text-sm)` × **8** — `.boot__tagline:304`, `.page__sub:379`, `.gate__sub:2802`, `.dash__date:2904`, `.book__sub, .book__authors:3314`, `.print-head__title:3599`, `.savebar__text:3995`, `.licence-hero__text:4074`
- `color: var(--muted); font-size: var(--text-xs)` × **8** — `.card__sub:864`, `.field__hint:1659`, `.errstate details:2400`, `.gate__tagline:2784`, `.import-summary__label:3285`, `.pickrow__sub:3783`, `.picked__sub:3826`, `.queue-row__meta:3960`
- `font-size: var(--text-md); font-weight: 600; letter-spacing: -0.01em` × **3** — `.section__title:404`, `.card__title:858`, `.queue-card__title:3911`
- `background: color-mix(in srgb, var(--text) 6%, transparent); color: var(--text)` × **3** — `.icon-btn:hover:742`, `.btn--ghost:hover:1580`, `.pager__btn:hover:1987`
- `display:flex; flex-direction:column; gap:1px; min-width:0` × **4** — `.cat__title:3142`, `.book__field:3326`, `.copy-row__main:3369`, `.cell-stack:3660`

The 351 near-duplicate pairs are dominated by two coincidental families that should **not** be merged: every `display:flex; flex-direction:column` container resembles every other one (`.section@391` vs `.u-col@2629` vs `.book__head@3302` — differing only in `gap`), and every `@keyframes` block resembles every other (`.modal-in@1221` vs `.toast-in@1388` vs `.menu-in@1894` — differing only in `transform`). Merging those would couple unrelated components.

**Why it matters.** Mild. The real cost is that the two 8-member "muted small text" families use `--text-sm` in one and `--text-xs` in the other, so a reader cannot tell whether the difference is intentional. And the `--text-md / 600 / -0.01em` heading recipe exists as three literal copies rather than one token, so a type-scale change is a 3-site edit.

**Recommendation — keep the flex and keyframe clusters (justified); merge the two text families.** Add `.u-sub` (muted, `--text-sm`) and `.u-hint` (muted, `--text-xs`) beside the existing utilities at `app.css:2619-2700`, and replace the 16 declarations with the class. Add `--type-heading-sm` as a token for the 3-copy heading recipe. Do not attempt the 351 pairs — a review that recommends merging everything is not useful.

---

## F18 · Design tokens re-typed as magic numbers in JS — 51 sites

**What is duplicated.** `.u-col` (`app.css:2629-2633`) sets `gap: var(--space-2)` (8px). It is then overridden inline in **51 places** across 18 modules:

| Inline value | Count | On the token scale? |
|---|---|---|
| `16px` | 18 | `--space-4` |
| `12px` | 13 | `--space-3` |
| `8px` | 5 | `--space-2` (redundant) |
| `14px` | 4 | **no token exists** |
| `6px` | 4 | **no token exists** |
| `4px` | 5 | `--space-1` |
| `20px` | 1 | **no token exists** (`--space-5` is 24) |
| `2px` | 1 | **no token exists** |

Worst files: `_book-drawer.js` 6 (`:109`, `:227`, `:334`, `:361`, `:456`, `:464`), `member.js` 6, `reading-lists.js` 5.

Separately, `app.css` itself bypasses its own scale for `gap` in 33 rules against 102 token uses — `gap: 2px` ×12, `gap: 1px` ×7, `gap: 4px` ×6, `gap: 6px` ×4, `gap: 5px` ×2, `gap: 3px` ×2. The scale bottoms out at `--space-1: 4px` (`app.css:75`), so the 21 sub-4px sites have no token to use.

**Why it matters.** The token layer is the one thing that makes this stylesheet maintainable, and 51 JS sites and 33 CSS rules route around it. `14px` is not even on the 4px grid, so the same visual rhythm is expressed as 12px in one panel and 14px in the next (`overdue.js:158` vs `:203`, both dialog bodies). A density change (compact mode, larger touch targets) would have to find all 84 sites.

**Recommendation — merge.** Add three modifiers beside `.u-col` — `.u-col--lg` (16px), `.u-col--md` (12px), `.u-col--sm` (4px) — and replace the 41 sites that already sit on the scale. Fold `14px` into `12px` or `16px` (this is a visual change; check the two `overdue.js` dialogs side by side). Add `--space-hair: 2px` to the token block for the sub-4px CSS cases. The remaining `20px`/`6px` sites should be inspected individually rather than force-fitted.

---

## F19 · Two byte formatters

**What is duplicated.** `web/js/pages/backup.js:25-30` `sizeLabel(bytes)` vs `web/js/i18n.js:458-472` `fmtBytes(value)`.

**Why it matters.** `sizeLabel` stops at MB (a 1.4 GB backup renders as `"1433.6 MB"`), uses `.toFixed(1)` rather than the locale-aware `fmtNum`, and is not translated. `fmtBytes` handles GB/TB, uses `fmtNum`, and returns `t('common.na')` for junk input. So the backup screen shows a different number, formatted differently, from every other size in the product.

**Recommendation — delete `sizeLabel`**, import `fmtBytes` from `../i18n.js`.

---

## F20 · Two XML escapers

**What is duplicated.** `imri/labels.py:718-728` `_xml_escape()` and `imri/transfer.py:334-341` `escape()` (a closure inside `write_xlsx`). Both replace `&`, `<`, `>` and `"`.

**Why it matters.** `transfer.py:334-341` omits the `'` → `&apos;` replacement that `labels.py:724` performs. Today no XLSX cell contains a literal apostrophe that matters (single quotes are legal in XML text nodes), so this is latent rather than broken — but it means the two "escape for XML" rules are not the same rule, and the one in `transfer.py` is invisible to anyone grepping for a helper.

**Recommendation — merge.** `config.xml_escape()`, used by both. Low urgency; do it when either file is next touched.

---

## F21 · `dewey_bucket()` exists; reports reimplement it inline

**What is duplicated.**
- `imri/catalog.py:209-217` — `dewey_bucket(code)`, documented, correct (`"823.914"` → `"823"`). **Zero callers repo-wide.**
- `imri/reporting.py:364-367` — the same logic inline in `collection()`:
  ```python
  bucket = row["dewey"].split(".")[0] if row["dewey"] else "000"
  bucket = ("%03d" % int(bucket)) if str(bucket).isdigit() else "000"
  ```

**Why it matters.** They differ for empty input: `dewey_bucket("")` returns `""` while the inline version returns `"000"`. So the report's Dewey breakdown always has a `000` bucket even when no title is unclassified, and `dewey_bucket` — the function with the docstring and the unit test — is the one nobody calls. Anyone fixing Dewey bucketing will fix the dead one.

**Recommendation — merge.** `from .catalog import dewey_bucket` and use `dewey_bucket(row["dewey"]) or "000"` at `reporting.py:365`, preserving today's report output exactly while making the rule single-sourced.

---

## F22 · 16 Python functions with no call site (15 to delete, 1 to adopt)

Verified with zero references across `imri/`, `main.py`, `tests/`, `tools/` and `build_exe.py` (route handlers excluded — they are registered through the `@route` decorator and are reachable by the dispatcher).

| Symbol | Location |
|---|---|
| `dewey_bucket` | `imri/catalog.py:209` |
| `CatalogService.generate_barcode` | `imri/catalog.py:496` |
| `CatalogService.set_subjects` | `imri/catalog.py:1417` (only `_set_subjects_conn` is used, `:1027`) |
| `CatalogService.set_languages` | `imri/catalog.py:1446` (only `_set_languages_conn` is used, `:1031`, `:1033`) |
| `CirculationService.due_today` | `imri/circulation.py:1401` (see F6) |
| `money_eq` | `imri/config.py:285` |
| `Schema.add_column` | `imri/db.py:408` |
| `Database.vacuum` | `imri/db.py:740` |
| `_month_key` | `imri/reporting.py:28` |
| `caps_of` | `imri/security.py:134` |
| `iter_caps` | `imri/security.py:1024` |
| `Server.wait_for_close` | `imri/server.py:176` |
| `free_port_hint` | `imri/server.py:688` |
| `read_table` | `imri/transfer.py:294` |
| `BackupService.prune` | `imri/transfer.py:1132` |
| `decode_json` | `imri/api/__init__.py:390` |

**Why it matters.** Two are load-bearing traps. `CatalogService.set_subjects` / `set_languages` are the *public* entry points — they open their own transaction and call `_require_book` — while the private `_conn` variants are what `create_book` actually uses. A future caller reaching for the obvious public method gets a different code path (one transaction, a book-existence check) than the internal one, with no test coverage on the public one. `money_eq` (`config.py:285`) is the float-equality helper that should be used instead of the `0.004` literals in F1 — it exists and is unused.

**Recommendation — delete** the 13 that are pure dead weight; **keep `money_eq`** and use it to replace the `0.004` literals (F1); **keep `add_column` and `vacuum`** if the migration/backup roadmap needs them, but then add a call site — dead code that is "for later" is still dead code.

---

## F23 · 14 API endpoints the client never calls

Of 96 `@route` declarations, 18 have no matching call in `web/js/` (matching accounts for template-literal paths such as `` `/api/reports/${kind}` `` and `` `/api/loans/${id}/payments` ``).

**Ops endpoints — keep (justified):** `GET /api/health` (`api/core.py:56`), `GET /api/version` (`api/core.py:654`), `GET /api/auth/session` (`api/core.py:155`), `POST /api/system/reveal` (`api/core.py:551`). These serve the desktop shell, monitoring and support, not the SPA.

**Genuinely uncalled — the client uses a different endpoint for the same job:**

| Endpoint | Location | What the client does instead |
|---|---|---|
| `GET /api/circulation/active` | `api/circulation.py:85` | `GET /api/circulation/overdue` |
| `GET /api/loans/{id}` | `api/circulation.py:153` | `GET /api/members/{id}` (profile embeds loans) |
| `POST /api/loans/{id}/lost` | `api/circulation.py:190` | nothing — no "mark lost" UI at all |
| `GET /api/labels/qr.svg` | `api/insights.py:100` | labels come from `/api/labels/book/{copy_id}.svg` |
| `GET /api/labels/code39.svg` | `api/insights.py:117` | same |
| `PUT /api/books/{id}/marc` | `api/library.py:158` | MARC is read-only in `_book-drawer.js:333-336` |
| `GET /api/subjects` | `api/library.py:220` | `_book-form.js:188` takes free-text subjects |
| `POST /api/members/import/preview` | `api/library.py:257` | only the books importer is wired |
| `POST /api/members/import/commit` | `api/library.py:264` | same |
| `GET /api/members/export` | `api/library.py:284` | no member export button |
| `GET /api/members/{id}/loans` | `api/library.py:331` | profile endpoint already embeds them |
| `GET /api/members/{id}/fines` | `api/library.py:340` | same |
| `GET /api/nfc/status` | `api/library.py:426` | no NFC UI |
| `POST /api/nfc/read` | `api/library.py:432` | no NFC UI |
| `POST /api/nfc/write` | `api/library.py:444` | no NFC UI |

**Why it matters.** Each is a handler, a capability check, an audit path and — in the NFC case — a whole 391-line module (`imri/nfc.py`) with a ctypes bridge that must be maintained and cannot be exercised from the product. `POST /api/loans/{id}/lost` is the clearest loss: `CirculationService.mark_lost` (`circulation.py:634-684`) implements a complete business rule (charge the borrower, write off the copy, refresh the rollup) that no screen can reach, so a librarian cannot record a lost book.

**Recommendation — split.** Wire `POST /api/loans/{id}/lost` into the book drawer (it is a working, tested rule with no UI). Wire the member import (`/api/members/import/*`) since `TransferService.preview_members` / `commit_members` already exist (`transfer.py:653`, `:733`). **Delete** the two `/api/labels/*.svg` aliases, `GET /api/subjects`, `GET /api/members/{id}/loans`, `GET /api/members/{id}/fines`, `GET /api/loans/{id}` — each duplicates a live endpoint's data. **Keep the NFC endpoints** and `imri/nfc.py` only if the hardware roadmap is real; otherwise delete both, because an unreachable ctypes bridge is the most expensive kind of dead code.

---

## F24 · 12 `ui.js` exports used only inside `ui.js`

`svgEl:149`, `frag:167`, `qs:183`, `qsa:188`, `cx:197`, `hasIcon:339`, `focusFirst:434`, `linkButton:514`, `inputGroup:756`, `section:861`, `closeMenu:1382`, `attachTooltip:1995`.

All 12 have internal call sites (`cx` 47, `section` 6, `svgEl` 5, `focusFirst` 4, …) and **zero** external ones.

**Why it matters.** Not duplication — surface area. Each exported name is a promise to the 26 page modules. `cx` in particular invites a page to build class strings by hand instead of using the component factories, which is how F18's inline styles accumulated.

**Recommendation — keep the exports but document the intent**, or demote the 9 that no page could reasonably want (`qs`, `qsa`, `frag`, `svgEl`, `hasIcon`, `focusFirst`, `closeMenu`, `attachTooltip`, `inputGroup`) to module-private. `cx` and `section` are worth keeping public — they are the two a new page genuinely needs.

---

## F25 · `debounce` re-implemented 6 times

| Site | Pattern |
|---|---|
| `web/js/pages/members.js:70-74` | `clearTimeout(searchTimer); setTimeout(…, 150)` |
| `web/js/pages/catalogue.js:169-176` | same, 150 ms |
| `web/js/pages/reading-lists.js:168-169` | same, 180 ms |
| `web/js/pages/audit.js:50-66` | two independent timers (`:51`, `:65`) |
| `web/js/pages/labels.js:58-60` | same |
| `web/js/shell.js:856` | same, 160 ms |

Plus `web/js/shell.js:991` (`pendingGoTimer`) for the `g`-prefix shortcut.

**Why it matters.** Four different delays (150/150/160/180 ms) for the same interaction, and each site is responsible for clearing its timer in `destroy()` — `members.js:304` remembers, `catalogue.js:778` remembers, `reading-lists.js` does not. A debounced call that fires after `destroy()` writes into a detached DOM node.

**Recommendation — merge.** `ui.debounce(fn, ms)` returning a function with a `.cancel()`, plus registration in the `destroy` teardown from F10's `mountPage`. Then the delay is one number, and the cleanup is automatic.

---

## F26 · UTC date arithmetic that contradicts `i18n.js`'s own rule

**What is duplicated.** Two places compute "today minus N days" with a UTC slice:
- `web/js/pages/reports.js:42-46` — `daysAgo(days)` → `date.toISOString().slice(0, 10)`
- `web/js/pages/reports.js:319` — `fmtDate(new Date().toISOString().slice(0, 10))`

**Why it matters.** This is not stylistic. `web/js/i18n.js:272-276` documents the exact hazard in a comment: *"`new Date('2025-03-12')` would parse as UTC midnight, which is the previous day"* — and provides `toDate()` (`:280`) to parse local. `daysAgo` reintroduces the bug in the other direction. In Malaysia (UTC+8), between 00:00 and 08:00 local, `new Date()` is still the previous UTC day, so `toISOString().slice(0,10)` returns **yesterday**. The consequence: the reports screen's default range (`reports.js:118-121`) and its "generated" footer (`:319`) are off by one day for the first eight hours of every working day, and `preset=year` computes `from` as 1 January while `to` is 31 December of the previous year.

**Recommendation — merge into i18n.** Add `i18n.toISODate(date)` (local-year/month/day, no `toISOString`) and `i18n.daysAgo(n)` built on it. Replace `reports.js:42-46`, `:114`, `:118`, `:319`. This is a ~15-minute fix for a live, reproducible defect.

---

## F27 · `statusPill` re-implements `pill`'s element

**What is duplicated.** `web/js/ui.js:912-930` `statusPill()` builds the same `span.pill` element that `pill()` builds at `web/js/ui.js:935-950` — same `cx('pill', 'pill--' + tone, …)`, same `attrs.title`, same `text`.

**Why it matters.** Both apply `pill--lg`/`pill--plain`; both set `title`. The only difference is that `statusPill` derives `tone` from `STATUS_TONES` (`ui.js:876-904`) and the label from `t('status.' + camelKey)`. If `pill` grows a `role="status"` or a new size, `statusPill` will not.

**Recommendation — merge.** `return pill(label, tone, {large: opts.large, plain: opts.plain, title: label})`. Six lines saved and one fewer place to forget.

---

## F28 · Hand-built `cell-stack` and `stat-mini` markup

**What is duplicated.**
- `cell-stack` (a two-line main/sub cell, defined at `app.css:3660`) built by hand in 6 places: `audit.js:161`, `backup.js:195`, `member.js:212`, `members.js:188`, `overdue.js:241`, `overdue.js:248`.
- `stat-mini` (a label/value tile, `app.css:3700-3720`) built by hand in **12** places across 5 modules: `overdue.js` 6 (`:160`, `:217`, `:220`, `:224`, `:227`, `:230`), `counter.js` 2 (`:376`, `:379`), `reservations.js` 2 (`:199`, `:202`), `member.js` 1 (`:71`), `reports.js` 1 (`:213`).

**Why it matters.** `ui.js` already has `statCard` (`:812`) and `kv` (`:844`) but neither is the compact inline tile these screens want, so each screen rolled its own. The `overdue.js` copies at `:217-231` and the `counter.js` copies at `:376-380` are near-identical including the `u-warn-text` placement — and `counter.js` omits the `u-num` class the others set, so its numbers are not tabular.

**Recommendation — merge.** Two small factories in `ui.js`: `ui.cellStack(main, sub, opts)` and `ui.statMini(label, value, tone)`. 18 call sites collapse to 18 one-liners, and the tabular-numerals decision becomes one decision.

---

## F29 · Sort-key allow-lists duplicated between service and API

**What is duplicated.** Four pairs of the same key list, one as an ordered `tuple` for `parse_page`, one as a `dict` mapping key → SQL:

| Service dict | API tuple |
|---|---|
| `catalog.py:84-101` `BOOK_SORTS` (16 keys) | `api/library.py:21-38` `BOOK_SORT_KEYS` (16 keys) |
| `circulation.py:30-37` `LOAN_SORTS` (6) | `api/circulation.py:13` `LOAN_SORT_KEYS` (6) |
| `circulation.py:39-43` `RESERVATION_SORTS` (3) | `api/circulation.py:14` `RESERVATION_SORT_KEYS` (3) |
| `members.py:39-50` `MEMBER_SORTS` (10) | `api/library.py:40-51` `MEMBER_SORT_KEYS` (10) |

**Why it matters.** `parse_page(request.query, sorts=BOOK_SORT_KEYS, …)` (`api/library.py:86`) validates against the **tuple**; `list_books` then looks the key up in the **dict** (`catalog.py:839`). So the tuple is the enforcement point and the dict is the implementation — they must agree, and nothing checks that they do. Add a key to the tuple only and the request passes validation then silently falls back to the default sort (`BOOK_SORTS.get(sort_key, BOOK_SORTS["title"])`). 35 keys are maintained twice.

**Recommendation — merge.** Export the dicts from the service modules and derive the tuples: `BOOK_SORT_KEYS = tuple(catalog.BOOK_SORTS)` at `api/library.py:21`. One source, no possibility of a validated-but-unsortable key.

---

# Part 2 — Top 5 fixes, ranked by risk reduction per unit of effort

**1. Fix the two client-side defaults that disagree with the server — 20 minutes.**
`web/js/store.js:280` → `numericSetting('fine_per_day', 0)`; `web/js/store.js:251` → `numericSetting('page_size_default', 50)`. Then add `Database.setting(key)` resolving through `config.DEFAULT_SETTINGS` and replace the 14 Python literals at `circulation.py:64-69`, `:1139`, `reporting.py:283`, `:551`, `:730`, `:731`, `catalog.py:977`, `security.py:767`, `api/library.py:407`. Highest ratio on the list: two live wrong numbers, 16 lines changed.

**2. Collapse the 13 balance implementations to one — 1 hour.**
Add `BALANCE_SQL` beside `CirculationService.balance()` (`circulation.py:114`); use it at `circulation.py:992`, `:994`, `:1024`, `members.py:319`, `:452`, `reporting.py:787`. Replace `circulation.py:1018`, `members.py:476` and `reporting.py:576` with `circulation.balance(row)`. **Delete** `counter.js:335`, `overdue.js:113`, `overdue.js:185` and read `row.balance`. Prevents the one bug the product cannot survive: a receipt that disagrees with the ledger.

**3. Delete the three dead reads in the counter and share the pay dialog — 1 hour.**
Fix `counter.js:284` to `result.queue && result.queue.next_member`; fix `counter.js:274` to `record.copy.current_loan`; make `counter.js:278-280` post `copy_barcode` (or `loan_id` from `current_loan.id`) instead of the ignored `copy_id`. Then extract `web/js/pages/_pay-dialog.js` from `overdue.js:112-169` and delete `counter.js:334-389`. Three unreachable branches and 55 duplicated lines in one pass.

**4. Fix `daysAgo` to stop using a UTC slice — 15 minutes.**
Add `i18n.toISODate()` / `i18n.daysAgo()`; replace `reports.js:42-46`, `:114`, `:118`, `:319`. Reproducible off-by-one in the report range and footer for eight hours of every day in UTC+8.

**5. Make import validation call the form validation — 1 hour.**
Extract `validate_book_fields` / `validate_member_fields` from `catalog.py:323-464` and `members.py:138-221`, returning `(clean, field_errors)`; have `_clean_*_fields` raise and `transfer._validate_*_row` (`transfer.py:431-517`, `:519-564`) map the first error to a code. Deletes 133 lines and removes the accession-sanitisation divergence (`transfer.py:459-463` vs `catalog.py:928`) that makes the import wizard's "N rows ready" a lie.

*Next in line if there is appetite:* F2 (overdue-day SQL), F15 (delete 141 dead i18n keys — zero risk, purely mechanical), F6 (delete the dead `due_today`).

---

# Part 3 — Not duplication (do not "fix" these)

Recording these so a future reader does not spend an afternoon on them.

**1. `transfer.py` importing validators from `catalog.py` / `members.py`.**
`transfer.py:433-441` imports `isbn_normalize`, `to_isbn10`, `to_isbn13`, `validate_call_no`, `validate_dewey`, `validate_isbn`, `validate_year`; `transfer.py:521` imports `validate_email`, `validate_ic`. This is the *correct* pattern — the primitives are shared, only the assembly differs (which is F4's problem, not this one's). Do not inline these.

**2. `reporting.py` importing `safe_cell` from `transfer.py`.**
`reporting.py:25` with the comment at `:22-24` explaining it. One implementation of the CSV injection guard, deliberately in the module that owns every import/export format. Correct.

**3. `LabelService` methods sharing names with module functions.**
`labels.py:942-983` (`qr_matrix`, `qr_svg`, `code39_pattern`, `code39_svg`, `book_label_svg`, `member_card_svg`, `sheet_svg`) each delegate to the module-level function of the same name (`:490`, `:536`, `:663`, `:668`, `:733`, `:798`, `:880`). A façade so `ctx.labels` is one object. Not duplication — the bodies are one-line `return`s.

**4. `CatalogService.refresh_book_rollup` (`catalog.py:781-784`) vs `refresh_book_rollup` (`catalog.py:241`).**
The method opens a transaction and delegates to the module function so callers outside a transaction have a safe entry point. The docstring at `:241-247` explains why the module function must never be called outside one. Keep both.

**5. `CirculationService.catalog` (`circulation.py:53-58`) and `MemberService.catalog` (`members.py:115-122`).**
Same shape — lazy property, same guard — but they hold different classes and exist to break an import cycle (`members.py:119` says so). Two 6-line properties are cheaper than a mixin.

**6. `_serialise_book` / `_serialise_copy` / `_serialise_member`.**
`catalog.py:281`, `:289`, `members.py:101`. Three functions with the same name shape but different row schemas and different normalisations (`copies`→int and `price`→money for books; `ic_warning` for members). Coincidental, not shared.

**7. `reporting.PdfWriter._escape` (`reporting.py:78-87`) vs the XML escapers (F20).**
PDF literal-string escaping (`\\`, `(`, `)`) is a different grammar from XML escaping. They look alike; they are not alike. Keep separate.

**8. The seven paged queries' *bodies*.**
`list_books`, `active_loans`, `overdue_list`, `fines`, `list_reservations`, `list_members`, `member_loans` share the *boilerplate* (F7) but their joins, filters, derived columns and post-processing are genuinely different — `list_books` does a second aggregate query (`catalog.py:851`), `fines` does a totals query (`:1020`), `overdue_list` accumulates `accrued`. Merge the envelope, not the queries.

**9. `catalog._where_from_filters` vs `members._where_from_filters` (F9).**
Different columns, different boolean filters. Keep.

**10. `charts.barChart` / `charts.donut` used by both `dashboard.js` and `reports.js`.**
Correct reuse of a shared module — `dashboard.js:253,265` and `reports.js:226,240,254,267,280` all go through `charts.js`. This is what the rest of the frontend should look like.

**11. `ui.table`, `ui.pagination`, `ui.chips`, `ui.modal`, `ui.drawer`, `ui.emptyState`, `ui.errorState`, `ui.statusPill`, `ui.formatError`, `ui.skeleton`.**
All are used consistently by the page modules (e.g. `ui.errorState` at 12 sites, `ui.table` everywhere). The UI kit is not the problem — the gaps in it (F10, F13, F25, F28) are.

**12. The `0.004` epsilon at `circulation.py:730`, `:736`, `:820`, `:824`, `:992`, `:994`, `members.py:452`, `reporting.py:577`.**
Same literal, but it is the float-comparison tolerance for money, not a duplicated business rule. Once F1 centralises the SQL, the remaining occurrences should use `config.money_eq` (`config.py:285`) rather than a bare literal — but do not "deduplicate" them into a named constant that reads like a business threshold.

**13. `nav.group.library` / `nav.group.circulation` / … in `shell.js:42-77`.**
Five group labels, each used once, in a table. Not duplication.

**14. The `@keyframes` blocks that resemble each other** (`modal-in:1221`, `toast-in:1388`, `toast-out:1399`, `menu-in:1894`, `donut-in:2608`).
Each differs in `transform` and easing. Merging them into a parameterised `fade-slide` would save four blocks and couple four unrelated components' motion. Not worth it.

**15. `pages/book.js` (52 lines) re-using `catalogue.js`'s renderer.**
`book.js:22` calls `renderCatalogue(ctx)` then opens the drawer, and relies on `catalogue.js:775` exposing `reload`. That is deliberate reuse of the list, not a copy — and `catalogue.reload` exists, so the `if (list.reload)` guard at `book.js:34` is defensive rather than dead.

---

## Appendix — measurement notes

- **i18n key census.** 912 keys in `en.js`; `ms.js` and `zh.js` match exactly (0 missing, 0 extra, 0 intra-file duplicates). A key counts as *dead* only if its literal string appears in no `.js`/`.html` file under `web/` outside `lang/`, **and** it does not start with a dynamically-constructed prefix. Detected prefixes: `status.`, `err.`, `type.`, `role.`, `memberType.`, `condition.`, `theme.`, `lang.`, `set.`. Result: 141 dead. (A naive `t('…')`-only scan reports 273 — that over-counts, because `shell.js` and `_book-form.js` pass keys as data, e.g. `labelKey: 'cat.form.title'` at `_book-form.js:172`.)
- **CSS census.** 552 rules parsed after comment stripping. Duplicate-selector count: 1. Identical declaration blocks (≥2 declarations, ≥2 selectors): 32 groups. Near-duplicate pairs at Jaccard ≥ 0.8 with differing values: 351. Distinct class names in CSS: 401; 40 have no literal reference outside CSS, of which 21 are genuinely unreachable once dynamic families (`btn--*`, `pill--*`, `toast--*`, `stat__delta--*`) are excluded.
- **Python census.** 547 `def` statements across `imri/` + `main.py`. Excluding `@route`-decorated handlers (reachable through the decorator registry, not by name) and dunders, 16 have no call site anywhere in the repo — all 16 are listed in F22, of which 15 should be deleted and `money_eq` retained for use by F1.
- **Route census.** 96 `@route` declarations; matching against client paths normalises both `{id}` and `${expr}` path segments to a wildcard and requires segment-count equality, so `` `/api/reports/${kind}` `` correctly matches `/api/reports/trend`. 18 unmatched, 4 of which are ops endpoints.
