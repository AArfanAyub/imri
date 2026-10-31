"""Persistence: schema, migration, transaction, settings, audit.

This is the **only** module in IMRI that imports :mod:`sqlite3`. Every other
module talks to the database through the :class:`Database` object handed to it,
which is why ``grep -r "import sqlite3" imri/`` returns exactly one hit.

Design notes (ARCHITECTURE §3.1):

* One file, one long-lived connection, ``isolation_level=None`` so IMRI owns
  the transaction boundaries explicitly.
* ``journal_mode=WAL`` + ``synchronous=NORMAL`` + ``busy_timeout=5000``.
* A single :class:`threading.RLock` serialises writes, because
  ``ThreadingHTTPServer`` serves requests on separate threads.
* Multi-statement mutations use :meth:`Database.transaction`, which issues
  ``BEGIN IMMEDIATE`` so a row and its audit entry are one journal write.
* Every statement is parameterised. The only dynamic SQL is a ``SET`` clause
  built from a frozen tuple of column names validated against a set literal.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
from typing import Any, Iterable, Iterator, Mapping, Sequence

from . import config, errors
from .config import get_logger

_log = get_logger("db")

#: Re-exported so the domain modules can catch integrity failures and annotate
#: connection parameters without importing :mod:`sqlite3` themselves. Keeping
#: the driver import in one file is what makes the docstring above true.
IntegrityError = sqlite3.IntegrityError
Error = sqlite3.Error
Connection = sqlite3.Connection

# --------------------------------------------------------------------------- #
# DDL — copied verbatim from ARCHITECTURE §3.3 (executed and verified)
# --------------------------------------------------------------------------- #

DDL = """
-- ============================================================ 0. meta
CREATE TABLE IF NOT EXISTS schema_meta (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    version     INTEGER NOT NULL,
    app_version TEXT,
    applied_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);

