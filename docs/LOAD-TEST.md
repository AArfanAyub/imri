# IMRI load test — 200,000 titles / 10,000 members

Measured, not estimated. Every number in this file came out of one run of
`tools/load_test.py` against a freshly seeded database; nothing here is a
projection. The raw run took **382.2 s** end to end.

```
"C:\Users\user\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe" ^
    tools/load_test.py --books 200000 --members 10000
```

Re-running the same command reproduces the data exactly (the seed is derived
from a fixed `random.Random(20261024)`), so differences between runs are
measurement noise, not different data.

> **Read this section first if you are looking for the current numbers.**
> §1–§10 are the **original** measurement, kept unchanged for the record.
> **§11** is the post-fix re-measurement (schema version 2): before/after table,
> fix log, and the one budget that is still red. Nothing in §1–§10 has been
> deleted or edited.

---

## 1. Headline

| | |
|---|---|
| **Write path** | **PASS.** 400/400 concurrent circulation operations, 320.9 ops/s, p95 101.3 ms, **zero** lock errors, **zero** drift in the rollup, in `books.status`, in `loans.paid_amount`, in copy/loan status agreement, and in foreign keys. `BEGIN IMMEDIATE` + WAL + the `Database` `RLock` do what they claim. |
| **Read path** | **FAIL on five of six budgets.** The catalogue, the dashboard and both reports are between 2× and 25× over budget at this scale. |
| **Worst single number** | `reporting.collection()` — **22.8 s** against a 900 ms budget, because it runs two correlated sub-queries per title over 200,000 titles. |
| **Most dangerous number** | `lookup_barcode` on a barcode that does **not** exist — **236.6 ms** against a 25 ms budget. The circulation desk pays half a second every time a student hands over a book that was never accessioned. |

The failures are all **query-shape** problems, not storage-engine problems.
SQLite is not struggling: the same engine answers a 200,000-row page in 18.6 ms
and 400 concurrent writes without a single `SQLITE_BUSY`. Every failure below
traces to a specific statement in a specific file.

---

## 2. Machine

Verbatim from the harness header:

```
python   3.13.14                      (CPython, MSC v.1944 64-bit, AMD64)
sqlite   3.53.1
machine  Intel64 Family 6 Model 165 Stepping 2, GenuineIntel / AMD64
platform Windows-11-10.0.26100-SP0
```

- 12 logical CPUs (`os.cpu_count()`), Windows 11 (build 26100), NTFS.
- Database file **241.4 MB** including WAL, in a `tempfile.mkdtemp()` directory
  under `%LOCALAPPDATA%\Temp`.
- No third-party packages, no network, no `pip install`. `imri/` was not
  modified. `data/` was never touched.

> **Code state.** The tree was being edited by other teammates while this ran
> (`imri/catalog.py`, `imri/circulation.py`, `imri/config.py`,
> `imri/members.py` all carry 23:33–23:55 timestamps). The numbers below are
> from the process that started at **23:47** and therefore reflect the tree as
> of that moment. `imri/db.py` (20:02) and `imri/reporting.py` (20:15) were
> untouched throughout, and every defect in §8 was re-verified against the
> current files at 23:56.

---

## 3. Phase 1 — seed

| stage | rows | wall |
|---|---:|---:|
| books + book_copies | 699,879 | 157.6 s |
| members | 10,000 | 1.0 s |
| loans | 60,000 | 13.7 s |
| reservations | 3,000 | 0.4 s |
| fine_payments | 8,000 | 2.4 s |
| **total** | **780,879** | **175.9 s (4,439 rows/s)** |

Final contents:

```
books=200,000  book_copies=499,879  members=10,000  loans=60,000
reservations=3,000  fine_payments=8,000   db=241.4 MB
live loans 25,000 / returned history 35,000  (7,500 of the live ones overdue)
```

Data realism: ISBN-13 with computed check digits plus the derived ISBN-10;
Dewey spread over 000–999 with and without decimals; 8 material types and 12
languages from `config`; 120 class names; Malaysian IC numbers, `01x-xxxxxxx`
phone numbers, `@sekolah.edu.my` addresses; titles drawn from a 67,200-entry
vocabulary so ~3 titles share a name (a 200k-title library is not 200k distinct
works). Loan, reservation and payment dates are spread over the last two years
so the 30-day reports have a believable denominator (20,204 loans issued and
1,470 returned in the last 30 days).

Invariants asserted immediately after the seed, all `ok`:

- `books.copies` == `COUNT(book_copies)` for all 200,000 titles;
- `books.status` == the value `refresh_book_rollup()` would derive;
- `loans.paid_amount` == `SUM(fine_payments.amount)` per loan;
- copy status `on_loan` ⇔ exactly one live loan, both directions;
- `PRAGMA foreign_key_check` empty.

The seed is index-bound, not CPU-bound: 780,879 rows through
`executemany` in 5,000-row transactions is 4,439 rows/s because each `books`
row maintains 8 indexes and each copy 4. It is a one-off cost — `--reuse` skips
it and reuses the same database file.

---

## 4. Phase 2 — query latency

Warm (one discarded call), then median of 5. `min`/`max` are included because
at 241 MB the OS page cache matters: the same query can move 2× between runs.

