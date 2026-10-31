"""Circulation endpoints: issue, return, renew, loans, fines, reservations."""

from __future__ import annotations

from typing import Any

from .. import config, errors
from ..config import get_logger
from . import Request, parse_page, route

_log = get_logger("api.circulation")

LOAN_SORT_KEYS = ("due_date", "issue_date", "return_date", "member_code", "accession_no", "id")
RESERVATION_SORT_KEYS = ("queue_position", "reserved_date", "id")


@route("POST", "/api/circulation/issue", cap="circ.write")
def issue(ctx: Any, request: Request) -> dict[str, Any]:
    """Lend a copy to a member."""
    copy_ref = {
        "copy_barcode": request.field("copy_barcode"),
        "accession_no": request.field("accession_no"),
        "copy_id": request.field("copy_id"),
    }
    member_ref = {
        "member_code": request.field("member_code"),
        "member_id": request.field("member_id"),
    }
    if not any(value not in (None, "") for value in copy_ref.values()):
        raise errors.ImriError(
            errors.VALIDATION_FAILED,
            errors.status_for(errors.VALIDATION_FAILED),
            fields={"copy_barcode": errors.VALIDATION_FAILED},
            details={"reason": "copy_reference_required"},
        )
    if not any(value not in (None, "") for value in member_ref.values()):
        raise errors.ImriError(
            errors.VALIDATION_FAILED,
            errors.status_for(errors.VALIDATION_FAILED),
            fields={"member_code": errors.VALIDATION_FAILED},
            details={"reason": "member_reference_required"},
        )
    return ctx.circulation.issue(
        copy_ref,
        member_ref,
        actor=request.actor,
        due_date=request.field("due_date"),
        ip=request.client,
    )


@route("POST", "/api/circulation/return", cap="circ.write")
def return_copy(ctx: Any, request: Request) -> dict[str, Any]:
    """Take a copy back, compute the fine and promote the queue."""
    loan_id = request.field("loan_id")
    copy_ref = request.field("copy_barcode")
    if loan_id in (None, "") and copy_ref in (None, ""):
        raise errors.ImriError(
            errors.VALIDATION_FAILED,
            errors.status_for(errors.VALIDATION_FAILED),
            fields={"copy_barcode": errors.VALIDATION_FAILED},
            details={"reason": "loan_or_copy_required"},
        )
    return ctx.circulation.return_copy(
        loan_id=loan_id,
        actor=request.actor,
        condition=request.field("condition"),
        as_of=request.field("as_of"),
        copy_ref=copy_ref,
        ip=request.client,
    )


@route("POST", "/api/circulation/renew", cap="circ.write")
def renew(ctx: Any, request: Request) -> dict[str, Any]:
    """Extend a loan."""
    return ctx.circulation.renew(
        request.field("loan_id"),
        actor=request.actor,
        extend_days=request.field("extend_days"),
        ip=request.client,
    )


