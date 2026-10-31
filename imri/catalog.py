"""Catalogue: books, copies, classification, vocabulary and barcodes.

The one invariant this module exists to protect (ARCHITECTURE §8.6):

    ``refresh_book_rollup`` is the ONLY writer of ``books.copies`` and
    ``books.status``, and it is always called inside the same transaction as
    the copy mutation that made it necessary.

Every other module that touches a copy calls it too — ``circulation.issue``,
``return_copy``, ``mark_lost``, ``expire_holds``, ``fulfil_reservation``,
``transfer`` commit and ``backup`` restore. Anything else makes the catalogue,
the dashboard and the reports silently disagree, which is the single nastiest
class of bug in this product.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping, Sequence

from . import config, errors
from .config import get_logger
from .db import SEARCH_TABLE, Connection, IntegrityError

_log = get_logger("catalog")

#: Characters a search token may keep. Everything else is a separator: FTS5's
#: own query language gives ``-``, ``*``, ``:``, ``^``, ``"`` and ``NEAR``
#: meaning, and a librarian typing a barcode is searching, not writing a query.
_SEARCH_SPLIT = re.compile(r"[^0-9A-Za-z\u00c0-\uffff]+")

#: Cap on how many words one search becomes. Each extra token is another
#: ``AND`` term; a pasted paragraph is not a better search than its first eight
#: words, and the cap keeps the MATCH expression bounded.
_SEARCH_MAX_TOKENS = 8


def search_match_query(term: Any) -> str:
    """Turn a user term into an FTS5 ``MATCH`` expression.

    Every token becomes a quoted prefix phrase joined by ``AND``, so ``Alge``
    finds *Algebra* and ``Ahmad Ismail`` finds *Ahmad bin Ismail*. Quoting is
    what makes this safe: an FTS5 string literal cannot terminate the expression
    or introduce an operator, so no user input can produce a syntax error or
    reach the query planner as SQL.

    Returns ``""`` when the term holds no searchable token at all (``"%%%"``,
    ``"---"``), which the caller treats as "matches nothing".
    """
    tokens = [token for token in _SEARCH_SPLIT.split(str(term or "")) if token]
    if not tokens:
        return ""
    return " AND ".join('"%s"*' % token for token in tokens[:_SEARCH_MAX_TOKENS])


#: Frozen allow-list of writable book columns. User input never reaches SQL
#: text — only names from this tuple do (ARCHITECTURE §9).
_BOOK_COLUMNS: tuple[str, ...] = (
    "accession_no",
    "isbn",
    "isbn10",
    "title",
    "subtitle",
    "authors",
    "publisher",
    "pub_year",
    "edition",
    "language",
    "dewey",
    "classification",
    "call_no",
    "subjects",
    "material_type",
    "location",
    "copies",
    "price",
    "currency",
    "synopsis",
    "marc_blob",
    "cover_path",
    "barcode",
    "status",
    "source",
)

#: Columns a client may change after creation. ``accession_no`` is a business
#: key and immutable (ADR-10); ``copies`` and ``status`` are derived from
#: ``book_copies`` and owned exclusively by :func:`refresh_book_rollup`
#: (ARCHITECTURE §8.6). A title is "on loan" because its copies are, not
#: because someone typed it.
_IMMUTABLE_BOOK_COLUMNS = frozenset({"accession_no", "copies", "status"})

#: Book statuses that describe the *shelf*, not the loan desk, and can be
#: applied to copies by a bulk edit. ``on_loan`` and ``unavailable`` are
#: deliberately absent: they are conclusions the rollup draws.
BULK_COPY_STATUSES = frozenset({"available", "withdrawn", "lost", "damaged"})

#: Insert column list. The two timestamps are NOT NULL in the schema and have no
#: default, so they must be written explicitly — but they are never client
#: input, which is why they stay out of :data:`_BOOK_COLUMNS`.
_BOOK_INSERT_COLUMNS: tuple[str, ...] = _BOOK_COLUMNS + ("created_at", "updated_at")

#: Character caps for the short descriptive fields (CAT-02). Generous for real
#: bibliographic data, but small enough that one pasted novel cannot wreck a
#: table row, a spine label or a printed report.
_FIELD_LIMITS: dict[str, int] = {
    "subtitle": 512,
    "authors": 512,
    "publisher": 256,
    "edition": 64,
    "location": 128,
    "synopsis": 4000,
}

_COPY_COLUMNS: tuple[str, ...] = (
    "status",
    "location",
    "condition",
    "acquired_at",
    "note",
)

#: Sort keys the list endpoint accepts, mapped to real SQL expressions.
BOOK_SORTS: dict[str, str] = {
    "id": "b.id",
    "title": "b.title COLLATE NOCASE",
    "accession_no": "b.accession_no",
    "authors": "b.authors COLLATE NOCASE",
    "publisher": "b.publisher COLLATE NOCASE",
    "pub_year": "b.pub_year",
    "dewey": "b.dewey",
    "call_no": "b.call_no",
    "material_type": "b.material_type",
    "language": "b.language",
    "status": "b.status",
    "copies": "b.copies",
    "price": "b.price",
    "location": "b.location COLLATE NOCASE",
    "created_at": "b.created_at",
    "updated_at": "b.updated_at",
}

_ISBN_STRIP = re.compile(r"[\s-]")
_DEWEY_RE = re.compile(r"^\d{1,3}(\.\d{1,3})?$")
_CALL_NO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ./\-:]*$")
_BARCODE_SAFE = re.compile(r"[^A-Za-z0-9\-]")


# --------------------------------------------------------------------------- #
# ISBN (ISO 2108)
# --------------------------------------------------------------------------- #


def isbn_normalize(isbn: Any) -> str:
    """Strip spaces and dashes and upper-case an ISBN string."""
    return _ISBN_STRIP.sub("", str(isbn or "")).upper()


def validate_isbn(isbn: Any) -> bool:
    """Validate an ISBN-10 or ISBN-13 check digit.

    An empty value is valid — ISBN is optional.
    """
    text = isbn_normalize(isbn)
    if not text:
        return True
    if len(text) == 10:
        if not re.fullmatch(r"[0-9X]{10}", text):
            return False
        total = 0
        for index, char in enumerate(text):
            value = 10 if char == "X" else int(char)
            total += value * (10 - index)
        return total % 11 == 0
    if len(text) == 13:
        if not re.fullmatch(r"[0-9]{13}", text):
            return False
        total = sum(
            int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(text)
        )
        return total % 10 == 0
    return False


def to_isbn13(isbn: Any) -> str | None:
    """Return the ISBN-13 form of a valid ISBN-10 or ISBN-13, else ``None``."""
    text = isbn_normalize(isbn)
    if not text:
        return None
    if len(text) == 10:
        if not validate_isbn(text):
            return None
        core = "978" + text[:9]
        total = sum(
            int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(core)
        )
        return core + str((10 - (total % 10)) % 10)
    if len(text) == 13:
        return text if validate_isbn(text) else None
    return None


def to_isbn10(isbn: Any) -> str | None:
    """Return the ISBN-10 form of a 978-prefixed ISBN-13, else ``None``."""
    text = isbn_normalize(isbn)
    if len(text) == 13 and text.startswith("978") and validate_isbn(text):
        core = text[3:12]
        total = sum(value * (10 - index) for index, value in enumerate(int(c) for c in core))
        remainder = total % 11
        check = 0 if remainder == 0 else 11 - remainder
        return core + ("X" if check == 10 else str(check))
    if len(text) == 10 and validate_isbn(text):
        return text
    return None


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def validate_dewey(code: Any) -> bool:
    """Validate a Dewey Decimal number: ``###`` or ``###.###``."""
    if code in (None, ""):
        return True
    return bool(_DEWEY_RE.match(str(code).strip()))


