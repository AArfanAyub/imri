"""Catalogue, copy, member, reading-list, import/export and NFC endpoints."""

from __future__ import annotations

from typing import Any

from .. import config, errors
from ..config import get_logger
from ..labels import member_card_svg
from ..nfc import (
    REASON_NO_CARD,
    REASON_NO_DLL,
    REASON_NO_READER,
    REASON_TIMEOUT,
    REASON_TOO_LONG,
)
from . import Raw, Request, parse_page, route

_log = get_logger("api.library")

BOOK_SORT_KEYS = (
    "id",
    "title",
    "accession_no",
    "authors",
    "publisher",
    "pub_year",
    "dewey",
    "call_no",
    "material_type",
    "language",
    "status",
    "copies",
    "price",
    "location",
    "created_at",
    "updated_at",
)

MEMBER_SORT_KEYS = (
    "id",
    "name",
    "member_code",
    "class_name",
    "form",
    "member_type",
    "status",
    "membership_date",
    "expiry_date",
    "created_at",
)

BOOK_FILTER_KEYS = (
    "q",
    "material_type",
    "language",
    "status",
    "classification",
    "dewey_from",
    "dewey_to",
    "location",
    "available_only",
)

MEMBER_FILTER_KEYS = ("q", "status", "member_type", "form", "class_name", "has_overdue")


def _book_filters(request: Request) -> dict[str, Any]:
    """Collect the catalogue filter set from the query string."""
    return {key: request.get(key) for key in BOOK_FILTER_KEYS if request.get(key) is not None}


def _member_filters(request: Request) -> dict[str, Any]:
    """Collect the member filter set from the query string."""
    return {key: request.get(key) for key in MEMBER_FILTER_KEYS if request.get(key) is not None}


# --------------------------------------------------------------------------- #
# Books
# --------------------------------------------------------------------------- #


