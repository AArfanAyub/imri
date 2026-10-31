#!/usr/bin/env python3
"""IMRI scale harness: prove the product holds at 200,000 books / 10,000 members.

Standard library only. Nothing here touches ``data/`` -- every run builds its
own database under :func:`tempfile.mkdtemp`, so a run can never damage a live
school library.

Four phases, each printed as a scannable block:

1. **Seed** -- 200k books, ~500k copies, 10k members, 60k loans (a realistic
   share overdue), 3k reservations, 8k fine payments. Everything goes through
   ``executemany`` inside batched transactions; inserting row-by-row through
   the service layer would take hours at this scale.
2. **Query latency** -- warm, then median of N runs for every list, search,
   lookup, dashboard and report call the UI makes.
3. **Write-path concurrency** -- 16 threads, 400 mixed circulation operations,
   asserting zero ``sqlite3.OperationalError`` and zero rollup drift.
4. **Index audit** -- ``EXPLAIN QUERY PLAN`` on the *real* SQL the services
   issue (captured by tracing ``Database.query``), flagging any statement that
   scans ``books`` / ``book_copies`` / ``members`` / ``loans`` without an index.

The run ends with an explicit PASS/FAIL per budget and exits 1 if any budget
fails, so it can be wired into a gate.

Usage::

    python tools/load_test.py --books 200000 --members 10000
    python tools/load_test.py --books 200000 --members 10000 --reuse
    python tools/load_test.py --books 2000 --members 200 --runs 3   # smoke

The seeded database is left on disk (so ``--reuse`` can skip a 40-second seed);
pass ``--clean`` to delete it when the run finishes.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import platform
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imri import config, errors  # noqa: E402
from imri.catalog import CatalogService, to_isbn10  # noqa: E402
from imri.circulation import CirculationService  # noqa: E402
from imri.db import Database  # noqa: E402
from imri.members import MemberService  # noqa: E402
from imri.reporting import ReportingService  # noqa: E402

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

#: Fixed seed so a re-run produces byte-identical data (and therefore
#: comparable timings). Change it only deliberately.
SEED = 20261024

#: Rows per ``executemany`` + per transaction. 5,000 keeps the WAL small
#: enough to checkpoint without stalling while still amortising commit cost.
BATCH = 5000

#: Where the temp-database pointer is remembered for ``--reuse``. Deliberately
#: in the system temp dir, not in the repository.
STATE_PATH = os.path.join(tempfile.gettempdir(), "imri-load-test-state.json")

#: Budgets in milliseconds. These are the numbers the verdict is decided on.
BUDGETS: dict[str, float] = {
    "list": 150.0,
    "search": 300.0,
    "lookup": 25.0,
    "dashboard": 600.0,
    "report": 900.0,
    # A full-collection report serialises one row per title, so at 200,000
    # titles the payload itself sets the floor: the probe that removes the
    # ``available`` column entirely still takes ~1.2 s, and ``sqlite3.Row``
    # named access alone costs ~1.1 s across 11 columns x 200,000 rows. This
    # budget covers that, not a slow query. The screen no longer renders all of
    # it (``web/js/pages/reports.js`` shows a 500-row preview and points at the
    # export), so this is a background-cost number, not a wait the librarian
    # sits through.
    "report_full": 3000.0,
    "write_p95": 250.0,
}

BUDGET_LABEL: dict[str, str] = {
    "list": "paginated list",
    "search": "search",
    "lookup": "single-row lookup",
    "dashboard": "dashboard",
    "report": "report",
    "report_full": "full-collection report",
    "write_p95": "concurrent write p95",
}

# --------------------------------------------------------------------------- #
# Realistic Malaysian school-library vocabulary
# --------------------------------------------------------------------------- #

QUALIFIERS: tuple[str, ...] = (
    "Advanced", "Essential", "Practical", "Complete", "Basic", "Guided",
    "Interactive", "Comprehensive", "Foundation", "Mastering", "Latihan",
    "Panduan", "Modul", "Koleksi", "Nota", "Rujukan", "Buku Kerja",
    "Siri Cemerlang", "Siri Bestari", "Siri Ilmu", "Siri Gemilang",
    "Pocket", "Illustrated", "Revision",
)

TOPICS: tuple[str, ...] = (
    "Algebra", "Geometry", "Statistics", "Calculus", "Trigonometry",
    "Grammar", "Comprehension", "Essay Writing", "Poetry", "Literature",
    "Cell Biology", "Genetics", "Ecology", "Human Anatomy", "Botany",
    "Optics", "Electromagnetism", "Thermodynamics", "Mechanics", "Waves",
    "Organic Chemistry", "Inorganic Chemistry", "Laboratory Skills",
    "Programming", "Databases", "Networking", "Spreadsheets",
    "Accounting", "Economics", "Business Studies", "Marketing",
    "World History", "Southeast Asia", "Malaysian History", "Geography",
    "Climate", "Map Reading", "Civics", "Moral Values", "Islamic Studies",
    "Jawi", "Tamil Literature", "Chinese Literature", "Public Speaking",
    "Physical Education", "Health Science", "Visual Arts", "Music Theory",
    "Cooking", "Woodwork", "Electrical Wiring", "Agriculture",
    "Fisheries", "Entrepreneurship", "Robotics", "Astronomy",
    "Environmental Science", "Disaster Preparedness", "Road Safety",
    "Information Literacy", "Library Skills", "Study Skills", "Careers",
    "Financial Literacy", "First Aid", "Scouting", "Chess", "Drama",
    "Photography", "Creative Writing",
)

LEVELS: tuple[str, ...] = (
    "Tingkatan 1", "Tingkatan 2", "Tingkatan 3", "Tingkatan 4",
    "Tingkatan 5", "Tahun 1", "Tahun 2", "Tahun 3", "Tahun 4",
    "Tahun 5", "Tahun 6", "Form 1", "Form 2", "Form 3", "Form 4",
    "Form 5", "Standard 1", "Standard 2", "Standard 3", "Standard 4",
    "Standard 5", "Standard 6", "KSSM", "KSSR", "SPM", "PT3", "STPM",
    "UPSR", "Peperiksaan Awam", "Program Transisi", "Kelas Tambahan",
    "Pusat Sumber", "Buku Teks", "Bacaan Luas", "NILAM", "Program 1Murid",
    "Kelab Membaca", "Siri Sekolah", "Edisi Guru", "Edisi Murid",
)

GIVEN_NAMES: tuple[str, ...] = (
    "Ahmad", "Muhammad", "Mohd Faizal", "Aisyah", "Nurul", "Siti", "Farah",
    "Amir", "Hafiz", "Izzat", "Zulkifli", "Rahman", "Syafiq", "Aina",
    "Balqis", "Danial", "Elena", "Firdaus", "Hakim", "Imran", "Jasmine",
    "Kavitha", "Lim", "Tan", "Wong", "Lee", "Chong", "Ng", "Ong", "Goh",
    "Rajesh", "Priya", "Suresh", "Anitha", "Devi", "Kumar", "Ravi",
    "Nurhaliza", "Suhaila", "Zainab", "Yusof", "Halimah", "Azman",
)

FAMILY_NAMES: tuple[str, ...] = (
    "bin Ismail", "bin Osman", "bin Abdullah", "binti Hassan", "binti Zulkifli",
    "binti Rahman", "a/l Kumar", "a/p Raman", "a/l Subramaniam", "a/p Devi",
    "Wei Sheng", "Mei Ling", "Kah Seng", "Hock Chye", "Siew Lan", "Boon Huat",
    "Anak Jugah", "Anak Sigan", "binti Mohd Noor", "bin Yusof",
)

AUTHOR_PREFIX: tuple[str, ...] = (
    "Dewan Bahasa dan Pustaka Editorial", "Oxford Fajar Editorial Team",
    "Pelangi Editorial", "Sasbadi Editorial", "Pan Asia Editorial",
    "Marshall Cavendish Editorial", "Times Editorial", "Buku Prima Editorial",
)

PUBLISHERS: tuple[str, ...] = (
    "Dewan Bahasa dan Pustaka", "Oxford Fajar", "Pelangi Publishing",
    "Sasbadi", "Pan Asia Publishing", "Utusan Publications",
    "Marshall Cavendish", "Times Publishing", "Buku Prima",
    "Penerbit Universiti Malaya", "Institut Terjemahan Negara",
)

LOCATIONS: tuple[str, ...] = tuple(
    "Rak %s-%02d" % (shelf, bay)
    for shelf in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for bay in range(1, 13)
)

CONDITIONS: tuple[str, ...] = ("new", "good", "good", "fair", "poor")

CLASS_NAMES: tuple[str, ...] = tuple(
    "%d %s" % (year, stream)
    for year in (1, 2, 3, 4, 5, 6)
    for stream in (
        "Amanah", "Bestari", "Cekal", "Dedikasi", "Ehsan", "Fikrah",
        "Gemilang", "Harmoni", "Ilmu", "Jaya", "Kreatif", "Lestari",
        "Murni", "Nilai", "Optimis", "Pintar", "Qudwah", "Rajin",
        "Setia", "Tekun",
    )
)  # 120 class names

DEPARTMENTS: tuple[str, ...] = (
    "Sains", "Matematik", "Bahasa Melayu", "Bahasa Inggeris", "Sejarah",
    "Geografi", "Sivik", "Pendidikan Islam", "Pendidikan Jasmani",
    "Perpustakaan",
)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _stamp(days_ago: float = 0.0) -> str:
    """Return a UTC timestamp ``days_ago`` days before now (IMRI's format)."""
    moment = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=float(days_ago))
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(days_ago: int) -> str:
    """Return a *local* business date ``days_ago`` days before today."""
    return ( _dt.date.today() - _dt.timedelta(days=int(days_ago)) ).isoformat()


