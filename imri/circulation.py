"""Circulation: issue, return, renew, fines, payments, reservations.

This module holds the two rules that are easiest to get subtly wrong:

**Fine arithmetic** — ``days_late`` is computed from *local* dates
(:func:`imri.config.today_local`), never UTC. A 00:30 return in Kuala Lumpur
must not be counted as "yesterday". Balance is
``max(0, fine - paid - waived)`` and an overpayment is rejected, never
silently clamped.

**The reservation queue** — issue refuses a copy that is ``reserved`` for a
different member (``copy_on_hold``); return promotes the queue head to
``ready``, marks the copy ``reserved`` and sets ``expires_at``; expiry promotes
the next member in line. Positions are recomputed with
``ROW_NUMBER() OVER (PARTITION BY book_id ORDER BY reserved_date, id)`` so they
never develop gaps or duplicates.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from . import config, errors
from .catalog import CatalogService, refresh_book_rollup
from .config import get_logger
from .db import Connection, IntegrityError

_log = get_logger("circulation")

LOAN_SORTS: dict[str, str] = {
    "due_date": "l.due_date",
    "issue_date": "l.issue_date",
    "return_date": "l.return_date",
    "member_code": "l.member_code",
    "accession_no": "l.accession_no",
    "id": "l.id",
}

RESERVATION_SORTS: dict[str, str] = {
    "queue_position": "r.queue_position",
    "reserved_date": "r.reserved_date",
    "id": "r.id",
}


class CirculationService:
    """Every rule about a copy leaving and coming back."""

    def __init__(self, db: Any, catalog: CatalogService | None = None) -> None:
        self.db = db
        self._catalog = catalog

    @property
    def catalog(self) -> CatalogService:
        """Lazily resolve the catalogue service."""
        if self._catalog is None:
            self._catalog = CatalogService(self.db)
        return self._catalog

    # -- policy settings (read at call time so changes apply immediately) -- #
    def _policy(self) -> dict[str, Any]:
        """Return the live loan policy from ``settings``."""
        return {
            "fine_per_day": config.money(self.db.get_setting_float("fine_per_day", 0.20)),
            "loan_days": max(1, self.db.get_setting_int("loan_days", 14)),
            "max_loans": max(1, self.db.get_setting_int("max_loans", 3)),
            "max_renewals": max(0, self.db.get_setting_int("max_renewals", 1)),
            "hold_days": max(1, self.db.get_setting_int("hold_days", 3)),
            "currency": self.db.get_setting("currency", "RM") or "RM",
        }

    @property
    def fine_per_day(self) -> float:
        """The configured daily fine."""
        return self._policy()["fine_per_day"]

    @property
    def loan_days(self) -> int:
        """The configured loan period in days."""
        return self._policy()["loan_days"]

    @property
    def max_loans(self) -> int:
        """The configured per-member loan limit."""
        return self._policy()["max_loans"]

    @property
    def max_renewals(self) -> int:
        """The configured renewal limit."""
        return self._policy()["max_renewals"]

    @property
    def hold_days(self) -> int:
        """How long a promoted hold sits on the shelf."""
        return self._policy()["hold_days"]

    # -- derived values --------------------------------------------------- #
    def overdue_days(self, loan: Mapping[str, Any], as_of: Any = None) -> int:
        """Return whole days past due, from *local* dates (never UTC)."""
        if not loan:
            return 0
        due = loan.get("due_date")
        reference = as_of or config.today_local()
        if not due:
            return 0
        return max(0, config.days_between(due, reference))

    def accrued_fine(
        self, loan: Mapping[str, Any], as_of: Any = None, fine_per_day: float | None = None
    ) -> float:
        """Return the fine that has accrued so far on an open loan.

        ``fine_per_day`` lets a caller decorating a whole page resolve the
        policy once instead of re-reading ``settings`` for every row.
        """
        if not loan or str(loan.get("status")) != "on_loan":
            return config.money(loan.get("fine_amount") or 0) if loan else 0.0
        rate = self.fine_per_day if fine_per_day is None else fine_per_day
        return config.money(self.overdue_days(loan, as_of) * rate)

    def _decorate_loan(
        self, loan: dict[str, Any], as_of: Any, fine_per_day: float
    ) -> None:
        """Fill in the three derived columns every loan list renders.

        ``fine_per_day`` is passed in because :meth:`_policy` issues six
        ``SELECT``s against ``settings``: calling the properties from the row
        loop made a 50-row overdue page run 300 settings queries.
        """
        loan["overdue_days"] = self.overdue_days(loan, as_of)
        loan["accrued_fine"] = self.accrued_fine(loan, as_of, fine_per_day)
        loan["balance"] = self.balance(loan)

    def balance(self, loan: Mapping[str, Any]) -> float:
        """Return ``max(0, fine - paid - waived)``."""
        if not loan:
            return 0.0
        fine = config.money(loan.get("fine_amount") or 0)
        paid = config.money(loan.get("paid_amount") or 0)
        waived = config.money(loan.get("waived_amount") or 0)
        return config.money(max(0.0, fine - paid - waived))

    # -- resolution helpers ----------------------------------------------- #
    def _resolve_member(self, ref: Any) -> dict[str, Any]:
        """Resolve a member from an id, a code, or a mapping carrying either."""
        member_id: Any = None
        member_code: Any = None
        if isinstance(ref, Mapping):
            member_id = ref.get("member_id")
            member_code = ref.get("member_code")
        elif isinstance(ref, int):
            member_id = ref
        elif isinstance(ref, str):
            member_code = ref
        if member_id not in (None, ""):
            row = self.db.one("SELECT * FROM members WHERE id = ?", (config.to_int(member_id, -1),))
            if row:
                return dict(row)
        if member_code:
            row = self.db.one(
                "SELECT * FROM members WHERE member_code = ?", (str(member_code).strip(),)
            )
            if row:
                return dict(row)
        raise errors.ImriError(
            errors.NOT_FOUND,
            errors.status_for(errors.NOT_FOUND),
            details={"what": "member"},
        )

    def _resolve_copy(self, ref: Any, require_available: bool = False) -> dict[str, Any]:
        """Resolve a copy from an id, barcode, or an accession number."""
        copy_id: Any = None
        barcode: Any = None
        accession: Any = None
        if isinstance(ref, Mapping):
            copy_id = ref.get("copy_id")
            barcode = ref.get("copy_barcode") or ref.get("barcode")
            accession = ref.get("accession_no")
        elif isinstance(ref, int):
            copy_id = ref
        elif isinstance(ref, str):
            barcode = ref

        row: dict[str, Any] | None = None
        if copy_id not in (None, ""):
            row = self.db.one("SELECT * FROM book_copies WHERE id = ?", (config.to_int(copy_id, -1),))
        if row is None and barcode:
            row = self.db.one(
                "SELECT * FROM book_copies WHERE barcode = ?", (str(barcode).strip(),)
            )
        if row is None and accession:
            book = self.db.one(
                "SELECT id FROM books WHERE accession_no = ?", (str(accession).strip(),)
            )
            if book:
                row = self.db.one(
                    "SELECT * FROM book_copies WHERE book_id = ? ORDER BY "
                    "CASE status WHEN 'available' THEN 0 ELSE 1 END, copy_no LIMIT 1",
                    (int(book["id"]),),
                )
        if row is None and isinstance(ref, str) and not copy_id:
            # A scanned accession number can arrive as a bare string too.
            book = self.db.one("SELECT id FROM books WHERE accession_no = ?", (ref.strip(),))
            if book:
                row = self.db.one(
                    "SELECT * FROM book_copies WHERE book_id = ? ORDER BY "
                    "CASE status WHEN 'available' THEN 0 ELSE 1 END, copy_no LIMIT 1",
                    (int(book["id"]),),
                )
        if row is None:
            raise errors.ImriError(
                errors.COPY_NOT_FOUND,
                errors.status_for(errors.COPY_NOT_FOUND),
            )
        if require_available and row["status"] != "available":
            raise errors.ImriError(
                errors.NO_COPIES_AVAILABLE,
                errors.status_for(errors.NO_COPIES_AVAILABLE),
                details={"status": row["status"], "barcode": row["barcode"]},
            )
        return dict(row)

    def _member_guard(self, member: Mapping[str, Any]) -> None:
        """Refuse a member who may not borrow (MEM-04)."""
        status = str(member.get("status") or "active").lower()
        if status == "suspended":
            raise errors.ImriError(
                errors.MEMBER_SUSPENDED, errors.status_for(errors.MEMBER_SUSPENDED)
            )
        if status == "expired":
            raise errors.ImriError(
                errors.MEMBER_EXPIRED, errors.status_for(errors.MEMBER_EXPIRED)
            )
        if status != "active":
            raise errors.ImriError(
                errors.MEMBER_INACTIVE, errors.status_for(errors.MEMBER_INACTIVE)
            )
        expiry = config.parse_date(member.get("expiry_date"))
        if expiry is not None and expiry < config.parse_date(config.today_local()):
            raise errors.ImriError(
                errors.MEMBER_EXPIRED,
                errors.status_for(errors.MEMBER_EXPIRED),
                details={"expiry_date": member.get("expiry_date")},
            )

    def _open_loan_count(self, member_id: int) -> int:
        """Return how many loans the member currently has out."""
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans WHERE member_id = ? AND status = 'on_loan'",
                (int(member_id),),
                default=0,
            )
        )

    def _hold_for_other(
        self, copy_id: int, member_id: int
    ) -> dict[str, Any] | None:
        """Return a ``ready`` reservation holding this copy for someone else."""
        return self.db.one(
            "SELECT r.id, r.member_id, r.expires_at, m.member_code, m.name AS member_name "
            "FROM reservations r JOIN members m ON m.id = r.member_id "
            "WHERE r.copy_id = ? AND r.status = 'ready' AND r.member_id <> ? "
            "ORDER BY r.queue_position LIMIT 1",
            (int(copy_id), int(member_id)),
        )

    # -- issue ------------------------------------------------------------ #
    def issue(
        self,
        copy_ref: Any,
        member_ref: Any,
        actor: str = "system",
        due_date: Any = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Lend one copy to one member.

        Guards run in the documented order: member exists and is active and not
        expired → copy exists and is available → open loans below the limit →
        copy not held for another member → no duplicate active loan. The loan
        row, the copy status, the rollup and the audit entry are one commit.
        """
        policy = self._policy()
        member = self._resolve_member(member_ref)
        self._member_guard(member)
        copy = self._resolve_copy(copy_ref)

        if copy["status"] == "reserved":
            holder = self.db.one(
                "SELECT member_id FROM reservations WHERE copy_id = ? AND status = 'ready' LIMIT 1",
                (int(copy["id"]),),
            )
            if holder and int(holder["member_id"]) != int(member["id"]):
                raise errors.ImriError(
                    errors.COPY_ON_HOLD, errors.status_for(errors.COPY_ON_HOLD)
                )
            if not holder:
                raise errors.ImriError(
                    errors.COPY_UNAVAILABLE, errors.status_for(errors.COPY_UNAVAILABLE)
                )
        elif copy["status"] != "available":
            raise errors.ImriError(
                errors.COPY_UNAVAILABLE,
                errors.status_for(errors.COPY_UNAVAILABLE),
                details={"status": copy["status"]},
            )

        held_for_other = self._hold_for_other(int(copy["id"]), int(member["id"]))
        if held_for_other:
            raise errors.ImriError(
                errors.COPY_ON_HOLD,
                errors.status_for(errors.COPY_ON_HOLD),
                details={
                    "holder_code": held_for_other["member_code"],
                    "expires_at": held_for_other["expires_at"],
                },
            )

        open_loans = self._open_loan_count(int(member["id"]))
        if open_loans >= policy["max_loans"]:
            raise errors.ImriError(
                errors.MAX_LOANS_REACHED,
                errors.status_for(errors.MAX_LOANS_REACHED),
                details={"limit": policy["max_loans"], "current": open_loans},
            )

        existing = self.db.one(
            "SELECT id FROM loans WHERE copy_id = ? AND status = 'on_loan'", (int(copy["id"]),)
        )
        if existing:
            raise errors.ImriError(
                errors.DUPLICATE_ACTIVE_LOAN,
                errors.status_for(errors.DUPLICATE_ACTIVE_LOAN),
                details={"loan_id": existing["id"]},
            )

        book = self.db.one("SELECT * FROM books WHERE id = ?", (int(copy["book_id"]),))
        if book is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))

        issue_date = config.today_local()
        resolved_due = config.clean_text(due_date)
        if resolved_due:
            if config.parse_date(resolved_due) is None:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"due_date": errors.VALIDATION_FAILED},
                )
            resolved_due = resolved_due[:10]
            if resolved_due < issue_date:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"due_date": errors.VALIDATION_FAILED},
                    details={"reason": "before_issue_date"},
                )
        else:
            resolved_due = config.date_add(issue_date, policy["loan_days"])

        now = config.now_utc()
        with self.db.transaction() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO loans(book_id, copy_id, accession_no, member_id, member_code, "
                    "issue_date, due_date, status, currency, issued_by, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'on_loan', ?, ?, ?, ?)",
                    (
                        int(copy["book_id"]),
                        int(copy["id"]),
                        book["accession_no"],
                        int(member["id"]),
                        member["member_code"],
                        issue_date,
                        resolved_due,
                        policy["currency"],
                        actor,
                        now,
                        now,
                    ),
                )
            except IntegrityError as exc:
                raise errors.ImriError(
                    errors.DUPLICATE_ACTIVE_LOAN,
                    errors.status_for(errors.DUPLICATE_ACTIVE_LOAN),
                ) from exc

            loan_id = int(cursor.lastrowid)
            conn.execute(
                "UPDATE book_copies SET status = 'on_loan', updated_at = ? WHERE id = ?",
                (now, int(copy["id"])),
            )
            # If the member was the holder of this copy, the hold is now spent.
            conn.execute(
                "UPDATE reservations SET status = 'fulfilled', updated_at = ? "
                "WHERE copy_id = ? AND status = 'ready' AND member_id = ?",
                (now, int(copy["id"]), int(member["id"])),
            )
            refresh_book_rollup(conn, int(copy["book_id"]))
            self.rebalance_queue(int(copy["book_id"]))
            self.db.audit(
                actor,
                "loan.issue",
                target=book["accession_no"],
                detail={
                    "loan_id": loan_id,
                    "member_code": member["member_code"],
                    "copy_barcode": copy["barcode"],
                    "due_date": resolved_due,
                },
                ip=ip,
                target_type="loan",
            )

        # Re-read everything the client will render. The rows fetched during
        # validation are pre-transaction snapshots: ``copy`` still says
        # "available" and ``book`` still says the old status, which would make
        # the issue screen show a stale badge for one round trip.
        loan = self.db.one("SELECT * FROM loans WHERE id = ?", (loan_id,)) or {}
        fresh_copy = self.db.one("SELECT * FROM book_copies WHERE id = ?", (int(copy["id"]),))
        fresh_book = self.db.one("SELECT * FROM books WHERE id = ?", (int(copy["book_id"]),))
        return {
            "loan": loan,
            "book": dict(fresh_book or book),
            "copy": dict(fresh_copy or copy),
            "member": dict(member),
            "due_date": resolved_due,
        }

    # -- return ----------------------------------------------------------- #
    def _find_open_loan(self, loan_id: Any = None, copy_ref: Any = None) -> dict[str, Any] | None:
        """Locate the open loan for a loan id or a copy reference."""
        if loan_id not in (None, ""):
            return self.db.one("SELECT * FROM loans WHERE id = ?", (config.to_int(loan_id, -1),))
        if copy_ref is None:
            return None
        copy = self._resolve_copy(copy_ref)
        return self.db.one(
            "SELECT * FROM loans WHERE copy_id = ? AND status = 'on_loan'", (int(copy["id"]),)
        )

    def return_copy(
        self,
        loan_id: Any = None,
        actor: str = "system",
        condition: Any = None,
        as_of: Any = None,
        copy_ref: Any = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Take a copy back, compute the fine and promote the queue."""
        policy = self._policy()
        reference = config.clean_text(as_of) or config.today_local()
        if config.parse_date(reference) is None:
            raise errors.ImriError(
                errors.VALIDATION_FAILED,
                errors.status_for(errors.VALIDATION_FAILED),
                fields={"as_of": errors.VALIDATION_FAILED},
            )
        reference = reference[:10]

        loan = self._find_open_loan(loan_id, copy_ref)
        if loan is None:
            if loan_id not in (None, ""):
                existing = self.db.one(
                    "SELECT * FROM loans WHERE id = ?", (config.to_int(loan_id, -1),)
                )
                if existing is None:
                    raise errors.ImriError(
                        errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND)
                    )
                raise errors.ImriError(
                    errors.ALREADY_RETURNED,
                    errors.status_for(errors.ALREADY_RETURNED),
                    details={"status": existing["status"]},
                )
            raise errors.ImriError(
                errors.ALREADY_RETURNED, errors.status_for(errors.ALREADY_RETURNED)
            )

        if str(loan["status"]) != "on_loan":
            raise errors.ImriError(
                errors.ALREADY_RETURNED,
                errors.status_for(errors.ALREADY_RETURNED),
                details={"status": loan["status"]},
            )

        days_late = max(0, config.days_between(loan["due_date"], reference))
        fine = config.money(days_late * policy["fine_per_day"])
        now = config.now_utc()

        condition_value = config.clean_text(condition)
        if condition_value and condition_value not in config.COPY_CONDITIONS:
            condition_value = None

        promoted: dict[str, Any] | None = None
        copy_status = "available"

        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE loans SET return_date = ?, status = 'returned', fine_amount = ?, "
                "returned_by = ?, updated_at = ? WHERE id = ?",
                (reference, fine, actor, now, int(loan["id"])),
            )

            copy_id = loan.get("copy_id")
            if copy_id is not None:
                next_hold = conn.execute(
                    "SELECT r.id, r.member_id, m.member_code, m.name "
                    "FROM reservations r JOIN members m ON m.id = r.member_id "
                    "WHERE r.book_id = ? AND r.status = 'open' "
                    "ORDER BY r.queue_position, r.reserved_date, r.id LIMIT 1",
                    (int(loan["book_id"]),),
                ).fetchone()
                if next_hold is not None:
                    expires_at = config.date_add(reference, policy["hold_days"])
                    conn.execute(
                        "UPDATE reservations SET status = 'ready', copy_id = ?, ready_at = ?, "
                        "expires_at = ?, updated_at = ? WHERE id = ?",
                        (int(copy_id), now, expires_at, now, int(next_hold["id"])),
                    )
                    copy_status = "reserved"
                    promoted = {
                        "reservation_id": int(next_hold["id"]),
                        "member_id": int(next_hold["member_id"]),
                        "member_code": next_hold["member_code"],
                        "member_name": next_hold["name"],
                        "expires_at": expires_at,
                    }
                conn.execute(
                    "UPDATE book_copies SET status = ?, condition = COALESCE(?, condition), "
                    "updated_at = ? WHERE id = ?",
                    (copy_status, condition_value, now, int(copy_id)),
                )
            refresh_book_rollup(conn, int(loan["book_id"]))
            self.rebalance_queue(int(loan["book_id"]))
            self.db.audit(
                actor,
                "loan.return",
                target=loan["accession_no"],
                detail={
                    "loan_id": int(loan["id"]),
                    "days_late": days_late,
                    "fine": fine,
                    "copy_status": copy_status,
                    "promoted": bool(promoted),
                },
                ip=ip,
                target_type="loan",
            )

        updated = self.db.one("SELECT * FROM loans WHERE id = ?", (int(loan["id"]),)) or loan
        return {
            "loan": updated,
            "fine": {
                "amount": fine,
                "days_late": days_late,
                "per_day": policy["fine_per_day"],
                "balance": self.balance(updated),
            },
            "queue": {
                "promoted": bool(promoted),
                "next_member": promoted,
            },
            "copy_status": copy_status,
        }

    # -- renew ------------------------------------------------------------ #
    def renew(
        self,
        loan_id: Any,
        actor: str = "system",
        extend_days: Any = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Extend a loan, refusing when overdue, at the limit, or in demand."""
        policy = self._policy()
        loan = self.db.one("SELECT * FROM loans WHERE id = ?", (config.to_int(loan_id, -1),))
        if loan is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        if str(loan["status"]) != "on_loan":
            raise errors.ImriError(
                errors.ALREADY_RETURNED,
                errors.status_for(errors.ALREADY_RETURNED),
                details={"status": loan["status"]},
            )
        if str(loan["due_date"]) < config.today_local():
            raise errors.ImriError(
                errors.LOAN_OVERDUE,
                errors.status_for(errors.LOAN_OVERDUE),
                details={"due_date": loan["due_date"]},
            )
        if int(loan["renewal_count"] or 0) >= policy["max_renewals"]:
            raise errors.ImriError(
                errors.RENEWAL_LIMIT_REACHED,
                errors.status_for(errors.RENEWAL_LIMIT_REACHED),
                details={"limit": policy["max_renewals"], "current": int(loan["renewal_count"] or 0)},
            )

        waiting = self.db.one(
            "SELECT r.id, m.member_code FROM reservations r "
            "JOIN members m ON m.id = r.member_id "
            "WHERE r.book_id = ? AND r.status IN ('open','ready') AND r.member_id <> ? "
            "ORDER BY r.queue_position LIMIT 1",
            (int(loan["book_id"]), int(loan["member_id"])),
        )
        if waiting:
            raise errors.ImriError(
                errors.RENEWAL_BLOCKED_RESERVATION,
                errors.status_for(errors.RENEWAL_BLOCKED_RESERVATION),
                details={"waiting_member": waiting["member_code"]},
            )

        days = config.to_int(extend_days, 0) or policy["loan_days"]
        if days < 1 or days > 365:
            raise errors.ImriError(
                errors.VALIDATION_FAILED,
                errors.status_for(errors.VALIDATION_FAILED),
                fields={"extend_days": errors.VALIDATION_FAILED},
            )
        new_due = config.date_add(loan["due_date"], days)
        now = config.now_utc()

        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE loans SET due_date = ?, renewal_count = renewal_count + 1, "
                "updated_at = ? WHERE id = ?",
                (new_due, now, int(loan["id"])),
            )
            self.db.audit(
                actor,
                "loan.renew",
                target=loan["accession_no"],
                detail={
                    "loan_id": int(loan["id"]),
                    "from": loan["due_date"],
                    "to": new_due,
                    "renewal_count": int(loan["renewal_count"] or 0) + 1,
                },
                ip=ip,
                target_type="loan",
            )

        updated = self.db.one("SELECT * FROM loans WHERE id = ?", (int(loan["id"]),)) or loan
        return {
            "loan": updated,
            "new_due_date": new_due,
            "renewal_count": int(updated["renewal_count"] or 0),
        }

    # -- lost ------------------------------------------------------------- #
    def mark_lost(
        self,
        loan_id: Any,
        charge: Any = 0,
        actor: str = "system",
        note: Any = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Declare a loan lost: charge the borrower and write off the copy."""
        loan = self.db.one("SELECT * FROM loans WHERE id = ?", (config.to_int(loan_id, -1),))
        if loan is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        if str(loan["status"]) != "on_loan":
            raise errors.ImriError(
                errors.ALREADY_RETURNED,
                errors.status_for(errors.ALREADY_RETURNED),
                details={"status": loan["status"]},
            )
        amount = config.money(charge)
        if amount < 0:
            raise errors.ImriError(
                errors.INVALID_AMOUNT, errors.status_for(errors.INVALID_AMOUNT)
            )
        now = config.now_utc()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE loans SET status = 'lost', fine_amount = ?, note = COALESCE(?, note), "
                "updated_at = ? WHERE id = ?",
                (amount, config.clean_text(note), now, int(loan["id"])),
            )
            if loan.get("copy_id") is not None:
                conn.execute(
                    "UPDATE book_copies SET status = 'lost', updated_at = ? WHERE id = ?",
                    (now, int(loan["copy_id"])),
                )
            refresh_book_rollup(conn, int(loan["book_id"]))
            self.db.audit(
                actor,
                "loan.lost",
                target=loan["accession_no"],
                detail={"loan_id": int(loan["id"]), "charge": amount},
                ip=ip,
                target_type="loan",
            )
        updated = self.db.one("SELECT * FROM loans WHERE id = ?", (int(loan["id"]),)) or loan
        copy = (
            self.db.one("SELECT * FROM book_copies WHERE id = ?", (int(loan["copy_id"]),))
            if loan.get("copy_id") is not None
            else None
        )
        return {"loan": updated, "copy": copy, "balance": self.balance(updated)}

    # -- payments and waivers --------------------------------------------- #
    def _next_receipt_no(self, day: str | None = None) -> str:
        """Return the next ``IMRI-R-YYYYMMDD-NNNN`` receipt number."""
        stamp = (day or config.today_local()).replace("-", "")
        prefix = "IMRI-R-%s-" % stamp
        existing = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM fine_payments WHERE receipt_no LIKE ?",
                (prefix + "%",),
                default=0,
            )
        )
        candidate = existing + 1
        for _ in range(10000):
            value = "%s%04d" % (prefix, candidate)
            if not self.db.one("SELECT id FROM fine_payments WHERE receipt_no = ?", (value,)):
                return value
            candidate += 1
        raise errors.ImriError(
            errors.RECEIPT_CONFLICT, errors.status_for(errors.RECEIPT_CONFLICT)
        )

    def record_payment(
        self,
        loan_id: Any,
        amount: Any,
        actor: str = "system",
        note: Any = None,
        method: str = "cash",
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Take a (possibly partial) payment against a fine."""
        loan = self.db.one("SELECT * FROM loans WHERE id = ?", (config.to_int(loan_id, -1),))
        if loan is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))

        value = config.money(amount)
        if value <= 0:
            raise errors.ImriError(
                errors.INVALID_AMOUNT,
                errors.status_for(errors.INVALID_AMOUNT),
                fields={"amount": errors.INVALID_AMOUNT},
            )
        outstanding = self.balance(loan)
        if outstanding <= 0.004:
            raise errors.ImriError(
                errors.INVALID_AMOUNT,
                errors.status_for(errors.INVALID_AMOUNT),
                details={"reason": "nothing_outstanding"},
            )
        if value > outstanding + 0.004:
            raise errors.ImriError(
                errors.OVERPAYMENT,
                errors.status_for(errors.OVERPAYMENT),
                fields={"amount": errors.OVERPAYMENT},
                details={"outstanding": outstanding, "offered": value},
            )

        payment_method = (method or "cash").strip().lower()
        if payment_method not in ("cash", "other"):
            payment_method = "cash"
        now = config.now_utc()

        with self.db.transaction() as conn:
            receipt = self._next_receipt_no()
            conn.execute(
                "INSERT INTO fine_payments(loan_id, member_id, amount, paid_at, receipt_no, "
                "method, actor, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(loan["id"]),
                    int(loan["member_id"]),
                    value,
                    now,
                    receipt,
                    payment_method,
                    actor,
                    config.clean_text(note),
                    now,
                ),
            )
            conn.execute(
                "UPDATE loans SET paid_amount = ROUND(paid_amount + ?, 2), updated_at = ? "
                "WHERE id = ?",
                (value, now, int(loan["id"])),
            )
            self.db.audit(
                actor,
                "fine.payment",
                target=loan["accession_no"],
                detail={"loan_id": int(loan["id"]), "amount": value, "receipt_no": receipt},
                ip=ip,
                target_type="loan",
            )

        updated = self.db.one("SELECT * FROM loans WHERE id = ?", (int(loan["id"]),)) or loan
        return {
            "payment": {
                "receipt_no": receipt,
                "amount": value,
                "method": payment_method,
                "paid_at": now,
            },
            "balance": self.balance(updated),
            "loan": updated,
        }

    def waive(
        self,
        loan_id: Any,
        amount: Any,
        reason: Any,
        actor: str = "system",
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Waive part or all of a fine. A reason is mandatory."""
        loan = self.db.one("SELECT * FROM loans WHERE id = ?", (config.to_int(loan_id, -1),))
        if loan is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))

        text = config.clean_text(reason)
        if not text:
            raise errors.ImriError(
                errors.REASON_REQUIRED,
                errors.status_for(errors.REASON_REQUIRED),
                fields={"reason": errors.REASON_REQUIRED},
            )
        value = config.money(amount)
        if value <= 0:
            raise errors.ImriError(
                errors.INVALID_AMOUNT,
                errors.status_for(errors.INVALID_AMOUNT),
                fields={"amount": errors.INVALID_AMOUNT},
            )
        outstanding = self.balance(loan)
        if outstanding <= 0.004:
            raise errors.ImriError(
                errors.NOTHING_TO_WAIVE, errors.status_for(errors.NOTHING_TO_WAIVE)
            )
        if value > outstanding + 0.004:
            raise errors.ImriError(
                errors.OVERPAYMENT,
                errors.status_for(errors.OVERPAYMENT),
                fields={"amount": errors.OVERPAYMENT},
                details={"outstanding": outstanding, "offered": value},
            )

        now = config.now_utc()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE loans SET waived_amount = ROUND(waived_amount + ?, 2), "
                "note = COALESCE(note, ?), updated_at = ? WHERE id = ?",
                (value, text, now, int(loan["id"])),
            )
            self.db.audit(
                actor,
                "fine.waive",
                target=loan["accession_no"],
                detail={"loan_id": int(loan["id"]), "amount": value, "reason": text},
                ip=ip,
                target_type="loan",
            )

        updated = self.db.one("SELECT * FROM loans WHERE id = ?", (int(loan["id"]),)) or loan
        return {
            "waived": value,
            "balance": self.balance(updated),
            "loan": updated,
        }

    # -- reads ------------------------------------------------------------ #
    def loan_detail(self, loan_id: Any) -> dict[str, Any]:
        """Return a loan with its book, member, payments and balance."""
        loan = self.db.one("SELECT * FROM loans WHERE id = ?", (config.to_int(loan_id, -1),))
        if loan is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        book = self.db.one("SELECT * FROM books WHERE id = ?", (int(loan["book_id"]),))
        member = self.db.one("SELECT * FROM members WHERE id = ?", (int(loan["member_id"]),))
        return {
            "loan": loan,
            "book": book,
            "member": member,
            "payments": self.payments(int(loan["id"])),
            "balance": self.balance(loan),
            "overdue_days": self.overdue_days(loan),
            "accrued_fine": self.accrued_fine(loan),
        }

    def payments(self, loan_id: Any) -> list[dict[str, Any]]:
        """Return every payment recorded against a loan."""
        return self.db.query(
            "SELECT id, loan_id, member_id, amount, paid_at, receipt_no, method, actor, note "
            "FROM fine_payments WHERE loan_id = ? ORDER BY id",
            (config.to_int(loan_id, -1),),
        )

    def active_loans(
        self, filters: Mapping[str, Any] | None, page: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Return open loans with the derived overdue days and accrued fine."""
        criteria = dict(filters or {})
        clauses = ["l.status = 'on_loan'"]
        params: list[Any] = []

        if criteria.get("member_id") not in (None, ""):
            clauses.append("l.member_id = ?")
            params.append(config.to_int(criteria.get("member_id"), -1))
        if criteria.get("book_id") not in (None, ""):
            clauses.append("l.book_id = ?")
            params.append(config.to_int(criteria.get("book_id"), -1))
        term = config.clean_text(criteria.get("q"))
        if term:
            like = config.like_term(term)
            clauses.append(
                "(l.accession_no LIKE ? ESCAPE '\\' OR l.member_code LIKE ? ESCAPE '\\' "
                " OR b.title LIKE ? ESCAPE '\\' OR m.name LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 4)

        where = " WHERE " + " AND ".join(clauses)
        sort_key = str(page.get("sort") or "due_date")
        sort_sql = LOAN_SORTS.get(sort_key, LOAN_SORTS["due_date"])
        order = "DESC" if str(page.get("order", "asc")).lower() == "desc" else "ASC"
        limit = int(page.get("page_size", 50))
        offset = config.page_offset(page, limit)

        joins = " LEFT JOIN books b ON b.id = l.book_id LEFT JOIN members m ON m.id = l.member_id"
        # Only the free-text filter reads a joined column, so only then does the
        # count need the joins; without it they are two rowid lookups per row
        # that provably cannot change a COUNT(*).
        count_from = " FROM loans l" + (joins if term else "")
        total = int(self.db.scalar("SELECT COUNT(*)" + count_from + where, params, default=0))
        rows = self.db.query(
            "SELECT l.*, b.title AS book_title, b.authors AS book_authors, "
            "       m.name AS member_name, m.class_name FROM loans l"
            + joins
            + where
            + " ORDER BY %s %s, l.id ASC LIMIT ? OFFSET ?" % (sort_sql, order),
            params + [limit, offset],
        )
        fine_per_day = self.fine_per_day
        for row in rows:
            self._decorate_loan(row, None, fine_per_day)
        pages = (total + limit - 1) // limit if limit else 0
        return {
            "items": rows,
            "meta": {
                "page": int(page.get("page", 1)),
                "page_size": limit,
                "total": total,
                "pages": pages,
                "sort": sort_key if sort_key in LOAN_SORTS else "due_date",
                "order": order.lower(),
            },
        }

    def overdue_list(self, as_of: Any = None, page: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return every overdue loan with days late and accrued fine."""
        reference = config.clean_text(as_of) or config.today_local()
        reference = reference[:10]
        paging = dict(page or {})
        limit = int(paging.get("page_size", 50))
        offset = config.page_offset(paging, limit)

        # The count must not run through the joins. They cannot change a
        # COUNT(*), but SQLite executes them anyway: 7,500 overdue loans x two
        # rowid lookups into a 241 MB file measured 229 ms against 0.8 ms for
        # the same count on ``loans`` alone.
        total = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans WHERE status = 'on_loan' AND due_date < ?",
                (reference,),
                default=0,
            )
        )
        rows = self.db.query(
            "SELECT l.*, b.title AS book_title, b.authors AS book_authors, "
            "       m.name AS member_name, m.member_code, m.class_name, m.phone, m.email "
            "FROM loans l LEFT JOIN books b ON b.id = l.book_id "
            "LEFT JOIN members m ON m.id = l.member_id "
            "WHERE l.status = 'on_loan' AND l.due_date < ? "
            "ORDER BY l.due_date ASC, l.id ASC LIMIT ? OFFSET ?",
            (reference, limit, offset),
        )
        fine_per_day = self.fine_per_day
        accrued = 0.0
        for row in rows:
            self._decorate_loan(row, reference, fine_per_day)
            accrued += row["accrued_fine"]

        pages = (total + limit - 1) // limit if limit else 0
        return {
            "items": rows,
            "totals": {"count": total, "fine_accrued": config.money(accrued)},
            "meta": {
                "page": int(paging.get("page", 1)),
                "page_size": limit,
                "total": total,
                "pages": pages,
                "sort": "due_date",
                "order": "asc",
            },
        }

    def fines(
        self, filters: Mapping[str, Any] | None, page: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Return the fine ledger across members."""
        criteria = dict(filters or {})
        clauses = ["l.fine_amount > 0"]
        params: list[Any] = []

        if criteria.get("member_id") not in (None, ""):
            clauses.append("l.member_id = ?")
            params.append(config.to_int(criteria.get("member_id"), -1))
        status = (config.clean_text(criteria.get("status")) or "").lower()
        if status in ("outstanding", "open"):
            clauses.append("(l.fine_amount - l.paid_amount - l.waived_amount) > 0.004")
        elif status == "paid":
            clauses.append("(l.fine_amount - l.paid_amount - l.waived_amount) <= 0.004")

        where = " WHERE " + " AND ".join(clauses)
        limit = int(page.get("page_size", 50))
        offset = config.page_offset(page, limit)

        base = (
            " FROM loans l LEFT JOIN books b ON b.id = l.book_id "
            "LEFT JOIN members m ON m.id = l.member_id"
        )
        # No clause here reads a joined column, so the count and the totals run
        # on ``loans`` alone; the joins only exist to label the page.
        total = int(self.db.scalar("SELECT COUNT(*) FROM loans l" + where, params, default=0))
        rows = self.db.query(
            "SELECT l.id AS loan_id, l.member_id, l.member_code, l.accession_no, "
            "       l.fine_amount, l.paid_amount, l.waived_amount, l.status AS loan_status, "
            "       l.due_date, l.return_date, b.title AS book_title, m.name AS member_name "
            + base
            + where
            + " ORDER BY l.id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        for row in rows:
            row["fine"] = config.money(row["fine_amount"] or 0)
            row["paid"] = config.money(row["paid_amount"] or 0)
            row["waived"] = config.money(row["waived_amount"] or 0)
            row["balance"] = config.money(max(0.0, row["fine"] - row["paid"] - row["waived"]))

        totals_row = self.db.one(
            "SELECT COALESCE(SUM(l.fine_amount), 0) AS accrued, "
            "       COALESCE(SUM(l.paid_amount), 0) AS paid, "
            "       COALESCE(SUM(l.waived_amount), 0) AS waived, "
            "       COALESCE(SUM(CASE WHEN l.fine_amount - l.paid_amount - l.waived_amount > 0 "
            "                         THEN l.fine_amount - l.paid_amount - l.waived_amount "
            "                         ELSE 0 END), 0) AS outstanding FROM loans l" + where,
            params,
        ) or {}

        pages = (total + limit - 1) // limit if limit else 0
        return {
            "items": rows,
            "meta": {
                "page": int(page.get("page", 1)),
                "page_size": limit,
                "total": total,
                "pages": pages,
                "sort": "id",
                "order": "desc",
            },
            "totals": {
                "accrued": config.money(totals_row.get("accrued") or 0),
                "paid": config.money(totals_row.get("paid") or 0),
                "waived": config.money(totals_row.get("waived") or 0),
                "outstanding": config.money(totals_row.get("outstanding") or 0),
            },
        }

    # -- reservations ----------------------------------------------------- #
    def rebalance_queue(self, book_id: int) -> None:
        """Recompute queue positions for one title with no gaps or duplicates.

        Uses ``ROW_NUMBER() OVER (PARTITION BY book_id ORDER BY reserved_date, id)``
        over ``status IN ('open','ready')`` (ADR-15). Call inside a transaction
        that changed a reservation.
        """
        self.db.execute(
            "UPDATE reservations SET queue_position = ("
            "  SELECT rn FROM ("
            "    SELECT id, ROW_NUMBER() OVER ("
            "      PARTITION BY book_id ORDER BY reserved_date, id) AS rn "
            "    FROM reservations WHERE book_id = ? AND status IN ('open','ready')"
            "  ) ranked WHERE ranked.id = reservations.id"
            ") WHERE book_id = ? AND status IN ('open','ready')",
            (int(book_id), int(book_id)),
        )

    def reserve(
        self,
        book_id: Any,
        member_id: Any,
        actor: str = "system",
        note: Any = None,
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Place a FIFO reservation on a title with no available copy."""
        book = self.db.one("SELECT * FROM books WHERE id = ?", (config.to_int(book_id, -1),))
        if book is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        member = self._resolve_member(member_id)
        self._member_guard(member)

        if self.catalog.available_copies(int(book["id"])) > 0:
            raise errors.ImriError(
                errors.TITLE_AVAILABLE, errors.status_for(errors.TITLE_AVAILABLE)
            )

        existing = self.db.one(
            "SELECT id FROM reservations WHERE book_id = ? AND member_id = ? "
            "AND status IN ('open','ready')",
            (int(book["id"]), int(member["id"])),
        )
        if existing:
            raise errors.ImriError(
                errors.RESERVATION_EXISTS,
                errors.status_for(errors.RESERVATION_EXISTS),
                details={"reservation_id": existing["id"]},
            )

        now = config.now_utc()
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO reservations(book_id, member_id, accession_no, member_code, "
                "reserved_date, queue_position, status, note, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 'open', ?, ?, ?)",
                (
                    int(book["id"]),
                    int(member["id"]),
                    book["accession_no"],
                    member["member_code"],
                    config.today_local(),
                    config.clean_text(note),
                    now,
                    now,
                ),
            )
            reservation_id = int(cursor.lastrowid)
            self.rebalance_queue(int(book["id"]))
            self.db.audit(
                actor,
                "reservation.create",
                target=book["accession_no"],
                detail={"reservation_id": reservation_id, "member_code": member["member_code"]},
                ip=ip,
                target_type="reservation",
            )

        reservation = self.db.one("SELECT * FROM reservations WHERE id = ?", (reservation_id,)) or {}
        return {
            "reservation": reservation,
            "queue_position": int(reservation.get("queue_position") or 0),
        }

    def _promote_next(self, conn: Connection, book_id: int, copy_id: int | None) -> dict[str, Any] | None:
        """Promote the head of the open queue to ``ready`` on ``copy_id``."""
        if copy_id is None:
            return None
        hold_days = self.db.get_setting_int("hold_days", 3)
        next_row = conn.execute(
            "SELECT r.id, r.member_id, m.member_code, m.name "
            "FROM reservations r JOIN members m ON m.id = r.member_id "
            "WHERE r.book_id = ? AND r.status = 'open' "
            "ORDER BY r.queue_position, r.reserved_date, r.id LIMIT 1",
            (int(book_id),),
        ).fetchone()
        if next_row is None:
            return None
        now = config.now_utc()
        expires_at = config.date_add(config.today_local(), hold_days)
        conn.execute(
            "UPDATE reservations SET status = 'ready', copy_id = ?, ready_at = ?, "
            "expires_at = ?, updated_at = ? WHERE id = ?",
            (int(copy_id), now, expires_at, now, int(next_row["id"])),
        )
        conn.execute(
            "UPDATE book_copies SET status = 'reserved', updated_at = ? WHERE id = ?",
            (now, int(copy_id)),
        )
        return {
            "reservation_id": int(next_row["id"]),
            "member_id": int(next_row["member_id"]),
            "member_code": next_row["member_code"],
            "member_name": next_row["name"],
            "expires_at": expires_at,
        }

    def cancel_reservation(
        self, reservation_id: Any, reason: Any = None, actor: str = "system", ip: str | None = None
    ) -> dict[str, Any]:
        """Cancel a live reservation and, if it held a copy, pass it on."""
        reservation = self.db.one(
            "SELECT * FROM reservations WHERE id = ?", (config.to_int(reservation_id, -1),)
        )
        if reservation is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        if str(reservation["status"]) not in ("open", "ready"):
            raise errors.ImriError(
                errors.RESERVATION_NOT_OPEN,
                errors.status_for(errors.RESERVATION_NOT_OPEN),
                details={"status": reservation["status"]},
            )

        now = config.now_utc()
        held_copy = reservation.get("copy_id") if reservation["status"] == "ready" else None
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE reservations SET status = 'cancelled', note = COALESCE(?, note), "
                "updated_at = ? WHERE id = ?",
                (config.clean_text(reason), now, int(reservation["id"])),
            )
            if held_copy is not None:
                conn.execute(
                    "UPDATE book_copies SET status = 'available', updated_at = ? WHERE id = ?",
                    (now, int(held_copy)),
                )
                self._promote_next(conn, int(reservation["book_id"]), int(held_copy))
            refresh_book_rollup(conn, int(reservation["book_id"]))
            self.rebalance_queue(int(reservation["book_id"]))
            self.db.audit(
                actor,
                "reservation.cancel",
                target=reservation["accession_no"],
                detail={"reservation_id": int(reservation["id"]), "reason": config.clean_text(reason)},
                ip=ip,
                target_type="reservation",
            )

        updated = (
            self.db.one("SELECT * FROM reservations WHERE id = ?", (int(reservation["id"]),))
            or reservation
        )
        return {"reservation": updated}

    def fulfil_reservation(
        self,
        reservation_id: Any,
        copy_ref: Any = None,
        actor: str = "system",
        ip: str | None = None,
    ) -> dict[str, Any]:
        """Turn a live reservation into a loan."""
        reservation = self.db.one(
            "SELECT * FROM reservations WHERE id = ?", (config.to_int(reservation_id, -1),)
        )
        if reservation is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        if str(reservation["status"]) not in ("open", "ready"):
            raise errors.ImriError(
                errors.RESERVATION_NOT_OPEN,
                errors.status_for(errors.RESERVATION_NOT_OPEN),
                details={"status": reservation["status"]},
            )

        if copy_ref not in (None, ""):
            copy = self._resolve_copy(copy_ref)
        elif reservation.get("copy_id") is not None:
            copy = self._resolve_copy({"copy_id": reservation["copy_id"]})
        else:
            copy = self.db.one(
                "SELECT * FROM book_copies WHERE book_id = ? AND status = 'available' "
                "ORDER BY copy_no LIMIT 1",
                (int(reservation["book_id"]),),
            )
            if copy is None:
                raise errors.ImriError(
                    errors.NO_COPIES_AVAILABLE,
                    errors.status_for(errors.NO_COPIES_AVAILABLE),
                )

        result = self.issue(
            {"copy_id": int(copy["id"])},
            {"member_id": int(reservation["member_id"])},
            actor=actor,
            ip=ip,
        )

        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE reservations SET status = 'fulfilled', copy_id = ?, updated_at = ? "
                "WHERE id = ?",
                (int(copy["id"]), config.now_utc(), int(reservation["id"])),
            )
            self.rebalance_queue(int(reservation["book_id"]))
            self.db.audit(
                actor,
                "reservation.fulfil",
                target=reservation["accession_no"],
                detail={"reservation_id": int(reservation["id"]), "loan_id": result["loan"]["id"]},
                ip=ip,
                target_type="reservation",
            )

        updated = (
            self.db.one("SELECT * FROM reservations WHERE id = ?", (int(reservation["id"]),))
            or reservation
        )
        return {"reservation": updated, "loan": result["loan"]}

    def expire_holds(self, as_of: Any = None) -> dict[str, Any]:
        """Expire stale ``ready`` holds, returning copies to the shelf.

        Idempotent: running it twice on the same day expires nothing the second
        time. Runs at startup, on demand from the reservations screen, and from
        ``POST /api/reservations/expire``.
        """
        reference = config.clean_text(as_of) or config.today_local()
        reference = reference[:10]

        stale = self.db.query(
            "SELECT * FROM reservations WHERE status = 'ready' AND expires_at IS NOT NULL "
            "AND expires_at < ? ORDER BY book_id, queue_position",
            (reference,),
        )
        if not stale:
            return {"expired": 0, "promoted": 0}

        now = config.now_utc()
        expired = 0
        promoted = 0
        touched_books: set[int] = set()

        with self.db.transaction() as conn:
            for reservation in stale:
                conn.execute(
                    "UPDATE reservations SET status = 'expired', updated_at = ? WHERE id = ?",
                    (now, int(reservation["id"])),
                )
                expired += 1
                book_id = int(reservation["book_id"])
                touched_books.add(book_id)

                copy_id = reservation.get("copy_id")
                if copy_id is not None:
                    still_held = conn.execute(
                        "SELECT COUNT(*) FROM reservations WHERE copy_id = ? "
                        "AND status = 'ready'",
                        (int(copy_id),),
                    ).fetchone()[0]
                    if not still_held:
                        conn.execute(
                            "UPDATE book_copies SET status = 'available', updated_at = ? "
                            "WHERE id = ? AND status = 'reserved'",
                            (now, int(copy_id)),
                        )
                        if self._promote_next(conn, book_id, int(copy_id)):
                            promoted += 1

            for book_id in touched_books:
                refresh_book_rollup(conn, book_id)
                self.rebalance_queue(book_id)

            self.db.audit(
                "system",
                "reservation.expire",
                target="reservations",
                detail={"expired": expired, "promoted": promoted, "as_of": reference},
                target_type="reservation",
            )

        return {"expired": expired, "promoted": promoted}

    def list_reservations(
        self, filters: Mapping[str, Any] | None, page: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Return reservations with their queue positions and hold state."""
        criteria = dict(filters or {})
        clauses: list[str] = []
        params: list[Any] = []

        status = (config.clean_text(criteria.get("status")) or "").lower()
        if status and status != "all":
            if status == "live":
                clauses.append("r.status IN ('open','ready')")
            else:
                clauses.append("r.status = ?")
                params.append(status)
        if criteria.get("book_id") not in (None, ""):
            clauses.append("r.book_id = ?")
            params.append(config.to_int(criteria.get("book_id"), -1))
        if criteria.get("member_id") not in (None, ""):
            clauses.append("r.member_id = ?")
            params.append(config.to_int(criteria.get("member_id"), -1))

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = int(page.get("page_size", 50))
        offset = config.page_offset(page, limit)

        base = (
            " FROM reservations r LEFT JOIN books b ON b.id = r.book_id "
            "LEFT JOIN members m ON m.id = r.member_id"
        )
        total = int(self.db.scalar("SELECT COUNT(*)" + base + where, params, default=0))
        rows = self.db.query(
            "SELECT r.*, b.title AS book_title, m.name AS member_name, "
            "       m.class_name, m.phone "
            + base
            + where
            + " ORDER BY r.book_id, r.queue_position, r.id LIMIT ? OFFSET ?",
            params + [limit, offset],
        )
        today = config.today_local()
        for row in rows:
            row["ready"] = str(row.get("status")) == "ready"
            row["expired"] = bool(
                row.get("expires_at") and str(row["expires_at"]) < today and row["ready"]
            )
        pages = (total + limit - 1) // limit if limit else 0
        return {
            "items": rows,
            "meta": {
                "page": int(page.get("page", 1)),
                "page_size": limit,
                "total": total,
                "pages": pages,
                "sort": "queue_position",
                "order": "asc",
            },
        }

    # -- dashboard / search helpers --------------------------------------- #
    def due_today(self, as_of: Any = None, limit: int = 5) -> list[dict[str, Any]]:
        """Return loans due on ``as_of`` (dashboard panel)."""
        reference = (config.clean_text(as_of) or config.today_local())[:10]
        return self.db.query(
            "SELECT l.id, l.due_date, l.accession_no, l.member_code, "
            "       b.title AS book_title, m.name AS member_name "
            "FROM loans l LEFT JOIN books b ON b.id = l.book_id "
            "LEFT JOIN members m ON m.id = l.member_id "
            "WHERE l.status = 'on_loan' AND l.due_date = ? ORDER BY l.id LIMIT ?",
            (reference, int(limit)),
        )

    def search(self, term: str, limit: int = 5) -> dict[str, Any]:
        """Grouped global search used by the command palette (UX-03)."""
        text = config.clean_text(term) or ""
        if not text:
            return {"loans": {"count": 0, "items": []}, "reservations": {"count": 0, "items": []}}
        like = config.like_term(text)
        loans = self.db.query(
            "SELECT l.id, l.accession_no, l.member_code, l.due_date, l.status, "
            "       b.title AS book_title, m.name AS member_name "
            "FROM loans l LEFT JOIN books b ON b.id = l.book_id "
            "LEFT JOIN members m ON m.id = l.member_id "
            "WHERE l.accession_no LIKE ? ESCAPE '\\' OR l.member_code LIKE ? ESCAPE '\\' "
            "   OR b.title LIKE ? ESCAPE '\\' "
            "ORDER BY l.id DESC LIMIT ?",
            (like, like, like, int(limit)),
        )
        reservations = self.db.query(
            "SELECT r.id, r.accession_no, r.member_code, r.queue_position, r.status, "
            "       b.title AS book_title, m.name AS member_name "
            "FROM reservations r LEFT JOIN books b ON b.id = r.book_id "
            "LEFT JOIN members m ON m.id = r.member_id "
            "WHERE r.accession_no LIKE ? ESCAPE '\\' OR r.member_code LIKE ? ESCAPE '\\' "
            "   OR b.title LIKE ? ESCAPE '\\' "
            "ORDER BY r.id DESC LIMIT ?",
            (like, like, like, int(limit)),
        )
        return {
            "loans": {"count": len(loans), "items": loans},
            "reservations": {"count": len(reservations), "items": reservations},
        }