@route("GET", "/api/books", cap="book.read", sorts=BOOK_SORT_KEYS)
def list_books(ctx: Any, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return one page of titles with copy aggregates."""
    page = parse_page(request.query, sorts=BOOK_SORT_KEYS, default_sort="title")
    result = ctx.catalog.list_books(_book_filters(request), page)
    return {"items": result["items"]}, result["meta"]


@route("POST", "/api/books", cap="book.write")
def create_book(ctx: Any, request: Request) -> dict[str, Any]:
    """Create a title plus its copies and vocabulary links."""
    payload = dict(request.body or {})
    if not isinstance(payload, dict):
        raise errors.ImriError(
            errors.VALIDATION_FAILED, errors.status_for(errors.VALIDATION_FAILED)
        )
    return ctx.catalog.create_book(payload, actor=request.actor, ip=request.client)


@route("GET", "/api/books/lookup", cap="book.read")
def lookup_book(ctx: Any, request: Request) -> dict[str, Any]:
    """Resolve a scanned barcode to ``{book, copy, member}``."""
    barcode = request.get("barcode")
    if not barcode:
        raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
    return ctx.catalog.lookup_barcode(barcode)


@route("GET", "/api/books/duplicates", cap="book.read")
def duplicate_books(ctx: Any, request: Request) -> dict[str, Any]:
    """Return likely duplicates for the editor's advisory hint."""
    exclude = request.get("exclude_id")
    candidates = ctx.catalog.find_duplicates(
        isbn=request.get("isbn"),
        title=request.get("title"),
        authors=request.get("authors"),
        exclude_id=config.to_int(exclude, -1) if exclude else None,
    )
    return {"candidates": candidates}


@route("POST", "/api/books/bulk", cap="book.write")
def bulk_update_books(ctx: Any, request: Request) -> dict[str, Any]:
    """Apply a small patch to many titles."""
    ids = request.field("ids") or []
    patch = request.field("patch") or {}
    if not isinstance(ids, list) or not isinstance(patch, dict):
        raise errors.ImriError(
            errors.VALIDATION_FAILED, errors.status_for(errors.VALIDATION_FAILED)
        )
    updated = ctx.catalog.bulk_update(ids, patch, actor=request.actor, ip=request.client)
    return {"updated": updated}


@route("GET", "/api/books/{id}", cap="book.read")
def get_book(ctx: Any, request: Request) -> dict[str, Any]:
    """Return a title with copies, vocabulary, MARC and loan history."""
    return ctx.catalog.get_book(request.param_int("id"))


@route("PATCH", "/api/books/{id}", cap="book.write")
def update_book(ctx: Any, request: Request) -> dict[str, Any]:
    """Patch a title (business keys and the rollup column are read-only)."""
    return ctx.catalog.update_book(
        request.param_int("id"), request.body or {}, actor=request.actor, ip=request.client
    )


@route("DELETE", "/api/books/{id}", cap="book.delete")
def delete_book(ctx: Any, request: Request) -> dict[str, Any]:
    """Hard-delete a title, guarded by loans and live holds."""
    ctx.catalog.delete_book(request.param_int("id"), actor=request.actor, ip=request.client)
    return {"deleted": True}


@route("PUT", "/api/books/{id}/marc", cap="book.write")
def put_marc(ctx: Any, request: Request) -> dict[str, Any]:
    """Replace a title's MARC21-lite blob, preserving unknown tags."""
    blob = request.field("blob")
    if not isinstance(blob, dict):
        raise errors.ImriError(
            errors.VALIDATION_FAILED,
            errors.status_for(errors.VALIDATION_FAILED),
            fields={"blob": errors.VALIDATION_FAILED},
        )
    return {"marc": ctx.catalog.set_marc(request.param_int("id"), blob)}


# --------------------------------------------------------------------------- #
# Copies
# --------------------------------------------------------------------------- #


@route("GET", "/api/books/{id}/copies", cap="book.read")
def list_copies(ctx: Any, request: Request) -> dict[str, Any]:
    """Return every copy of a title with its current loan."""
    return {"items": ctx.catalog.list_copies(request.param_int("id"))}


@route("POST", "/api/books/{id}/copies", cap="copy.write")
def add_copies(ctx: Any, request: Request) -> dict[str, Any]:
    """Add copies to a title."""
    count = config.to_int(request.field("count"), 1)
    options = {
        "location": request.field("location"),
        "condition": request.field("condition"),
        "start_no": request.field("start_no"),
        "acquired_at": request.field("acquired_at"),
    }
    created = ctx.catalog.add_copies(
        request.param_int("id"), count, options, actor=request.actor, ip=request.client
    )
    return {"created": created}


@route("PATCH", "/api/copies/{id}", cap="copy.write")
def update_copy(ctx: Any, request: Request) -> dict[str, Any]:
    """Patch one copy."""
    return {
        "copy": ctx.catalog.update_copy(
            request.param_int("id"), request.body or {}, actor=request.actor, ip=request.client
        )
    }


@route("DELETE", "/api/copies/{id}", cap="copy.write")
def delete_copy(ctx: Any, request: Request) -> dict[str, Any]:
    """Delete one copy, refusing an on-loan copy or the last one."""
    ctx.catalog.delete_copy(request.param_int("id"), actor=request.actor, ip=request.client)
    return {"deleted": True}


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


@route("GET", "/api/subjects", cap="book.read")
def list_subjects(ctx: Any, request: Request) -> dict[str, Any]:
    """Return controlled subject headings."""
    return {"items": ctx.catalog.list_subjects(request.get("q"))}


@route("GET", "/api/languages", cap="book.read")
def list_languages(ctx: Any, request: Request) -> dict[str, Any]:
    """Return the language vocabulary."""
    return {"items": [{"code": code, "label": label} for code, label in config.LANGUAGES]}


# --------------------------------------------------------------------------- #
# Import / export
# --------------------------------------------------------------------------- #


def _import_body(request: Request) -> bytes:
    """Return the raw upload body, rejecting an empty one."""
    if not request.raw_body:
        raise errors.ImriError(errors.IMPORT_EMPTY, errors.status_for(errors.IMPORT_EMPTY))
    return request.raw_body


@route("POST", "/api/books/import/preview", cap="import.run")
def preview_books(ctx: Any, request: Request) -> dict[str, Any]:
    """Dry-run a books file. Zero writes to ``books``."""
    filename = str(request.get("filename") or "books.csv")
    return ctx.transfer.preview_books(_import_body(request), filename, actor=request.actor)


@route("POST", "/api/books/import/commit", cap="import.run")
def commit_books(ctx: Any, request: Request) -> dict[str, Any]:
    """Insert only the rows that passed validation."""
    return ctx.transfer.commit_books(str(request.field("batch_token") or ""), actor=request.actor)


@route("POST", "/api/members/import/preview", cap="import.run")
def preview_members(ctx: Any, request: Request) -> dict[str, Any]:
    """Dry-run a members file. Zero writes to ``members``."""
    filename = str(request.get("filename") or "members.csv")
    return ctx.transfer.preview_members(_import_body(request), filename, actor=request.actor)


@route("POST", "/api/members/import/commit", cap="import.run")
def commit_members(ctx: Any, request: Request) -> dict[str, Any]:
    """Insert only the rows that passed validation."""
    return ctx.transfer.commit_members(str(request.field("batch_token") or ""), actor=request.actor)


@route("DELETE", "/api/import/{batch_token}", cap="import.run")
def cancel_import(ctx: Any, request: Request) -> dict[str, Any]:
    """Drop a cached preview."""
    ctx.transfer.cancel(str(request.param("batch_token") or ""), actor=request.actor)
    return {"cancelled": True}


@route("GET", "/api/books/export", cap="export.run")
def export_books(ctx: Any, request: Request) -> dict[str, Any]:
    """Export the current filtered catalogue view."""
    fmt = str(request.get("format") or "csv").lower()
    return ctx.transfer.export_books(_book_filters(request), fmt=fmt, actor=request.actor)


@route("GET", "/api/members/export", cap="export.run")
def export_members(ctx: Any, request: Request) -> dict[str, Any]:
    """Export the current filtered member view."""
    fmt = str(request.get("format") or "csv").lower()
    return ctx.transfer.export_members(_member_filters(request), fmt=fmt, actor=request.actor)


# --------------------------------------------------------------------------- #
# Members
# --------------------------------------------------------------------------- #


@route("GET", "/api/members", cap="member.read", sorts=MEMBER_SORT_KEYS)
def list_members(ctx: Any, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return one page of members with loan and fine aggregates."""
    page = parse_page(request.query, sorts=MEMBER_SORT_KEYS, default_sort="name")
    result = ctx.members.list_members(_member_filters(request), page)
    return {"items": result["items"]}, result["meta"]


@route("POST", "/api/members", cap="member.write")
def create_member(ctx: Any, request: Request) -> dict[str, Any]:
    """Create a member."""
    return ctx.members.create_member(request.body or {}, actor=request.actor, ip=request.client)


@route("GET", "/api/members/{id}", cap="member.read")
def get_member(ctx: Any, request: Request) -> dict[str, Any]:
    """Return the 360° member profile."""
    return ctx.members.profile(request.param_int("id"))


@route("PATCH", "/api/members/{id}", cap="member.write")
def update_member(ctx: Any, request: Request) -> dict[str, Any]:
    """Patch a member."""
    return ctx.members.update_member(
        request.param_int("id"), request.body or {}, actor=request.actor, ip=request.client
    )


@route("DELETE", "/api/members/{id}", cap="member.delete")
def delete_member(ctx: Any, request: Request) -> dict[str, Any]:
    """Delete a member, refusing while loans reference them."""
    ctx.members.delete_member(request.param_int("id"), actor=request.actor, ip=request.client)
    return {"deleted": True}


@route("GET", "/api/members/{id}/loans", cap="member.read", sorts=("due_date", "issue_date", "id"))
def member_loans(ctx: Any, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a member's current or historical loans."""
    scope = str(request.get("scope") or "current").lower()
    page = parse_page(request.query, sorts=("due_date", "issue_date", "id"), default_sort="due_date")
    result = ctx.members.member_loans(request.param_int("id"), scope=scope, page=page)
    return {"items": result["items"]}, result["meta"]


@route("GET", "/api/members/{id}/fines", cap="fine.read")
def member_fines(ctx: Any, request: Request) -> dict[str, Any]:
    """Return a member's fine ledger."""
    status = str(request.get("status") or "outstanding").lower()
    return ctx.members.member_fines(request.param_int("id"), status=status)


# --------------------------------------------------------------------------- #
# Reading lists
# --------------------------------------------------------------------------- #


@route("GET", "/api/members/{id}/reading-list", cap="member.read")
def get_reading_list(ctx: Any, request: Request) -> dict[str, Any]:
    """Return a member's reading list."""
    return {"items": ctx.members.reading_list(request.param_int("id"))}


@route("POST", "/api/members/{id}/reading-list", cap="member.write")
def add_reading_list(ctx: Any, request: Request) -> dict[str, Any]:
    """Append a title to a member's reading list."""
    book_id = config.to_int(request.field("book_id"), -1)
    if book_id < 0:
        raise errors.ImriError(
            errors.VALIDATION_FAILED,
            errors.status_for(errors.VALIDATION_FAILED),
            fields={"book_id": errors.VALIDATION_FAILED},
        )
    item = ctx.members.add_to_reading_list(
        request.param_int("id"), book_id, note=request.field("note")
    )
    return {"item": item}


@route("PATCH", "/api/members/{id}/reading-list/{itemId}", cap="member.write")
def patch_reading_list(ctx: Any, request: Request) -> dict[str, Any]:
    """Patch a reading-list row."""
    item = ctx.members.update_reading_item(
        request.param_int("id"), request.param_int("itemId"), request.body or {}
    )
    return {"item": item}


@route("DELETE", "/api/members/{id}/reading-list/{itemId}", cap="member.write")
def delete_reading_list(ctx: Any, request: Request) -> dict[str, Any]:
    """Remove one reading-list row."""
    ctx.members.remove_from_reading_list(request.param_int("id"), request.param_int("itemId"))
    return {"deleted": True}


@route("POST", "/api/members/{id}/reading-list/{itemId}/reserve", cap="reservation.write")
def reserve_from_list(ctx: Any, request: Request) -> dict[str, Any]:
    """Convert a reading-list entry into a reservation."""
    return ctx.members.reserve_from_reading_list(
        request.param_int("id"), request.param_int("itemId"), actor=request.actor
    )


@route("GET", "/api/members/{id}/card.svg", cap="member.read")
def member_card(ctx: Any, request: Request) -> Raw:
    """Return the printable member card as SVG."""
    member = ctx.members.get_member(request.param_int("id"))
    scale = max(1, min(12, request.get_int("scale", 3)))
    svg = member_card_svg(
        member,
        {
            "scale": scale,
            "school_name": ctx.db.get_setting("school_name", ""),
        },
    )
    return Raw(svg, content_type="image/svg+xml; charset=utf-8")


# --------------------------------------------------------------------------- #
# NFC bridge
# --------------------------------------------------------------------------- #

_NFC_REASON_TO_CODE = {
    REASON_NO_DLL: errors.NFC_UNAVAILABLE,
    REASON_NO_READER: errors.NFC_UNAVAILABLE,
    REASON_NO_CARD: errors.NFC_NO_CARD,
    REASON_TIMEOUT: errors.NFC_TIMEOUT,
    REASON_TOO_LONG: errors.NFC_PAYLOAD_TOO_LONG,
}


@route("GET", "/api/nfc/status", cap="nfc.read")
def nfc_status(ctx: Any, request: Request) -> dict[str, Any]:
    """Report reader availability. Always 200; never an error (NFC-01)."""
    return ctx.nfc.status()


@route("POST", "/api/nfc/read", cap="nfc.read")
def nfc_read(ctx: Any, request: Request) -> dict[str, Any]:
    """Read a card UID or a text record."""
    timeout = max(200, min(20000, request.get_int("timeout_ms", 5000)))
    ok, payload = ctx.nfc.read(timeout)
    if ok:
        return payload
    reason = str(payload.get("reason") or REASON_NO_CARD)
    code = _NFC_REASON_TO_CODE.get(reason, errors.NFC_UNAVAILABLE)
    raise errors.ImriError(code, errors.status_for(code), details={"reason": reason})


@route("POST", "/api/nfc/write", cap="nfc.write")
def nfc_write(ctx: Any, request: Request) -> dict[str, Any]:
    """Write a short NDEF text record (48 bytes max)."""
    text = str(request.field("text") or "")
    if not text:
        raise errors.ImriError(
            errors.LABEL_PAYLOAD_EMPTY, errors.status_for(errors.LABEL_PAYLOAD_EMPTY)
        )
    timeout = max(200, min(20000, request.get_int("timeout_ms", 5000)))
    ok, payload = ctx.nfc.write_text(text, timeout)
    if ok:
        return {"written": True, "bytes": int(payload)}
    reason = str(payload)
    code = _NFC_REASON_TO_CODE.get(reason, errors.NFC_UNAVAILABLE)
    raise errors.ImriError(code, errors.status_for(code), details={"reason": reason})