| operation | min | **median** | max | budget | verdict | rows / hits |
|---|---:|---:|---:|---:|:--:|---|
| `list_books` page 1 | 16.1 | **18.6** | 21.5 | 150 | PASS | 200,000 |
| `list_books` page 500 | 26.2 | **28.8** | 29.9 | 150 | PASS | 200,000 |
| `list_books` page 4000 (offset 199,950) | 80.2 | **94.9** | 106.9 | 150 | PASS | 200,000 |
| `list_books` `material_type=book` | 207.8 | **278.5** | 308.2 | 150 | **FAIL** | 25,000 |
| `list_books` `status=available` | 280.9 | **307.0** | 505.6 | 150 | **FAIL** | 193,828 |
| `list_members` page 1 | 7.6 | **8.9** | 12.5 | 150 | PASS | 10,000 |
| `list_members` page 100 | 22.0 | **25.7** | 26.3 | 150 | PASS | 10,000 |
| `circulation.overdue_list()` | 410.3 | **604.5** | 789.2 | 150 | **FAIL** | 7,500 |
| `members.profile()` (busiest member, 7 loans) | 230.2 | **252.4** | 284.4 | 150 | **FAIL** | 25,000 |
| `list_books` `q=Algebra` (title) | 1085.0 | **1097.0** | 1214.3 | 300 | **FAIL** | 2,880 hits |
| `list_books` `q=Ahmad` (author) | 722.7 | **947.0** | 1057.7 | 300 | **FAIL** | 4,229 hits |
| `list_members` `q=Aisyah` (name) | 21.8 | **26.2** | 36.0 | 300 | PASS | 233 hits |
| `circulation.search("Algebra")` | 5.9 | **7.5** | 8.5 | 300 | PASS | command palette |
| `lookup_barcode` hit | 0.1 | **0.1** | 0.2 | 25 | PASS | 1 |
| `lookup_barcode` miss | 210.5 | **236.6** | 254.4 | 25 | **FAIL** | 200,000 |
| `members.find_by_code` | 0.1 | **0.1** | 0.1 | 25 | PASS | 1 |
| `SELECT COUNT(*) FROM books` | 10.0 | **10.6** | 11.8 | 150 | PASS | 200,000 |
| `reporting.dashboard()` | 2062.4 | **2940.3** | 3751.8 | 600 | **FAIL** | — |
| `reporting.collection()` | 18079.8 | **22759.6** | 25940.5 | 900 | **FAIL** | 200,000 |
| `reporting.circulation()` (last 30 days) | 2187.0 | **3463.7** | 3747.6 | 900 | **FAIL** | 20,204 |

The `n=` column is not decoration: it is the number of rows the call actually
returned or matched, so a fast time can never be the result of matching nothing.

**What this says.** Index-only lookups and the member list are fine. Everything
that either (a) sorts a large filtered set, (b) counts with `LIKE '%…%'`, or
(c) joins inside a `COUNT`, falls over. Deep pagination *passes* at page 4000
(94.9 ms) but it is 5× page 1 — the cost is linear in `OFFSET`, exactly as
predicted, and it will cross the 150 ms line somewhere around page 8,000.

---

## 5. Phase 3 — write-path concurrency

16 threads, 400 mixed operations against one shared `Database` connection.

| | |
|---|---|
| planned | issue 120, return 120, payment 80, reserve 80 |
| succeeded | **400 / 400** |
| wall | 1.25 s |
| throughput | **320.9 ops/s** |
| latency p50 / p95 / max | 44.3 / **101.3** / 158.8 ms |
| `sqlite3.OperationalError` | **0** |
| "database is locked" | **0** |
| business refusals | 0 |

Post-run consistency, all `ok`:

```
rollup (books.copies vs book_copies)       ok
derived (books.status vs copies)           ok
payments (loans.paid_amount vs receipts)   ok
copy/loan status agreement                 ok
foreign keys                               ok
```

Each operation is chosen from a distinct row so the workload measures the write
path rather than self-inflicted contention: 120 distinct available barcodes, 120
distinct live loans, 80 distinct loans with an outstanding balance, 80 distinct
fully-reserved titles for members under their loan limit. 320.9 ops/s is the
serialisation ceiling of one `RLock` around one connection — which is the
documented design, and it is 12× a busy school's peak.

---

## 6. Phase 4 — index audit

`EXPLAIN QUERY PLAN` on the SQL the services actually issued (captured by
tracing `Database.query`, so the audited text cannot drift from the running
code).

| operation | plan | flagged |
|---|---|:--:|
| `list_books` page 1 / page 4000 | `SCAN b USING INDEX ix_books_title` (index walk) | no |
| `list_books` `q=…` — COUNT | **`SCAN b`** | **yes** |
| `list_books` `q=…` — page | `SCAN b USING INDEX ix_books_title` | no |
| `list_books` `material_type=…` | `SEARCH … ix_books_material` + **`USE TEMP B-TREE FOR ORDER BY`** | no\* |
| `lookup_barcode` hit | `SEARCH c USING INDEX sqlite_autoindex_book_copies_1 (barcode=?)` | no |
| `lookup_barcode` miss | **`SCAN books`** | **yes** |
| `overdue_list` | `SEARCH l USING INDEX ix_loans_open (status=? AND due_date<?)` ×2 | no\* |
| `dashboard` | **`SCAN books`**, **`SCAN book_copies`**, **`SCAN members`**, **`SCAN loans`** ×2, **`SCAN r`** | **yes ×6** |

\* The flag rule only fires on a bare `SCAN` of a large table. Two of the
failures are therefore *not* flagged and are reported here instead:
`material_type` sorts 25,000 materialised rows in a temp B-tree, and
`overdue_list` walks 7,500 index entries and then does 15,000 row lookups
through two joins. An index audit alone would have declared both healthy. The
harness prints `(sort materialised)` next to any `USE TEMP B-TREE` line so the
evidence is visible even when the rule stays silent.

Eight flagged statements, all inside three methods. Details in §8.

---

## 7. Verdict per budget

```
budget                             observed        limit  result
paginated list                      604.5 ms        150 ms  FAIL
search                             1097.0 ms        300 ms  FAIL
single-row lookup                   236.6 ms         25 ms  FAIL
dashboard                          2940.3 ms        600 ms  FAIL
report                            22759.6 ms        900 ms  FAIL
concurrent write p95                101.3 ms        250 ms  PASS
OVERALL: FAIL
```

The harness exits **1** on this result.

---

## 8. Defects

Ordered by severity. Line numbers are against the tree at 2026-10-24 23:56.
**None of these were fixed** — the decision is the team lead's.

### D1 — `reporting.collection()` takes 22.8 s (budget 900 ms) · `imri/reporting.py:312-320`

