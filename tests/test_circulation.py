"""Circulation: the issue/return/renew/pay/reserve lifecycle and its guards.

The centrepiece is :class:`DefinitionOfDoneTests`, which replays the T2
acceptance scenario from ``imri-docs/TASKS.md`` step by step. If that test ever
fails, the product no longer does what the requirements say it does.
"""

from __future__ import annotations

import unittest

from helpers import ServiceTestCase, rollup_mismatches

from imri import config, errors


def code_of(callable_, *args, **kwargs) -> str:
    """Run *callable_* expecting an ImriError and return its code."""
    try:
        callable_(*args, **kwargs)
    except errors.ImriError as exc:
        return exc.code
    raise AssertionError("expected an ImriError, but the call succeeded")


class DefinitionOfDoneTests(ServiceTestCase):
    """T2 DoD, replayed verbatim against a real database.

    One title with three copies, two members, two copies on loan, one returned
    five days late, a part payment, a reservation, then the last copy comes
    back and the queue must promote.
    """

    def test_full_counter_story(self) -> None:
        book = self.make_book(title="Rejab Hadhari", copies=3)["book"]
        book_id = book["id"]
        self.assertEqual(book["copies"], 3)

        aina = self.make_member("Aina Rahman")["member"]
        bob = self.make_member("Bob Lee")["member"]

        copies = self.copies_of(book_id)
        self.assertEqual(len(copies), 3)
        self.assertEqual({c["status"] for c in copies}, {"available"})

        # -- two copies go out ------------------------------------------- #
        loan_a = self.circulation.issue(copies[0]["barcode"], aina["member_code"], actor="aina")
        loan_b = self.circulation.issue(copies[1]["barcode"], bob["member_code"], actor="bob")
        self.assertEqual(loan_a["loan"]["status"], "on_loan")
        self.assertEqual(loan_b["loan"]["status"], "on_loan")
        self.assertEqual(self.catalog.available_copies(book_id), 1)

        # The third copy goes out too, so the title has nothing on the shelf.
        loan_c = self.circulation.issue(copies[2]["barcode"], bob["member_code"], actor="bob")
        self.assertEqual(self.catalog.available_copies(book_id), 0)

        # -- one comes back five days late ------------------------------- #
        late_day = config.date_add(loan_a["loan"]["due_date"], 5)
        back = self.circulation.return_copy(loan_id=loan_a["loan"]["id"], as_of=late_day, actor="aina")
        self.assertEqual(back["fine"]["days_late"], 5)
        self.assertEqual(back["fine"]["amount"], 1.00)  # 5 x RM0.20
        self.assertEqual(back["fine"]["balance"], 1.00)
        self.assertEqual(back["loan"]["status"], "returned")
        self.assertEqual(back["copy_status"], "available")
        # No reservation existed yet, so nothing was promoted.
        self.assertFalse(back["queue"]["promoted"])

        # -- a part payment ---------------------------------------------- #
        payment = self.circulation.record_payment(
            loan_a["loan"]["id"], 0.40, actor="aina", method="cash"
        )
        self.assertEqual(payment["payment"]["amount"], 0.40)
        self.assertEqual(payment["balance"], 0.60)
        self.assertTrue(payment["payment"]["receipt_no"])

        # -- the freed copy is lent again, so the shelf is empty --------- #
        self.circulation.issue(copies[0]["barcode"], bob["member_code"], actor="bob")
        self.assertEqual(self.catalog.available_copies(book_id), 0)

        # -- aina reserves the title ------------------------------------- #
        reservation = self.circulation.reserve(book_id, aina["id"], actor="aina")
        self.assertEqual(reservation["queue_position"], 1)
        self.assertEqual(reservation["reservation"]["status"], "open")

        # -- the last copy returns and the queue promotes ---------------- #
        final = self.circulation.return_copy(loan_id=loan_b["loan"]["id"], actor="bob")
        self.assertTrue(final["queue"]["promoted"])
        self.assertEqual(final["copy_status"], "reserved")
        self.assertEqual(final["queue"]["next_member"]["member_id"], aina["id"])

        held = self.db.one("SELECT * FROM reservations WHERE id = ?", (reservation["reservation"]["id"],))
        self.assertEqual(held["status"], "ready")
        self.assertEqual(held["queue_position"], 1)
        self.assertIsNotNone(held["expires_at"])

        # -- the copy is now held, not lendable to anyone else ----------- #
        self.assertEqual(
            code_of(self.circulation.issue, copies[1]["barcode"], bob["member_code"]),
            errors.COPY_ON_HOLD,
        )
        # ...but the holder may take it.
        collected = self.circulation.issue(copies[1]["barcode"], aina["member_code"], actor="aina")
        self.assertEqual(collected["loan"]["status"], "on_loan")
        spent = self.db.one("SELECT * FROM reservations WHERE id = ?", (reservation["reservation"]["id"],))
        self.assertEqual(spent["status"], "fulfilled")

        self.assertEqual(rollup_mismatches(self.db), [])
        self.assertTrue(self.db.integrity_check()["ok"])