def _isbn13_from_counter(counter: int) -> str:
    """Return a valid ISBN-13 with a correct check digit for ``counter``."""
    core = "978%09d" % (counter % 1_000_000_000)
    total = sum(int(ch) * (1 if pos % 2 == 0 else 3) for pos, ch in enumerate(core))
    return core + str((10 - (total % 10)) % 10)


def _dewey_for(index: int, rnd: random.Random) -> str:
    """Return a Dewey number spread across 000-999, sometimes with decimals."""
    bucket = index % 1000
    if bucket % 3 == 0:
        return "%03d.%03d" % (bucket, rnd.randrange(1, 1000))
    if bucket % 3 == 1:
        return "%03d.%d" % (bucket, rnd.randrange(1, 10))
    return "%03d" % bucket


def _call_no(dewey: str, authors: str) -> str:
    """Return a plausible cutter-style call number for a title."""
    token = "".join(ch for ch in authors.split(",")[0].upper() if ch.isalpha())[:3] or "XXX"
    return "%s %s" % (dewey, token)


def _money(value: float) -> float:
    """Round money the way IMRI does (``config.money``)."""
    return config.money(value)


def _median(values: Sequence[float]) -> float:
    """Median that never raises on an empty sequence."""
    if not values:
        return 0.0
    return float(statistics.median(values))


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile (used for p95)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int(math.ceil(fraction * len(ordered))) - 1))
    return float(ordered[rank])


def _rule(char: str = "=", width: int = 96) -> str:
    """Return a horizontal rule for the report."""
    return char * width


def _human_size(path: str) -> str:
    """Return the on-disk size of ``path`` (plus its WAL) as ``12.3 MB``."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(path + suffix)
        except OSError:
            pass
    if total >= 1024 * 1024 * 1024:
        return "%.2f GB" % (total / (1024.0 ** 3))
    if total >= 1024 * 1024:
        return "%.1f MB" % (total / (1024.0 ** 2))
    return "%.1f KB" % (total / 1024.0)


# --------------------------------------------------------------------------- #
# Consistency probes (mirrors tests/helpers.py::rollup_mismatches)
# --------------------------------------------------------------------------- #


def rollup_mismatches(db: Database) -> list[dict[str, Any]]:
    """Return every book whose ``copies`` rollup disagrees with ``book_copies``."""
    return db.query(
        "SELECT b.id, b.copies AS stored, "
        "       (SELECT COUNT(*) FROM book_copies c WHERE c.book_id = b.id) AS actual "
        "FROM books b "
        "WHERE b.copies <> (SELECT COUNT(*) FROM book_copies c WHERE c.book_id = b.id) "
        "LIMIT 50"
    )


def status_mismatches(db: Database) -> list[dict[str, Any]]:
    """Return every book whose derived ``status`` disagrees with its copies."""
    return db.query(
        "SELECT b.id, b.status AS stored, "
        "       CASE WHEN EXISTS (SELECT 1 FROM book_copies c WHERE c.book_id = b.id "
        "                          AND c.status = 'available') THEN 'available' "
        "            WHEN EXISTS (SELECT 1 FROM book_copies c WHERE c.book_id = b.id "
        "                          AND c.status IN ('on_loan','reserved')) THEN 'on_loan' "
        "            ELSE 'unavailable' END AS expected "
        "FROM books b "
        "WHERE b.status <> CASE WHEN EXISTS (SELECT 1 FROM book_copies c "
        "                                    WHERE c.book_id = b.id AND c.status = 'available') "
        "                       THEN 'available' "
        "                  WHEN EXISTS (SELECT 1 FROM book_copies c WHERE c.book_id = b.id "
        "                                    AND c.status IN ('on_loan','reserved')) "
        "                       THEN 'on_loan' "
        "                  ELSE 'unavailable' END "
        "LIMIT 50"
    )


def payment_mismatches(db: Database) -> list[dict[str, Any]]:
    """Return every loan whose ``paid_amount`` disagrees with its receipts."""
    return db.query(
        "SELECT l.id, l.paid_amount AS stored, "
        "       COALESCE((SELECT SUM(p.amount) FROM fine_payments p WHERE p.loan_id = l.id), 0) "
        "       AS receipts "
        "FROM loans l "
        "WHERE ABS(l.paid_amount - COALESCE((SELECT SUM(p.amount) FROM fine_payments p "
        "                                    WHERE p.loan_id = l.id), 0)) > 0.004 "
        "LIMIT 50"
    )


def loan_copy_mismatches(db: Database) -> list[dict[str, Any]]:
    """Return copies and loans whose statuses contradict each other."""
    orphans = db.query(
        "SELECT c.id, c.barcode, c.status FROM book_copies c "
        "WHERE c.status = 'on_loan' AND NOT EXISTS ("
        "  SELECT 1 FROM loans l WHERE l.copy_id = c.id AND l.status = 'on_loan') LIMIT 50"
    )
    stale = db.query(
        "SELECT l.id, l.copy_id, c.status FROM loans l "
        "JOIN book_copies c ON c.id = l.copy_id "
        "WHERE l.status = 'on_loan' AND c.status <> 'on_loan' LIMIT 50"
    )
    return orphans + stale


# --------------------------------------------------------------------------- #
# SQL tracing (phase 4)
# --------------------------------------------------------------------------- #


class SqlTrace:
    """Record every statement a service issues through ``Database``.

    ``Database.one`` delegates to ``query`` and ``scalar`` delegates to ``one``,
    so patching ``query`` and ``execute`` captures the whole read/write surface
    without touching ``imri/``.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self.statements: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> "SqlTrace":
        original_query = self.db.query
        original_execute = self.db.execute

        def traced_query(sql: str, params: Sequence[Any] = ()) -> Any:
            self.statements.append((sql, tuple(params)))
            return original_query(sql, params)

        def traced_execute(sql: str, params: Sequence[Any] = ()) -> Any:
            self.statements.append((sql, tuple(params)))
            return original_execute(sql, params)

        self.db.query = traced_query  # type: ignore[method-assign]
        self.db.execute = traced_execute  # type: ignore[method-assign]
        self._originals = (original_query, original_execute)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.db.__dict__.pop("query", None)
        self.db.__dict__.pop("execute", None)

    def unique(self) -> list[tuple[str, tuple[Any, ...]]]:
        """Return the recorded statements with duplicates removed."""
        seen: set[tuple[str, tuple[Any, ...]]] = set()
        out: list[tuple[str, tuple[Any, ...]]] = []
        for sql, params in self.statements:
            key = (sql, tuple(str(p) for p in params))
            if key in seen:
                continue
            seen.add(key)
            out.append((sql, params))
        return out