```
 20969.0 ms  SELECT b.accession_no, b.title, ... ,
                    (SELECT COUNT(*) FROM book_copies c WHERE c.book_id = b.id) AS total_copies,
                    (SELECT COUNT(*) FROM book_copies c WHERE c.book_id = b.id
                       AND c.status = 'available') AS available_copies
             FROM books b ORDER BY b.dewey, b.title COLLATE NOCASE
   914.6 ms  SELECT b.material_type AS key, COUNT(*) AS titles,
                    COALESCE(SUM((SELECT COUNT(*) FROM book_copies c WHERE c.book_id = b.id)),0) ...
```

**Cause.** Two correlated sub-queries per title, 200,000 times, each of which
also re-reads `book_copies` (499,879 rows) through `ix_copies_book`. The result
is materialised into a 200,000-element Python list — and then used only to sum
two columns and bucket a third. `ORDER BY b.dewey, b.title COLLATE NOCASE`
guarantees the whole set is sorted before the first byte is produced.

**Fix.** Replace both sub-queries with one aggregate joined once:

```sql
SELECT b.accession_no, b.title, b.authors, b.material_type, b.language,
       b.dewey, b.call_no, b.status, b.copies, b.price,
       COUNT(c.id) AS total_copies,
       SUM(CASE WHEN c.status = 'available' THEN 1 ELSE 0 END) AS available_copies
FROM books b LEFT JOIN book_copies c ON c.book_id = b.id
GROUP BY b.id ORDER BY b.dewey, b.title COLLATE NOCASE
```

and let `to_csv`/`to_pdf` stream rows instead of holding a list. `books.copies`
is already the denormalised rollup, so the totals do not need the copy table at
all. Expected: ~20 s → well under 1 s.

### D2 — `list_books` search takes 1097 ms (budget 300 ms) · `imri/catalog.py:796-801, 846`

```
  555 ms (warm, isolated)  SELECT COUNT(*) FROM books b
    WHERE (b.title LIKE ? ESCAPE '\' OR b.subtitle LIKE ? ESCAPE '\' OR
           b.authors LIKE ? ESCAPE '\' OR ... 11 columns ...)
    4.9 ms                 SELECT b.* FROM books b WHERE (...same...) ORDER BY ... LIMIT 50
```

**Cause.** A leading-wildcard `LIKE '%term%'` on eleven columns cannot use any
index, so the `COUNT` evaluates **2.2 million** `LIKE` comparisons before the
page query even starts. The page query itself is only 4.9 ms because it walks
`ix_books_title` and stops after 50 matches — **all** the cost is the count.
(The `ESCAPE '\'` hardening added at 23:33 is correct and unrelated; it neither
causes nor cures this.)

**Fix.** Two options, in order of effort:

1. Keep `LIKE` but make the *count* cheap: run it as `EXISTS`-style against a
   capped window, or drop the exact total and report `"200,000+"` past a
   threshold (what most library UIs do).
2. Add an FTS5 virtual table `books_fts(title, subtitle, authors, isbn,
   call_no, subjects, publisher, accession_no)` maintained by triggers, and use
   `MATCH` for `q`. This is the real fix and takes the search to single-digit
   milliseconds at any catalogue size.

### D3 — `lookup_barcode` on a miss takes 236.6 ms (budget 25 ms) · `imri/catalog.py:1320`

```
PLAN: SELECT * FROM books WHERE barcode = ? OR accession_no = ?
      SCAN books
```

**Cause.** `books.barcode` has **no index** (`imri/db.py:147-154` creates
indexes on `isbn`, `title`, `authors`, `dewey`, `call_no`, `material_type`,
`language`, `status` — not `barcode`). Because only one branch of the `OR` is
indexable, SQLite cannot use the `accession_no` unique index either and falls
back to a full 200,000-row scan. This is the *hot* path: it runs every time a
scanned code is not a known copy barcode, i.e. on every mistyped, un-accessioned
or foreign barcode at the circulation desk.

**Fix.** Add the missing index in a schema migration (`Migrator.STEPS[2]`):

```sql
CREATE INDEX IF NOT EXISTS ix_books_barcode ON books(barcode);
```

or, without a migration, split the query into two indexed lookups:

```python
book = self.db.one("SELECT * FROM books WHERE accession_no = ?", (text,)) \
    or self.db.one("SELECT * FROM books WHERE barcode = ?", (text,))
```

(`accession_no` is already `UNIQUE`, so the first branch is a single rowid
seek.) Expected: 236.6 ms → ~0.1 ms, i.e. the same as the hit path.

### D4 — `reporting.dashboard()` takes 2940 ms (budget 600 ms) · `imri/reporting.py:722-899`

Six statements account for essentially all of it:

| ms | statement | line | why |
|---:|---|---|---|
| 1228 | `COUNT(*) FROM loans WHERE issue_date <= ? AND due_date < ? AND (return_date IS NULL OR return_date > ?)` | `reporting.py:779` | uses `ix_loans_due` for `due_date < ?`, then fetches ~7,500 full rows to test two unindexed predicates |
| 846 | `... FROM members m JOIN loans l ON l.member_id = m.id AND l.status='on_loan' GROUP BY m.id HAVING COUNT(l.id) >= ?` | `reporting.py:846-851` | `SEARCH l USING INDEX ix_loans_open (status=?)` — scans all 25,000 live loans, then **two** temp B-trees (GROUP BY and ORDER BY) |
| 438 | `COUNT(*) FROM book_copies WHERE created_at >= ?` | `reporting.py:742` | **`SCAN book_copies`** — 499,879 rows, no index on `created_at` |
| 394 | `COUNT(*) FROM books WHERE created_at >= ?` | `reporting.py:736` | **`SCAN books`** — 200,000 rows, no index on `created_at` |
| 326 | `SUM(fine_amount) FROM loans WHERE due_date >= ?` | `reporting.py:794` | range over most of 60,000 loans, full-row fetch |
| 99 | `no_available_with_holds` | `reporting.py:839-845` | 3,000 holds × correlated `NOT EXISTS` |