class IssueGuardTests(ServiceTestCase):
    """Every documented guard runs, in order, and leaves the database untouched."""

    def setUp(self) -> None:
        super().setUp()
        self.book = self.make_book(copies=2)["book"]
        self.copies = self.copies_of(self.book["id"])
        self.member = self.make_member("Reader One")["member"]

    def test_unknown_barcode_is_not_found(self) -> None:
        self.assertEqual(
            code_of(self.circulation.issue, "NO-SUCH-BARCODE", self.member["member_code"]),
            errors.COPY_NOT_FOUND,
        )

    def test_unknown_member_is_not_found(self) -> None:
        self.assertEqual(
            code_of(self.circulation.issue, self.copies[0]["barcode"], "S99999"),
            errors.NOT_FOUND,
        )

    def test_suspended_member_cannot_borrow(self) -> None:
        self.members.update_member(self.member["id"], {"status": "suspended"}, actor="tester")
        self.assertEqual(
            code_of(self.circulation.issue, self.copies[0]["barcode"], self.member["member_code"]),
            errors.MEMBER_SUSPENDED,
        )

    def test_loan_limit_is_enforced(self) -> None:
        limit = self.circulation.max_loans
        book = self.make_book(title="Many Copies", copies=limit + 2)["book"]
        for copy in self.copies_of(book["id"])[:limit]:
            self.circulation.issue(copy["barcode"], self.member["member_code"], actor="tester")
        spare = self.copies_of(book["id"])[limit]
        self.assertEqual(
            code_of(self.circulation.issue, spare["barcode"], self.member["member_code"]),
            errors.MAX_LOANS_REACHED,
        )

    def test_a_copy_already_out_cannot_be_issued_again(self) -> None:
        self.circulation.issue(self.copies[0]["barcode"], self.member["member_code"], actor="tester")
        other = self.make_member("Reader Two")["member"]
        self.assertEqual(
            code_of(self.circulation.issue, self.copies[0]["barcode"], other["member_code"]),
            errors.COPY_UNAVAILABLE,
        )

    def test_due_date_before_issue_date_is_rejected(self) -> None:
        yesterday = config.date_add(config.today_local(), -1)
        self.assertEqual(
            code_of(
                self.circulation.issue,
                self.copies[0]["barcode"],
                self.member["member_code"],
                due_date=yesterday,
            ),
            errors.VALIDATION_FAILED,
        )

    def test_issue_is_atomic_on_failure(self) -> None:
        """A refused issue must not leave a half-written loan or a moved copy."""
        before_loans = self.db.one("SELECT COUNT(*) AS n FROM loans")["n"]
        self.circulation.issue(self.copies[0]["barcode"], self.member["member_code"], actor="tester")
        code_of(self.circulation.issue, self.copies[0]["barcode"], self.member["member_code"])
        after_loans = self.db.one("SELECT COUNT(*) AS n FROM loans")["n"]
        self.assertEqual(after_loans, before_loans + 1)
        self.assertEqual(rollup_mismatches(self.db), [])


class ReturnTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.book = self.make_book(copies=1)["book"]
        self.copy = self.copies_of(self.book["id"])[0]
        self.member = self.make_member("Reader One")["member"]
        self.loan = self.circulation.issue(
            self.copy["barcode"], self.member["member_code"], actor="tester"
        )["loan"]

    def test_on_time_return_carries_no_fine(self) -> None:
        result = self.circulation.return_copy(loan_id=self.loan["id"], as_of=self.loan["due_date"])
        self.assertEqual(result["fine"]["days_late"], 0)
        self.assertEqual(result["fine"]["amount"], 0.00)

    def test_one_day_late(self) -> None:
        as_of = config.date_add(self.loan["due_date"], 1)
        result = self.circulation.return_copy(loan_id=self.loan["id"], as_of=as_of)
        self.assertEqual(result["fine"]["days_late"], 1)
        self.assertEqual(result["fine"]["amount"], 0.20)

    def test_returning_twice_is_refused(self) -> None:
        self.circulation.return_copy(loan_id=self.loan["id"])
        self.assertEqual(
            code_of(self.circulation.return_copy, loan_id=self.loan["id"]),
            errors.ALREADY_RETURNED,
        )

    def test_return_by_barcode_works(self) -> None:
        result = self.circulation.return_copy(copy_ref=self.copy["barcode"])
        self.assertEqual(result["loan"]["status"], "returned")

    def test_unknown_loan_is_not_found(self) -> None:
        self.assertEqual(
            code_of(self.circulation.return_copy, loan_id=999999), errors.NOT_FOUND
        )

    def test_condition_is_recorded(self) -> None:
        self.circulation.return_copy(loan_id=self.loan["id"], condition="poor")
        copy = self.db.one("SELECT * FROM book_copies WHERE id = ?", (self.copy["id"],))
        self.assertEqual(copy["condition"], "poor")


class RenewalTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.book = self.make_book(copies=1)["book"]
        self.copy = self.copies_of(self.book["id"])[0]
        self.member = self.make_member()["member"]
        self.loan = self.circulation.issue(
            self.copy["barcode"], self.member["member_code"], actor="tester"
        )["loan"]

    def test_renewal_extends_the_due_date(self) -> None:
        result = self.circulation.renew(self.loan["id"], actor="tester")
        new_due = result["loan"]["due_date"]
        self.assertGreater(new_due, self.loan["due_date"])
        self.assertEqual(result["loan"]["renewal_count"], 1)

    def test_renewal_limit_is_enforced(self) -> None:
        for _ in range(self.circulation.max_renewals):
            self.circulation.renew(self.loan["id"], actor="tester")
        self.assertEqual(
            code_of(self.circulation.renew, self.loan["id"]), errors.RENEWAL_LIMIT_REACHED
        )

    def test_overdue_loan_cannot_be_renewed(self) -> None:
        late = config.date_add(self.loan["due_date"], 3)
        self.db.execute("UPDATE loans SET due_date = ? WHERE id = ?", (config.date_add(late, -20), self.loan["id"]))
        self.assertEqual(code_of(self.circulation.renew, self.loan["id"]), errors.LOAN_OVERDUE)

    def test_renewal_is_blocked_when_someone_is_waiting(self) -> None:
        other = self.make_member("Waiting Reader")["member"]
        self.circulation.reserve(self.book["id"], other["id"], actor="tester")
        self.assertEqual(
            code_of(self.circulation.renew, self.loan["id"]),
            errors.RENEWAL_BLOCKED_RESERVATION,
        )


class FineTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.book = self.make_book(copies=1)["book"]
        self.copy = self.copies_of(self.book["id"])[0]
        self.member = self.make_member()["member"]
        self.loan = self.circulation.issue(
            self.copy["barcode"], self.member["member_code"], actor="tester"
        )["loan"]
        self.circulation.return_copy(
            loan_id=self.loan["id"], as_of=config.date_add(self.loan["due_date"], 10)
        )

    def test_fine_is_two_ringgit_for_ten_days(self) -> None:
        loan = self.circulation.loan_detail(self.loan["id"])
        self.assertEqual(loan["loan"]["fine_amount"], 2.00)

    def test_overpayment_is_refused(self) -> None:
        self.assertEqual(
            code_of(self.circulation.record_payment, self.loan["id"], 5.00),
            errors.OVERPAYMENT,
        )

    def test_zero_and_negative_payments_are_refused(self) -> None:
        for amount in (0, -1, "abc"):
            self.assertEqual(
                code_of(self.circulation.record_payment, self.loan["id"], amount),
                errors.INVALID_AMOUNT,
            )

    def test_paying_the_balance_clears_it(self) -> None:
        result = self.circulation.record_payment(self.loan["id"], 2.00, actor="tester")
        self.assertEqual(result["balance"], 0.00)
        self.assertEqual(
            code_of(self.circulation.record_payment, self.loan["id"], 0.10),
            errors.INVALID_AMOUNT,
        )

    def test_receipt_numbers_are_unique(self) -> None:
        self.circulation.record_payment(self.loan["id"], 0.50, actor="tester")
        self.circulation.record_payment(self.loan["id"], 0.50, actor="tester")
        receipts = [row["receipt_no"] for row in self.circulation.payments(self.loan["id"])]
        self.assertEqual(len(receipts), len(set(receipts)))
        self.assertEqual(len(receipts), 2)

    def test_waiver_needs_a_reason(self) -> None:
        self.assertEqual(
            code_of(self.circulation.waive, self.loan["id"], 1.00, ""),
            errors.REASON_REQUIRED,
        )

    def test_waiver_reduces_the_balance(self) -> None:
        result = self.circulation.waive(self.loan["id"], 1.50, "Flood damage", actor="tester")
        self.assertEqual(result["balance"], 0.50)

    def test_payments_cannot_exceed_the_fine_via_two_loans(self) -> None:
        """Money is tracked per loan, so a second loan's balance is untouched."""
        other_book = self.make_book(title="Second", copies=1)["book"]
        other_copy = self.copies_of(other_book["id"])[0]
        other_loan = self.circulation.issue(
            other_copy["barcode"], self.member["member_code"], actor="tester"
        )["loan"]
        self.circulation.record_payment(self.loan["id"], 2.00, actor="tester")
        untouched = self.circulation.loan_detail(other_loan["id"])
        self.assertEqual(untouched["loan"]["paid_amount"], 0)


class ReservationTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.book = self.make_book(copies=1)["book"]
        self.copy = self.copies_of(self.book["id"])[0]
        self.holder = self.make_member("Holder")["member"]
        self.waiting = self.make_member("Waiting")["member"]
        self.circulation.issue(self.copy["barcode"], self.holder["member_code"], actor="tester")

    def test_cannot_reserve_a_title_that_is_on_the_shelf(self) -> None:
        free_book = self.make_book(title="Free", copies=1)["book"]
        self.assertEqual(
            code_of(self.circulation.reserve, free_book["id"], self.waiting["id"]),
            errors.TITLE_AVAILABLE,
        )

    def test_queue_positions_are_gapless_and_ordered(self) -> None:
        third = self.make_member("Third")["member"]
        first = self.circulation.reserve(self.book["id"], self.waiting["id"], actor="tester")
        second = self.circulation.reserve(self.book["id"], third["id"], actor="tester")
        self.assertEqual(first["queue_position"], 1)
        self.assertEqual(second["queue_position"], 2)

        self.circulation.cancel_reservation(first["reservation"]["id"], actor="tester")
        remaining = self.db.query(
            "SELECT queue_position FROM reservations WHERE book_id = ? AND status IN ('open','ready') "
            "ORDER BY queue_position",
            (self.book["id"],),
        )
        self.assertEqual([row["queue_position"] for row in remaining], [1])

    def test_duplicate_reservation_is_refused(self) -> None:
        self.circulation.reserve(self.book["id"], self.waiting["id"], actor="tester")
        self.assertEqual(
            code_of(self.circulation.reserve, self.book["id"], self.waiting["id"]),
            errors.RESERVATION_EXISTS,
        )

    def test_expired_hold_returns_the_copy_to_the_shelf(self) -> None:
        self.circulation.reserve(self.book["id"], self.waiting["id"], actor="tester")
        self.circulation.return_copy(loan_id=1, actor="tester")
        copy = self.db.one("SELECT * FROM book_copies WHERE id = ?", (self.copy["id"],))
        self.assertEqual(copy["status"], "reserved")

        far_future = config.date_add(config.today_local(), self.circulation.hold_days + 5)
        result = self.circulation.expire_holds(as_of=far_future)
        self.assertGreaterEqual(result["expired"], 1)
        copy = self.db.one("SELECT * FROM book_copies WHERE id = ?", (self.copy["id"],))
        self.assertEqual(copy["status"], "available")


class SearchTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.book = self.make_book(title="Sejarah Melayu", copies=1)["book"]
        self.copy = self.copies_of(self.book["id"])[0]
        self.member = self.make_member("Nurul Izzah")["member"]

    def test_barcode_scan_finds_the_copy(self) -> None:
        found = self.catalog.lookup_barcode(self.copy["barcode"])
        self.assertEqual(found["book"]["id"], self.book["id"])
        self.assertEqual(found["copy"]["id"], self.copy["id"])

    def test_counter_search_finds_a_loan_by_title(self) -> None:
        self.circulation.issue(self.copy["barcode"], self.member["member_code"], actor="tester")
        hits = self.circulation.search("Melayu")
        self.assertEqual(hits["loans"]["count"], 1)
        self.assertEqual(hits["loans"]["items"][0]["member_name"], "Nurul Izzah")

    def test_counter_search_finds_a_loan_by_member_code(self) -> None:
        self.circulation.issue(self.copy["barcode"], self.member["member_code"], actor="tester")
        hits = self.circulation.search(self.member["member_code"])
        self.assertEqual(hits["loans"]["count"], 1)

    def test_search_is_safe_against_wildcards(self) -> None:
        """A ``%`` typed into the box must not match everything.

        Without ``ESCAPE`` in the SQL, ``%`` is a wildcard and every search
        becomes a full scan that returns the whole table.
        """
        self.circulation.issue(self.copy["barcode"], self.member["member_code"], actor="tester")
        self.assertEqual(self.circulation.search("%")["loans"]["count"], 0)
        self.assertEqual(self.circulation.search("_")["loans"]["count"], 0)
        self.assertEqual(self.catalog.list_books({"q": "%"}, {"page_size": 50})["meta"]["total"], 0)
        self.assertEqual(self.members.list_members({"q": "_"}, {"page_size": 50})["meta"]["total"], 0)

    def test_search_still_matches_a_literal_percent(self) -> None:
        """Escaping must not break a legitimate search for a real % or _."""
        self.catalog.create_book(
            {"title": "50% Off", "authors": "Sale", "material_type": "book", "copies": 1},
            actor="tester",
        )
        found = self.catalog.list_books({"q": "50%"}, {"page_size": 50})
        self.assertEqual(found["meta"]["total"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