def validate_call_no(code: Any) -> bool:
    """Validate a call number (≤64 chars, alphanumerics plus ``./-:``)."""
    if code in (None, ""):
        return True
    text = str(code).strip()
    if len(text) > 64:
        return False
    return bool(_CALL_NO_RE.match(text))


def validate_year(year: Any) -> bool:
    """Validate a four-digit publication year between 1000 and 2999."""
    if year in (None, ""):
        return True
    text = str(year).strip()
    if not re.fullmatch(r"\d{4}", text):
        return False
    return 1000 <= int(text) <= 2999


def dewey_bucket(code: Any) -> str:
    """Return the three-digit bucket of a Dewey number (``823.914`` -> ``823``)."""
    text = str(code or "").strip()
    if not text:
        return ""
    head = text.split(".", 1)[0]
    if head.isdigit():
        return "%03d" % int(head)
    return ""


# --------------------------------------------------------------------------- #
# Barcodes and accession numbers
# --------------------------------------------------------------------------- #


def sanitize_barcode(text: Any) -> str:
    """Reduce a value to Code-39-safe characters (``A-Z``, ``0-9``, ``-``)."""
    cleaned = _BARCODE_SAFE.sub("", str(text or "").upper())
    return cleaned or "NOACC"


def barcode_for(accession_no: str, copy_no: int) -> str:
    """Return the canonical per-copy barcode ``<accession>-<copy_no>``."""
    return sanitize_barcode(accession_no) + "-" + str(int(copy_no))


# --------------------------------------------------------------------------- #
# The rollup — single writer of books.copies / books.status
# --------------------------------------------------------------------------- #


def refresh_book_rollup(conn: Connection, book_id: int) -> dict[str, Any]:
    """Recompute ``books.copies`` and ``books.status`` for one title.

    Call inside the SAME transaction as any ``book_copies`` mutation. Never
    call it outside one: an out-of-transaction rollup is how the catalogue and
    the copy table start disagreeing.

    Returns the recomputed ``{total, available, on_loan, status}``.
    """
    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM book_copies WHERE book_id = ?", (int(book_id),)
        ).fetchone()[0]
    )
    available = int(
        conn.execute(
            "SELECT COUNT(*) FROM book_copies WHERE book_id = ? AND status = 'available'",
            (int(book_id),),
        ).fetchone()[0]
    )
    busy = int(
        conn.execute(
            "SELECT COUNT(*) FROM book_copies WHERE book_id = ? "
            "AND status IN ('on_loan','reserved')",
            (int(book_id),),
        ).fetchone()[0]
    )
    status = "available" if available else ("on_loan" if busy else "unavailable")
    conn.execute(
        "UPDATE books SET copies = ?, status = ?, updated_at = ? WHERE id = ?",
        (total, status, config.now_utc(), int(book_id)),
    )
    return {"total": total, "available": available, "on_loan": busy, "status": status}


# --------------------------------------------------------------------------- #
# Row serialisation
# --------------------------------------------------------------------------- #