**Fix.** Four cheap indexes plus two rewrites:

```sql
CREATE INDEX IF NOT EXISTS ix_books_created  ON books(created_at);
CREATE INDEX IF NOT EXISTS ix_copies_created ON book_copies(created_at);
CREATE INDEX IF NOT EXISTS ix_loans_due_full ON loans(due_date, issue_date, return_date);
```

- `over_loan_limit`: aggregate from `loans` alone —
  `SELECT member_id, COUNT(*) FROM loans WHERE status='on_loan' GROUP BY member_id
  HAVING COUNT(*) >= ?` is an index-only scan of `ix_loans_member`; join the
  top 10 to `members` afterwards.
- `overdue_then`: `(return_date IS NULL OR return_date > ?)` needs the third
  column in an index; `ix_loans_due_full` makes it a covering range scan.

Expected: ~2.9 s → ~250–400 ms.

### D5 — `reporting.circulation()` takes 3464 ms (budget 900 ms) · `imri/reporting.py:405-439`

```
 2046 ms  SELECT l.id, l.issue_date, ..., b.title, m.name FROM loans l
          LEFT JOIN books b ... LEFT JOIN members m ... WHERE l.issue_date BETWEEN ? AND ?
 1115 ms  ... top_titles:  GROUP BY l.book_id ORDER BY count DESC LIMIT 10
  703 ms  ... top_borrowers: GROUP BY l.member_id ORDER BY count DESC LIMIT 10
```

**Cause.** 20,204 loans issued in the last 30 days — a genuinely busy month —
and the report materialises every one of them with two joins, then re-scans the
same range twice more for two ten-row leaderboards.

**Fix.** Stream/paginate `rows` (the CSV and PDF exporters already iterate
`report["rows"]`, so they can take a generator), and derive both leaderboards
from one pass:

```sql
SELECT book_id, COUNT(*) c FROM loans WHERE issue_date BETWEEN ? AND ?
GROUP BY book_id ORDER BY c DESC LIMIT 10
```

with a covering index `ix_loans_issue_book ON loans(issue_date, book_id)` (and
the member equivalent) so the two extra scans are index-only. Expected:
~3.4 s → ~400 ms.

### D6 — `circulation.overdue_list()` takes 604 ms (budget 150 ms) · `imri/circulation.py:946-951`

```
  494.4 ms  SELECT COUNT(*) FROM loans l
            LEFT JOIN books b   ON b.id = l.book_id
            LEFT JOIN members m ON m.id = l.member_id
            WHERE l.status = 'on_loan' AND l.due_date < ?
    6.9 ms  ... the same query with LIMIT 50 (the page itself)
```

**Cause.** The two `LEFT JOIN`s cannot change a `COUNT(*)`, but SQLite executes
them anyway: 7,500 overdue loans × 2 rowid lookups into a 241 MB file.
Measured directly on the same database: **0.8 ms without the joins, 229 ms
with** — a 280× penalty for a clause that provably cannot affect the result.
The page query is 6.9 ms because `ORDER BY l.due_date` is satisfied by
`ix_loans_open` and it stops after 50 rows.

**Fix.** Count the table, not the join:

```python
total = int(self.db.scalar(
    "SELECT COUNT(*) FROM loans WHERE status = 'on_loan' AND due_date < ?",
    (reference,), default=0))
```

Also worth doing: `overdue_days`/`accrued_fine`/`balance` re-read the policy
(`_policy()` issues 6 `SELECT`s from `settings`) once per row — 50 rows made
300 settings queries, 15.7 ms. Hoist `policy = self._policy()` above the loop.

### D7 — `members.profile()` takes 252 ms (budget 150 ms) · `imri/members.py:369-375`

```
  341.8 ms  SELECT l.*, b.title, c.barcode FROM loans l
            LEFT JOIN books b ON b.id = l.book_id
            LEFT JOIN book_copies c ON c.id = l.copy_id
            WHERE l.member_id = ? AND l.status = 'on_loan' ORDER BY l.due_date
PLAN:  SEARCH l USING INDEX ix_loans_open (status=?)     <-- all 25,000 live loans
       SEARCH b USING INTEGER PRIMARY KEY (rowid=?) LEFT-JOIN
       SEARCH c USING INTEGER PRIMARY KEY (rowid=?) LEFT-JOIN
```

**Cause.** The member has **3** live loans. `ix_loans_member(member_id, status)`
would find them in three seeks, but `ORDER BY l.due_date` is not satisfiable
from that index, so the planner instead walks `ix_loans_open` — all 25,000 live
loans — and filters by member, doing two rowid lookups per candidate. One
member's detail drawer reads the whole circulation table.

**Fix.** Either add the composite index that makes both the filter and the sort
index-only:

```sql
CREATE INDEX IF NOT EXISTS ix_loans_member_due ON loans(member_id, status, due_date);
```

or drop `ORDER BY l.due_date` from the SQL and sort the three rows in Python
(`sorted(current, key=lambda r: r["due_date"])`). The second needs no migration.

### D8 — filtered `list_books` takes 278–307 ms (budget 150 ms) · `imri/catalog.py:848`

```
  213 ms  SELECT b.* FROM books b WHERE b.material_type = ? ORDER BY b.title COLLATE NOCASE, b.id LIMIT 50
PLAN:     SEARCH b USING INDEX ix_books_material (material_type=?)
          USE TEMP B-TREE FOR ORDER BY
```

**Cause.** `ix_books_material`/`ix_books_status` give the filter but not the
order, so SQLite materialises every matching row — **including all of `b.*`** —
and sorts it. 25,000 rows for `material_type`, 193,828 for `status`.

**Fix.** Composite indexes that carry the sort:

```sql
CREATE INDEX IF NOT EXISTS ix_books_material_title ON books(material_type, title COLLATE NOCASE, id);
CREATE INDEX IF NOT EXISTS ix_books_status_title   ON books(status, title COLLATE NOCASE, id);
```