-- ============================================================ 1. people
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT    NOT NULL COLLATE NOCASE UNIQUE,
    password_hash        TEXT    NOT NULL,
    display_name         TEXT    NOT NULL DEFAULT '',
    role                 TEXT    NOT NULL DEFAULT 'assistant'
                         CHECK (role IN ('admin','librarian','assistant','viewer')),
    is_active            INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0,1)),
    password_changed_at  TEXT,
    last_login_at        TEXT,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    client       TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_sessions_exp  ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS members (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    member_code     TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    member_type     TEXT NOT NULL DEFAULT 'student'
                    CHECK (member_type IN ('student','teacher','patron')),
    ic_number       TEXT,
    form            TEXT,
    class_name      TEXT,
    address         TEXT,
    phone           TEXT,
    email           TEXT,
    photo_path      TEXT,
    membership_date TEXT NOT NULL,
    expiry_date     TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','suspended','expired')),
    note            TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_members_name   ON members(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_members_ic     ON members(ic_number);
CREATE INDEX IF NOT EXISTS ix_members_status ON members(status);
CREATE INDEX IF NOT EXISTS ix_members_class  ON members(class_name);

-- ============================================================ 2. catalogue
CREATE TABLE IF NOT EXISTS books (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    accession_no   TEXT NOT NULL UNIQUE,
    isbn           TEXT,
    isbn10         TEXT,
    title          TEXT NOT NULL,
    subtitle       TEXT,
    authors        TEXT,
    publisher      TEXT,
    pub_year       TEXT,
    edition        TEXT,
    language       TEXT NOT NULL DEFAULT 'en',
    dewey          TEXT,
    classification TEXT NOT NULL DEFAULT 'ddc' CHECK (classification IN ('ddc','lcc','local')),
    call_no        TEXT,
    subjects       TEXT,
    material_type  TEXT NOT NULL DEFAULT 'book'
                   CHECK (material_type IN ('book','textbook','reference','magazine',
                                            'av','kit','thesis','other')),
    location       TEXT,
    copies         INTEGER NOT NULL DEFAULT 1 CHECK (copies >= 0),
    price          REAL    NOT NULL DEFAULT 0 CHECK (price >= 0),
    currency       TEXT    NOT NULL DEFAULT 'RM',
    synopsis       TEXT,
    marc_blob      TEXT,
    cover_path     TEXT,
    barcode        TEXT,
    status         TEXT NOT NULL DEFAULT 'available'
                   CHECK (status IN ('available','on_loan','unavailable','lost','withdrawn')),
    source         TEXT NOT NULL DEFAULT 'manual',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_books_isbn     ON books(isbn);
CREATE INDEX IF NOT EXISTS ix_books_title    ON books(title COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_books_author   ON books(authors COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS ix_books_dewey    ON books(dewey);
CREATE INDEX IF NOT EXISTS ix_books_callno   ON books(call_no);
CREATE INDEX IF NOT EXISTS ix_books_material ON books(material_type);
CREATE INDEX IF NOT EXISTS ix_books_lang     ON books(language);
CREATE INDEX IF NOT EXISTS ix_books_status   ON books(status);

CREATE TABLE IF NOT EXISTS book_copies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    copy_no     INTEGER NOT NULL CHECK (copy_no >= 1),
    barcode     TEXT    NOT NULL UNIQUE,
    status      TEXT    NOT NULL DEFAULT 'available'
                CHECK (status IN ('available','on_loan','reserved','lost','damaged','withdrawn')),
    location    TEXT,
    condition   TEXT    CHECK (condition IS NULL OR condition IN ('new','good','fair','poor')),
    acquired_at TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (book_id, copy_no)
);
CREATE INDEX IF NOT EXISTS ix_copies_book   ON book_copies(book_id, status);
CREATE INDEX IF NOT EXISTS ix_copies_status ON book_copies(status);

CREATE TABLE IF NOT EXISTS subjects (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    heading    TEXT NOT NULL COLLATE NOCASE UNIQUE,
    scope_note TEXT
);
CREATE TABLE IF NOT EXISTS book_subjects (
    book_id    INTEGER NOT NULL REFERENCES books(id)    ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    PRIMARY KEY (book_id, subject_id)
);
CREATE INDEX IF NOT EXISTS ix_booksub_subject ON book_subjects(subject_id);

CREATE TABLE IF NOT EXISTS book_languages (
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    lang    TEXT    NOT NULL,
    PRIMARY KEY (book_id, lang)
);

CREATE TABLE IF NOT EXISTS authorities (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    type TEXT NOT NULL DEFAULT 'person'
         CHECK (type IN ('person','corporate','conference'))
);

-- ============================================================ 3. circulation
CREATE TABLE IF NOT EXISTS loans (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id       INTEGER NOT NULL REFERENCES books(id)       ON DELETE RESTRICT,
    copy_id       INTEGER          REFERENCES book_copies(id) ON DELETE RESTRICT,
    accession_no  TEXT    NOT NULL,
    member_id     INTEGER NOT NULL REFERENCES members(id)     ON DELETE RESTRICT,
    member_code   TEXT    NOT NULL,
    issue_date    TEXT    NOT NULL,
    due_date      TEXT    NOT NULL,
    return_date   TEXT,
    renewal_count INTEGER NOT NULL DEFAULT 0 CHECK (renewal_count >= 0),
    status        TEXT    NOT NULL DEFAULT 'on_loan'
                  CHECK (status IN ('on_loan','returned','lost','damaged')),
    fine_amount   REAL    NOT NULL DEFAULT 0 CHECK (fine_amount   >= 0),
    paid_amount   REAL    NOT NULL DEFAULT 0 CHECK (paid_amount   >= 0),
    waived_amount REAL    NOT NULL DEFAULT 0 CHECK (waived_amount >= 0),
    currency      TEXT    NOT NULL DEFAULT 'RM',
    issued_by     TEXT,
    returned_by   TEXT,
    note          TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
-- A copy may be on loan at most once. Database invariant, not app hope.
CREATE UNIQUE INDEX IF NOT EXISTS ux_loans_active_copy ON loans(copy_id) WHERE status = 'on_loan';
CREATE INDEX IF NOT EXISTS ix_loans_open   ON loans(status, due_date);
CREATE INDEX IF NOT EXISTS ix_loans_member ON loans(member_id, status);
CREATE INDEX IF NOT EXISTS ix_loans_book   ON loans(book_id, status);
CREATE INDEX IF NOT EXISTS ix_loans_copy   ON loans(copy_id, status);
CREATE INDEX IF NOT EXISTS ix_loans_issue  ON loans(issue_date);
CREATE INDEX IF NOT EXISTS ix_loans_due    ON loans(due_date);

CREATE TABLE IF NOT EXISTS fine_payments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id    INTEGER NOT NULL REFERENCES loans(id)   ON DELETE CASCADE,
    member_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE RESTRICT,
    amount     REAL    NOT NULL CHECK (amount > 0),
    paid_at    TEXT    NOT NULL,
    receipt_no TEXT    NOT NULL UNIQUE,
    method     TEXT    NOT NULL DEFAULT 'cash' CHECK (method IN ('cash','other')),
    actor      TEXT    NOT NULL,
    note       TEXT,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pay_loan   ON fine_payments(loan_id);
CREATE INDEX IF NOT EXISTS ix_pay_member ON fine_payments(member_id, paid_at);

CREATE TABLE IF NOT EXISTS reservations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id       INTEGER NOT NULL REFERENCES books(id)   ON DELETE CASCADE,
    member_id     INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    accession_no  TEXT    NOT NULL,
    member_code   TEXT    NOT NULL,
    copy_id       INTEGER          REFERENCES book_copies(id) ON DELETE SET NULL,
    reserved_date TEXT    NOT NULL,
    queue_position INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open','ready','fulfilled','expired','cancelled')),
    ready_at      TEXT,
    expires_at    TEXT,
    note          TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
-- One live reservation per member per title.
CREATE UNIQUE INDEX IF NOT EXISTS ux_res_active
    ON reservations(book_id, member_id) WHERE status IN ('open','ready');
CREATE INDEX IF NOT EXISTS ix_res_queue  ON reservations(book_id, status, queue_position);
CREATE INDEX IF NOT EXISTS ix_res_member ON reservations(member_id, status);

CREATE TABLE IF NOT EXISTS reading_lists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id  INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    book_id    INTEGER NOT NULL REFERENCES books(id)   ON DELETE CASCADE,
    note       TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (member_id, book_id)
);
CREATE INDEX IF NOT EXISTS ix_rl_member ON reading_lists(member_id, sort_order);

-- ============================================================ 4. audit + import
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'system',
    actor_id    INTEGER,
    action      TEXT NOT NULL,
    target      TEXT,
    target_type TEXT,
    detail      TEXT,
    ip          TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_ts     ON audit_log(ts);
CREATE INDEX IF NOT EXISTS ix_audit_actor  ON audit_log(actor, ts);
CREATE INDEX IF NOT EXISTS ix_audit_action ON audit_log(action, ts);

CREATE TRIGGER IF NOT EXISTS trg_audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TABLE IF NOT EXISTS import_batches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filename     TEXT,
    kind         TEXT NOT NULL DEFAULT 'books' CHECK (kind IN ('books','members')),
    row_count    INTEGER NOT NULL DEFAULT 0,
    imported     INTEGER NOT NULL DEFAULT 0,
    rejected     INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'preview'
                 CHECK (status IN ('preview','committed','cancelled','failed')),
    actor        TEXT,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS import_batch_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id  INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    row_no    INTEGER NOT NULL,
    key_value TEXT,
    status    TEXT NOT NULL CHECK (status IN ('ok','error','warning')),
    code      TEXT,
    message   TEXT
);
CREATE INDEX IF NOT EXISTS ix_ibi_batch ON import_batch_items(batch_id, status);
"""

#: Tables whose row counts ``GET /api/system/info`` reports.
ROW_COUNT_TABLES = (
    "books",
    "book_copies",
    "members",
    "loans",
    "reservations",
    "fine_payments",
    "users",
    "audit_log",
    "subjects",
    "reading_lists",
)


# --------------------------------------------------------------------------- #
# Schema 2 — read-path indexes and the FTS5 catalogue search index
# --------------------------------------------------------------------------- #

#: Indexes added in schema 2. Every one of them removes a specific full scan
#: measured in ``docs/LOAD-TEST.md``; the comment above each names the query it
#: exists for, so a later reader can tell whether it is still earning its keep.
#:
#: They live in a migration step rather than in :data:`DDL` so an existing
#: library is upgraded in place — the DDL script runs before the steps, and
#: creating an index for a column a step has not added yet is exactly the bug
#: that made legacy files unstartable in the reference implementation.
PERFORMANCE_INDEXES = """
-- lookup_barcode's *miss* path. `barcode = ? OR accession_no = ?` degraded to
-- `SCAN books` because only one branch of the OR was indexable; with this the
-- planner builds a MULTI-INDEX OR and both branches are seeks.
CREATE INDEX IF NOT EXISTS ix_books_barcode        ON books(barcode);
-- dashboard: "titles added in the last 30 days" (was SCAN books).
CREATE INDEX IF NOT EXISTS ix_books_created        ON books(created_at);
-- dashboard: "copies added in the last 30 days" (was SCAN book_copies).
CREATE INDEX IF NOT EXISTS ix_copies_created       ON book_copies(created_at);
-- dashboard: "members joined in the last 30 days" (was SCAN members).
CREATE INDEX IF NOT EXISTS ix_members_membership   ON members(membership_date);
-- dashboard: "returned in the last 30 days" and the average loan length. Both
-- filter on return_date and read issue_date; return_date had no index at all.
CREATE INDEX IF NOT EXISTS ix_loans_return         ON loans(return_date, issue_date);
-- dashboard: `overdue_then` is a range on due_date narrowed by two more
-- columns; covering all three (plus fine_amount, which the same shape needs for
-- `fines_30`) turns 7,500 full-row fetches into an index range scan.
CREATE INDEX IF NOT EXISTS ix_loans_due_cover      ON loans(due_date, issue_date, return_date, fine_amount);
-- circulation report: both leaderboards are one issue_date range grouped by
-- book_id / member_id; these make them index-only.
CREATE INDEX IF NOT EXISTS ix_loans_issue_book     ON loans(issue_date, book_id);
CREATE INDEX IF NOT EXISTS ix_loans_issue_member   ON loans(issue_date, member_id);
-- members.profile: a member's few live loans, ordered by due_date. Without the
-- third column the planner walked all 25,000 live loans to satisfy the sort.
CREATE INDEX IF NOT EXISTS ix_loans_member_due     ON loans(member_id, status, due_date);
-- list_books with a filter: the filter index supplied the filter but not the
-- order, so SQLite materialised up to 193,828 full rows into a temp B-tree.
CREATE INDEX IF NOT EXISTS ix_books_material_title ON books(material_type, title COLLATE NOCASE, id);
CREATE INDEX IF NOT EXISTS ix_books_status_title   ON books(status, title COLLATE NOCASE, id);
-- reporting.collection(): the report's only ordering is (dewey, title) and it
-- reads nine columns, so covering all of them makes it an index scan instead of
-- 200,000 scattered row fetches.
CREATE INDEX IF NOT EXISTS ix_books_report         ON books(
    dewey, title COLLATE NOCASE, accession_no, authors, material_type,
    call_no, status, price, copies);
-- dashboard "fines outstanding" and the fine ledger: a whole-table SUM over
-- three money columns, which is index-only once they are all in the index.
CREATE INDEX IF NOT EXISTS ix_loans_fines          ON loans(fine_amount, paid_amount, waived_amount);
-- dashboard "stale holds": ready holds whose expiry has passed.
CREATE INDEX IF NOT EXISTS ix_res_status_expires   ON reservations(status, expires_at);
"""

#: The FTS5 virtual table that backs free-text catalogue search.
SEARCH_TABLE = "books_fts"

#: The columns the search index covers. This tuple is the single source of
#: truth: the virtual table, its triggers and the ``MATCH`` built by
#: :mod:`imri.catalog` are all derived from it, so the index and the query
#: cannot drift apart. It is the same eleven columns the old ``LIKE`` scan
#: compared against.
SEARCH_COLUMNS: tuple[str, ...] = (
    "title",
    "subtitle",
    "authors",
    "isbn",
    "isbn10",
    "call_no",
    "subjects",
    "publisher",
    "accession_no",
    "barcode",
    "location",
)

#: Triggers that keep :data:`SEARCH_TABLE` in step with ``books``. Named so the
#: load-test seeder can suspend them around a bulk insert.
SEARCH_TRIGGERS: tuple[str, ...] = (
    "trg_books_fts_ai",
    "trg_books_fts_ad",
    "trg_books_fts_au",
)

#: Everything ``books_fts`` brings into ``sqlite_master``. An FTS5 virtual table
#: is one logical object made of five tables; ``object_counts`` reports the
#: schema a librarian can see, so it hides these the same way it hides
#: ``sqlite_%``.
SEARCH_INTERNAL_OBJECTS: frozenset[str] = frozenset(
    {SEARCH_TABLE}
    | {SEARCH_TABLE + suffix for suffix in ("_data", "_idx", "_docsize", "_config", "_content")}
    | set(SEARCH_TRIGGERS)
)


def search_index_ddl() -> str:
    """Return the DDL that creates the FTS5 index and its three sync triggers.

    The table is ``content='books'``: it stores only the inverted index and
    reads the text back from ``books``, so a 200,000-title catalogue pays about
    29 MB rather than duplicating eleven text columns.

    The update trigger is ``AFTER UPDATE OF <indexed columns>`` on purpose.
    ``refresh_book_rollup`` rewrites ``copies``, ``status`` and ``updated_at``
    on every issue and every return; without the column list this trigger would
    re-index a title on every circulation operation.
    """
    columns = ", ".join(SEARCH_COLUMNS)
    new_values = ", ".join("new.%s" % column for column in SEARCH_COLUMNS)
    old_values = ", ".join("old.%s" % column for column in SEARCH_COLUMNS)
    delete_args = ", ".join(["'delete'", "old.id"] + ["old.%s" % c for c in SEARCH_COLUMNS])
    return """
CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5(
    {columns},
    content='books',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS {insert_trigger} AFTER INSERT ON books BEGIN
    INSERT INTO {table}(rowid, {columns}) VALUES (new.id, {new_values});
END;

CREATE TRIGGER IF NOT EXISTS {delete_trigger} AFTER DELETE ON books BEGIN
    INSERT INTO {table}({table}, rowid, {columns}) VALUES ({delete_args});
END;

CREATE TRIGGER IF NOT EXISTS {update_trigger} AFTER UPDATE OF {columns} ON books BEGIN
    INSERT INTO {table}({table}, rowid, {columns}) VALUES ({delete_args});
    INSERT INTO {table}(rowid, {columns}) VALUES (new.id, {new_values});
END;
""".format(
        table=SEARCH_TABLE,
        columns=columns,
        new_values=new_values,
        old_values=old_values,
        delete_args=delete_args,
        insert_trigger=SEARCH_TRIGGERS[0],
        delete_trigger=SEARCH_TRIGGERS[1],
        update_trigger=SEARCH_TRIGGERS[2],
    )


def fts5_available(conn: sqlite3.Connection) -> bool:
    """Return True when this SQLite build can create an FTS5 table.

    Probed with a throwaway ``temp`` table so a build without FTS5 leaves no
    trace in the file. SQLite ships FTS5 by default from 3.9 onward and the
    bundled interpreter has it, but a stripped system library may not, and a
    school must never be unable to open its own library because of it.
    """
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.__imri_fts_probe USING fts5(x)")
        conn.execute("DROP TABLE temp.__imri_fts_probe")
        return True
    except sqlite3.Error:  # pragma: no cover - depends on the SQLite build
        return False


def _search_objects_present(conn: sqlite3.Connection) -> bool:
    """True when the index table and all three triggers already exist."""
    wanted = (SEARCH_TABLE,) + SEARCH_TRIGGERS
    placeholders = ",".join("?" for _ in wanted)
    found = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN (%s)" % placeholders, wanted
        )
    }
    return len(found) == len(wanted)


def _search_index_is_empty(conn: sqlite3.Connection) -> bool:
    """True when the index holds no row at all."""
    return conn.execute("SELECT 1 FROM %s LIMIT 1" % SEARCH_TABLE).fetchone() is None


def rebuild_search_index(conn: sqlite3.Connection) -> bool:
    """Repopulate the search index from ``books``."""
    try:
        conn.execute("INSERT INTO %s(%s) VALUES('rebuild')" % (SEARCH_TABLE, SEARCH_TABLE))
        return True
    except sqlite3.Error:  # pragma: no cover - defensive
        _log.exception("catalogue search index rebuild failed")
        return False


def ensure_search_objects(conn: sqlite3.Connection) -> bool:
    """Create the search index and its triggers if they are missing.

    Idempotent and cheap when everything is already in place (one
    ``sqlite_master`` lookup). Called on every open, and it doubles as the
    repair path: a run that was killed between :meth:`Database.begin_bulk_load`
    and :meth:`Database.end_bulk_load` leaves the triggers dropped, and the
    missing trigger is exactly the signal that the index has to be rebuilt
    before it can be trusted again.
    """
    if _search_objects_present(conn):
        return True
    if not fts5_available(conn):
        _log.warning("FTS5 is unavailable; catalogue search will use the LIKE scan")
        return False
    try:
        conn.executescript(search_index_ddl())
    except sqlite3.Error:  # pragma: no cover - defensive
        _log.exception("could not create the catalogue search index")
        return False
    return rebuild_search_index(conn)


def _step_catalogue_search_index(conn: sqlite3.Connection) -> None:
    """Schema 2: read-path indexes, then the FTS5 catalogue search index.

    Safe to re-run. The rebuild is what makes an existing library searchable
    immediately after the upgrade; on a fresh file ``books`` is empty, so it
    costs nothing. ``ensure_search_objects`` has already rebuilt the index when
    it had to create it, which is why this only fills an index that is still
    empty.
    """
    conn.executescript(PERFORMANCE_INDEXES)
    if not ensure_search_objects(conn):
        return
    if _search_index_is_empty(conn):
        rebuild_search_index(conn)



# --------------------------------------------------------------------------- #
# Migrator
# --------------------------------------------------------------------------- #


def _noop(_conn: sqlite3.Connection) -> None:
    """Migration step placeholder for versions with no work to do."""


class Migrator:
    """Idempotent, forward-only schema migration (ARCHITECTURE §3.2).

    Safe to run on every start, on a fresh file, and on a file written by any
    older build.

    Order matters and is deliberate: ``executescript(DDL)`` → read version →
    run steps → write version. Creating an index before the migration that
    adds its column is exactly the bug that made legacy databases unstartable
    in the reference implementation.
    """

    target: int = config.SCHEMA_VERSION

    #: ``{version: callable(conn)}``. Each step must be safe to re-run.
    #: Reached through the dict, so the values stay plain functions.
    STEPS: dict[int, Any] = {
        2: _step_catalogue_search_index,
    }

    def version(self, conn: sqlite3.Connection) -> int:
        """Return the schema version stored in the file (0 when absent)."""
        try:
            row = conn.execute("SELECT version FROM schema_meta WHERE id = 1").fetchone()
        except sqlite3.Error:
            return 0
        return int(row[0]) if row else 0

    def ensure(self, conn: sqlite3.Connection) -> int:
        """Apply the DDL, run pending steps, and record the target version."""
        conn.executescript(DDL)

        # Not a numbered step: this is the repair path for a file whose search
        # triggers were dropped mid-bulk-load. It is one ``sqlite_master``
        # lookup when everything is already in place.
        ensure_search_objects(conn)

        current = self.version(conn)
        if current > self.target:
            raise errors.ImriError(
                errors.DB_NEWER_THAN_APP,
                errors.status_for(errors.DB_NEWER_THAN_APP),
                details={"found": current, "supported": self.target},
            )

        for step_version in range(current + 1, self.target + 1):
            step = self.STEPS.get(step_version, _noop)
            step(conn)

        conn.execute(
            "INSERT INTO schema_meta(id, version, app_version, applied_at) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET version = excluded.version, "
            "app_version = excluded.app_version, applied_at = excluded.applied_at",
            (self.target, config.APP_VERSION, config.now_utc()),
        )
        conn.commit()
        return self.target

    # -- column helpers used by future migration steps --------------------- #
    @staticmethod
    def columns(conn: sqlite3.Connection, table: str) -> set[str]:
        """Return the current column names of ``table``."""
        return {row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)}

    @classmethod
    def add_column(cls, conn: sqlite3.Connection, table: str, column: str, decl: str) -> bool:
        """``ALTER TABLE ADD COLUMN`` guarded by ``PRAGMA table_info``.

        Only constant defaults are legal here — no ``datetime('now')``.
        """
        if column in cls.columns(conn, table):
            return False
        conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, column, decl))
        return True


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #


class Database:
    """A thin, safe wrapper around one long-lived SQLite connection."""

    def __init__(self, path: str | os.PathLike[str] | None = None, migrate: bool = True) -> None:
        self.path = str(path) if path is not None else str(config.db_path())
        self._conn_obj: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._tx_depth = 0
        self._closed = False
        self._has_search: bool | None = None
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        if migrate:
            self._bootstrap()

    # -- connection ------------------------------------------------------- #
    @property
    def conn(self) -> sqlite3.Connection:
        """Return the shared connection, opening it on first use."""
        if self._conn_obj is None:
            with self._lock:
                if self._conn_obj is None:
                    self._conn_obj = self._open()
                    self._closed = False
        return self._conn_obj

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        for pragma in (
            "PRAGMA journal_mode = WAL",
            "PRAGMA synchronous = NORMAL",
            "PRAGMA busy_timeout = 5000",
            # A 200,000-title library is a 300 MB file and the read paths walk
            # large index ranges: the collection report alone touches every
            # ``books`` row and probes ``book_copies`` 200,000 times. SQLite's
            # 2 MB default page cache means those probes re-read pages from the
            # OS each time. 64 MB of page cache plus a read-only mapping of the
            # file took the collection report from 2.4 s to 1.6 s and cannot
            # corrupt anything — both are read-side caches.
            "PRAGMA cache_size = -65536",
            "PRAGMA mmap_size = 268435456",
        ):
            try:
                conn.execute(pragma)
            except sqlite3.Error:  # pragma: no cover - some filesystems refuse WAL
                _log.warning("pragma failed: %s", pragma)
        return conn

    def _bootstrap(self) -> None:
        """Run the DDL, the migrator, the settings seed and the admin seed."""
        conn = self.conn
        Migrator().ensure(conn)
        self.seed_settings()
        self.seed_admin()

    # -- raw access ------------------------------------------------------- #
    @staticmethod
    def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
        if cursor.description is None:
            return []
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, tuple(row))) for row in cursor.fetchall()]

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Run a SELECT and return a list of dicts."""
        with self._lock:
            cursor = self.conn.execute(sql, tuple(params))
            return self._rows(cursor)

    def iter_tuples(
        self, sql: str, params: Sequence[Any] = ()
    ) -> Iterator[tuple[Any, ...]]:
        """Stream a SELECT as plain tuples, for reports that read every row.

        :meth:`query` materialises a list of dicts, which costs roughly 700 ms
        for the 200,000 rows a full collection report returns. Yielding tuples
        instead skips that, but it has to be *tuples*: ``sqlite3.Row`` resolves
        ``row["title"]`` by scanning the result's column list, so named access
        costs a linear search per column per row — about 1.6 s across the
        collection report. ``row[1]`` is a tuple index.

        Callers must therefore read by position and keep the column order in
        the SQL and in the loop next to each other.

        The connection lock is held until the iterator is exhausted or closed,
        matching :meth:`query`'s guarantee that no other thread's statement
        interleaves with a read. Do not mutate the rows being streamed.
        """
        with self._lock:
            cursor = self.conn.execute(sql, tuple(params))
            cursor.row_factory = None
            for row in cursor:
                yield row

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        """Run a SELECT and return the first row as a dict, or ``None``."""
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        """Run a single-value SELECT and return the first column."""
        row = self.one(sql, params)
        if not row:
            return default
        return next(iter(row.values()))

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a write statement.

        Returns the new rowid for an ``INSERT`` and the affected row count for
        anything else. Inside :meth:`transaction` the statement joins the open
        transaction instead of committing on its own.
        """
        with self._lock:
            cursor = self.conn.execute(sql, tuple(params))
            if self._tx_depth == 0:
                self.conn.commit()
            head = sql.lstrip()[:6].upper()
            if head.startswith("INSERT"):
                return int(cursor.lastrowid or 0)
            return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> int:
        """Run a write statement for each parameter tuple."""
        with self._lock:
            cursor = self.conn.executemany(sql, [tuple(r) for r in rows])
            if self._tx_depth == 0:
                self.conn.commit()
            return int(cursor.rowcount if cursor.rowcount is not None else 0)

    def executescript(self, sql: str) -> None:
        """Run a multi-statement script (DDL only)."""
        with self._lock:
            self.conn.executescript(sql)
            if self._tx_depth == 0:
                self.conn.commit()

    # -- transactions ----------------------------------------------------- #
    @property
    def busy(self) -> bool:
        """True while a transaction is open (the shutdown loop checks this)."""
        return self._tx_depth > 0

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run several writes as one atomic journal write.

        Issues ``BEGIN IMMEDIATE`` so the write lock is taken up front (no
        upgrade deadlock), and ``ROLLBACK`` on any exception so an
        :class:`~imri.errors.ImriError` raised mid-way leaves no partial write
        and no orphan audit row.

        Re-entrant: a nested call joins the enclosing transaction rather than
        failing with "cannot start a transaction within a transaction".
        """
        with self._lock:
            if self._tx_depth > 0:
                self._tx_depth += 1
                try:
                    yield self.conn
                finally:
                    self._tx_depth -= 1
                return

            conn = self.conn
            conn.execute("BEGIN IMMEDIATE")
            self._tx_depth = 1
            try:
                yield conn
            except BaseException:
                self._tx_depth = 0
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:  # pragma: no cover - already rolled back
                    pass
                raise
            else:
                self._tx_depth = 0
                conn.execute("COMMIT")

    @contextlib.contextmanager
    def exclusive(self) -> Iterator["Database"]:
        """Hold the write lock for the whole block (backup/restore).

        The connection is closed for the duration so the underlying file can be
        replaced, then reopened before the block exits.
        """
        with self._lock:
            self.close()
            try:
                yield self
            finally:
                self.reopen()

    # -- settings --------------------------------------------------------- #
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Return one setting value (a string), or ``default``."""
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def get_setting_int(self, key: str, default: int) -> int:
        """Return one setting coerced to ``int``."""
        return config.to_int(self.get_setting(key, default), default)

    def get_setting_float(self, key: str, default: float) -> float:
        """Return one setting coerced to ``float``."""
        return config.to_float(self.get_setting(key, default), default)

    def set_setting(self, key: str, value: Any) -> None:
        """Upsert one setting."""
        self.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, "" if value is None else str(value), config.now_utc()),
        )

    def all_settings(self) -> dict[str, str]:
        """Return every setting as ``{key: value}``."""
        return {row["key"]: row["value"] for row in self.query("SELECT key, value FROM settings")}

    def seed_settings(self) -> int:
        """Insert any missing default setting. Never overwrites a live value."""
        now = config.now_utc()
        inserted = 0
        with self.transaction() as conn:
            for key, value in config.DEFAULT_SETTINGS.items():
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, now),
                )
                inserted += 1 if cursor.rowcount else 0
        return inserted

    def seed_admin(self) -> bool:
        """Create ``admin`` / ``admin123`` when the users table is empty."""
        if self.scalar("SELECT COUNT(*) FROM users", default=0):
            return False
        from . import security  # local import: keeps the module graph acyclic

        now = config.now_utc()
        self.execute(
            "INSERT INTO users(username, password_hash, display_name, role, is_active, "
            "must_change_password, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 1, ?, ?)",
            (
                security.DEFAULT_ADMIN_USERNAME,
                security.hash_password(security.DEFAULT_ADMIN_PASSWORD),
                "Administrator",
                "admin",
                now,
                now,
            ),
        )
        _log.info("seeded default administrator account")
        return True

    # -- audit ------------------------------------------------------------ #
    def audit(
        self,
        actor: str,
        action: str,
        target: Any = None,
        detail: Any = None,
        ip: str | None = None,
        target_type: str | None = None,
        actor_id: int | None = None,
    ) -> int:
        """Append one audit row (append-only, enforced by two triggers).

        ``detail`` may be a dict; it is serialised to JSON. Never pass
        passwords, hashes, the activation code, or full card numbers.
        """
        if isinstance(detail, (dict, list)):
            detail_text = json.dumps(detail, ensure_ascii=False, sort_keys=True)
        elif detail is None:
            detail_text = None
        else:
            detail_text = str(detail)
        return self.execute(
            "INSERT INTO audit_log(ts, actor, actor_id, action, target, target_type, detail, ip) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                config.now_utc(),
                actor or "system",
                actor_id,
                action,
                None if target is None else str(target),
                target_type,
                detail_text,
                ip,
            ),
        )

    # -- maintenance ------------------------------------------------------ #
    def checkpoint(self, mode: str = "TRUNCATE") -> bool:
        """Fold the WAL back into the main file so a copied folder is complete."""
        safe_mode = mode if mode in ("PASSIVE", "FULL", "RESTART", "TRUNCATE") else "TRUNCATE"
        try:
            with self._lock:
                self.conn.execute("PRAGMA wal_checkpoint(%s)" % safe_mode)
            return True
        except sqlite3.Error:  # pragma: no cover - defensive
            return False

    def integrity_check(self) -> dict[str, Any]:
        """Run ``PRAGMA integrity_check`` and report the result."""
        try:
            row = self.one("PRAGMA integrity_check")
            result = next(iter(row.values())) if row else "unknown"
            return {"ok": result == "ok", "result": result}
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            return {"ok": False, "result": str(exc)}

    def foreign_key_check(self) -> list[dict[str, Any]]:
        """Return any foreign-key violations (empty when the file is sound)."""
        try:
            return self.query("PRAGMA foreign_key_check")
        except sqlite3.Error:  # pragma: no cover - defensive
            return []

    def row_counts(self) -> dict[str, int]:
        """Return the row count of every user-visible table."""
        counts: dict[str, int] = {}
        for table in ROW_COUNT_TABLES:
            try:
                counts[table] = int(self.scalar("SELECT COUNT(*) FROM %s" % table, default=0))
            except sqlite3.Error:
                counts[table] = 0
        return counts

    def schema_version(self) -> int:
        """Return the version recorded in ``schema_meta``."""
        return Migrator().version(self.conn)

    # -- catalogue search index ------------------------------------------- #
    @property
    def has_search_index(self) -> bool:
        """True when this file has the FTS5 catalogue index.

        ``CatalogService`` asks once per filter build. A build without FTS5, or
        a file written by a schema-1 release that has not been upgraded, simply
        answers False and search falls back to the ``LIKE`` scan.
        """
        if self._has_search is None:
            try:
                self._has_search = bool(
                    self.scalar(
                        "SELECT COUNT(*) FROM sqlite_master "
                        "WHERE type = 'table' AND name = ?",
                        (SEARCH_TABLE,),
                        default=0,
                    )
                )
            except sqlite3.Error:  # pragma: no cover - defensive
                self._has_search = False
        return self._has_search

    def begin_bulk_load(self) -> bool:
        """Suspend the search-index triggers around a bulk insert.

        Returns True when the triggers were actually dropped, so the caller
        knows whether :meth:`end_bulk_load` has work to do. Reindexing 200,000
        titles row by row and rebuilding once afterwards cost about the same;
        the difference is that a rebuild cannot be derailed by one malformed
        row, and it keeps the seeding time independent of FTS5.
        """
        if not self.has_search_index:
            return False
        with self._lock:
            for name in SEARCH_TRIGGERS:
                self.conn.execute("DROP TRIGGER IF EXISTS %s" % name)
            self.conn.commit()
        return True

    def end_bulk_load(self) -> bool:
        """Rebuild the search index from ``books`` and restore its triggers."""
        if not self.has_search_index:
            return False
        with self._lock:
            rebuild_search_index(self.conn)
            self.conn.executescript(search_index_ddl())
            self.conn.commit()
        return True

    def object_counts(self) -> dict[str, int]:
        """Return counts of user tables, indexes and triggers (diagnostics).

        The five tables and three triggers that make up the FTS5 search index
        are the internals of one logical object, not schema the librarian can
        see, so they are excluded — the same reason ``sqlite_%`` is.
        """
        kinds = {"table": "tables", "index": "indexes", "trigger": "triggers"}
        counts = {"tables": 0, "indexes": 0, "triggers": 0}
        for row in self.query("SELECT type, name FROM sqlite_master"):
            name = str(row["name"])
            if name.startswith("sqlite_") or name in SEARCH_INTERNAL_OBJECTS:
                continue
            key = kinds.get(str(row["type"]))
            if key is not None:
                counts[key] += 1
        return counts

    def vacuum(self) -> bool:
        """Reclaim free pages. Offered from Diagnostics, never automatic."""
        try:
            with self._lock:
                self.conn.execute("VACUUM")
            return True
        except sqlite3.Error:  # pragma: no cover - defensive
            return False

    # -- lifecycle -------------------------------------------------------- #
    def close(self) -> None:
        """Fold the WAL back and release the shared connection."""
        with self._lock:
            conn = self._conn_obj
            if conn is None:
                return
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:  # pragma: no cover - defensive
                pass
            try:
                conn.close()
            except sqlite3.Error:  # pragma: no cover - defensive
                pass
            self._conn_obj = None
            self._tx_depth = 0
            self._has_search = None
            self._closed = True

    def reopen(self) -> None:
        """Reopen the shared connection after a restore replaced the file."""
        with self._lock:
            self._conn_obj = None
            self._has_search = None
            _ = self.conn
            self._closed = False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Database(%r)" % self.path


def rollup_mismatches(db: Database) -> list[dict[str, Any]]:
    """Return every book whose stored ``copies`` disagrees with ``book_copies``.

    ``GET /api/system/diagnostics`` reports this count; the whole point of
    funnelling every copy mutation through one helper is that it stays zero.
    """
    return db.query(
        "SELECT b.id, b.accession_no, b.title, b.copies AS stored, "
        "       COUNT(c.id) AS actual "
        "FROM books b LEFT JOIN book_copies c ON c.book_id = b.id "
        "GROUP BY b.id HAVING b.copies <> COUNT(c.id) ORDER BY b.id"
    )