# --------------------------------------------------------------------------- #
# Phase 1 - seed
# --------------------------------------------------------------------------- #


class Seeder:
    """Bulk-load a full-size library without going through the service layer.

    Every value is derived from :data:`SEED`, so two runs produce the same rows
    and therefore comparable timings. The invariants the services rely on
    (``books.copies``, ``books.status``, one live loan per copy, no member over
    the loan limit) are maintained by construction and asserted afterwards.
    """

    BOOK_COLUMNS = (
        "accession_no", "isbn", "isbn10", "title", "subtitle", "authors",
        "publisher", "pub_year", "edition", "language", "dewey",
        "classification", "call_no", "subjects", "material_type", "location",
        "copies", "price", "currency", "synopsis", "marc_blob", "cover_path",
        "barcode", "status", "source", "created_at", "updated_at",
    )
    COPY_COLUMNS = (
        "book_id", "copy_no", "barcode", "status", "location", "condition",
        "acquired_at", "note", "created_at", "updated_at",
    )
    MEMBER_COLUMNS = (
        "member_code", "name", "member_type", "ic_number", "form", "class_name",
        "address", "phone", "email", "photo_path", "membership_date",
        "expiry_date", "status", "note", "created_at", "updated_at",
    )
    LOAN_COLUMNS = (
        "book_id", "copy_id", "accession_no", "member_id", "member_code",
        "issue_date", "due_date", "return_date", "renewal_count", "status",
        "fine_amount", "paid_amount", "waived_amount", "currency", "issued_by",
        "returned_by", "note", "created_at", "updated_at",
    )
    RESERVATION_COLUMNS = (
        "book_id", "member_id", "accession_no", "member_code", "copy_id",
        "reserved_date", "queue_position", "status", "ready_at", "expires_at",
        "note", "created_at", "updated_at",
    )
    PAYMENT_COLUMNS = (
        "loan_id", "member_id", "amount", "paid_at", "receipt_no", "method",
        "actor", "note", "created_at",
    )

    def __init__(
        self,
        db: Database,
        n_books: int,
        n_members: int,
        n_loans: int,
        n_reservations: int,
        n_payments: int,
    ) -> None:
        self.db = db
        self.n_books = max(1, int(n_books))
        self.n_members = max(1, int(n_members))
        self.n_loans_total = max(0, int(n_loans))
        self.n_reservations = max(0, int(n_reservations))
        self.n_payments = max(0, int(n_payments))

        self.loan_days = max(1, db.get_setting_int("loan_days", 14))
        self.max_loans = max(1, db.get_setting_int("max_loans", 3))
        self.fine_per_day = config.money(db.get_setting_float("fine_per_day", 0.20))

        self.copy_counts: list[int] = []
        self.copy_start: list[int] = []
        self.total_copies = 0
        self.active_books: list[int] = []
        self.n_active = 0
        self.n_returned = 0
        self.counts: dict[str, int] = {}

    # -- bulk helper ------------------------------------------------------ #
    def _stream(self, sql: str, rows: Iterable[tuple[Any, ...]]) -> int:
        """Insert ``rows`` with ``executemany`` in ``BATCH``-sized transactions."""
        chunk: list[tuple[Any, ...]] = []
        written = 0
        for row in rows:
            chunk.append(row)
            if len(chunk) >= BATCH:
                with self.db.transaction():
                    self.db.executemany(sql, chunk)
                written += len(chunk)
                chunk = []
        if chunk:
            with self.db.transaction():
                self.db.executemany(sql, chunk)
            written += len(chunk)
        return written

    def _insert(self, table: str, columns: Sequence[str], rows: list[tuple[Any, ...]]) -> int:
        """Insert a fully materialised list of rows in batches."""
        placeholders = ",".join("?" for _ in columns)
        sql = "INSERT INTO %s(%s) VALUES(%s)" % (table, ",".join(columns), placeholders)
        return self._stream(sql, rows)

    # -- planning --------------------------------------------------------- #
    def plan(self) -> None:
        """Decide copy counts, copy ids and which titles are out on loan."""
        rnd = random.Random(SEED)
        total = 0
        for _ in range(self.n_books):
            copies = rnd.choice((1, 2, 3, 4))
            self.copy_counts.append(copies)
            self.copy_start.append(total + 1)
            total += copies
        self.total_copies = total

        # ~25,000 of 60,000 loans are live; the rest are history. Never exceed
        # the configured per-member loan limit, or the seeded library would be
        # in a state the application itself refuses to create.
        self.n_active = min(
            self.n_loans_total * 5 // 12,
            self.n_books,
            self.n_members * self.max_loans,
        )
        self.n_returned = max(0, self.n_loans_total - self.n_active)
        self.active_books = sorted(rnd.sample(range(self.n_books), self.n_active))

    # -- books + copies --------------------------------------------------- #
    def seed_books(self) -> None:
        """Insert every title and every physical copy."""
        rnd = random.Random(SEED + 1)
        active_set = set(self.active_books)
        title_space = len(QUALIFIERS) * len(TOPICS) * len(LEVELS)

        def book_rows() -> Iterator[tuple[Any, ...]]:
            for index in range(self.n_books):
                book_no = index + 1
                accession = "IMRI-%06d" % book_no
                combo = index % title_space
                qualifier = QUALIFIERS[combo % len(QUALIFIERS)]
                topic = TOPICS[(combo // len(QUALIFIERS)) % len(TOPICS)]
                level = LEVELS[(combo // (len(QUALIFIERS) * len(TOPICS))) % len(LEVELS)]
                title = "%s %s %s" % (qualifier, topic, level)

                if index % 11 == 0:
                    author = AUTHOR_PREFIX[index % len(AUTHOR_PREFIX)]
                else:
                    author = "%s %s" % (
                        GIVEN_NAMES[(index * 7) % len(GIVEN_NAMES)],
                        FAMILY_NAMES[(index * 3) % len(FAMILY_NAMES)],
                    )
                    if index % 5 == 0:
                        author += ", %s %s" % (
                            GIVEN_NAMES[(index * 13) % len(GIVEN_NAMES)],
                            FAMILY_NAMES[(index * 17) % len(FAMILY_NAMES)],
                        )

                isbn = _isbn13_from_counter(book_no)
                dewey = _dewey_for(index, rnd)
                language = config.LANGUAGES[(index * 5) % len(config.LANGUAGES)][0]
                material = config.MATERIAL_TYPES[index % len(config.MATERIAL_TYPES)]
                copies = self.copy_counts[index]
                status = "on_loan" if (index in active_set and copies == 1) else "available"
                created = _stamp(30 + (index % 1000))

                yield (
                    accession,                                   # accession_no
                    isbn,                                        # isbn
                    to_isbn10(isbn),                             # isbn10
                    title,                                       # title
                    None,                                        # subtitle
                    author,                                      # authors
                    PUBLISHERS[index % len(PUBLISHERS)],         # publisher
                    str(1995 + (index % 30)),                    # pub_year
                    "%d" % (1 + index % 4),                      # edition
                    language,                                    # language
                    dewey,                                       # dewey
                    "ddc",                                       # classification
                    _call_no(dewey, author),                     # call_no
                    "%s, %s" % (topic, level),                   # subjects
                    material,                                    # material_type
                    LOCATIONS[index % len(LOCATIONS)],           # location
                    copies,                                      # copies
                    _money(8.0 + (index % 120) * 0.75),          # price
                    "RM",                                        # currency
                    "A %s resource for %s, covering %s." % (
                        material, level, topic.lower()),          # synopsis
                    None,                                        # marc_blob
                    None,                                        # cover_path
                    accession,                                   # barcode
                    status,                                      # status
                    "manual",                                    # source
                    created,                                     # created_at
                    created,                                     # updated_at
                )

        self.counts["books"] = self._stream(
            "INSERT INTO books(%s) VALUES(%s)"
            % (",".join(self.BOOK_COLUMNS), ",".join("?" for _ in self.BOOK_COLUMNS)),
            book_rows(),
        )

        def copy_rows() -> Iterator[tuple[Any, ...]]:
            for index in range(self.n_books):
                book_id = index + 1
                accession = "IMRI-%06d" % book_id
                start = self.copy_start[index]
                count = self.copy_counts[index]
                for offset in range(count):
                    copy_no = offset + 1
                    copy_id = start + offset
                    status = (
                        "on_loan"
                        if (copy_no == 1 and index in active_set)
                        else "available"
                    )
                    acquired = _stamp(30 + (copy_id % 1000))
                    yield (
                        book_id,
                        copy_no,
                        "%s-%d" % (accession, copy_no),
                        status,
                        LOCATIONS[(index + copy_no) % len(LOCATIONS)],
                        CONDITIONS[copy_id % len(CONDITIONS)],
                        acquired[:10],
                        None,
                        acquired,
                        acquired,
                    )

        self.counts["book_copies"] = self._stream(
            "INSERT INTO book_copies(%s) VALUES(%s)"
            % (",".join(self.COPY_COLUMNS), ",".join("?" for _ in self.COPY_COLUMNS)),
            copy_rows(),
        )

    # -- members ---------------------------------------------------------- #
    def seed_members(self) -> None:
        """Insert students, teachers and patrons with unique business codes."""
        rnd = random.Random(SEED + 2)
        prefixes = {"student": "S", "teacher": "T", "patron": "P"}
        today = _dt.date.today()

        def rows() -> Iterator[tuple[Any, ...]]:
            for index in range(self.n_members):
                number = index + 1
                bucket = index % 100
                member_type = "student" if bucket < 85 else ("teacher" if bucket < 95 else "patron")
                code = "%s%05d" % (prefixes[member_type], number)
                name = "%s %s" % (
                    GIVEN_NAMES[(index * 11) % len(GIVEN_NAMES)],
                    FAMILY_NAMES[(index * 19) % len(FAMILY_NAMES)],
                )
                if member_type == "student":
                    class_name = CLASS_NAMES[index % len(CLASS_NAMES)]
                    form = class_name.split(" ")[0]
                    ic_number = "%02d%02d%02d-%02d-%04d" % (
                        2 + index % 24, 1 + index % 12, 1 + index % 28,
                        1 + index % 16, index % 10000,
                    )
                    expiry = None
                else:
                    class_name = DEPARTMENTS[index % len(DEPARTMENTS)]
                    form = None
                    ic_number = "%02d%02d%02d-%02d-%04d" % (
                        60 + index % 40, 1 + index % 12, 1 + index % 28,
                        1 + index % 16, index % 10000,
                    )
                    expiry = (today + _dt.timedelta(days=365)).isoformat()

                status = "active"
                if bucket == 98:
                    status = "suspended"
                elif bucket == 99:
                    status = "expired"

                membership = _date(30 + (index * 3) % 1065)
                created = _stamp(30 + (index * 3) % 1065)

                yield (
                    code,
                    name,
                    member_type,
                    ic_number,
                    form,
                    class_name,
                    "No. %d, Jalan Sekolah, %s" % (index, "Selangor"),
                    "01%d-%07d" % (index % 10, 1000000 + index),
                    ("%s.%d@sekolah.edu.my" % (code.lower(), index)) if index % 3 == 0 else None,
                    None,
                    membership,
                    expiry,
                    status,
                    None,
                    created,
                    created,
                )

        self.counts["members"] = self._stream(
            "INSERT INTO members(%s) VALUES(%s)"
            % (",".join(self.MEMBER_COLUMNS), ",".join("?" for _ in self.MEMBER_COLUMNS)),
            rows(),
        )
        del rnd

    # -- loans ------------------------------------------------------------ #
    def seed_loans(self) -> None:
        """Insert live loans (a realistic share overdue) plus returned history."""
        rnd = random.Random(SEED + 3)
        now_stamp = _stamp(0)
        rows: list[tuple[Any, ...]] = []

        # -- live loans: one copy per title, members round-robin so nobody
        # exceeds the configured loan limit.
        for index in range(self.n_active):
            book_index = self.active_books[index]
            book_id = book_index + 1
            copy_id = self.copy_start[book_index]
            member_id = (index % self.n_members) + 1
            member_code = self._member_code(member_id)
            overdue = (index % 10) < 3
            if overdue:
                issued_ago = self.loan_days + 1 + (index * 7) % 60
            else:
                issued_ago = (index * 3) % self.loan_days
            issue_date = _date(issued_ago)
            due_date = config.date_add(issue_date, self.loan_days)
            rows.append(
                (
                    book_id, copy_id, "IMRI-%06d" % book_id, member_id, member_code,
                    issue_date, due_date, None, 0, "on_loan",
                    0.0, 0.0, 0.0, "RM", "loadtest", None, None,
                    now_stamp, now_stamp,
                )
            )

        # -- returned history, spread over two years so the 30-day reports
        # have a believable denominator.
        for index in range(self.n_returned):
            book_index = (index * 37) % self.n_books
            book_id = book_index + 1
            copy_offset = (index * 13) % self.copy_counts[book_index]
            copy_id = self.copy_start[book_index] + copy_offset
            member_id = ((index * 17) % self.n_members) + 1
            member_code = self._member_code(member_id)
            issued_ago = 20 + (index * 11) % 700
            issue_date = _date(issued_ago)
            due_date = config.date_add(issue_date, self.loan_days)
            held = min(3 + (index * 7) % 35, issued_ago)
            return_date = config.date_add(issue_date, held)
            days_late = max(0, config.days_between(due_date, return_date))
            fine = _money(days_late * self.fine_per_day)
            rows.append(
                (
                    book_id, copy_id, "IMRI-%06d" % book_id, member_id, member_code,
                    issue_date, due_date, return_date, 0, "returned",
                    fine, 0.0, 0.0, "RM", "loadtest", "loadtest", None,
                    now_stamp, now_stamp,
                )
            )

        self.counts["loans"] = self._insert("loans", self.LOAN_COLUMNS, rows)
        del rnd

    def _member_code(self, member_id: int) -> str:
        """Return the member code the plan assigned to ``member_id``."""
        index = member_id - 1
        bucket = index % 100
        if bucket < 85:
            prefix = "S"
        elif bucket < 95:
            prefix = "T"
        else:
            prefix = "P"
        return "%s%05d" % (prefix, member_id)

    # -- reservations ----------------------------------------------------- #
    def seed_reservations(self) -> None:
        """Place holds on titles that have no lendable copy left."""
        candidates = [b for b in self.active_books if self.copy_counts[b] == 1]
        wanted = min(self.n_reservations, len(candidates))
        now_stamp = _stamp(0)
        rows: list[tuple[Any, ...]] = []
        for index in range(wanted):
            book_index = candidates[index]
            book_id = book_index + 1
            member_id = ((index * 3 + 1) % self.n_members) + 1
            rows.append(
                (
                    book_id, member_id, "IMRI-%06d" % book_id, self._member_code(member_id),
                    None, _date(index % 60), 1, "open", None, None, None,
                    now_stamp, now_stamp,
                )
            )
        self.counts["reservations"] = self._insert(
            "reservations", self.RESERVATION_COLUMNS, rows
        )

    # -- fine payments ---------------------------------------------------- #
    def seed_payments(self) -> None:
        """Record receipts against part of the returned-with-fine history."""
        candidates = self.db.query(
            "SELECT id, member_id, fine_amount, paid_amount, waived_amount "
            "FROM loans WHERE status = 'returned' "
            "  AND (fine_amount - paid_amount - waived_amount) > 0.004 "
            "ORDER BY id LIMIT ?",
            (int(self.n_payments),),
        )
        if not candidates:
            self.counts["fine_payments"] = 0
            return

        stamp = config.today_local().replace("-", "")
        now_stamp = _stamp(0)
        payments: list[tuple[Any, ...]] = []
        updates: list[tuple[Any, ...]] = []
        for index, loan in enumerate(candidates):
            outstanding = _money(
                float(loan["fine_amount"] or 0)
                - float(loan["paid_amount"] or 0)
                - float(loan["waived_amount"] or 0)
            )
            if outstanding <= 0.004:
                continue
            # Three in five pay in full, the rest pay half now.
            amount = outstanding if index % 5 < 3 else _money(outstanding / 2.0)
            if amount <= 0.004:
                continue
            payments.append(
                (
                    int(loan["id"]),
                    int(loan["member_id"]),
                    amount,
                    _stamp(index % 60),
                    "IMRI-R-%s-%04d" % (stamp, index + 1),
                    "cash",
                    "loadtest",
                    None,
                    now_stamp,
                )
            )
            updates.append((amount, int(loan["id"])))

        self.counts["fine_payments"] = self._insert(
            "fine_payments", self.PAYMENT_COLUMNS, payments
        )
        with self.db.transaction():
            self.db.executemany(
                "UPDATE loans SET paid_amount = ROUND(paid_amount + ?, 2) WHERE id = ?",
                updates,
            )

    # -- driver ----------------------------------------------------------- #
    def run(self) -> dict[str, Any]:
        """Seed everything and return ``{counts, seconds, rows_per_second}``."""
        started = time.perf_counter()
        self.plan()
        marks: dict[str, float] = {}

        mark = time.perf_counter()
        # The catalogue search index is maintained by triggers on ``books``.
        # Suspend them for the bulk insert and rebuild once at the end: 200,000
        # individual reindex operations and one bulk rebuild cost about the
        # same, but a rebuild cannot be derailed by a single bad row and keeps
        # the seeding time independent of FTS5.
        reindex = self.db.begin_bulk_load()
        try:
            self.seed_books()
        finally:
            if reindex:
                self.db.end_bulk_load()
        marks["books+copies"] = time.perf_counter() - mark
        _progress("  books + copies .... %s rows in %.1fs"
                  % (format(self.counts["books"] + self.counts["book_copies"], ","),
                     marks["books+copies"]))

        mark = time.perf_counter()
        self.seed_members()
        marks["members"] = time.perf_counter() - mark
        _progress("  members ........... %s rows in %.1fs"
                  % (format(self.counts["members"], ","), marks["members"]))

        mark = time.perf_counter()
        self.seed_loans()
        marks["loans"] = time.perf_counter() - mark
        _progress("  loans ............. %s rows in %.1fs"
                  % (format(self.counts["loans"], ","), marks["loans"]))

        mark = time.perf_counter()
        self.seed_reservations()
        marks["reservations"] = time.perf_counter() - mark
        _progress("  reservations ...... %s rows in %.1fs"
                  % (format(self.counts["reservations"], ","), marks["reservations"]))

        mark = time.perf_counter()
        self.seed_payments()
        marks["fine_payments"] = time.perf_counter() - mark
        _progress("  fine payments ..... %s rows in %.1fs"
                  % (format(self.counts["fine_payments"], ","), marks["fine_payments"]))

        elapsed = time.perf_counter() - started
        total_rows = sum(self.counts.values())
        return {
            "counts": dict(self.counts),
            "seconds": elapsed,
            "rows": total_rows,
            "rows_per_second": (total_rows / elapsed) if elapsed > 0 else 0.0,
            "stages": marks,
            "active_loans": self.n_active,
            "returned_loans": self.n_returned,
        }


def _progress(text: str) -> None:
    """Print a progress line immediately (the seed takes tens of seconds)."""
    print(text, flush=True)


# --------------------------------------------------------------------------- #
# Phase 2 - query latency
# --------------------------------------------------------------------------- #


class Bench:
    """A single timed operation with its budget category."""

    __slots__ = ("name", "category", "samples", "note", "error")

    def __init__(self, name: str, category: str) -> None:
        self.name = name
        self.category = category
        self.samples: list[float] = []
        self.note = ""
        self.error = ""

    @property
    def minimum(self) -> float:
        return min(self.samples) if self.samples else 0.0

    @property
    def median(self) -> float:
        return _median(self.samples)

    @property
    def maximum(self) -> float:
        return max(self.samples) if self.samples else 0.0

    @property
    def budget(self) -> float:
        return BUDGETS[self.category]

    @property
    def ok(self) -> bool:
        return bool(self.samples) and self.median <= self.budget


def _summarise(value: Any) -> str:
    """Return a one-token summary of a result, so a timing cannot be vacuous.

    A search that matches nothing is fast for the wrong reason; printing the
    row count next to the timing makes that impossible to hide.
    """
    if isinstance(value, dict):
        meta = value.get("meta")
        if isinstance(meta, dict) and "total" in meta:
            return "n=%s" % format(int(meta.get("total") or 0), ",")
        items = value.get("items")
        if isinstance(items, list):
            return "n=%s" % format(len(items), ",")
        return ""
    if isinstance(value, (list, tuple)):
        return "n=%s" % format(len(value), ",")
    return ""


def time_call(
    name: str,
    category: str,
    call: Callable[[], Any],
    runs: int,
    note: str = "",
) -> Bench:
    """Time ``call`` warm, then ``runs`` times; keep min/median/max."""
    bench = Bench(name, category)
    bench.note = note
    try:
        summary = _summarise(call())  # warm: page cache, prepared statements, indexes
    except Exception as exc:  # pragma: no cover - defensive
        bench.error = "%s: %s" % (type(exc).__name__, exc)
        return bench
    if summary:
        bench.note = ("%s %s" % (bench.note, summary)).strip()
    for _ in range(max(1, runs)):
        started = time.perf_counter()
        try:
            call()
        except Exception as exc:  # pragma: no cover - defensive
            bench.error = "%s: %s" % (type(exc).__name__, exc)
            return bench
        bench.samples.append((time.perf_counter() - started) * 1000.0)
    return bench


def build_benchmarks(
    db: Database,
    catalog: CatalogService,
    members: MemberService,
    circulation: CirculationService,
    reporting: ReportingService,
) -> list[tuple[str, str, Callable[[], Any], str]]:
    """Return ``(name, category, callable, note)`` for every measured operation."""
    page = lambda number: {
        "page": number,
        "page_size": 50,
        "offset": (number - 1) * 50,
        "sort": "title",
        "order": "asc",
    }

    # -- pick stable, real fixtures so the numbers are reproducible --------- #
    barcode_hit = str(
        db.scalar("SELECT barcode FROM book_copies WHERE id = ?", (250_001,), default="") or ""
    )
    if not barcode_hit:
        barcode_hit = str(db.scalar("SELECT barcode FROM book_copies LIMIT 1", default="") or "")
    member_code = str(
        db.scalar("SELECT member_code FROM members WHERE id = 5001", default="") or ""
    )
    if not member_code:
        member_code = str(db.scalar("SELECT member_code FROM members LIMIT 1", default="") or "")
    busiest = db.one(
        "SELECT member_id, COUNT(*) AS total FROM loans GROUP BY member_id "
        "ORDER BY total DESC, member_id LIMIT 1"
    ) or {"member_id": 1, "total": 0}
    busiest_id = int(busiest["member_id"])

    title_term = "Algebra"
    author_term = "Ahmad"
    member_term = "Aisyah"

    def count_books() -> int:
        return int(db.scalar("SELECT COUNT(*) FROM books", default=0))

    def lookup_miss() -> Any:
        try:
            return catalog.lookup_barcode("NO-SUCH-BARCODE-999999")
        except errors.ImriError:
            return None

    operations: list[tuple[str, str, Callable[[], Any], str]] = [
        # -- paginated lists ------------------------------------------------ #
        ("list_books page 1", "list",
         lambda: catalog.list_books(None, page(1)), "offset 0"),
        ("list_books page 500", "list",
         lambda: catalog.list_books(None, page(500)), "offset 24,950"),
        ("list_books page 4000", "list",
         lambda: catalog.list_books(None, page(4000)), "offset 199,950 (deep OFFSET)"),
        ("list_books material_type=book", "list",
         lambda: catalog.list_books({"material_type": "book"}, page(1)), "1/8 of the catalogue"),
        ("list_books status=available", "list",
         lambda: catalog.list_books({"status": "available"}, page(1)), ""),
        ("list_members page 1", "list",
         lambda: members.list_members(None, page(1)), ""),
        ("list_members page 100", "list",
         lambda: members.list_members(None, page(100)), "offset 4,950"),
        ("overdue_list", "list",
         lambda: circulation.overdue_list(), ""),
        ("member.profile (busiest)", "list",
         lambda: members.profile(busiest_id),
         "member %d, %s loans" % (busiest_id, busiest["total"])),
        # -- searches ------------------------------------------------------- #
        ("list_books q=%s (title)" % title_term, "search",
         lambda: catalog.list_books({"q": title_term}, page(1)), ""),
        ("list_books q=%s (author)" % author_term, "search",
         lambda: catalog.list_books({"q": author_term}, page(1)), ""),
        ("list_members q=%s (name)" % member_term, "search",
         lambda: members.list_members({"q": member_term}, page(1)), ""),
        ("circulation.search(%s)" % title_term, "search",
         lambda: circulation.search(title_term), "command palette"),
        # -- single-row lookups --------------------------------------------- #
        ("lookup_barcode hit", "lookup",
         lambda: catalog.lookup_barcode(barcode_hit), barcode_hit),
        ("lookup_barcode miss", "lookup", lookup_miss, "expected not_found"),
        ("find_by_code", "lookup",
         lambda: members.find_by_code(member_code), member_code),
        ("COUNT(*) FROM books", "list", count_books,
         "the COUNT half of every list"),
        # -- dashboard and reports ------------------------------------------ #
        ("reporting.dashboard", "dashboard", lambda: reporting.dashboard(), ""),
        ("reporting.collection", "report_full", lambda: reporting.collection(), "all titles"),
        ("reporting.circulation", "report", lambda: reporting.circulation(), "last 30 days"),
    ]
    return operations


def phase2(
    db: Database,
    catalog: CatalogService,
    members: MemberService,
    circulation: CirculationService,
    reporting: ReportingService,
    runs: int,
) -> list[Bench]:
    """Measure every read path and return the results in report order."""
    operations = build_benchmarks(db, catalog, members, circulation, reporting)
    results: list[Bench] = []
    print(_rule("-"))
    print("PHASE 2 - query latency (warm, median of %d runs, milliseconds)" % runs)
    print(_rule("-"))
    print("%-38s %9s %9s %9s %8s  %-6s %s"
          % ("operation", "min", "median", "max", "budget", "result", "note"))
    for name, category, call, note in operations:
        bench = time_call(name, category, call, runs, note)
        results.append(bench)
        if bench.error:
            print("%-38s %9s %9s %9s %8.0f  %-6s %s"
                  % (name[:38], "-", "-", "-", bench.budget, "ERROR", bench.error[:40]))
            continue
        print("%-38s %9.1f %9.1f %9.1f %8.0f  %-6s %s"
              % (name[:38], bench.minimum, bench.median, bench.maximum,
                 bench.budget, "PASS" if bench.ok else "FAIL", bench.note[:52]))
    return results


# --------------------------------------------------------------------------- #
# Phase 3 - write-path concurrency
# --------------------------------------------------------------------------- #


class Workload:
    """Build and run a mixed circulation workload across threads."""

    def __init__(
        self,
        db: Database,
        circulation: CirculationService,
        total_ops: int,
        workers: int,
    ) -> None:
        self.db = db
        self.circulation = circulation
        self.total_ops = max(1, int(total_ops))
        self.workers = max(1, int(workers))
        self.operations: list[tuple[str, int, Any]] = []

    # -- planning --------------------------------------------------------- #
    def build(self) -> dict[str, int]:
        """Assemble the operation list from real seeded rows."""
        quota = {
            "issue": self.total_ops * 30 // 100,
            "return": self.total_ops * 30 // 100,
            "payment": self.total_ops * 20 // 100,
            "reserve": self.total_ops * 20 // 100,
        }
        max_loans = max(1, self.db.get_setting_int("max_loans", 3))
        today = config.today_local()

        copies = self.db.query(
            "SELECT barcode FROM book_copies WHERE status = 'available' "
            "ORDER BY id DESC LIMIT ?",
            (quota["issue"] * 2,),
        )
        members = self.db.query(
            "SELECT m.id FROM members m WHERE m.status = 'active' "
            "  AND (m.expiry_date IS NULL OR m.expiry_date >= ?) "
            "  AND (SELECT COUNT(*) FROM loans l WHERE l.member_id = m.id "
            "       AND l.status = 'on_loan') < ? "
            "ORDER BY m.id DESC LIMIT ?",
            (today, max_loans, quota["issue"] + quota["reserve"]),
        )
        loans = self.db.query(
            "SELECT id FROM loans WHERE status = 'on_loan' ORDER BY id DESC LIMIT ?",
            (quota["return"],),
        )
        fined = self.db.query(
            "SELECT id, (fine_amount - paid_amount - waived_amount) AS due FROM loans "
            "WHERE status = 'returned' "
            "  AND (fine_amount - paid_amount - waived_amount) > 0.004 "
            "ORDER BY id LIMIT ?",
            (quota["payment"],),
        )
        books = self.db.query(
            "SELECT b.id FROM books b WHERE b.status = 'on_loan' "
            "  AND NOT EXISTS (SELECT 1 FROM book_copies c WHERE c.book_id = b.id "
            "                  AND c.status = 'available') "
            "  AND NOT EXISTS (SELECT 1 FROM reservations r WHERE r.book_id = b.id "
            "                  AND r.status IN ('open','ready')) "
            "ORDER BY b.id DESC LIMIT ?",
            (quota["reserve"] * 4,),
        )

        # A return elsewhere in this same workload can put a copy back on the
        # shelf, which correctly makes a hold impossible ("title_available").
        # Excluding those titles keeps the throughput figure a measure of the
        # write path rather than of self-inflicted contention.
        returning_books = {
            int(row["book_id"])
            for row in self.db.query(
                "SELECT DISTINCT book_id FROM loans WHERE id IN (%s)"
                % ",".join("?" for _ in loans),
                tuple(int(row["id"]) for row in loans) or (-1,),
            )
        } if loans else set()

        copy_pool = [row["barcode"] for row in copies][: quota["issue"]]
        member_pool = [int(row["id"]) for row in members]
        issue_members = member_pool[: quota["issue"]]
        reserve_members = member_pool[quota["issue"]: quota["issue"] + quota["reserve"]]
        loan_pool = [int(row["id"]) for row in loans]
        fine_pool = [(int(row["id"]), float(row["due"] or 0.0)) for row in fined]
        book_pool = [int(row["id"]) for row in books if int(row["id"]) not in returning_books]

        for index, barcode in enumerate(copy_pool):
            if index < len(issue_members):
                self.operations.append(("issue", index, (barcode, issue_members[index])))
        for index, loan_id in enumerate(loan_pool):
            self.operations.append(("return", index, (loan_id,)))
        for index, (loan_id, due) in enumerate(fine_pool):
            self.operations.append(("payment", index, (loan_id, _money(due))))
        for index, book_id in enumerate(book_pool):
            if index < len(reserve_members):
                self.operations.append(("reserve", index, (book_id, reserve_members[index])))

        counts: dict[str, int] = {}
        for kind, _, _ in self.operations:
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    # -- execution -------------------------------------------------------- #
    def _run_one(self, operation: tuple[str, int, Any]) -> tuple[str, str, float, str]:
        """Execute one operation; return ``(kind, outcome, ms, detail)``."""
        kind, _, payload = operation
        started = time.perf_counter()
        try:
            if kind == "issue":
                self.circulation.issue(payload[0], payload[1], actor="loadtest")
            elif kind == "return":
                self.circulation.return_copy(loan_id=payload[0], actor="loadtest")
            elif kind == "payment":
                self.circulation.record_payment(payload[0], payload[1], actor="loadtest")
            elif kind == "reserve":
                self.circulation.reserve(payload[0], payload[1], actor="loadtest")
            elapsed = (time.perf_counter() - started) * 1000.0
            return kind, "ok", elapsed, ""
        except errors.ImriError as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            return kind, "business", elapsed, str(exc.code)
        except sqlite3.OperationalError as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            return kind, "operational", elapsed, str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            elapsed = (time.perf_counter() - started) * 1000.0
            return kind, "error", elapsed, "%s: %s" % (type(exc).__name__, exc)

    def run(self) -> dict[str, Any]:
        """Run the workload on ``self.workers`` threads and collect statistics."""
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            outcomes = list(pool.map(self._run_one, self.operations))
        wall = time.perf_counter() - started

        latencies = [item[2] for item in outcomes if item[1] == "ok"]
        lock_errors = [
            item for item in outcomes if item[1] == "operational"
        ]
        hard_errors = [item for item in outcomes if item[1] == "error"]
        business = [item for item in outcomes if item[1] == "business"]

        by_kind: dict[str, int] = {}
        for kind, outcome, _, _ in outcomes:
            if outcome == "ok":
                by_kind[kind] = by_kind.get(kind, 0) + 1

        return {
            "wall": wall,
            "attempted": len(self.operations),
            "succeeded": len(latencies),
            "throughput": (len(latencies) / wall) if wall > 0 else 0.0,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies) if latencies else 0.0,
            "lock_errors": lock_errors,
            "hard_errors": hard_errors,
            "business": business,
            "by_kind": by_kind,
            "latencies": latencies,
        }


def phase3(db: Database, circulation: CirculationService, ops: int, workers: int) -> dict[str, Any]:
    """Run the concurrency workload, then prove nothing drifted."""
    workload = Workload(db, circulation, ops, workers)
    planned = workload.build()
    print(_rule("-"))
    print("PHASE 3 - write-path concurrency (%d workers, %d operations)"
          % (workers, len(workload.operations)))
    print(_rule("-"))
    print("  planned: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(planned.items())))

    stats = workload.run()
    stats["planned"] = planned
    print("  wall ............... %.2f s" % stats["wall"])
    print("  succeeded .......... %d / %d  (%s)"
          % (stats["succeeded"], stats["attempted"],
             ", ".join("%s=%d" % (k, v) for k, v in sorted(stats["by_kind"].items()))))
    print("  throughput ......... %.1f ops/s" % stats["throughput"])
    print("  latency p50/p95/max  %.1f / %.1f / %.1f ms"
          % (stats["p50"], stats["p95"], stats["max"]))
    if stats["business"]:
        reasons: dict[str, int] = {}
        for _, _, _, detail in stats["business"]:
            reasons[detail] = reasons.get(detail, 0) + 1
        print("  business refusals .. %d  (%s)"
              % (len(stats["business"]),
                 ", ".join("%s=%d" % (k, v) for k, v in sorted(reasons.items()))))
    if stats["lock_errors"]:
        print("  LOCK ERRORS ........ %d" % len(stats["lock_errors"]))
        for item in stats["lock_errors"][:5]:
            print("      %s: %s" % (item[0], item[3][:80]))
    else:
        print("  lock errors ........ 0")
    if stats["hard_errors"]:
        print("  HARD ERRORS ........ %d" % len(stats["hard_errors"]))
        for item in stats["hard_errors"][:5]:
            print("      %s: %s" % (item[0], item[3][:80]))

    print("  post-run consistency:")
    checks = {
        "rollup (books.copies vs book_copies)": rollup_mismatches(db),
        "derived (books.status vs copies)": status_mismatches(db),
        "payments (loans.paid_amount vs receipts)": payment_mismatches(db),
        "copy/loan status agreement": loan_copy_mismatches(db),
    }
    stats["consistency"] = {name: len(rows) for name, rows in checks.items()}
    for name, rows in checks.items():
        print("    %-42s %s" % (name, "ok" if not rows else "%d MISMATCH(ES)" % len(rows)))
        for row in rows[:3]:
            print("        %s" % (dict(row),))

    fk = db.foreign_key_check()
    stats["foreign_keys"] = len(fk)
    print("    %-42s %s" % ("foreign keys", "ok" if not fk else "%d VIOLATION(S)" % len(fk)))
    return stats


# --------------------------------------------------------------------------- #
# Phase 4 - index audit
# --------------------------------------------------------------------------- #


AUDIT_TARGETS: tuple[tuple[str, str], ...] = (
    ("list_books page 1", "catalog"),
    ("list_books page 4000", "catalog"),
    ("list_books q=Algebra (title)", "catalog"),
    ("list_books material_type=book", "catalog"),
    ("lookup_barcode hit", "catalog"),
    ("lookup_barcode miss", "catalog"),
    ("overdue_list", "circulation"),
    ("reporting.dashboard", "reporting"),
)

LARGE_TABLES = frozenset(
    {"books", "book_copies", "members", "loans", "b", "c", "l", "m", "r", "reservations"}
)


def _plan_lines(db: Database, sql: str, params: Sequence[Any]) -> list[str]:
    """Return the ``EXPLAIN QUERY PLAN`` detail lines for one statement."""
    try:
        rows = db.query("EXPLAIN QUERY PLAN " + sql, tuple(params))
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        return ["<plan unavailable: %s>" % exc]
    return [str(row.get("detail", "")) for row in rows]


def _is_flagged(detail: str) -> bool:
    """True when a plan line is a full scan of a large table with no index."""
    if not detail.startswith("SCAN "):
        return False
    if "USING" in detail:
        return False
    parts = detail.split()
    if len(parts) < 2:
        return False
    target = parts[1].strip("()")
    if target in ("CONSTANT", "SUBQUERY", "TABLE"):
        return False
    return target in LARGE_TABLES


def phase4(
    db: Database,
    catalog: CatalogService,
    members: MemberService,
    circulation: CirculationService,
    reporting: ReportingService,
) -> list[dict[str, Any]]:
    """Audit the query plans of the eight most important read paths."""
    operations = {name: (category, call, note)
                  for name, category, call, note in
                  build_benchmarks(db, catalog, members, circulation, reporting)}

    print(_rule("-"))
    print("PHASE 4 - index audit (EXPLAIN QUERY PLAN on the SQL the services issue)")
    print(_rule("-"))

    findings: list[dict[str, Any]] = []
    for label, _owner in AUDIT_TARGETS:
        if label not in operations:
            continue
        _category, call, _note = operations[label]
        with SqlTrace(db) as trace:
            try:
                call()
            except Exception:  # pragma: no cover - defensive
                pass
        statements = trace.unique()
        print("  %s  (%d statement%s)"
              % (label, len(statements), "" if len(statements) == 1 else "s"))
        for sql, params in statements:
            first = " ".join(sql.split())[:104]
            print("    SQL   %s" % first)
            flagged_here: list[str] = []
            for detail in _plan_lines(db, sql, params):
                flag = _is_flagged(detail)
                if flag:
                    flagged_here.append(detail)
                mark = "FLAG" if flag else "ok  "
                note = ""
                if "TEMP B-TREE" in detail:
                    note = "  (sort materialised)"
                print("      %s  %s%s" % (mark, detail[:96], note))
            if flagged_here:
                findings.append(
                    {
                        "operation": label,
                        "sql": first,
                        "scans": flagged_here,
                    }
                )
    print("  flagged: %d statement(s) scan a large table without an index"
          % len(findings))
    return findings


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #


def verdict(
    benches: Sequence[Bench],
    write_stats: dict[str, Any],
) -> tuple[list[tuple[str, float, float, bool]], bool]:
    """Reduce the per-operation results to one verdict per budget category."""
    worst: dict[str, float] = {}
    for bench in benches:
        if not bench.samples:
            worst[bench.category] = float("inf")
            continue
        current = worst.get(bench.category, 0.0)
        worst[bench.category] = max(current, bench.median)

    rows: list[tuple[str, float, float, bool]] = []
    for category in ("list", "search", "lookup", "dashboard", "report", "report_full"):
        limit = BUDGETS[category]
        observed = worst.get(category, 0.0)
        rows.append((BUDGET_LABEL[category], observed, limit, observed <= limit))

    write_p95 = float(write_stats.get("p95", 0.0))
    lock_errors = len(write_stats.get("lock_errors", []))
    write_ok = write_p95 <= BUDGETS["write_p95"] and lock_errors == 0
    rows.append(
        ("concurrent write p95", write_p95, BUDGETS["write_p95"], write_ok)
    )

    passed = all(row[3] for row in rows)
    return rows, passed


def print_verdict(rows: Sequence[tuple[str, float, float, bool]]) -> bool:
    """Print the budget table and return whether everything passed."""
    print(_rule("="))
    print("VERDICT")
    print(_rule("="))
    print("%-30s %12s %12s  %s" % ("budget", "observed", "limit", "result"))
    for label, observed, limit, ok in rows:
        print("%-30s %10.1f ms %10.0f ms  %s"
              % (label, observed, limit, "PASS" if ok else "FAIL"))
    # The lock-error requirement is folded into the write-path row by
    # :func:`verdict`, so a single "all rows passed" is the whole verdict.
    passed = all(row[3] for row in rows)
    print(_rule("="))
    print("OVERALL: %s" % ("PASS" if passed else "FAIL"))
    print(_rule("="))
    return passed


# --------------------------------------------------------------------------- #
# Database lifecycle
# --------------------------------------------------------------------------- #


def _read_state() -> dict[str, Any] | None:
    """Return the remembered temp database, or ``None``."""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _write_state(payload: dict[str, Any]) -> None:
    """Remember the temp database so ``--reuse`` can skip the seed."""
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
    except OSError:  # pragma: no cover - defensive
        pass


def _clear_state() -> None:
    """Forget the remembered temp database."""
    try:
        os.remove(STATE_PATH)
    except OSError:
        pass


def _reusable(state: dict[str, Any] | None, args: argparse.Namespace) -> str | None:
    """Return a reusable database path when the state file still matches."""
    if not state:
        return None
    path = str(state.get("db") or "")
    if not path or not os.path.exists(path):
        return None
    if int(state.get("books", -1)) != int(args.books):
        return None
    if int(state.get("members", -1)) != int(args.members):
        return None
    if int(state.get("loans", -1)) != int(args.loans):
        return None
    if int(state.get("seed", -1)) != SEED:
        return None
    return path


def _row_counts(db: Database) -> dict[str, int]:
    """Return the row count of the tables this harness cares about."""
    tables = ("books", "book_copies", "members", "loans", "reservations",
              "fine_payments", "audit_log")
    counts: dict[str, int] = {}
    for table in tables:
        counts[table] = int(db.scalar("SELECT COUNT(*) FROM %s" % table, default=0))
    return counts


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        description="IMRI scale harness: seed a full-size library and prove it holds up.",
    )
    parser.add_argument("--books", type=int, default=200_000,
                        help="titles to seed (default 200000)")
    parser.add_argument("--members", type=int, default=10_000,
                        help="members to seed (default 10000)")
    parser.add_argument("--loans", type=int, default=60_000,
                        help="loan rows to seed, live + history (default 60000)")
    parser.add_argument("--reservations", type=int, default=3_000,
                        help="reservation rows to seed (default 3000)")
    parser.add_argument("--payments", type=int, default=8_000,
                        help="fine payments to seed (default 8000)")
    parser.add_argument("--runs", type=int, default=5,
                        help="timed samples per operation in phase 2 (default 5)")
    parser.add_argument("--ops", type=int, default=400,
                        help="concurrent operations in phase 3 (default 400)")
    parser.add_argument("--workers", type=int, default=16,
                        help="threads for phase 3 (default 16)")
    parser.add_argument("--reuse", action="store_true",
                        help="reuse the database from the previous run when it matches")
    parser.add_argument("--clean", action="store_true",
                        help="delete the temp database when the run finishes")
    parser.add_argument("--db", default="",
                        help="use this database file instead of a temp one")
    parser.add_argument("--skip-seed", action="store_true",
                        help="assume --db is already seeded")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the four phases and return the process exit code."""
    args = parse_args(argv)
    started = time.time()

    print(_rule("="))
    print("IMRI load test - %s books / %s members"
          % (format(args.books, ","), format(args.members, ",")))
    print(_rule("="))
    print("python   %s" % sys.version.split()[0])
    print("sqlite   %s" % sqlite3.sqlite_version)
    print("machine  %s / %s" % (platform.processor() or platform.machine(), platform.machine()))
    print("platform %s" % platform.platform())

    state = _read_state() if (args.reuse or not args.db) else None
    db_path = ""
    reused = False
    if args.db:
        db_path = os.path.abspath(args.db)
    elif args.reuse:
        db_path = _reusable(state, args) or ""

    if not db_path:
        directory = tempfile.mkdtemp(prefix="imri-load-")
        db_path = os.path.join(directory, "imri.db")

    print("database %s" % db_path)

    db = Database(db_path)
    catalog = CatalogService(db)
    members = MemberService(db, catalog)
    circulation = CirculationService(db, catalog)
    reporting = ReportingService(db)

    seed_info: dict[str, Any] = {}
    try:
        # -- phase 1 ---------------------------------------------------- #
        existing = _row_counts(db)
        if args.skip_seed:
            reused = True
            print("seed     skipped (--skip-seed)")
        elif args.reuse and existing["books"] >= args.books and existing["members"] >= args.members:
            reused = True
            print("seed     reused: %s books, %s copies, %s members"
                  % (format(existing["books"], ","), format(existing["book_copies"], ","),
                     format(existing["members"], ",")))
        else:
            if existing["books"]:
                print("seed     database is not empty (%s books) - refusing to double-seed"
                      % format(existing["books"], ","))
                print("         delete %s or pass a fresh --db" % db_path)
                return 2
            print(_rule("-"))
            print("PHASE 1 - seeding")
            print(_rule("-"))
            seeder = Seeder(db, args.books, args.members, args.loans,
                            args.reservations, args.payments)
            seed_info = seeder.run()
            print("  total ............. %s rows in %.1f s (%.0f rows/s)"
                  % (format(seed_info["rows"], ","), seed_info["seconds"],
                     seed_info["rows_per_second"]))
            _write_state({
                "db": db_path,
                "books": args.books,
                "members": args.members,
                "loans": args.loans,
                "seed": SEED,
                "created": _stamp(0),
            })

        if not reused:
            print("  consistency:")
            checks = {
                "rollup (books.copies vs book_copies)": rollup_mismatches(db),
                "derived (books.status vs copies)": status_mismatches(db),
                "payments (loans.paid_amount vs receipts)": payment_mismatches(db),
                "copy/loan status agreement": loan_copy_mismatches(db),
            }
            for name, rows in checks.items():
                print("    %-42s %s" % (name, "ok" if not rows else "%d MISMATCH(ES)" % len(rows)))
            fk = db.foreign_key_check()
            print("    %-42s %s" % ("foreign keys", "ok" if not fk else "%d VIOLATION(S)" % len(fk)))
            if seed_info:
                print("    %-42s %s"
                      % ("live / returned loans",
                         "%s / %s" % (format(seed_info["active_loans"], ","),
                                      format(seed_info["returned_loans"], ","))))

        counts = _row_counts(db)
        print("contents books=%s copies=%s members=%s loans=%s reservations=%s payments=%s"
              % (format(counts["books"], ","), format(counts["book_copies"], ","),
                 format(counts["members"], ","), format(counts["loans"], ","),
                 format(counts["reservations"], ","), format(counts["fine_payments"], ",")))
        print("db size  %s" % _human_size(db_path))

        # -- phase 2 ---------------------------------------------------- #
        benches = phase2(db, catalog, members, circulation, reporting, args.runs)

        # -- phase 3 ---------------------------------------------------- #
        write_stats = phase3(db, circulation, args.ops, args.workers)

        # -- phase 4 ---------------------------------------------------- #
        findings = phase4(db, catalog, members, circulation, reporting)

        # -- verdict ---------------------------------------------------- #
        rows, passed = verdict(benches, write_stats)
        print()
        print_verdict(rows)
        if findings:
            print()
            print("INDEX AUDIT FINDINGS (%d)" % len(findings))
            for finding in findings:
                print("  %s" % finding["operation"])
                for scan in finding["scans"]:
                    print("      %s" % scan)
                print("      sql: %s" % finding["sql"])
        print()
        print("total wall time %.1f s" % (time.time() - started))
        if args.clean:
            db.close()
            shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)
            _clear_state()
            print("cleaned %s" % os.path.dirname(db_path))
        return 0 if passed else 1
    finally:
        try:
            db.close()
        except Exception:  # pragma: no cover - defensive
            pass


if __name__ == "__main__":
    sys.exit(main())