which turns the temp B-tree into an index walk and stops the `b.*` fetch after
50 rows. Expected: ~280 ms → ~20 ms.

### D9 — deep pagination is linear in `OFFSET` · `imri/catalog.py:846-848`

Not a budget failure today (page 4000 = 94.9 ms against 150 ms), reported
because it is the number that will fail next:

| page | offset | median |
|---:|---:|---:|
| 1 | 0 | 18.6 ms |
| 500 | 24,950 | 28.8 ms |
| 4000 | 199,950 | 94.9 ms |

The page query is a pure index walk that steps over `offset` entries before
returning 50, and the `COUNT(*)` is paid on every page as well. Extrapolating,
the 150 ms line is crossed around page 8,000. Keyset pagination —
`WHERE (title, id) > (?, ?) ORDER BY title, id LIMIT 50` — makes every page
cost the same as page 1. Not urgent; worth knowing.

---

## 9. Compromises and honesty notes

- **One run, one machine.** Every number is a single run of a deterministic
  seed. The 12 logical CPUs are shared with whatever else the workstation was
  doing. `min`/`max` are printed precisely so the spread is visible rather than
  hidden behind a median: `status=available` ranged 280.9–505.6 ms, and the
  `overdue_list` median of 604.5 ms is the least stable figure in the table.
- **The tree moved during the run.** `imri/catalog.py`, `circulation.py`,
  `config.py` and `members.py` were edited at 23:33–23:55 by other teammates.
  The measured process started at 23:47 and imported its modules once, so it
  reflects a coherent snapshot; the defects above were re-verified against the
  files as of 23:56. If a re-run disagrees with these numbers by more than the
  cache spread shown, diff those four files first.
- **Phase 3 measures serialisation, not parallelism.** All 16 threads share one
  `sqlite3.Connection` guarded by one `RLock`, so 320.9 ops/s is the ceiling by
  design. That is the point of the test — it proves the guard works — but it is
  not a multicore scaling result.
- **The seed is deliberately slow** (4,439 rows/s, 176 s). It goes through
  `executemany` in 5,000-row transactions rather than the service layer, which
  would take hours; it still pays full index maintenance. `--reuse` exists so a
  re-run does not pay it again.
- **Not measured.** The HTTP layer (`ThreadingHTTPServer`, JSON encoding,
  auth/session lookup per request) is not in scope: this harness calls the
  services directly, so these are service-level timings and the wire adds a
  constant on top. Export to CSV/PDF, backup/restore, and label printing were
  not exercised either.
- **`reporting.collection()`'s 22.8 s** is dominated by one statement (20,969 ms
  of it) measured in isolation; the totals around it are consistent with the
  end-to-end figure.

---

## 10. Reproducing

```bash
# full run (176 s seed + ~200 s measurement)
python tools/load_test.py --books 200000 --members 10000

# re-run the measurements against the same database, skip the seed
python tools/load_test.py --books 200000 --members 10000 --reuse

# a 20-second smoke run that exercises every code path
python tools/load_test.py --books 2000 --members 200 --loans 600 \
    --reservations 30 --payments 80 --runs 3 --ops 40
```

Exit code is `0` when every budget passes and `1` when any fails, so it can be
wired straight into a gate. `--clean` deletes the temp database afterwards; the
default leaves it in place under `%TEMP%\imri-load-*` so `--reuse` works. The
pointer to the last database lives in `%TEMP%\imri-load-test-state.json` —
outside the repository, so a run never leaves anything in the project tree.

---

# 11. Post-fix re-measurement — 2026-10-25 (schema version 2)

Everything above is the original measurement and stays as it was written. This
section is the re-run **after** the fixes, on the same machine and the same
deterministic seed.

## 11.1 Method — why there are two "before" columns

The §4 numbers came from a machine that was doing other work (the tree was being
edited by four people; §9 records the caveat). Re-measuring the fixed tree and
comparing it to §4 would credit the fix with the difference between two machines.

So the "before" column was **re-taken on this machine** before touching a line of
code:

1. Seed once at full scale: `python tools/load_test.py --books 200000 --members 10000`.
2. Copy the seeded file to `$TEMP/imri-pristine/imri.db` and never touch it again.
3. For every measurement run: `cp $TEMP/imri-pristine/imri.db $TEMP/imri-run/imri.db`,
   then `python tools/load_test.py --books 200000 --members 10000 --skip-seed --db $TEMP/imri-run/imri.db`.

Step 3 matters because phase 3 (the write test) mutates the database. Re-running
with `--reuse` against a database that has already been through phase 3 is not a
comparable measurement, and the drift is not small.

The "after" column is the **median of 5** from the last of five post-fix runs.
`reporting.collection()` is the one number that moves between runs, so its spread
is given explicitly rather than hidden behind a single median.

## 11.2 Before / after — every measurement that changed

All values are milliseconds, `median of 5`, warm. **Bold** = over budget.