def _serialise_book(row: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a ``books`` row into a JSON-ready dict with real booleans."""
    book = dict(row)
    book["copies"] = int(book.get("copies") or 0)
    book["price"] = config.money(book.get("price") or 0)
    return book


def _serialise_copy(row: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a ``book_copies`` row into a JSON-ready dict."""
    return dict(row)


def _parse_marc(blob: Any) -> dict[str, Any]:
    """Parse a MARC21-lite JSON blob, tolerating junk."""
    if not blob:
        return {}
    try:
        parsed = json.loads(blob) if isinstance(blob, str) else blob
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_marc_blob(fields: Mapping[str, Any] | None) -> str:
    """Serialise a ``{tag: value}`` mapping into a MARC21-lite JSON blob."""
    clean = {str(k): v for k, v in (fields or {}).items() if v not in (None, "")}
    return json.dumps(clean, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------- #
# CatalogService
# --------------------------------------------------------------------------- #


class CatalogService:
    """Books, copies, subjects, languages, MARC blobs and barcode generation."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # -- validation ------------------------------------------------------- #
    def _clean_book_fields(
        self, fields: Mapping[str, Any], creating: bool
    ) -> dict[str, Any]:
        """Validate and normalise incoming book fields.

        Raises :class:`~imri.errors.ImriError` with per-field codes so the
        client can put the message under the right input.
        """
        out: dict[str, Any] = {}
        field_errors: dict[str, str] = {}

        if "title" in fields or creating:
            title = config.clean_text(fields.get("title"))
            if not title:
                raise errors.ImriError(
                    errors.TITLE_REQUIRED,
                    errors.status_for(errors.TITLE_REQUIRED),
                    fields={"title": errors.TITLE_REQUIRED},
                )
            out["title"] = self._bounded(title, "title", 512, field_errors)

        if "isbn" in fields or creating:
            raw_isbn = config.clean_text(fields.get("isbn"))
            if raw_isbn:
                if not validate_isbn(raw_isbn):
                    raise errors.ImriError(
                        errors.INVALID_ISBN,
                        errors.status_for(errors.INVALID_ISBN),
                        fields={"isbn": errors.INVALID_ISBN},
                    )
                isbn13 = to_isbn13(raw_isbn)
                out["isbn"] = isbn13 or isbn_normalize(raw_isbn)
                out["isbn10"] = to_isbn10(out["isbn"])
            else:
                out["isbn"] = None
                out["isbn10"] = None

        if "dewey" in fields:
            dewey = config.clean_text(fields.get("dewey"))
            if dewey and not validate_dewey(dewey):
                raise errors.ImriError(
                    errors.INVALID_DEWEY,
                    errors.status_for(errors.INVALID_DEWEY),
                    fields={"dewey": errors.INVALID_DEWEY},
                )
            out["dewey"] = dewey

        if "call_no" in fields:
            call_no = config.clean_text(fields.get("call_no"))
            if call_no and not validate_call_no(call_no):
                raise errors.ImriError(
                    errors.INVALID_CALL_NO,
                    errors.status_for(errors.INVALID_CALL_NO),
                    fields={"call_no": errors.INVALID_CALL_NO},
                )
            out["call_no"] = call_no

        if "pub_year" in fields:
            year = config.clean_text(fields.get("pub_year"))
            if year and not validate_year(year):
                raise errors.ImriError(
                    errors.INVALID_YEAR,
                    errors.status_for(errors.INVALID_YEAR),
                    fields={"pub_year": errors.INVALID_YEAR},
                )
            out["pub_year"] = year

        if "material_type" in fields:
            material = config.clean_text(fields.get("material_type")) or "book"
            material = material.lower()
            if material not in config.MATERIAL_TYPES:
                raise errors.ImriError(
                    errors.UNKNOWN_MATERIAL_TYPE,
                    errors.status_for(errors.UNKNOWN_MATERIAL_TYPE),
                    fields={"material_type": errors.UNKNOWN_MATERIAL_TYPE},
                    details={"allowed": list(config.MATERIAL_TYPES)},
                )
            out["material_type"] = material

        if "classification" in fields:
            classification = (config.clean_text(fields.get("classification")) or "ddc").lower()
            if classification not in config.CLASSIFICATIONS:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"classification": errors.VALIDATION_FAILED},
                    details={"allowed": list(config.CLASSIFICATIONS)},
                )
            out["classification"] = classification

        if "status" in fields:
            status = (config.clean_text(fields.get("status")) or "available").lower()
            if status not in config.BOOK_STATUSES:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"status": errors.VALIDATION_FAILED},
                    details={"allowed": list(config.BOOK_STATUSES)},
                )
            out["status"] = status

        if "price" in fields:
            price = config.to_float(fields.get("price"), -1.0)
            if price < 0:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"price": errors.VALIDATION_FAILED},
                )
            out["price"] = config.money(price)

        if "language" in fields:
            out["language"] = (config.clean_text(fields.get("language")) or "en").lower()

        if "marc_blob" in fields:
            blob = fields.get("marc_blob")
            if isinstance(blob, Mapping):
                out["marc_blob"] = build_marc_blob(blob)
            else:
                out["marc_blob"] = config.clean_text(blob)

        for column in (
            "subtitle",
            "authors",
            "publisher",
            "edition",
            "location",
            "synopsis",
            "cover_path",
            "source",
            "currency",
            "subjects",
            "barcode",
        ):
            if column in fields:
                text = config.clean_text(fields.get(column))
                limit = _FIELD_LIMITS.get(column)
                if limit:
                    text = self._bounded(text, column, limit, field_errors)
                out[column] = text

        if field_errors:
            raise errors.ImriError(
                errors.VALIDATION_FAILED, fields=field_errors, details={"count": len(field_errors)}
            )
        return out

    @staticmethod
    def _bounded(
        value: str | None, field: str, limit: int, field_errors: dict[str, str]
    ) -> str | None:
        """Flag an over-long field instead of storing it.

        A 5,000-character title cannot corrupt the database, but it destroys the
        catalogue row, the label sheet and the printed report, so it is refused
        at the door with a per-field error the form can point at.
        """
        if value is not None and len(value) > limit:
            field_errors[field] = errors.VALIDATION_FAILED
        return value

    def _cover_path_safe(self, proposed: Any) -> str | None:
        """Validate a cover path: relative, no traversal, allow-listed suffix."""
        text = config.clean_text(proposed)
        if not text:
            return None
        normalised = text.replace("\\", "/")
        if ".." in normalised.split("/"):
            return None
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", normalised):
            return None
        if normalised.startswith("/") or re.match(r"^[A-Za-z]:", normalised):
            return None
        if not re.fullmatch(r"[A-Za-z0-9_./\- ]+\.(png|jpg|jpeg|webp)", normalised, re.IGNORECASE):
            return None
        return normalised

    # -- accession / barcode generation ----------------------------------- #
    def generate_accession(self) -> str:
        """Return an unused ``<prefix>-NNNNNN`` accession number."""
        prefix = sanitize_barcode(self.db.get_setting("label_prefix", "IMRI") or "IMRI")
        if not prefix or prefix == "NOACC":
            prefix = "IMRI"
        start = int(self.db.scalar("SELECT COUNT(*) FROM books", default=0)) + 1
        candidate_no = max(1, start)
        for _ in range(100000):
            candidate = "%s-%06d" % (prefix, candidate_no)
            if not self.db.one("SELECT id FROM books WHERE accession_no = ?", (candidate,)):
                return candidate
            candidate_no += 1
        raise errors.ImriError(errors.INTERNAL, errors.status_for(errors.INTERNAL))

    def generate_barcode(self, book: Mapping[str, Any] | str, copy_no: int) -> str:
        """Return the Code-39-safe barcode for a copy of ``book``."""
        if isinstance(book, Mapping):
            accession = str(book.get("accession_no") or "")
        else:
            accession = str(book or "")
        return barcode_for(accession, copy_no)

    # -- copies ----------------------------------------------------------- #
    def _next_copy_no(self, book_id: int) -> int:
        """Return the next free ``copy_no`` for a title (1 when empty)."""
        current = self.db.scalar(
            "SELECT MAX(copy_no) FROM book_copies WHERE book_id = ?", (int(book_id),), default=0
        )
        return int(current or 0) + 1

    def _create_copies(
        self,
        conn: Connection,
        book_id: int,
        accession_no: str,
        count: int,
        options: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Insert ``count`` copy rows inside an open transaction.

        The caller is responsible for calling :func:`refresh_book_rollup`
        afterwards, in the same transaction.
        """
        now = config.now_utc()
        start_no = config.to_int(options.get("start_no"), 0) or self._next_copy_no(book_id)
        location = config.clean_text(options.get("location"))
        condition = config.clean_text(options.get("condition"))
        if condition and condition not in config.COPY_CONDITIONS:
            condition = None
        acquired_at = config.clean_text(options.get("acquired_at")) or config.today_local()

        created: list[dict[str, Any]] = []
        for offset in range(int(count)):
            copy_no = start_no + offset
            barcode = barcode_for(accession_no, copy_no)
            cursor = conn.execute(
                "INSERT INTO book_copies(book_id, copy_no, barcode, status, location, "
                "condition, acquired_at, note, created_at, updated_at) "
                "VALUES (?, ?, ?, 'available', ?, ?, ?, ?, ?, ?)",
                (
                    int(book_id),
                    copy_no,
                    barcode,
                    location,
                    condition,
                    acquired_at,
                    None,
                    now,
                    now,
                ),
            )
            created.append(
                {
                    "id": int(cursor.lastrowid),
                    "book_id": int(book_id),
                    "copy_no": copy_no,
                    "barcode": barcode,
                    "status": "available",
                    "location": location,
                    "condition": condition,
                    "acquired_at": acquired_at,
                    "note": None,
                }
            )
        return created

    def add_copies(
        self,
        book_id: int,
        count: int = 1,
        options: Mapping[str, Any] | None = None,
        actor: str = "system",
        ip: str | None = None,
    ) -> list[dict[str, Any]]:
        """Add ``count`` copies to a title, then refresh the rollup."""
        book = self.db.one("SELECT * FROM books WHERE id = ?", (int(book_id),))
        if book is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        quantity = config.to_int(count, 0)
        if quantity < 1 or quantity > 500:
            raise errors.ImriError(
                errors.VALIDATION_FAILED,
                errors.status_for(errors.VALIDATION_FAILED),
                fields={"count": errors.VALIDATION_FAILED},
                details={"min": 1, "max": 500},
            )
        opts = dict(options or {})
        with self.db.transaction() as conn:
            created = self._create_copies(conn, int(book_id), book["accession_no"], quantity, opts)
            refresh_book_rollup(conn, int(book_id))
            self.db.audit(
                actor,
                "copy.create",
                target=book["accession_no"],
                detail={"count": quantity, "copy_ids": [c["id"] for c in created]},
                ip=ip,
                target_type="book",
            )
        return created

    def update_copy(
        self,
        copy_id: int,
        fields: Mapping[str, Any],
        actor: str = "system",
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Patch one copy's ``status`` / ``location`` / ``condition`` / ``note``."""
        copy = self.db.one("SELECT * FROM book_copies WHERE id = ?", (int(copy_id),))
        if copy is None:
            raise errors.ImriError(
                errors.COPY_NOT_FOUND, errors.status_for(errors.COPY_NOT_FOUND)
            )

        sets: list[str] = []
        params: list[Any] = []
        changes: dict[str, Any] = {}

        if "status" in fields:
            status = (config.clean_text(fields.get("status")) or "").lower()
            if status not in config.COPY_STATUSES:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"status": errors.VALIDATION_FAILED},
                    details={"allowed": list(config.COPY_STATUSES)},
                )
            if copy["status"] == "on_loan" and status != "on_loan":
                raise errors.ImriError(
                    errors.COPY_ON_LOAN, errors.status_for(errors.COPY_ON_LOAN)
                )
            sets.append("status = ?")
            params.append(status)
            changes["status"] = status

        if "condition" in fields:
            condition = config.clean_text(fields.get("condition"))
            if condition and condition not in config.COPY_CONDITIONS:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"condition": errors.VALIDATION_FAILED},
                    details={"allowed": list(config.COPY_CONDITIONS)},
                )
            sets.append("condition = ?")
            params.append(condition)
            changes["condition"] = condition

        for column in ("location", "note"):
            if column in fields:
                sets.append("%s = ?" % column)
                params.append(config.clean_text(fields.get(column)))
                changes[column] = config.clean_text(fields.get(column))

        if not sets:
            return _serialise_copy(copy)

        sets.append("updated_at = ?")
        params.append(config.now_utc())
        params.append(int(copy_id))
        with self.db.transaction() as conn:
            conn.execute("UPDATE book_copies SET %s WHERE id = ?" % ", ".join(sets), params)
            refresh_book_rollup(conn, int(copy["book_id"]))
            self.db.audit(
                actor,
                "copy.update",
                target=copy["barcode"],
                detail=changes,
                ip=ip,
                target_type="copy",
            )
        fresh = self.db.one("SELECT * FROM book_copies WHERE id = ?", (int(copy_id),))
        return _serialise_copy(fresh or copy)

    def delete_copy(self, copy_id: int, actor: str = "system", ip: str | None = None) -> None:
        """Delete one copy, refusing an on-loan copy or the last remaining one."""
        copy = self.db.one("SELECT * FROM book_copies WHERE id = ?", (int(copy_id),))
        if copy is None:
            raise errors.ImriError(
                errors.COPY_NOT_FOUND, errors.status_for(errors.COPY_NOT_FOUND)
            )
        if copy["status"] == "on_loan":
            raise errors.ImriError(errors.COPY_ON_LOAN, errors.status_for(errors.COPY_ON_LOAN))
        total = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM book_copies WHERE book_id = ?", (int(copy["book_id"]),),
                default=0,
            )
        )
        if total <= 1:
            raise errors.ImriError(errors.LAST_COPY, errors.status_for(errors.LAST_COPY))
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM book_copies WHERE id = ?", (int(copy_id),))
            refresh_book_rollup(conn, int(copy["book_id"]))
            self.db.audit(
                actor,
                "copy.delete",
                target=copy["barcode"],
                detail={"copy_no": copy["copy_no"]},
                ip=ip,
                target_type="copy",
            )

    def get_copy(self, copy_id: int) -> dict[str, Any] | None:
        """Return one copy row, or ``None``."""
        return self.db.one("SELECT * FROM book_copies WHERE id = ?", (int(copy_id),))

    def list_copies(self, book_id: int) -> list[dict[str, Any]]:
        """Return every copy of a title with its current loan, if any."""
        copies = self.db.query(
            "SELECT c.*, l.id AS loan_id, l.member_id AS loan_member_id, "
            "       l.due_date AS loan_due_date, m.name AS loan_member_name, "
            "       m.member_code AS loan_member_code "
            "FROM book_copies c "
            "LEFT JOIN loans l ON l.copy_id = c.id AND l.status = 'on_loan' "
            "LEFT JOIN members m ON m.id = l.member_id "
            "WHERE c.book_id = ? ORDER BY c.copy_no",
            (int(book_id),),
        )
        out: list[dict[str, Any]] = []
        for row in copies:
            item = dict(row)
            loan_id = item.pop("loan_id", None)
            member_id = item.pop("loan_member_id", None)
            member_name = item.pop("loan_member_name", None)
            member_code = item.pop("loan_member_code", None)
            due_date = item.pop("loan_due_date", None)
            current_loan = None
            if loan_id is not None:
                current_loan = {
                    "id": int(loan_id),
                    "member_id": member_id,
                    "member_name": member_name,
                    "member_code": member_code,
                    "due_date": due_date,
                }
            item["current_loan"] = current_loan
            out.append(item)
        return out

    def available_copies(self, book_id: int) -> int:
        """Return how many copies of a title are lendable right now."""
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM book_copies WHERE book_id = ? AND status = 'available'",
                (int(book_id),),
                default=0,
            )
        )

    def copy_counts(self, book_ids: Sequence[int]) -> dict[int, dict[str, int]]:
        """Return ``{book_id: {total, available, on_loan, reserved}}`` for a page.

        This is the second query of the two-query page pattern: one aggregate
        for the whole page, never a query inside a loop (ARCHITECTURE §10).
        """
        ids = [int(b) for b in book_ids]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.db.query(
            "SELECT book_id, COUNT(*) AS total, "
            "       SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available, "
            "       SUM(CASE WHEN status = 'on_loan'   THEN 1 ELSE 0 END) AS on_loan, "
            "       SUM(CASE WHEN status = 'reserved'  THEN 1 ELSE 0 END) AS reserved "
            "FROM book_copies WHERE book_id IN (%s) GROUP BY book_id" % placeholders,
            ids,
        )
        return {
            int(row["book_id"]): {
                "total": int(row["total"] or 0),
                "available": int(row["available"] or 0),
                "on_loan": int(row["on_loan"] or 0),
                "reserved": int(row["reserved"] or 0),
            }
            for row in rows
        }

    def refresh_book_rollup(self, book_id: int) -> dict[str, Any]:
        """Transaction-safe wrapper around the module-level rollup."""
        with self.db.transaction() as conn:
            return refresh_book_rollup(conn, int(book_id))

    # -- books CRUD ------------------------------------------------------- #
    def _search_clause(self, term: str) -> tuple[str, list[Any]]:
        """Return the free-text part of the WHERE clause for ``term``.

        The FTS5 index is the fast path: ``MATCH`` answers a 200,000-title
        catalogue in single-digit milliseconds where eleven ``LIKE '%…%'``
        comparisons needed 2.2 million evaluations to produce a total.

        The ``LIKE`` branch is kept for the one case it is still the only
        option — a SQLite build without FTS5, or a file that has not been
        upgraded — and it is also what the module's wildcard-escaping
        behaviour (``config.like_term``) was written for, so search keeps
        working, only slower.
        """
        if self.db.has_search_index:
            match = search_match_query(term)
            if not match:
                # Nothing searchable in the term: no row can match. Saying so
                # is cheaper and more honest than scanning for a literal '%'.
                return "0", []
            return (
                "b.id IN (SELECT rowid FROM %s WHERE %s MATCH ?)" % (SEARCH_TABLE, SEARCH_TABLE),
                [match],
            )

        like = config.like_term(term)
        return (
            "(b.title LIKE ? ESCAPE '\\' OR b.subtitle LIKE ? ESCAPE '\\' "
            " OR b.authors LIKE ? ESCAPE '\\' OR b.accession_no LIKE ? ESCAPE '\\' "
            " OR b.isbn LIKE ? ESCAPE '\\' OR b.isbn10 LIKE ? ESCAPE '\\' "
            " OR b.call_no LIKE ? ESCAPE '\\' OR b.subjects LIKE ? ESCAPE '\\' "
            " OR b.publisher LIKE ? ESCAPE '\\' OR b.barcode LIKE ? ESCAPE '\\' "
            " OR b.location LIKE ? ESCAPE '\\')",
            [like] * 11,
        )

    def _where_from_filters(self, filters: Mapping[str, Any]) -> tuple[str, list[Any], str]:
        """Build a parameterised WHERE clause from the list filters.

        Returns ``(where, params, match)``. ``match`` is the FTS expression when
        a search term is the *only* filter, so the caller can answer
        ``COUNT(*)`` from the index alone; it is ``""`` whenever the count still
        has to run against ``books``.
        """
        clauses: list[str] = []
        params: list[Any] = []

        term = config.clean_text(filters.get("q"))
        if term:
            clause, values = self._search_clause(term)
            clauses.append(clause)
            params.extend(values)

        for column, key in (
            ("b.material_type", "material_type"),
            ("b.language", "language"),
            ("b.status", "status"),
            ("b.classification", "classification"),
            ("b.location", "location"),
        ):
            value = config.clean_text(filters.get(key))
            if value and value.lower() != "all":
                clauses.append("%s = ?" % column)
                params.append(value)

        dewey_from = config.clean_text(filters.get("dewey_from"))
        if dewey_from:
            clauses.append("b.dewey >= ?")
            params.append(dewey_from)
        dewey_to = config.clean_text(filters.get("dewey_to"))
        if dewey_to:
            clauses.append("b.dewey <= ?")
            params.append(dewey_to + "\uffff")

        if config.to_bool(filters.get("available_only")):
            clauses.append(
                "EXISTS (SELECT 1 FROM book_copies c WHERE c.book_id = b.id "
                "AND c.status = 'available')"
            )

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        # A lone search term is the case the search screen actually hits; the
        # MATCH expression is already in ``params`` and can be reused verbatim.
        # ``params`` is empty when the term held no searchable token at all.
        match = params[0] if (term and len(clauses) == 1 and params) else ""
        return where, params, match

    def _count_books(self, where: str, params: list[Any], match: str) -> int:
        """Count the titles a filter matches.

        A bare search is counted from the FTS index: ``COUNT(*)`` over a MATCH
        never touches ``books``, whereas the general ``id IN (SELECT rowid …)``
        form probes the table once per hit — 40 ms for a common author, and
        proportional to the number of hits, which is the wrong shape for a
        screen a librarian types into.
        """
        if match:
            return int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM %s WHERE %s MATCH ?" % (SEARCH_TABLE, SEARCH_TABLE),
                    (match,),
                    default=0,
                )
            )
        return int(self.db.scalar("SELECT COUNT(*) FROM books b" + where, params, default=0))

    def list_books(self, filters: Mapping[str, Any] | None, page: Mapping[str, Any]) -> dict[str, Any]:
        """Return one page of titles plus the copy aggregates for that page."""
        criteria = dict(filters or {})
        where, params, match = self._where_from_filters(criteria)

        sort_key = str(page.get("sort") or "title")
        sort_sql = BOOK_SORTS.get(sort_key, BOOK_SORTS["title"])
        order = "DESC" if str(page.get("order", "asc")).lower() == "desc" else "ASC"
        limit = int(page.get("page_size", 50))
        offset = config.page_offset(page, limit)

        total = self._count_books(where, params, match)
        rows = self.db.query(
            "SELECT b.* FROM books b" + where + " ORDER BY %s %s, b.id ASC LIMIT ? OFFSET ?"
            % (sort_sql, order),
            params + [limit, offset],
        )

        counts = self.copy_counts([row["id"] for row in rows])
        items: list[dict[str, Any]] = []
        for row in rows:
            book = _serialise_book(row)
            aggregate = counts.get(int(row["id"]), {})
            book["total_copies"] = aggregate.get("total", int(book.get("copies") or 0))
            book["available"] = aggregate.get("available", 0)
            book["on_loan"] = aggregate.get("on_loan", 0)
            book["reserved"] = aggregate.get("reserved", 0)
            items.append(book)

        pages = (total + limit - 1) // limit if limit else 0
        return {
            "items": items,
            "meta": {
                "page": int(page.get("page", 1)),
                "page_size": limit,
                "total": total,
                "pages": pages,
                "sort": sort_key if sort_key in BOOK_SORTS else "title",
                "order": order.lower(),
            },
        }

    def iter_books(self, filters: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Return every matching book (no pagination) — used by exports."""
        criteria = dict(filters or {})
        where, params, _match = self._where_from_filters(criteria)
        rows = self.db.query("SELECT b.* FROM books b" + where + " ORDER BY b.title COLLATE NOCASE", params)
        return [_serialise_book(row) for row in rows]

    def get_book(self, book_id: int) -> dict[str, Any]:
        """Return a title with its copies, subjects, languages, MARC and loans."""
        row = self.db.one("SELECT * FROM books WHERE id = ?", (int(book_id),))
        if row is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        book = _serialise_book(row)

        copies = self.list_copies(int(book_id))
        subjects = [s["heading"] for s in self.book_subjects(int(book_id))]
        languages = self.book_languages(int(book_id))

        current = self.db.query(
            "SELECT l.*, m.name AS member_name, m.member_code "
            "FROM loans l LEFT JOIN members m ON m.id = l.member_id "
            "WHERE l.book_id = ? AND l.status = 'on_loan' ORDER BY l.due_date",
            (int(book_id),),
        )
        history = self.db.query(
            "SELECT l.*, m.name AS member_name FROM loans l "
            "LEFT JOIN members m ON m.id = l.member_id "
            "WHERE l.book_id = ? AND l.status <> 'on_loan' "
            "ORDER BY l.return_date DESC, l.id DESC LIMIT 50",
            (int(book_id),),
        )

        return {
            "book": book,
            "copies": copies,
            "subjects": subjects,
            "languages": languages,
            "marc": _parse_marc(book.get("marc_blob")),
            "loans": {"current": current, "history": history},
            "available": sum(1 for c in copies if c.get("status") == "available"),
        }

    def create_book(
        self,
        fields: Mapping[str, Any],
        actor: str = "system",
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Create a title, its copies and its vocabulary links atomically."""
        clean = self._clean_book_fields(fields, creating=True)

        accession = config.clean_text(fields.get("accession_no"))
        if accession:
            accession = sanitize_barcode(accession)
            if self.db.one("SELECT id FROM books WHERE accession_no = ?", (accession,)):
                raise errors.ImriError(
                    errors.DUPLICATE_ACCESSION,
                    errors.status_for(errors.DUPLICATE_ACCESSION),
                    fields={"accession_no": errors.DUPLICATE_ACCESSION},
                )
        else:
            accession = self.generate_accession()

        isbn = clean.get("isbn")
        if isbn and self.db.one("SELECT id FROM books WHERE isbn = ?", (isbn,)):
            raise errors.ImriError(
                errors.DUPLICATE_ISBN,
                errors.status_for(errors.DUPLICATE_ISBN),
                fields={"isbn": errors.DUPLICATE_ISBN},
            )

        copies_count = config.to_int(fields.get("copies"), 1)
        if copies_count < 0 or copies_count > 500:
            raise errors.ImriError(
                errors.VALIDATION_FAILED,
                errors.status_for(errors.VALIDATION_FAILED),
                fields={"copies": errors.VALIDATION_FAILED},
                details={"min": 0, "max": 500},
            )

        now = config.now_utc()
        values: dict[str, Any] = {
            "accession_no": accession,
            "isbn": isbn,
            "isbn10": clean.get("isbn10"),
            "title": clean["title"],
            "subtitle": clean.get("subtitle"),
            "authors": clean.get("authors"),
            "publisher": clean.get("publisher"),
            "pub_year": clean.get("pub_year"),
            "edition": clean.get("edition"),
            "language": clean.get("language") or "en",
            "dewey": clean.get("dewey"),
            "classification": clean.get("classification") or "ddc",
            "call_no": clean.get("call_no"),
            "subjects": None,
            "material_type": clean.get("material_type") or "book",
            "location": clean.get("location"),
            "copies": 0,
            "price": clean.get("price") or 0.0,
            "currency": clean.get("currency") or self.db.get_setting("currency", "RM") or "RM",
            "synopsis": clean.get("synopsis"),
            "marc_blob": clean.get("marc_blob"),
            "cover_path": self._cover_path_safe(clean.get("cover_path")),
            "barcode": clean.get("barcode") or accession,
            "status": "unavailable",
            "source": clean.get("source") or "manual",
            "created_at": now,
            "updated_at": now,
        }

        columns = ",".join(_BOOK_INSERT_COLUMNS)
        placeholders = ",".join("?" for _ in _BOOK_INSERT_COLUMNS)
        params = [values.get(column) for column in _BOOK_INSERT_COLUMNS]

        subjects = fields.get("subjects")
        if isinstance(subjects, str):
            subjects = [part.strip() for part in subjects.split(",") if part.strip()]
        languages = fields.get("languages")
        if isinstance(languages, str):
            languages = [part.strip() for part in languages.split(",") if part.strip()]

        with self.db.transaction() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO books(%s) VALUES(%s)" % (columns, placeholders), params
                )
            except IntegrityError as exc:
                message = str(exc).lower()
                if "accession_no" in message:
                    code = errors.DUPLICATE_ACCESSION
                elif "isbn" in message:
                    code = errors.DUPLICATE_ISBN
                else:
                    code = errors.VALIDATION_FAILED
                raise errors.ImriError(code, errors.status_for(code)) from exc

            book_id = int(cursor.lastrowid)
            created_copies: list[dict[str, Any]] = []
            if copies_count:
                created_copies = self._create_copies(
                    conn,
                    book_id,
                    accession,
                    copies_count,
                    {
                        "location": values.get("location"),
                        "start_no": 1,
                    },
                )
            refresh_book_rollup(conn, book_id)
            if subjects:
                self._set_subjects_conn(conn, book_id, subjects)
            if languages:
                self._set_languages_conn(conn, book_id, languages)
            elif values.get("language"):
                self._set_languages_conn(conn, book_id, [values["language"]])

            self.db.audit(
                actor,
                "book.create",
                target=accession,
                detail={"title": values["title"], "copies": len(created_copies)},
                ip=ip,
                target_type="book",
            )

        fresh = self.db.one("SELECT * FROM books WHERE id = ?", (book_id,)) or values
        return {"book": _serialise_book(fresh), "copies_created": len(created_copies)}

    def update_book(
        self,
        book_id: int,
        fields: Mapping[str, Any],
        actor: str = "system",
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Patch a title. Business keys and the rollup column are read-only."""
        current = self.db.one("SELECT * FROM books WHERE id = ?", (int(book_id),))
        if current is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))

        if "accession_no" in fields:
            proposed = config.clean_text(fields.get("accession_no"))
            if proposed and sanitize_barcode(proposed) != current["accession_no"]:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"accession_no": errors.VALIDATION_FAILED},
                    details={"reason": "immutable"},
                )

        clean = self._clean_book_fields(fields, creating=False)

        # `copies` on an update means "make the title have N copies".
        target_copies: int | None = None
        if "copies" in fields:
            target_copies = config.to_int(fields.get("copies"), -1)
            if target_copies < 0:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"copies": errors.VALIDATION_FAILED},
                )

        if clean.get("isbn") and clean["isbn"] != current["isbn"]:
            clash = self.db.one(
                "SELECT id FROM books WHERE isbn = ? AND id <> ?", (clean["isbn"], int(book_id))
            )
            if clash:
                raise errors.ImriError(
                    errors.DUPLICATE_ISBN,
                    errors.status_for(errors.DUPLICATE_ISBN),
                    fields={"isbn": errors.DUPLICATE_ISBN},
                )

        sets: list[str] = []
        params: list[Any] = []
        changes: dict[str, Any] = {}
        for column in _BOOK_COLUMNS:
            if column in _IMMUTABLE_BOOK_COLUMNS:
                continue
            if column not in clean:
                continue
            value = clean[column]
            if column == "cover_path":
                value = self._cover_path_safe(value)
            sets.append("%s = ?" % column)
            params.append(value)
            changes[column] = value

        if not sets and target_copies is None:
            return {"book": _serialise_book(current)}

        now = config.now_utc()
        with self.db.transaction() as conn:
            if sets:
                sets.append("updated_at = ?")
                params.append(now)
                params.append(int(book_id))
                conn.execute("UPDATE books SET %s WHERE id = ?" % ", ".join(sets), params)

            if target_copies is not None:
                existing = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM book_copies WHERE book_id = ?", (int(book_id),)
                    ).fetchone()[0]
                )
                if target_copies > existing:
                    self._create_copies(
                        conn,
                        int(book_id),
                        current["accession_no"],
                        target_copies - existing,
                        {},
                    )
                elif target_copies < existing:
                    raise errors.ImriError(
                        errors.VALIDATION_FAILED,
                        errors.status_for(errors.VALIDATION_FAILED),
                        fields={"copies": errors.VALIDATION_FAILED},
                        details={"reason": "remove_copies_explicitly", "current": existing},
                    )
                refresh_book_rollup(conn, int(book_id))

            self.db.audit(
                actor,
                "book.update",
                target=current["accession_no"],
                detail=changes,
                ip=ip,
                target_type="book",
            )

        fresh = self.db.one("SELECT * FROM books WHERE id = ?", (int(book_id),)) or current
        return {"book": _serialise_book(fresh)}

    def delete_book(self, book_id: int, actor: str = "system", ip: str | None = None) -> None:
        """Hard-delete a title, refusing when copies are out or holds are live."""
        book = self.db.one("SELECT * FROM books WHERE id = ?", (int(book_id),))
        if book is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))

        active_loans = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans WHERE book_id = ? AND status = 'on_loan'",
                (int(book_id),),
                default=0,
            )
        )
        total_loans = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans WHERE book_id = ?", (int(book_id),), default=0
            )
        )
        live_reservations = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM reservations WHERE book_id = ? "
                "AND status IN ('open','ready')",
                (int(book_id),),
                default=0,
            )
        )

        if active_loans:
            raise errors.ImriError(
                errors.BOOK_HAS_ACTIVE_LOANS,
                errors.status_for(errors.BOOK_HAS_ACTIVE_LOANS),
                details={"active_loans": active_loans},
            )
        if total_loans:
            raise errors.ImriError(
                errors.BOOK_HAS_ACTIVE_LOANS,
                errors.status_for(errors.BOOK_HAS_ACTIVE_LOANS),
                details={"historical_loans": total_loans, "reason": "loan_history"},
            )
        if live_reservations:
            raise errors.ImriError(
                errors.BOOK_HAS_RESERVATIONS,
                errors.status_for(errors.BOOK_HAS_RESERVATIONS),
                details={"reservations": live_reservations},
            )

        with self.db.transaction() as conn:
            conn.execute("DELETE FROM books WHERE id = ?", (int(book_id),))
            self.db.audit(
                actor,
                "book.delete",
                target=book["accession_no"],
                detail={"title": book["title"]},
                ip=ip,
                target_type="book",
            )

    def bulk_update(
        self, ids: Iterable[int], patch: Mapping[str, Any], actor: str = "system", ip: str | None = None
    ) -> int:
        """Apply a small patch to many titles (CAT-15 backend).

        A ``status`` in the patch is applied to the *copies* that are sitting
        on the shelf, then the rollup recomputes the title. That keeps
        ``books.status`` the exclusive property of
        :func:`refresh_book_rollup` (ARCHITECTURE §8.6) while still letting the
        librarian withdraw a class set in one click.
        """
        allowed = {"material_type", "location"}
        columns = [c for c in allowed if c in patch]

        target_status: str | None = None
        if "status" in patch:
            candidate = (config.clean_text(patch.get("status")) or "").lower()
            if candidate not in BULK_COPY_STATUSES:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"status": errors.VALIDATION_FAILED},
                    details={
                        "allowed": sorted(BULK_COPY_STATUSES),
                        "reason": "derived_status",
                    },
                )
            target_status = candidate

        if not columns and target_status is None:
            return 0

        values = {c: config.clean_text(patch.get(c)) for c in columns}
        if "material_type" in values and values["material_type"] not in config.MATERIAL_TYPES:
            raise errors.ImriError(
                errors.UNKNOWN_MATERIAL_TYPE,
                errors.status_for(errors.UNKNOWN_MATERIAL_TYPE),
                fields={"material_type": errors.UNKNOWN_MATERIAL_TYPE},
            )
        target_ids = [int(i) for i in ids if str(i).strip().lstrip("-").isdigit()]
        if not target_ids:
            return 0

        now = config.now_utc()
        assignments = ", ".join("%s = ?" % c for c in columns)
        with self.db.transaction() as conn:
            updated = 0
            for book_id in target_ids:
                if columns:
                    cursor = conn.execute(
                        "UPDATE books SET %s, updated_at = ? WHERE id = ?" % assignments,
                        [values[c] for c in columns] + [now, book_id],
                    )
                    updated += int(cursor.rowcount or 0)
                else:
                    updated += 1

                if target_status is not None:
                    # Only copies that are actually on the shelf change state;
                    # a copy out on loan keeps its loan and its status.
                    conn.execute(
                        "UPDATE book_copies SET status = ?, updated_at = ? "
                        "WHERE book_id = ? AND status IN ('available','withdrawn','lost','damaged')",
                        (target_status, now, book_id),
                    )
                    refresh_book_rollup(conn, book_id)

            self.db.audit(
                actor,
                "book.bulk_update",
                target="books",
                detail={
                    "ids": target_ids,
                    "patch": values,
                    "copy_status": target_status,
                },
                ip=ip,
                target_type="book",
            )
        return updated

    # -- lookup and duplicates -------------------------------------------- #
    def lookup_barcode(self, barcode: Any) -> dict[str, Any]:
        """Resolve a scanned barcode to ``{book, copy, member}``.

        Tries a copy barcode first, then a book barcode or accession number.
        Raises ``not_found`` (404) so the counter can show an inline message
        without clearing the scan field.

        The ``OR`` is deliberately left as one statement. It only looks like a
        full scan: with ``ix_books_barcode`` (schema 2) beside the unique
        ``accession_no`` index, SQLite turns it into a MULTI-INDEX OR and both
        branches are seeks. Without that index the planner could use neither and
        fell back to scanning all 200,000 titles — 236 ms on every mistyped
        scan at the counter.
        """
        text = config.clean_text(barcode)
        if not text:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))

        copy = self.db.one(
            "SELECT c.*, b.title AS book_title, b.accession_no AS book_accession "
            "FROM book_copies c JOIN books b ON b.id = c.book_id WHERE c.barcode = ?",
            (text,),
        )
        if copy:
            book = self.db.one("SELECT * FROM books WHERE id = ?", (int(copy["book_id"]),))
            return {
                "book": _serialise_book(book or {}),
                "copy": _serialise_copy(
                    {k: v for k, v in copy.items() if not k.startswith("book_")}
                ),
                "member": None,
            }

        book = self.db.one(
            "SELECT * FROM books WHERE barcode = ? OR accession_no = ?", (text, text)
        )
        if book:
            first_copy = self.db.one(
                "SELECT * FROM book_copies WHERE book_id = ? ORDER BY "
                "CASE status WHEN 'available' THEN 0 ELSE 1 END, copy_no LIMIT 1",
                (int(book["id"]),),
            )
            return {
                "book": _serialise_book(book),
                "copy": _serialise_copy(first_copy) if first_copy else None,
                "member": None,
            }

        member = self.db.one("SELECT * FROM members WHERE member_code = ?", (text,))
        if member:
            return {"book": None, "copy": None, "member": dict(member)}

        raise errors.ImriError(
            errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND), details={"barcode": text}
        )

    def find_duplicates(
        self,
        isbn: Any = None,
        title: Any = None,
        authors: Any = None,
        exclude_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return likely duplicate titles with the reason each matched."""
        candidates: list[dict[str, Any]] = []
        seen: set[int] = set()

        normalised_isbn = to_isbn13(isbn) if isbn else None
        if normalised_isbn:
            rows = self.db.query(
                "SELECT * FROM books WHERE (isbn = ? OR isbn10 = ?) ORDER BY id LIMIT 20",
                (normalised_isbn, to_isbn10(normalised_isbn) or normalised_isbn),
            )
            for row in rows:
                if exclude_id is not None and int(row["id"]) == int(exclude_id):
                    continue
                seen.add(int(row["id"]))
                candidates.append({"book": _serialise_book(row), "reason": "same_isbn"})

        title_text = config.clean_text(title)
        if title_text:
            rows = self.db.query(
                "SELECT * FROM books WHERE title LIKE ? ESCAPE '\\' ORDER BY id LIMIT 20",
                (config.like_term(title_text),),
            )
            for row in rows:
                if exclude_id is not None and int(row["id"]) == int(exclude_id):
                    continue
                if int(row["id"]) in seen:
                    continue
                reason = "same_title"
                author_text = config.clean_text(authors)
                if author_text and row.get("authors"):
                    first_author = author_text.split(",")[0].strip().lower()
                    if first_author and first_author in str(row["authors"]).lower():
                        reason = "same_title_author"
                seen.add(int(row["id"]))
                candidates.append({"book": _serialise_book(row), "reason": reason})

        return candidates

    # -- vocabulary ------------------------------------------------------- #
    def _set_subjects_conn(
        self, conn: Connection, book_id: int, headings: Iterable[Any]
    ) -> list[str]:
        conn.execute("DELETE FROM book_subjects WHERE book_id = ?", (int(book_id),))
        labels: list[str] = []
        for raw in headings or []:
            heading = config.clean_text(raw)
            if not heading:
                continue
            row = conn.execute(
                "SELECT id FROM subjects WHERE heading = ?", (heading,)
            ).fetchone()
            if row:
                subject_id = int(row[0])
            else:
                cursor = conn.execute(
                    "INSERT INTO subjects(heading) VALUES (?)", (heading,)
                )
                subject_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT OR IGNORE INTO book_subjects(book_id, subject_id) VALUES (?, ?)",
                (int(book_id), subject_id),
            )
            labels.append(heading)
        conn.execute(
            "UPDATE books SET subjects = ?, updated_at = ? WHERE id = ?",
            (", ".join(labels) if labels else None, config.now_utc(), int(book_id)),
        )
        return labels

    def set_subjects(self, book_id: int, headings: Iterable[Any]) -> list[str]:
        """Replace a title's subject headings (controlled vocabulary, CAT-06)."""
        self._require_book(book_id)
        with self.db.transaction() as conn:
            return self._set_subjects_conn(conn, int(book_id), headings)

    def _set_languages_conn(
        self, conn: Connection, book_id: int, langs: Iterable[Any]
    ) -> list[str]:
        conn.execute("DELETE FROM book_languages WHERE book_id = ?", (int(book_id),))
        codes: list[str] = []
        for raw in langs or []:
            code = config.clean_text(raw)
            if not code:
                continue
            code = code.lower()
            conn.execute(
                "INSERT OR IGNORE INTO book_languages(book_id, lang) VALUES (?, ?)",
                (int(book_id), code),
            )
            codes.append(code)
        if codes:
            conn.execute(
                "UPDATE books SET language = ?, updated_at = ? WHERE id = ?",
                (codes[0], config.now_utc(), int(book_id)),
            )
        return codes

    def set_languages(self, book_id: int, langs: Iterable[Any]) -> list[str]:
        """Replace a title's language list; the first entry becomes primary."""
        self._require_book(book_id)
        with self.db.transaction() as conn:
            return self._set_languages_conn(conn, int(book_id), langs)

    def set_marc(self, book_id: int, blob: Mapping[str, Any]) -> dict[str, Any]:
        """Store a MARC21-lite blob, round-tripping unknown tags verbatim."""
        self._require_book(book_id)
        if not isinstance(blob, Mapping):
            raise errors.ImriError(
                errors.VALIDATION_FAILED,
                errors.status_for(errors.VALIDATION_FAILED),
                fields={"blob": errors.VALIDATION_FAILED},
            )
        text = build_marc_blob(blob)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE books SET marc_blob = ?, updated_at = ? WHERE id = ?",
                (text, config.now_utc(), int(book_id)),
            )
        return _parse_marc(text)

    def book_subjects(self, book_id: int) -> list[dict[str, Any]]:
        """Return a title's subject headings, alphabetically."""
        return self.db.query(
            "SELECT s.id, s.heading FROM subjects s "
            "JOIN book_subjects bs ON bs.subject_id = s.id "
            "WHERE bs.book_id = ? ORDER BY s.heading",
            (int(book_id),),
        )

    def book_languages(self, book_id: int) -> list[str]:
        """Return a title's language codes."""
        return [
            row["lang"]
            for row in self.db.query(
                "SELECT lang FROM book_languages WHERE book_id = ? ORDER BY lang",
                (int(book_id),),
            )
        ]

    def list_subjects(self, term: Any = None, limit: int = 200) -> list[dict[str, Any]]:
        """Return subject headings, optionally filtered by a search term."""
        text = config.clean_text(term)
        if text:
            return self.db.query(
                "SELECT id, heading FROM subjects WHERE heading LIKE ? ESCAPE '\\' "
                "ORDER BY heading LIMIT ?",
                (config.like_term(text), int(limit)),
            )
        return self.db.query(
            "SELECT id, heading FROM subjects ORDER BY heading LIMIT ?", (int(limit),)
        )

    def _require_book(self, book_id: int) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM books WHERE id = ?", (int(book_id),))
        if row is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        return row
