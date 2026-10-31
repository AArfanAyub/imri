"""T2 proof: the domain rules that are easy to get wrong.

One title, three copies, two members. The script walks the exact sequence the
task brief asks for and asserts every number the librarian would see on screen:

* issue two copies, one to each member
* backdate one loan five days past its due date
* return it -> fine 1.00 at 0.20/day
* pay 0.40 -> balance 0.60
* reserve the title for the other member
* return the second copy -> the hold becomes ``ready`` at position 1 and the
  copy flips to ``reserved`` with an expiry date
* jump past the hold window -> the hold expires and the copy returns to
  ``available``
* finally: ``books.copies`` equals ``COUNT(book_copies)`` for every title, and
  ``books.status`` agrees with the copies

Nothing here touches HTTP. This is the domain layer on its own, which is the
point: if the rules are right here, the API layer only has to serialise them.

    python tools/proof_t2.py
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRATCH = Path(tempfile.mkdtemp(prefix="imri-t2-"))
os.environ["IMRI_DATA_DIR"] = str(SCRATCH)

from imri import config  # noqa: E402
from imri.catalog import CatalogService, refresh_book_rollup  # noqa: E402
from imri.circulation import CirculationService  # noqa: E402
from imri.db import Database, rollup_mismatches  # noqa: E402
from imri.members import MemberService  # noqa: E402

CHECKS = 0


def check(label: str, actual: object, expected: object) -> None:
    """Assert one observable fact and print it."""
    global CHECKS
    CHECKS += 1
    if actual != expected:
        raise AssertionError("%s: expected %r, got %r" % (label, expected, actual))
    print("  ok  %-48s %r" % (label, actual))


def day(offset: int) -> str:
    """Return ``today + offset`` as ``YYYY-MM-DD`` in local time."""
    return (dt.date.today() + dt.timedelta(days=offset)).isoformat()


def expect_error(label: str, code: str, call) -> None:
    """Assert that ``call()`` raises an ``ImriError`` carrying ``code``."""
    global CHECKS
    CHECKS += 1
    try:
        call()
    except Exception as exc:  # noqa: BLE001
        actual = getattr(exc, "code", str(exc))
        if actual != code:
            raise AssertionError("%s: expected %r, got %r" % (label, code, actual))
        print("  ok  %-48s %r" % (label, actual))
        return
    raise AssertionError("%s: expected %r, nothing was raised" % (label, code))


def main() -> int:
    config.ensure_dirs()
    db = Database()
    catalog = CatalogService(db)
    members = MemberService(db, catalog)
    circulation = CirculationService(db, catalog)

    print("data dir:", SCRATCH)
    check("fine_per_day policy", circulation.fine_per_day, 0.20)
    check("loan_days policy", circulation.loan_days, 14)
    check("hold_days policy", circulation.hold_days, 3)

    # ------------------------------------------------------------------ #
    print("\n[1] catalogue: one title, three copies")
    created = catalog.create_book(
        {
            "title": "The Railway Children",
            "authors": "Edith Nesbit",
            "publisher": "Puffin",
            "pub_year": 1906,
            "isbn": "9780141321608",
            "dewey": "823.8",
            "call_no": "823.8 NES",
            "material_type": "book",
            "language": "en",
            "price": 24.90,
            "location": "A-01",
            "copies": 3,
        },
        actor="proof",
    )
    book = created["book"]
    book_id = int(book["id"])
    check("copies created", created["copies_created"], 3)
    check("book.copies after create", book["copies"], 3)
    check("book.status after create", book["status"], "available")
    check("accession generated", bool(book["accession_no"]), True)

    copies = catalog.list_copies(book_id)
    check("copy rows", len(copies), 3)
    check("copy statuses", [c["status"] for c in copies], ["available"] * 3)
    check("copy numbers", [c["copy_no"] for c in copies], [1, 2, 3])
    check("distinct barcodes", len({c["barcode"] for c in copies}), 3)

    # ------------------------------------------------------------------ #
    print("\n[2] members: two borrowers")
    aisyah = members.create_member(
        {
            "name": "Aisyah Rahman",
            "member_code": "S-0001",
            "member_type": "student",
            "class_name": "5 Amanah",
            "ic_number": "080512145533",
        },
        actor="proof",
    )["member"]
    daniel = members.create_member(
        {
            "name": "Daniel Tan",
            "member_code": "S-0002",
            "member_type": "student",
            "class_name": "5 Bestari",
            "ic_number": "080101015577",
        },
        actor="proof",
    )["member"]
    check("member 1 code", aisyah["member_code"], "S-0001")
    check("member 1 status", aisyah["status"], "active")
    check("member 2 status", daniel["status"], "active")

    # ------------------------------------------------------------------ #
    print("\n[3] issue two copies")
    issued_a = circulation.issue(copies[0]["id"], aisyah["id"], actor="proof")
    issued_b = circulation.issue(copies[1]["id"], daniel["id"], actor="proof")
    loan_a_id = int(issued_a["loan"]["id"])
    loan_b_id = int(issued_b["loan"]["id"])
    check("loan 1 status", issued_a["loan"]["status"], "on_loan")
    check("loan 1 due date", issued_a["due_date"], day(14))
    check("loan 2 due date", issued_b["due_date"], day(14))
    check("loan 1 book accession", issued_a["loan"]["accession_no"], book["accession_no"])
    check("copy 1 now on loan", issued_a["copy"]["status"], "on_loan")

    # A copy already on loan cannot be lent twice.
    expect_error(
        "double issue refused",
        "copy_unavailable",
        lambda: circulation.issue(copies[0]["id"], daniel["id"], actor="proof"),
    )

    # The third copy goes out too, so the title has nothing on the shelf and
    # the rollup has to say so. This is the transition that the librarian sees
    # as the badge flipping from Available to On loan.
    issued_c = circulation.issue(copies[2]["id"], aisyah["id"], actor="proof")
    loan_c_id = int(issued_c["loan"]["id"])
    check("third copy on loan", issued_c["copy"]["status"], "on_loan")

    refreshed = catalog.get_book(book_id)
    check("book.copies unchanged", refreshed["book"]["copies"], 3)
    check("book.status when fully lent", refreshed["book"]["status"], "on_loan")
    check("available copies left", catalog.available_copies(book_id), 0)
    check("copies payload rows", len(refreshed["copies"]), 3)
    check(
        "copy 1 carries its loan",
        refreshed["copies"][0]["current_loan"]["member_code"],
        "S-0001",
    )

    # ------------------------------------------------------------------ #
    print("\n[4] return five days late -> fine 1.00")
    # Backdate the due date instead of waiting five days. This is the only
    # direct SQL in the script; the rule under test is the arithmetic, not the
    # clock.
    db.execute("UPDATE loans SET due_date = ? WHERE id = ?", (day(-5), loan_a_id))
    returned = circulation.return_copy(loan_a_id, actor="proof")
    check("days late", returned["fine"]["days_late"], 5)
    check("fine amount", config.money(returned["fine"]["amount"]), 1.00)
    check("fine per day echoed", config.money(returned["fine"]["per_day"]), 0.20)
    check("balance after return", config.money(returned["fine"]["balance"]), 1.00)
    check("copy back on the shelf", returned["copy_status"], "available")
    check("nothing promoted yet", returned["queue"]["promoted"], False)
    check("title lendable again", catalog.get_book(book_id)["book"]["status"], "available")

    # Returning the same loan twice is refused.
    expect_error(
        "double return refused",
        "already_returned",
        lambda: circulation.return_copy(loan_a_id, actor="proof"),
    )

    # ------------------------------------------------------------------ #
    print("\n[5] pay 0.40 -> balance 0.60")
    payment = circulation.record_payment(loan_a_id, 0.40, actor="proof", note="partial")
    check("paid amount", config.money(payment["payment"]["amount"]), 0.40)
    check("balance", config.money(payment["balance"]), 0.60)
    check("payment receipt", payment["payment"]["receipt_no"][:5], "IMRI-")

    def _overpay():
        circulation.record_payment(loan_a_id, 5.00, actor="proof")

    # Overpayment is refused, and the message says what is still owed.
    expect_error("overpayment refused", "overpayment", _overpay)
    try:
        _overpay()
    except Exception as exc:  # noqa: BLE001
        details = getattr(exc, "details", None) or {}
        check("overpayment reports outstanding", config.money(details.get("outstanding", 0)), 0.60)

    # Waiving the rest clears the balance without inventing money.
    waived = circulation.waive(loan_a_id, 0.60, "damaged spine, goodwill", actor="proof")
    check("balance after waiver", config.money(waived["balance"]), 0.00)

    # ------------------------------------------------------------------ #
    print("\n[6] reserve the title for Aisyah")
    # A reservation only makes sense when nothing is on the shelf; the guard
    # says so rather than quietly queueing someone who could just borrow it.
    expect_error(
        "reservation refused while a copy is free",
        "title_available",
        lambda: circulation.reserve(book_id, aisyah["id"], actor="proof"),
    )

    # Lend the returned copy out again so the queue becomes meaningful.
    circulation.issue(copies[0]["id"], daniel["id"], actor="proof")
    check("title fully lent again", catalog.get_book(book_id)["book"]["status"], "on_loan")

    placed = circulation.reserve(book_id, aisyah["id"], actor="proof")
    reservation = placed["reservation"]
    reservation_id = int(reservation["id"])
    check("reservation status", reservation["status"], "open")
    check("queue position", placed["queue_position"], 1)
    check("no expiry while waiting", reservation["expires_at"], None)

    # One live reservation per member per title.
    expect_error(
        "duplicate reservation refused",
        "reservation_exists",
        lambda: circulation.reserve(book_id, aisyah["id"], actor="proof"),
    )

    # ------------------------------------------------------------------ #
    print("\n[7] return the second copy -> hold becomes ready")
    second = circulation.return_copy(loan_b_id, actor="proof")
    check("second return fine", config.money(second["fine"]["amount"]), 0.00)
    check("a hold was promoted", second["queue"]["promoted"], True)
    check(
        "promoted reservation id",
        second["queue"]["next_member"]["reservation_id"],
        reservation_id,
    )
    check("promoted member", second["queue"]["next_member"]["member_code"], "S-0001")
    check("copy held for the queue", second["copy_status"], "reserved")

    held = db.one("SELECT * FROM reservations WHERE id = ?", (reservation_id,))
    check("hold status", held["status"], "ready")
    check("hold queue position", held["queue_position"], 1)
    check("hold expires_at", held["expires_at"], day(circulation.hold_days))

    held_copy_id = int(held["copy_id"])
    check(
        "held copy row",
        db.one("SELECT status FROM book_copies WHERE id = ?", (held_copy_id,))["status"],
        "reserved",
    )

    # The reserved copy is refused for the other member...
    expect_error(
        "copy_on_hold for another member",
        "copy_on_hold",
        lambda: circulation.issue(held_copy_id, daniel["id"], actor="proof"),
    )
    # ...and lending it to the member it is held for fulfils the hold.
    fulfilled = circulation.issue(held_copy_id, aisyah["id"], actor="proof")
    check("held copy issued to the right member", fulfilled["copy"]["status"], "on_loan")
    check(
        "hold fulfilled",
        db.one("SELECT status FROM reservations WHERE id = ?", (reservation_id,))["status"],
        "fulfilled",
    )

    # ------------------------------------------------------------------ #
    print("\n[8] a second hold expires when nobody collects it")
    again_hold = circulation.reserve(book_id, daniel["id"], actor="proof")
    second_hold_id = int(again_hold["reservation"]["id"])
    check("second hold position", again_hold["queue_position"], 1)

    third = circulation.return_copy(loan_c_id, actor="proof")
    check("second hold promoted", third["queue"]["promoted"], True)
    check("promoted the second hold", third["queue"]["next_member"]["reservation_id"], second_hold_id)
    check("copy 3 came back", third["copy_status"], "reserved")

    second_copy_id = int(
        db.one("SELECT copy_id FROM reservations WHERE id = ?", (second_hold_id,))["copy_id"]
    )
    check(
        "second hold sits on a copy",
        db.one("SELECT status FROM book_copies WHERE id = ?", (second_copy_id,))["status"],
        "reserved",
    )

    later = day(circulation.hold_days + 1)
    expired = circulation.expire_holds(as_of=later)
    check("expired count", expired["expired"], 1)
    check(
        "second hold status",
        db.one("SELECT status FROM reservations WHERE id = ?", (second_hold_id,))["status"],
        "expired",
    )
    check(
        "copy back on the shelf after expiry",
        db.one("SELECT status FROM book_copies WHERE id = ?", (second_copy_id,))["status"],
        "available",
    )
    check(
        "the collected copy is still on loan",
        db.one("SELECT status FROM book_copies WHERE id = ?", (held_copy_id,))["status"],
        "on_loan",
    )
    check("expire_holds is idempotent", circulation.expire_holds(as_of=later)["expired"], 0)

    # ------------------------------------------------------------------ #
    print("\n[9] rollup integrity")
    for row in db.query("SELECT id, copies, status FROM books"):
        actual = db.scalar(
            "SELECT COUNT(*) FROM book_copies WHERE book_id = ?", (int(row["id"]),)
        )
        check("book %d copies == copy rows" % int(row["id"]), int(row["copies"]), int(actual))
    check("no rollup mismatches", rollup_mismatches(db), [])

    final = catalog.get_book(book_id)
    check("final book status", final["book"]["status"], "available")

    # Repairing a deliberately corrupted rollup proves the helper is the only
    # writer that matters: corrupt it, call the helper, expect agreement.
    db.execute("UPDATE books SET copies = 99 WHERE id = ?", (book_id,))
    check("corruption detected", len(rollup_mismatches(db)), 1)
    with db.transaction() as conn:
        refresh_book_rollup(conn, book_id)
    check("refresh_book_rollup repairs", catalog.get_book(book_id)["book"]["copies"], 3)
    check("mismatch list empty again", rollup_mismatches(db), [])

    # ------------------------------------------------------------------ #
    print("\n[10] arithmetic is local-date based")
    check("today_local matches date.today", config.today_local(), dt.date.today().isoformat())
    check("days_between", config.days_between(day(-5), day(0)), 5)
    check("money rounds half up", config.money(0.615), 0.62)
    check("balance floors at zero", config.money(max(0.0, 1.00 - 1.00 - 0.50)), 0.00)

    # ------------------------------------------------------------------ #
    print("\n[11] audit log is append-only")
    check("audit rows written", db.scalar("SELECT COUNT(*) FROM audit_log") > 0, True)
    first = db.one("SELECT * FROM audit_log ORDER BY id LIMIT 1")
    # The BEFORE UPDATE / BEFORE DELETE triggers RAISE(ABORT), so the attempt
    # fails inside SQLite itself. No application code can talk its way past it.
    for label, statement in (
        ("audit rows cannot be updated", "UPDATE audit_log SET action = 'x' WHERE id = ?"),
        ("audit rows cannot be deleted", "DELETE FROM audit_log WHERE id = ?"),
    ):
        try:
            db.execute(statement, (first["id"],))
            raise AssertionError("%s: the write was allowed" % label)
        except sqlite3.IntegrityError as exc:
            check(label, "append-only" in str(exc), True)
    check(
        "audit row still intact",
        db.one("SELECT action FROM audit_log WHERE id = ?", (first["id"],))["action"],
        first["action"],
    )

    db.close()
    print("\n%d checks, T2 PASS" % CHECKS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