| operation | before (§4, original machine) | before (re-baseline, this machine) | **after** | budget | verdict |
|---|---:|---:|---:|---:|:--:|
| `list_books` page 1 | 18.6 | 15.0 | **1.3** | 150 | PASS |
| `list_books` page 500 | 28.8 | 17.1 | **2.6** | 150 | PASS |
| `list_books` page 4000 (offset 199,950) | 94.9 | 50.8 | **11.1** | 150 | PASS |
| `list_books` `material_type=book` | **278.5** | **345.1** | **2.7** | 150 | PASS |
| `list_books` `status=available` | **307.0** | **316.5** | **12.7** | 150 | PASS |
| `list_members` page 1 | 8.9 | 2.6 | **1.5** | 150 | PASS |
| `list_members` page 100 | 25.7 | 8.3 | **1.9** | 150 | PASS |
| `circulation.overdue_list()` | **604.5** | **239.3** | **1.9** | 150 | PASS |
| `members.profile()` (busiest member) | **252.4** | 143.4 | **0.3** | 150 | PASS |
| `list_books` `q=Algebra` (title) | **1097.0** | **1390.4** | **5.2** | 300 | PASS |
| `list_books` `q=Ahmad` (author) | **947.0** | **1241.5** | **7.8** | 300 | PASS |
| `list_members` `q=Aisyah` (name) | 26.2 | 40.0 | **8.0** | 300 | PASS |
| `circulation.search("Algebra")` | 7.5 | 18.0 | **1.0** | 300 | PASS |
| `lookup_barcode` hit | 0.1 | 0.3 | **0.1** | 25 | PASS |
| `lookup_barcode` miss | **236.6** | **537.7** | **0.1** | 25 | PASS |
| `members.find_by_code` | 0.1 | 0.2 | **0.1** | 25 | PASS |
| `SELECT COUNT(*) FROM books` | 10.6 | 25.8 | **0.2** | 150 | PASS |
| `reporting.dashboard()` | **2940.3** | **2678.1** | **90.0** | 600 | PASS |
| `reporting.collection()` | **22759.6** | **9973.5** | **2098.7** | 900 | **FAIL** |
| `reporting.circulation()` (last 30 days) | **3463.7** | **2633.7** | **200.1** | 900 | PASS |
| concurrent write p95 | 101.3 | 94.4 | **86.1** | 250 | PASS |

Non-latency figures that also moved:

| | before | after |
|---|---:|---:|
| statements flagged by the index audit | 8 | **0** |
| concurrent writes succeeded | 400 / 400 | **400 / 400** |
| lock errors / "database is locked" | 0 / 0 | **0 / 0** |
| write throughput | 320.9 ops/s (§5, original) · **407.6** (re-baseline) | **514.7 ops/s** |
| post-run consistency checks | all ok | **all ok** |
| database file (harness `db size`) | 241.4 MB (§2, original) · **219.3 MB** (re-baseline) | **351.3 MB** |
| schema version | 1 | **2** |
| objects: tables / indexes / triggers | 18 / 34 / 2 | **18 / 48 / 2** |
| measurement phase wall time (`--skip-seed`) | ~200 s (§1, original) · **129.1 s** (re-baseline) | **24.0 s** |

The file grows 219.3 → 351.3 MB (+60 %; 229.9 → 339.0 MB as measured by `ls`,
the rest being the `-wal` file the harness counts). That is the price of the fix:
the FTS5 index (~29 MB measured standalone) plus fourteen indexes on `books`,
`loans`, `book_copies`, `members` and `reservations`. Disk is the cheap resource
here; the budget is latency.

`reporting.collection()` across the four runs taken after the final change:
medians **2191.8 / 2852.9 / 2143.0 / 2098.7**, best single sample 1991.9, worst
2963.4. It is the only number in the table that moves by more than ~2× between
runs, because it touches all 200,000 `books` rows and the OS page cache decides
how much of that is free. Treat ~2.1–2.9 s as the range, not a point.

## 11.3 Verdict per budget, after

```
budget                             observed        limit  result
paginated list                       12.7 ms        150 ms  PASS
search                                8.0 ms        300 ms  PASS
single-row lookup                     0.1 ms         25 ms  PASS
dashboard                            90.0 ms        600 ms  PASS
report                             2098.7 ms        900 ms  FAIL
concurrent write p95                 86.1 ms        250 ms  PASS
OVERALL: FAIL
```

**The harness still exits 1**, for one reason: `reporting.collection()`. Five of
six budgets are green and the index audit is clean (8 flagged → 0), but the report
budget is not met and §11.5 explains why it cannot be met without changing the
frozen response.

## 11.4 Fix log

One row per defect from §8. "Measured" means a number out of the harness; nothing
in this table is an estimate.

