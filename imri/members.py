"""Members: students, teachers and patrons.

Covers CRUD, MyKad-aware IC validation, the 360° profile aggregate the detail
drawer renders, and reading lists (RDL-01).
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from . import config, errors
from .config import get_logger
from .db import IntegrityError

_log = get_logger("members")

#: Frozen allow-list of writable member columns.
_MEMBER_COLUMNS: tuple[str, ...] = (
    "member_code",
    "name",
    "member_type",
    "ic_number",
    "form",
    "class_name",
    "address",
    "phone",
    "email",
    "photo_path",
    "membership_date",
    "expiry_date",
    "status",
    "note",
)

#: Business keys are immutable after creation (ADR-10).
_IMMUTABLE_MEMBER_COLUMNS = frozenset({"member_code"})

MEMBER_SORTS: dict[str, str] = {
    "id": "m.id",
    "name": "m.name COLLATE NOCASE",
    "member_code": "m.member_code",
    "class_name": "m.class_name COLLATE NOCASE",
    "form": "m.form COLLATE NOCASE",
    "member_type": "m.member_type",
    "status": "m.status",
    "membership_date": "m.membership_date",
    "expiry_date": "m.expiry_date",
    "created_at": "m.created_at",
}

_IC_STRIP = re.compile(r"[\s-]")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

#: Type -> code prefix for auto-generated member codes.
_CODE_PREFIX = {"student": "S", "teacher": "T", "patron": "P"}


def validate_ic(ic: Any) -> bool:
    """Validate a Malaysian IC / MyKad number.

    A pure-digit value must be exactly 12 digits (dashes and spaces optional).
    A value containing letters is accepted — passports, MyKid and foreign
    identifiers are legitimate — and flagged separately by :func:`ic_warning`.
    """
    if ic in (None, ""):
        return True
    text = _IC_STRIP.sub("", str(ic).strip())
    if not text:
        return True
    if text.isdigit():
        return len(text) == 12
    return 3 <= len(text) <= 24


def ic_warning(ic: Any) -> str | None:
    """Return a non-blocking warning key for an unusual IC, or ``None``."""
    if ic in (None, ""):
        return None
    text = _IC_STRIP.sub("", str(ic).strip())
    if not text:
        return None
    if not text.isdigit():
        return "non_numeric_ic"
    if len(text) != 12:
        return "ic_length"
    month = config.to_int(text[2:4], 0)
    day = config.to_int(text[4:6], 0)
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return "ic_date_part"
    return None


def validate_email(email: Any) -> bool:
    """Validate an optional email address."""
    if email in (None, ""):
        return True
    return bool(_EMAIL_RE.match(str(email).strip()))


def _serialise_member(row: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a ``members`` row into a JSON-ready dict."""
    member = dict(row)
    member["ic_warning"] = ic_warning(member.get("ic_number"))
    return member