@route("GET", "/api/circulation/active", cap="circ.read", sorts=LOAN_SORT_KEYS)
def active_loans(ctx: Any, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return open loans with overdue days and accrued fine."""
    page = parse_page(request.query, sorts=LOAN_SORT_KEYS, default_sort="due_date")
    result = ctx.circulation.active_loans(
        {"member_id": request.get("member_id"), "book_id": request.get("book_id"), "q": request.get("q")},
        page,
    )
    return {"items": result["items"]}, result["meta"]


@route("GET", "/api/circulation/overdue", cap="circ.read")
def overdue(ctx: Any, request: Request) -> dict[str, Any]:
    """Return every overdue loan with totals."""
    page = parse_page(request.query, sorts=("due_date",), default_sort="due_date")
    result = ctx.circulation.overdue_list(as_of=request.get("as_of"), page=page)
    return {"items": result["items"], "totals": result["totals"], "meta": result["meta"]}


@route("GET", "/api/circulation/overdue/export", cap="export.run")
def export_overdue(ctx: Any, request: Request) -> dict[str, Any]:
    """Export the overdue list to CSV."""
    reference = (request.get("as_of") or config.today_local())[:10]
    listing = ctx.circulation.overdue_list(as_of=reference, page={"page_size": 200, "offset": 0})
    items = listing["items"]
    while len(items) < listing["totals"]["count"]:
        listing = ctx.circulation.overdue_list(
            as_of=reference, page={"page_size": 200, "offset": len(items)}
        )
        if not listing["items"]:
            break
        items.extend(listing["items"])

    report = {
        "title": "Overdue loans %s" % reference,
        "as_of": reference,
        "columns": [
            "due_date",
            "overdue_days",
            "accession_no",
            "book_title",
            "member_code",
            "member_name",
            "class_name",
            "phone",
            "accrued_fine",
            "balance",
        ],
        "rows": [
            [
                row.get("due_date"),
                row.get("overdue_days"),
                row.get("accession_no"),
                row.get("book_title"),
                row.get("member_code"),
                row.get("member_name"),
                row.get("class_name"),
                row.get("phone"),
                row.get("accrued_fine"),
                row.get("balance"),
            ]
            for row in items
        ],
        "totals": listing["totals"],
    }
    return ctx.reporting.export_csv(report, "overdue", actor=request.actor)


@route("GET", "/api/loans/{id}", cap="circ.read")
def loan_detail(ctx: Any, request: Request) -> dict[str, Any]:
    """Return one loan with its book, member and payments."""
    return ctx.circulation.loan_detail(request.param_int("id"))


@route("GET", "/api/loans/{id}/payments", cap="fine.read")
def list_payments(ctx: Any, request: Request) -> dict[str, Any]:
    """Return every payment recorded against a loan."""
    return {"items": ctx.circulation.payments(request.param_int("id"))}


@route("POST", "/api/loans/{id}/payments", cap="fine.pay")
def record_payment(ctx: Any, request: Request) -> dict[str, Any]:
    """Take a (possibly partial) payment against a fine."""
    return ctx.circulation.record_payment(
        request.param_int("id"),
        request.field("amount"),
        actor=request.actor,
        note=request.field("note"),
        method=str(request.field("method") or "cash"),
        ip=request.client,
    )


@route("POST", "/api/loans/{id}/waive", cap="fine.waive")
def waive(ctx: Any, request: Request) -> dict[str, Any]:
    """Waive part or all of a fine; a reason is mandatory."""
    return ctx.circulation.waive(
        request.param_int("id"),
        request.field("amount"),
        request.field("reason"),
        actor=request.actor,
        ip=request.client,
    )


@route("POST", "/api/loans/{id}/lost", cap="circ.lost")
def mark_lost(ctx: Any, request: Request) -> dict[str, Any]:
    """Declare a loan lost and write the copy off."""
    return ctx.circulation.mark_lost(
        request.param_int("id"),
        request.field("charge") or 0,
        actor=request.actor,
        note=request.field("note"),
        ip=request.client,
    )


@route("GET", "/api/fines", cap="fine.read")
def fines(ctx: Any, request: Request) -> dict[str, Any]:
    """Return the fine ledger with totals."""
    page = parse_page(request.query, sorts=("id",), default_sort="id", default_order="desc")
    result = ctx.circulation.fines(
        {"member_id": request.get("member_id"), "status": request.get("status")}, page
    )
    return {"items": result["items"], "totals": result["totals"], "meta": result["meta"]}


@route("GET", "/api/reservations", cap="reservation.read", sorts=RESERVATION_SORT_KEYS)
def list_reservations(ctx: Any, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return reservations with their queue positions and hold state."""
    page = parse_page(request.query, sorts=RESERVATION_SORT_KEYS, default_sort="queue_position")
    result = ctx.circulation.list_reservations(
        {
            "status": request.get("status"),
            "book_id": request.get("book_id"),
            "member_id": request.get("member_id"),
        },
        page,
    )
    return {"items": result["items"]}, result["meta"]


@route("POST", "/api/reservations", cap="reservation.write")
def create_reservation(ctx: Any, request: Request) -> dict[str, Any]:
    """Place a FIFO reservation on a title."""
    member_ref = request.field("member_code") or request.field("member_id")
    if member_ref in (None, ""):
        raise errors.ImriError(
            errors.VALIDATION_FAILED,
            errors.status_for(errors.VALIDATION_FAILED),
            fields={"member_code": errors.VALIDATION_FAILED},
        )
    book_id = request.field("book_id")
    if book_id in (None, ""):
        raise errors.ImriError(
            errors.VALIDATION_FAILED,
            errors.status_for(errors.VALIDATION_FAILED),
            fields={"book_id": errors.VALIDATION_FAILED},
        )
    return ctx.circulation.reserve(
        book_id, member_ref, actor=request.actor, note=request.field("note"), ip=request.client
    )


@route("POST", "/api/reservations/expire", cap="reservation.write")
def expire_holds(ctx: Any, request: Request) -> dict[str, Any]:
    """Expire stale holds and promote the next member in line."""
    return ctx.circulation.expire_holds(as_of=request.field("as_of"))


@route("POST", "/api/reservations/{id}/cancel", cap="reservation.write")
def cancel_reservation(ctx: Any, request: Request) -> dict[str, Any]:
    """Cancel a live reservation."""
    return ctx.circulation.cancel_reservation(
        request.param_int("id"),
        reason=request.field("reason"),
        actor=request.actor,
        ip=request.client,
    )


@route("POST", "/api/reservations/{id}/fulfil", cap="circ.write")
def fulfil_reservation(ctx: Any, request: Request) -> dict[str, Any]:
    """Turn a live reservation into a loan."""
    return ctx.circulation.fulfil_reservation(
        request.param_int("id"),
        copy_ref=request.field("copy_barcode"),
        actor=request.actor,
        ip=request.client,
    )