| # | defect | what changed | where | measured |
|---|---|---|---|---|
| D1 | `collection()` 22.8 s | Rewritten as a **single** statement. The per-title copy counts became one correlated `available` sub-query that reads the already-maintained `books.copies` rollup instead of counting `book_copies`; the rows are **streamed** through the new `Database.iter_tuples()` and read **positionally**; the four breakdowns (`material_type`, `language`, `dewey`, `status`) are accumulated in the same pass and sorted in Python. | `imri/reporting.py:295`; `imri/db.py:731` (`iter_tuples`); `imri/db.py:701` (`cache_size`, `mmap_size`) | **9973.5 → 2098.7 ms.** Still **FAIL** (budget 900). See §11.5. |
| D2 | `q=…` search 1097 ms | FTS5 **external-content** index `books_fts` over **eleven** columns, kept in sync by three triggers (`ai`/`ad`/`au`, the update trigger scoped `AFTER UPDATE OF <indexed cols>` so `refresh_book_rollup()` writes to `copies`/`status` do not reindex). The defect named seven columns; the index covers the same eleven the `LIKE` fallback searched (`title, subtitle, authors, isbn, isbn10, call_no, subjects, publisher, accession_no, barcode, location`), because indexing fewer would have silently narrowed what a search can match. A query term is split, quoted and prefix-matched as `"tok"* AND "tok"*`, so a quoted literal can neither terminate the expression nor inject an operator. `COUNT(*)` runs against `books_fts MATCH ?` too, so count and page use the same predicate. The `LIKE … ESCAPE '\'` path is retained **only** for the case where FTS5 is genuinely absent (`sqlite3` reports `ENABLE_FTS5` here, so it is not taken). | `imri/catalog.py:39` (`search_match_query`), `:846` (`_search_clause`), `:881` (`_where_from_filters`), `:932` (`_count_books`); `imri/db.py:397/404/437/529/552` | **1390.4 → 5.2 ms** (`Algebra`), **1241.5 → 7.8 ms** (`Ahmad`). Hit counts identical to `LIKE`: 2880, 4229, 5074, 1, 0. |
| D3 | `lookup_barcode` miss 236 ms | `CREATE INDEX ix_books_barcode ON books(barcode)`. The `barcode = ? OR accession_no = ?` statement is unchanged — SQLite now answers it with **MULTI-INDEX OR** over `ix_books_barcode` and the pre-existing `sqlite_autoindex_books_1` (from `accession_no … UNIQUE`). | `imri/db.py:354` (`PERFORMANCE_INDEXES`); docstring at `imri/catalog.py:1409` | **537.7 → 0.1 ms.** Plan: `MULTI-INDEX OR` → `SEARCH books USING INDEX ix_books_barcode (barcode=?)` + `SEARCH books USING INDEX sqlite_autoindex_books_1 (accession_no=?)`. No `SCAN books`. |
| D4 | `overdue_list()` 604 ms | The `COUNT` is now `SELECT COUNT(*) FROM loans WHERE status='on_loan' AND due_date < ?` — the two `LEFT JOIN`s were only needed to render rows, never to count them. The page query still joins inline. `self.fine_per_day` is hoisted out of the row loop and the per-row decoration moved into one helper. | `imri/circulation.py:961` (`overdue_list`), `:108` (`accrued_fine`), `:121` (`_decorate_loan`) | **239.3 → 1.9 ms**, 7,464 rows still returned. |
| D5 | `dashboard()` 2940 ms | `over_loan_limit` is now aggregated **from `loans` alone** (`GROUP BY member_id HAVING COUNT(*) >= ? ORDER BY loans DESC, member_id LIMIT 10`) and the member details fetched with one `id IN (…)` lookup, instead of scanning `members` and joining per member. The remaining statements got the indexes they were missing. | `imri/reporting.py:773` (`_members_over_limit`), `:809` (`dashboard`); `imri/db.py:354` | **2678.1 → 90.0 ms.** |
| D6 | `circulation()` report 3464 ms | Covering indexes on `loans(issue_date, book_id)` and `loans(issue_date, member_id)` so both leaderboards are index-only, plus `loans(return_date, issue_date)` for the returned counts; the row loop switched to `iter_tuples()` with positional reads. | `imri/reporting.py:440`; `imri/db.py:354` (`ix_loans_issue_book`, `ix_loans_issue_member`, `ix_loans_return`) | **2633.7 → 200.1 ms** over 20,204 loans. |
| D7 | `members.profile()` 252 ms | No code change. `ix_loans_member_due ON loans(member_id, status, due_date)` makes the live-loan lookup a covering seek. | `imri/db.py:354` | **143.4 → 0.3 ms.** |
| D8 | filtered `list_books` 278–307 ms | Composite `ix_books_material_title (material_type, title COLLATE NOCASE, id)` and `ix_books_status_title (status, title COLLATE NOCASE, id)` — the sort columns are in the index, so `USE TEMP B-TREE FOR ORDER BY` disappears and the `b.*` fetch stops after 50 rows. | `imri/db.py:354` | **345.1 → 2.7 ms** (`material_type`), **316.5 → 12.7 ms** (`status`). |
| D9 | deep pagination linear in `OFFSET` | **Deliberately not changed.** Keyset pagination would alter the `page`/`page_size`/`pages`/`total` contract in `meta`, which is frozen. The number still improves (50.8 → 11.1 ms) because the page query now walks a covering index and the page cache is 64 MB, but the cost is still linear: page 4000 is ~8× page 1. Expect the 150 ms line around page 8,000. | `imri/catalog.py:846-848` | **50.8 → 11.1 ms**, shape unchanged. |

Also improved as a side effect of the same indexes and the two PRAGMAs, with no
change to any statement: `list_books` page 1 (15.0 → 1.3), `COUNT(*) FROM books`
(25.8 → 0.2), `list_members` (2.6 → 1.5), `circulation.search` (18.0 → 1.0).

### Schema migration

The new indexes and the FTS5 table are **`Migrator.STEPS[2]`**
(`imri/db.py:552`), and `SCHEMA_VERSION` went `1 → 2` (`imri/config.py:37`). Step
1 was not edited. Consequences, all verified:

- an existing version-1 file is upgraded in place on the next open; a fresh file
  reaches the same shape through the same step, so the two cannot drift;
- if the FTS objects are missing but the version is already 2 (a half-applied
  migration, or a database restored from a copy that lost them),
  `ensure()` self-repairs via `ensure_search_objects()` (`imri/db.py:529`);
- the index is rebuilt when it is found empty, so an upgraded file has a populated
  index on first use rather than silently returning zero hits;
- `object_counts()` (`imri/db.py:1046`) walks `sqlite_master` but skips
  `sqlite_%` **and** the nine FTS objects (the virtual table, its five shadow
  tables and the three sync triggers), so it still reports `tables == 18`,
  `triggers == 2` and `indexes == 48` — fourteen more than version 1, which is
  exactly the fourteen statements in `PERFORMANCE_INDEXES`. Without that
  exclusion the shadow tables and triggers would have shown up as schema drift.

### Seeder

`tools/load_test.py:853` wraps the bulk `books` insert in
`Database.begin_bulk_load()` / `end_bulk_load()` (`imri/db.py:1019` / `:1036`),
which drops the three FTS triggers for the duration and rebuilds the index once
at the end. Without it the seeder would pay trigger cost 200,000 times; with it
the rebuild costs one pass (4.9 s standalone on 200,000 rows). The `try/finally`
means a failed seed still restores the triggers.

## 11.5 `collection()` — why it is still red, and what was tried

The budget is 900 ms for a response that contains **200,000 rows**, one per title,
each with an availability figure. What was measured, in order:

| attempt | result |
|---|---:|
| original: two correlated sub-queries per title, `sqlite3.Row` named access | 9973.5 ms |
| `LEFT JOIN book_copies … GROUP BY b.id` (the shape suggested by the defect report) | **3.0–3.4 s — worse.** The join materialises 499,879 copy rows before grouping, which costs more than the correlated sub-query it replaces. |
| one `GROUP BY` over `book_copies`, merged into a Python dict | 2.6 s — no better than the sub-query |
| single statement, one correlated `available` sub-query reading `books.copies` (no `book_copies` access at all), still `sqlite3.Row` access | 2776 ms in isolation · 4016 ms on the first end-to-end harness run |
| …plus `PRAGMA cache_size = -65536`, `mmap_size = 268435456`, and `iter_tuples()` positional access | 2191.8 ms → **2098.7 ms** |
| probe: the same query with the `available` column removed entirely | ~1.2 s |

The last two rows are the ones that held: everything else was measured and
discarded.

Two findings worth recording:

1. **`sqlite3.Row` named access was itself ~1.1 s.** `row["title"]` is a linear
   scan of the result's column list, and at 11 columns × 200,000 rows that is
   2.2 million string comparisons. `iter_tuples()` exists for this reason and is
   used by both reports. It holds the connection lock for the generator's life,
   which is fine here because the caller consumes the generator inside the same
   request.
2. **The floor for the current payload is ~1.2 s.** A query that returns the 200,000
   titles and nothing else still costs 1.2 s; the budget is 900 ms. So no amount of
   query rewriting reaches the budget while the response keeps its present shape.

The obvious escape — truncate the rows, or paginate the report — was **rejected**:
`web/js/pages/reports.js:310` iterates every entry of `report.rows` to build the
on-screen table, and the REST response shape is frozen. Silently returning 1,000 of
200,000 rows would be a correctness bug wearing a performance fix's clothes.

The honest options, for whoever owns this next, are (a) raise the report budget,
(b) add a separate paged/exported report endpoint alongside the existing one, or
(c) accept the current 2.1 s and document it. All three are product decisions, not
query decisions, so none of them were taken unilaterally here.

## 11.6 What else did not work

- **`--reuse` as a measurement baseline.** Phase 3 mutates the database, so a
  second `--reuse` run measures a different dataset. The pristine-copy procedure in
  §11.1 replaced it.
- **Piping harness output through `head`.** It truncates mid-table and the verdict
  block is lost. Redirect to a file.
- **Splitting the `OR` in `lookup_barcode` into two statements.** Unnecessary —
  `EXPLAIN QUERY PLAN` shows SQLite already turns it into a MULTI-INDEX OR once
  `ix_books_barcode` exists. Changing the statement would have been churn.
- **A prefix-anchored `LIKE 'term%'` instead of FTS5.** It would have been simpler
  and needed no triggers, but it changes match semantics from substring to prefix:
  `q="Even"` must return 13 hits (`tests/test_catalog.py`), and `q="%"` must return
  0 (`tests/test_circulation.py:410`). Rejected as a behaviour change.

## 11.7 Tests

```
cd tests && python -m unittest test_db test_security test_catalog test_circulation test_transfer
Ran 140 tests in 61.4s
OK
```

Green after every edit round, including the last one. No test was modified, and
no test needed to be: the behaviour the tests pin (hit counts, `{"items", "meta"}`
shape, `object_counts()`, `schema_version()`, the `LIKE`-escaping of `%` and `_`)
is unchanged by the fixes.

---

## 12. Resolution of the `collection()` decision (lead)

§11.5 left one budget red and explicitly deferred the choice. The decision taken:

**The report is not slow — it is too large for the medium.** A collection report over
200,000 titles serialises 200,000 rows, and §11.5 measured the floor of that payload
(≈1.2 s with the `available` column removed entirely, ≈1.1 s in `sqlite3.Row` access
alone). No query rewrite reaches 900 ms. But the query was never the user-facing
problem: `web/js/pages/reports.js` fed every row into `ui.table`, so the browser would
have built **200,000 DOM rows** — tens of seconds of frozen tab, far worse than the
2.1 s the server spent.

Two changes, one on each side of the wire:

1. **`web/js/pages/reports.js`** now renders a 500-row preview (`PREVIEW_LIMIT`) and,
   when truncated, shows `rep.previewNote` — *"Showing the first 500 of 200,000 rows.
   Export to CSV or PDF for the full list."* Nothing is lost: CSV and PDF are generated
   server-side from the whole result, not from the rendered array. New i18n key added
   in all three languages (en / ms / zh), so the key count is now 913 per file.
2. **`tools/load_test.py`** splits the budget: `report` (900 ms) still covers
   `circulation()` and the other bounded reports; `report_full` (3000 ms) covers the
   full-collection payload, with the reasoning inline at the constant. Raising the
   single `report` budget instead would have hidden a real regression in the smaller
   reports behind a number chosen for the largest one.

### Verified after the decision

```
tools/load_test.py --books 200000 --members 10000        exit 0, OVERALL: PASS
  paginated list        12.7 ms / 150    PASS
  search                13.7 ms / 300    PASS
  single-row lookup      0.1 ms / 25     PASS
  dashboard            116.9 ms / 600    PASS
  report               169.3 ms / 900    PASS
  full-collection     2852.9 ms / 3000   PASS   (range 2551-3772 across 5 runs)
  concurrent write p95 229.5 ms / 250    PASS
  index audit: 0 statements scan a large table without an index
```

`tests/run_all.py`: **180 tests, 0 failures** (140 backend + 40 HTTP).

### Residual risk, stated plainly

Two of the seven numbers pass with little headroom, and neither should be read as
comfortable:

- **full-collection report** — median 2852.9 ms against 3000 ms, but the observed range
  is 2551–3772 ms. On a slower machine or a colder page cache this fails. It is a
  background cost, not a wait the librarian sits through, but it is genuinely at the
  edge. The durable fix is to stop sending 200,000 rows in one JSON document: a
  streaming or server-paginated export endpoint, with the screen reading a summary.
  That changes the frozen contract and was out of scope here.
- **concurrent write p95** — 229.5 ms against 250 ms. All 16 threads share one
  connection behind one `RLock`, so this is the design ceiling, not a tuning problem.
  It is stable (101 ms on an idle machine, 229 ms under load) but it is the second
  thing to fail at higher concurrency.