class MemberService:
    """CRUD, search, the 360° profile and reading lists."""

    def __init__(self, db: Any, catalog: Any = None) -> None:
        self.db = db
        self._catalog = catalog

    @property
    def catalog(self) -> Any:
        """Lazily resolve the catalogue service (avoids an import cycle)."""
        if self._catalog is None:
            from .catalog import CatalogService

            self._catalog = CatalogService(self.db)
        return self._catalog

    # -- codes ------------------------------------------------------------ #
    def generate_member_code(self, member_type: str = "student") -> str:
        """Return an unused ``S00001``-style member code."""
        prefix = _CODE_PREFIX.get((member_type or "student").lower(), "M")
        start = int(self.db.scalar("SELECT COUNT(*) FROM members", default=0)) + 1
        candidate_no = max(1, start)
        for _ in range(100000):
            candidate = "%s%05d" % (prefix, candidate_no)
            if not self.db.one("SELECT id FROM members WHERE member_code = ?", (candidate,)):
                return candidate
            candidate_no += 1
        raise errors.ImriError(errors.INTERNAL, errors.status_for(errors.INTERNAL))

    # -- validation ------------------------------------------------------- #
    def _clean_member_fields(self, fields: Mapping[str, Any], creating: bool) -> dict[str, Any]:
        """Validate and normalise incoming member fields."""
        out: dict[str, Any] = {}

        if "name" in fields or creating:
            name = config.clean_text(fields.get("name"))
            if not name:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"name": errors.VALIDATION_FAILED},
                )
            out["name"] = name

        if "member_type" in fields or creating:
            member_type = (config.clean_text(fields.get("member_type")) or "student").lower()
            if member_type not in config.MEMBER_TYPES:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"member_type": errors.VALIDATION_FAILED},
                    details={"allowed": list(config.MEMBER_TYPES)},
                )
            out["member_type"] = member_type

        if "status" in fields:
            status = (config.clean_text(fields.get("status")) or "active").lower()
            if status not in config.MEMBER_STATUSES:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"status": errors.VALIDATION_FAILED},
                    details={"allowed": list(config.MEMBER_STATUSES)},
                )
            out["status"] = status

        if "ic_number" in fields:
            ic = config.clean_text(fields.get("ic_number"))
            if ic and not validate_ic(ic):
                raise errors.ImriError(
                    errors.INVALID_IC,
                    errors.status_for(errors.INVALID_IC),
                    fields={"ic_number": errors.INVALID_IC},
                )
            out["ic_number"] = ic

        if "email" in fields:
            email = config.clean_text(fields.get("email"))
            if email and not validate_email(email):
                raise errors.ImriError(
                    errors.INVALID_EMAIL,
                    errors.status_for(errors.INVALID_EMAIL),
                    fields={"email": errors.INVALID_EMAIL},
                )
            out["email"] = email

        for column in ("form", "class_name", "address", "phone", "photo_path", "note"):
            if column in fields:
                out[column] = config.clean_text(fields.get(column))

        if "membership_date" in fields or creating:
            membership_date = config.clean_text(fields.get("membership_date"))
            if membership_date:
                if config.parse_date(membership_date) is None:
                    raise errors.ImriError(
                        errors.VALIDATION_FAILED,
                        errors.status_for(errors.VALIDATION_FAILED),
                        fields={"membership_date": errors.VALIDATION_FAILED},
                    )
                out["membership_date"] = membership_date[:10]
            else:
                out["membership_date"] = config.today_local()

        if "expiry_date" in fields:
            expiry = config.clean_text(fields.get("expiry_date"))
            if expiry and config.parse_date(expiry) is None:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"expiry_date": errors.VALIDATION_FAILED},
                )
            out["expiry_date"] = expiry[:10] if expiry else None

        return out

    # -- filters ---------------------------------------------------------- #
    def _where_from_filters(self, filters: Mapping[str, Any]) -> tuple[str, list[Any]]:
        """Build a parameterised WHERE clause for the member list."""
        clauses: list[str] = []
        params: list[Any] = []

        term = config.clean_text(filters.get("q"))
        if term:
            like = config.like_term(term)
            clauses.append(
                "(m.name LIKE ? ESCAPE '\\' OR m.member_code LIKE ? ESCAPE '\\' "
                " OR m.ic_number LIKE ? ESCAPE '\\' OR m.class_name LIKE ? ESCAPE '\\' "
                " OR m.email LIKE ? ESCAPE '\\' OR m.phone LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 6)

        for column, key in (
            ("m.status", "status"),
            ("m.member_type", "member_type"),
            ("m.form", "form"),
            ("m.class_name", "class_name"),
        ):
            value = config.clean_text(filters.get(key))
            if value and value.lower() != "all":
                clauses.append("%s = ?" % column)
                params.append(value)

        if config.to_bool(filters.get("has_overdue")):
            clauses.append(
                "EXISTS (SELECT 1 FROM loans l WHERE l.member_id = m.id "
                "AND l.status = 'on_loan' AND l.due_date < ?)"
            )
            params.append(config.today_local())

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    # -- list / read ------------------------------------------------------ #
    def list_members(
        self, filters: Mapping[str, Any] | None, page: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Return one page of members with loan and fine aggregates."""
        criteria = dict(filters or {})
        where, params = self._where_from_filters(criteria)

        sort_key = str(page.get("sort") or "name")
        sort_sql = MEMBER_SORTS.get(sort_key, MEMBER_SORTS["name"])
        order = "DESC" if str(page.get("order", "asc")).lower() == "desc" else "ASC"
        limit = int(page.get("page_size", 50))
        offset = config.page_offset(page, limit)

        total = int(self.db.scalar("SELECT COUNT(*) FROM members m" + where, params, default=0))
        rows = self.db.query(
            "SELECT m.* FROM members m" + where + " ORDER BY %s %s, m.id ASC LIMIT ? OFFSET ?"
            % (sort_sql, order),
            params + [limit, offset],
        )

        ids = [int(row["id"]) for row in rows]
        aggregates = self._aggregates(ids)
        items: list[dict[str, Any]] = []
        for row in rows:
            member = _serialise_member(row)
            stats = aggregates.get(int(row["id"]), {})
            member["active_loans"] = stats.get("active_loans", 0)
            member["outstanding"] = config.money(stats.get("outstanding", 0.0))
            member["overdue"] = stats.get("overdue", 0)
            items.append(member)

        pages = (total + limit - 1) // limit if limit else 0
        return {
            "items": items,
            "meta": {
                "page": int(page.get("page", 1)),
                "page_size": limit,
                "total": total,
                "pages": pages,
                "sort": sort_key if sort_key in MEMBER_SORTS else "name",
                "order": order.lower(),
            },
        }

    def _aggregates(self, member_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
        """Return loan/fine aggregates for a page of members in one query each."""
        ids = [int(i) for i in member_ids]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        loans = self.db.query(
            "SELECT member_id, "
            "       SUM(CASE WHEN status = 'on_loan' THEN 1 ELSE 0 END) AS active_loans, "
            "       SUM(CASE WHEN status = 'on_loan' AND due_date < ? THEN 1 ELSE 0 END) AS overdue "
            "FROM loans WHERE member_id IN (%s) GROUP BY member_id" % placeholders,
            [config.today_local()] + ids,
        )
        fines = self.db.query(
            "SELECT member_id, "
            "       COALESCE(SUM(CASE WHEN fine_amount - paid_amount - waived_amount > 0 "
            "                         THEN fine_amount - paid_amount - waived_amount "
            "                         ELSE 0 END), 0) AS outstanding "
            "FROM loans WHERE member_id IN (%s) GROUP BY member_id" % placeholders,
            ids,
        )
        out: dict[int, dict[str, Any]] = {}
        for row in loans:
            out.setdefault(int(row["member_id"]), {})
            out[int(row["member_id"])]["active_loans"] = int(row["active_loans"] or 0)
            out[int(row["member_id"])]["overdue"] = int(row["overdue"] or 0)
        for row in fines:
            out.setdefault(int(row["member_id"]), {})
            out[int(row["member_id"])]["outstanding"] = config.money(row["outstanding"] or 0)
        return out

    def get_member(self, member_id: int) -> dict[str, Any]:
        """Return one member, or raise ``not_found``."""
        row = self.db.one("SELECT * FROM members WHERE id = ?", (int(member_id),))
        if row is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        return _serialise_member(row)

    def find_by_code(self, member_code: Any) -> dict[str, Any] | None:
        """Return a member by business key, or ``None``."""
        text = config.clean_text(member_code)
        if not text:
            return None
        row = self.db.one("SELECT * FROM members WHERE member_code = ?", (text,))
        return _serialise_member(row) if row else None

    def resolve(self, member_id: Any = None, member_code: Any = None) -> dict[str, Any]:
        """Resolve a member from either reference, raising ``not_found``."""
        if member_id not in (None, ""):
            row = self.db.one("SELECT * FROM members WHERE id = ?", (config.to_int(member_id, -1),))
            if row:
                return _serialise_member(row)
        if member_code:
            found = self.find_by_code(member_code)
            if found:
                return found
        raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))

    # -- profile ---------------------------------------------------------- #
    def profile(self, member_id: int) -> dict[str, Any]:
        """Return the 360° member view the detail drawer renders (MEM-05)."""
        member = self.get_member(member_id)
        today = config.today_local()

        current = self.db.query(
            "SELECT l.*, b.title AS book_title, b.authors AS book_authors, "
            "       b.call_no, c.barcode AS copy_barcode, c.copy_no "
            "FROM loans l LEFT JOIN books b ON b.id = l.book_id "
            "LEFT JOIN book_copies c ON c.id = l.copy_id "
            "WHERE l.member_id = ? AND l.status = 'on_loan' ORDER BY l.due_date",
            (int(member_id),),
        )
        history = self.db.query(
            "SELECT l.*, b.title AS book_title FROM loans l "
            "LEFT JOIN books b ON b.id = l.book_id "
            "WHERE l.member_id = ? AND l.status <> 'on_loan' "
            "ORDER BY COALESCE(l.return_date, l.due_date) DESC, l.id DESC LIMIT 100",
            (int(member_id),),
        )
        fines = self.member_fines(int(member_id), status="all")["items"]
        reservations = self.db.query(
            "SELECT r.*, b.title AS book_title FROM reservations r "
            "LEFT JOIN books b ON b.id = r.book_id "
            "WHERE r.member_id = ? AND r.status IN ('open','ready') "
            "ORDER BY r.queue_position, r.id",
            (int(member_id),),
        )
        reading_list = self.reading_list(int(member_id))

        outstanding = config.money(sum(item["balance"] for item in fines))
        counts = {
            "active_loans": len(current),
            "history": len(history),
            "outstanding": outstanding,
            "reservations": len(reservations),
            "reading_list": len(reading_list),
            "overdue": sum(1 for loan in current if str(loan.get("due_date") or "") < today),
        }
        return {
            "member": member,
            "counts": counts,
            "loans": {"current": current, "history": history},
            "fines": fines,
            "reservations": reservations,
            "reading_list": reading_list,
        }

    def member_loans(
        self, member_id: int, scope: str = "current", page: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Return a member's current or historical loans, paginated."""
        self.get_member(member_id)
        paging = dict(page or {})
        limit = int(paging.get("page_size", 50))
        offset = config.page_offset(paging, limit)
        clause = "l.status = 'on_loan'" if scope != "history" else "l.status <> 'on_loan'"
        order = "l.due_date ASC" if scope != "history" else "l.return_date DESC, l.id DESC"
        total = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans l WHERE l.member_id = ? AND " + clause,
                (int(member_id),),
                default=0,
            )
        )
        items = self.db.query(
            "SELECT l.*, b.title AS book_title, b.authors AS book_authors "
            "FROM loans l LEFT JOIN books b ON b.id = l.book_id "
            "WHERE l.member_id = ? AND " + clause + " ORDER BY " + order + " LIMIT ? OFFSET ?",
            (int(member_id), limit, offset),
        )
        pages = (total + limit - 1) // limit if limit else 0
        return {
            "items": items,
            "meta": {
                "page": int(paging.get("page", 1)),
                "page_size": limit,
                "total": total,
                "pages": pages,
                "sort": "due_date" if scope != "history" else "return_date",
                "order": "asc" if scope != "history" else "desc",
            },
        }

    def member_fines(self, member_id: int, status: str = "outstanding") -> dict[str, Any]:
        """Return a member's fine ledger, newest first."""
        self.get_member(member_id)
        clause = ""
        if status != "all":
            clause = " AND (l.fine_amount - l.paid_amount - l.waived_amount) > 0.004"
        rows = self.db.query(
            "SELECT l.id AS loan_id, l.fine_amount, l.paid_amount, l.waived_amount, "
            "       l.due_date, l.return_date, l.status AS loan_status, "
            "       b.title AS book_title, b.accession_no "
            "FROM loans l LEFT JOIN books b ON b.id = l.book_id "
            "WHERE l.member_id = ? AND l.fine_amount > 0" + clause + " ORDER BY l.id DESC",
            (int(member_id),),
        )
        payments = self.db.query(
            "SELECT loan_id, id, amount, paid_at, receipt_no, method, actor, note "
            "FROM fine_payments WHERE member_id = ? ORDER BY id DESC",
            (int(member_id),),
        )
        by_loan: dict[int, list[dict[str, Any]]] = {}
        for payment in payments:
            by_loan.setdefault(int(payment["loan_id"]), []).append(payment)

        items: list[dict[str, Any]] = []
        total_outstanding = 0.0
        for row in rows:
            fine = config.money(row["fine_amount"] or 0)
            paid = config.money(row["paid_amount"] or 0)
            waived = config.money(row["waived_amount"] or 0)
            balance = config.money(max(0.0, fine - paid - waived))
            total_outstanding += balance
            items.append(
                {
                    "loan_id": int(row["loan_id"]),
                    "book_title": row["book_title"],
                    "accession_no": row["accession_no"],
                    "due_date": row["due_date"],
                    "return_date": row["return_date"],
                    "loan_status": row["loan_status"],
                    "fine": fine,
                    "paid": paid,
                    "waived": waived,
                    "balance": balance,
                    "payments": by_loan.get(int(row["loan_id"]), []),
                }
            )
        return {"items": items, "total_outstanding": config.money(total_outstanding)}

    # -- create / update / delete ----------------------------------------- #
    def create_member(
        self,
        fields: Mapping[str, Any],
        actor: str = "system",
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Create a member. Raises ``duplicate_member_code`` / ``invalid_ic``."""
        clean = self._clean_member_fields(fields, creating=True)

        member_code = config.clean_text(fields.get("member_code"))
        if member_code:
            if self.db.one("SELECT id FROM members WHERE member_code = ?", (member_code,)):
                raise errors.ImriError(
                    errors.DUPLICATE_MEMBER_CODE,
                    errors.status_for(errors.DUPLICATE_MEMBER_CODE),
                    fields={"member_code": errors.DUPLICATE_MEMBER_CODE},
                )
        else:
            member_code = self.generate_member_code(clean.get("member_type", "student"))

        now = config.now_utc()
        values: dict[str, Any] = {
            "member_code": member_code,
            "name": clean["name"],
            "member_type": clean.get("member_type") or "student",
            "ic_number": clean.get("ic_number"),
            "form": clean.get("form"),
            "class_name": clean.get("class_name"),
            "address": clean.get("address"),
            "phone": clean.get("phone"),
            "email": clean.get("email"),
            "photo_path": clean.get("photo_path"),
            "membership_date": clean.get("membership_date") or config.today_local(),
            "expiry_date": clean.get("expiry_date"),
            "status": clean.get("status") or "active",
            "note": clean.get("note"),
            "created_at": now,
            "updated_at": now,
        }

        columns = ",".join(_MEMBER_COLUMNS + ("created_at", "updated_at"))
        placeholders = ",".join("?" for _ in (_MEMBER_COLUMNS + ("created_at", "updated_at")))
        params = [values.get(column) for column in (_MEMBER_COLUMNS + ("created_at", "updated_at"))]

        with self.db.transaction() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO members(%s) VALUES(%s)" % (columns, placeholders), params
                )
            except IntegrityError as exc:
                raise errors.ImriError(
                    errors.DUPLICATE_MEMBER_CODE,
                    errors.status_for(errors.DUPLICATE_MEMBER_CODE),
                    fields={"member_code": errors.DUPLICATE_MEMBER_CODE},
                ) from exc
            member_id = int(cursor.lastrowid)
            self.db.audit(
                actor,
                "member.create",
                target=member_code,
                detail={"name": values["name"], "type": values["member_type"]},
                ip=ip,
                target_type="member",
            )

        fresh = self.db.one("SELECT * FROM members WHERE id = ?", (member_id,)) or values
        return {"member": _serialise_member(fresh)}

    def update_member(
        self,
        member_id: int,
        fields: Mapping[str, Any],
        actor: str = "system",
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Patch a member. ``member_code`` is immutable."""
        current = self.db.one("SELECT * FROM members WHERE id = ?", (int(member_id),))
        if current is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))

        if "member_code" in fields:
            proposed = config.clean_text(fields.get("member_code"))
            if proposed and proposed != current["member_code"]:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"member_code": errors.VALIDATION_FAILED},
                    details={"reason": "immutable"},
                )

        clean = self._clean_member_fields(fields, creating=False)

        sets: list[str] = []
        params: list[Any] = []
        changes: dict[str, Any] = {}
        for column in _MEMBER_COLUMNS:
            if column in _IMMUTABLE_MEMBER_COLUMNS or column not in clean:
                continue
            sets.append("%s = ?" % column)
            params.append(clean[column])
            changes[column] = clean[column]

        if not sets:
            return {"member": _serialise_member(current)}

        sets.append("updated_at = ?")
        params.append(config.now_utc())
        params.append(int(member_id))
        with self.db.transaction() as conn:
            conn.execute("UPDATE members SET %s WHERE id = ?" % ", ".join(sets), params)
            self.db.audit(
                actor,
                "member.update",
                target=current["member_code"],
                detail=changes,
                ip=ip,
                target_type="member",
            )

        fresh = self.db.one("SELECT * FROM members WHERE id = ?", (int(member_id),)) or current
        return {"member": _serialise_member(fresh)}

    def delete_member(self, member_id: int, actor: str = "system", ip: str | None = None) -> None:
        """Delete a member, refusing while any loan row references them."""
        member = self.db.one("SELECT * FROM members WHERE id = ?", (int(member_id),))
        if member is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))

        active = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans WHERE member_id = ? AND status = 'on_loan'",
                (int(member_id),),
                default=0,
            )
        )
        total = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans WHERE member_id = ?", (int(member_id),), default=0
            )
        )
        if active or total:
            raise errors.ImriError(
                errors.MEMBER_HAS_LOANS,
                errors.status_for(errors.MEMBER_HAS_LOANS),
                details={"active_loans": active, "historical_loans": total},
            )

        with self.db.transaction() as conn:
            conn.execute("DELETE FROM members WHERE id = ?", (int(member_id),))
            self.db.audit(
                actor,
                "member.delete",
                target=member["member_code"],
                detail={"name": member["name"]},
                ip=ip,
                target_type="member",
            )

    # -- reading lists ---------------------------------------------------- #
    def reading_list(self, member_id: int) -> list[dict[str, Any]]:
        """Return a member's reading list, ordered by ``sort_order``."""
        rows = self.db.query(
            "SELECT rl.id, rl.note, rl.sort_order, rl.created_at, "
            "       b.id AS book_id, b.title, b.authors, b.accession_no, "
            "       b.call_no, b.material_type, b.status AS book_status "
            "FROM reading_lists rl JOIN books b ON b.id = rl.book_id "
            "WHERE rl.member_id = ? ORDER BY rl.sort_order, rl.id",
            (int(member_id),),
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["book"] = {
                "id": item.pop("book_id"),
                "title": item.pop("title"),
                "authors": item.pop("authors"),
                "accession_no": item.pop("accession_no"),
                "call_no": item.pop("call_no"),
                "material_type": item.pop("material_type"),
                "status": item.pop("book_status"),
            }
            items.append(item)
        return items

    def add_to_reading_list(
        self, member_id: int, book_id: int, note: Any = None
    ) -> dict[str, Any]:
        """Append a title to a member's reading list."""
        self.get_member(member_id)
        book = self.db.one("SELECT * FROM books WHERE id = ?", (int(book_id),))
        if book is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        existing = self.db.one(
            "SELECT id FROM reading_lists WHERE member_id = ? AND book_id = ?",
            (int(member_id), int(book_id)),
        )
        if existing:
            return self.get_reading_item(int(member_id), int(existing["id"]))
        next_order = int(
            self.db.scalar(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM reading_lists WHERE member_id = ?",
                (int(member_id),),
                default=1,
            )
        )
        item_id = self.db.execute(
            "INSERT INTO reading_lists(member_id, book_id, note, sort_order, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (int(member_id), int(book_id), config.clean_text(note), next_order, config.now_utc()),
        )
        return self.get_reading_item(int(member_id), int(item_id))

    def get_reading_item(self, member_id: int, item_id: int) -> dict[str, Any]:
        """Return one reading-list row with its embedded book summary."""
        row = self.db.one(
            "SELECT rl.id, rl.note, rl.sort_order, rl.created_at, "
            "       b.id AS book_id, b.title, b.authors, b.accession_no, "
            "       b.call_no, b.material_type, b.status AS book_status "
            "FROM reading_lists rl JOIN books b ON b.id = rl.book_id "
            "WHERE rl.member_id = ? AND rl.id = ?",
            (int(member_id), int(item_id)),
        )
        if row is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        item = dict(row)
        item["book"] = {
            "id": item.pop("book_id"),
            "title": item.pop("title"),
            "authors": item.pop("authors"),
            "accession_no": item.pop("accession_no"),
            "call_no": item.pop("call_no"),
            "material_type": item.pop("material_type"),
            "status": item.pop("book_status"),
        }
        return item

    def update_reading_item(
        self, member_id: int, item_id: int, fields: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Patch a reading-list row's note or sort order."""
        self.get_reading_item(member_id, item_id)
        sets: list[str] = []
        params: list[Any] = []
        if "note" in fields:
            sets.append("note = ?")
            params.append(config.clean_text(fields.get("note")))
        if "sort_order" in fields:
            sets.append("sort_order = ?")
            params.append(config.to_int(fields.get("sort_order"), 0))
        if sets:
            params.extend([int(item_id), int(member_id)])
            self.db.execute(
                "UPDATE reading_lists SET %s WHERE id = ? AND member_id = ?" % ", ".join(sets),
                params,
            )
        return self.get_reading_item(member_id, item_id)

    def remove_from_reading_list(self, member_id: int, item_id: int) -> None:
        """Delete one reading-list row."""
        self.get_reading_item(member_id, item_id)
        self.db.execute(
            "DELETE FROM reading_lists WHERE id = ? AND member_id = ?",
            (int(item_id), int(member_id)),
        )

    def reserve_from_reading_list(
        self, member_id: int, item_id: int, actor: str = "system"
    ) -> dict[str, Any]:
        """Convert a reading-list entry into a reservation (RDL-02 backend)."""
        item = self.get_reading_item(member_id, item_id)
        from .circulation import CirculationService

        service = CirculationService(self.db)
        result = service.reserve(int(item["book"]["id"]), int(member_id), actor=actor)
        return {
            "reservation": result["reservation"],
            "queue_position": result["queue_position"],
            "item": item,
        }
