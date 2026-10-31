"""Insight endpoints: reports, CSV/PDF export, labels and backup management."""

from __future__ import annotations

import os
from typing import Any

from .. import config, errors
from ..config import get_logger
from ..labels import code39_svg, qr_svg, sheet_svg
from . import Raw, Request, route

_log = get_logger("api.insights")

REPORT_KINDS = ("collection", "circulation", "membership", "overdue-fines", "trend")


def _build_report(ctx: Any, kind: str, request: Request) -> dict[str, Any]:
    """Dispatch to the matching report builder."""
    if kind == "collection":
        return ctx.reporting.collection(
            {
                "material_type": request.get("material_type"),
                "language": request.get("language"),
                "classification": request.get("classification"),
                "status": request.get("status"),
            }
        )
    if kind == "circulation":
        return ctx.reporting.circulation(request.get("from"), request.get("to"))
    if kind == "membership":
        return ctx.reporting.membership(request.get("from"), request.get("to"))
    if kind in ("overdue-fines", "overdue_fines"):
        return ctx.reporting.overdue_fines(request.get("as_of"))
    if kind == "trend":
        return ctx.reporting.trend(
            request.get("from"), request.get("to"), compare=request.get_bool("compare")
        )
    raise errors.ImriError(
        errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND), details={"report": kind}
    )


@route("GET", "/api/reports/collection", cap="report.read")
def report_collection(ctx: Any, request: Request) -> dict[str, Any]:
    """Collection statistics."""
    return ctx.reporting.collection(
        {
            "material_type": request.get("material_type"),
            "language": request.get("language"),
            "classification": request.get("classification"),
            "status": request.get("status"),
        }
    )


@route("GET", "/api/reports/circulation", cap="report.read")
def report_circulation(ctx: Any, request: Request) -> dict[str, Any]:
    """Circulation statistics over a date range."""
    return ctx.reporting.circulation(request.get("from"), request.get("to"))


@route("GET", "/api/reports/membership", cap="report.read")
def report_membership(ctx: Any, request: Request) -> dict[str, Any]:
    """Membership statistics."""
    return ctx.reporting.membership(request.get("from"), request.get("to"))


@route("GET", "/api/reports/overdue-fines", cap="report.read")
def report_overdue_fines(ctx: Any, request: Request) -> dict[str, Any]:
    """Overdue and fines report, matching the dashboard KPI."""
    return ctx.reporting.overdue_fines(request.get("as_of"))


@route("GET", "/api/reports/trend", cap="report.read")
def report_trend(ctx: Any, request: Request) -> dict[str, Any]:
    """Monthly issued/returned series, optionally compared to the prior period."""
    return ctx.reporting.trend(
        request.get("from"), request.get("to"), compare=request.get_bool("compare")
    )


@route("GET", "/api/reports/{kind}/export", cap="export.run")
def export_report(ctx: Any, request: Request) -> dict[str, Any]:
    """Export any report as CSV or PDF."""
    kind = str(request.param("kind") or "").replace("_", "-")
    report = _build_report(ctx, kind, request)
    fmt = str(request.get("format") or "csv").lower()
    stem = "report-%s" % kind
    if fmt == "pdf":
        return ctx.reporting.export_pdf(report, stem, actor=request.actor)
    return ctx.reporting.export_csv(report, stem, actor=request.actor)


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #


@route("GET", "/api/labels/qr.svg", cap="label.read")
def label_qr(ctx: Any, request: Request) -> Raw:
    """Render any string as a QR code SVG."""
    data = str(request.get("data") or "")
    if not data:
        raise errors.ImriError(
            errors.LABEL_PAYLOAD_EMPTY, errors.status_for(errors.LABEL_PAYLOAD_EMPTY)
        )
    scale = max(1, min(40, request.get_int("scale", 8)))
    border = max(0, min(16, request.get_int("border", 4)))
    ec = str(request.get("ec") or "M").upper()
    return Raw(
        qr_svg(data, scale=scale, border=border, ec=ec),
        content_type="image/svg+xml; charset=utf-8",
    )


@route("GET", "/api/labels/code39.svg", cap="label.read")
def label_code39(ctx: Any, request: Request) -> Raw:
    """Render any string as a Code 39 barcode SVG."""
    data = str(request.get("data") or "")
    if not data:
        raise errors.ImriError(
            errors.LABEL_PAYLOAD_EMPTY, errors.status_for(errors.LABEL_PAYLOAD_EMPTY)
        )
    scale = max(1, min(10, request.get_int("scale", 2)))
    height = max(10, min(400, request.get_int("height", 60)))
    caption = request.get_bool("caption", True)
    return Raw(
        code39_svg(data, scale=scale, height=height, caption=caption),
        content_type="image/svg+xml; charset=utf-8",
    )


@route("GET", "/api/labels/book/{copy_id}.svg", cap="label.read")
def label_book(ctx: Any, request: Request) -> Raw:
    """Render a full book label (title, accession, QR and Code 39)."""
    copy = ctx.catalog.get_copy(request.param_int("copy_id"))
    if copy is None:
        raise errors.ImriError(errors.COPY_NOT_FOUND, errors.status_for(errors.COPY_NOT_FOUND))
    book = ctx.db.one("SELECT * FROM books WHERE id = ?", (int(copy["book_id"]),))
    if book is None:
        raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
    options = {
        "scale": max(1, min(12, request.get_int("scale", 4))),
        "code39": request.get_bool("code39", True),
    }
    return Raw(
        ctx.labels.book_label_svg(copy, book, options),
        content_type="image/svg+xml; charset=utf-8",
    )


@route("POST", "/api/labels/sheet", cap="label.read")
def label_sheet(ctx: Any, request: Request) -> dict[str, Any]:
    """Build a printable sticker sheet and write it into ``data/exports``."""
    copy_ids = request.field("copy_ids") or []
    if not isinstance(copy_ids, list) or not copy_ids:
        raise errors.ImriError(
            errors.LABEL_PAYLOAD_EMPTY, errors.status_for(errors.LABEL_PAYLOAD_EMPTY)
        )
    layout = str(request.field("layout") or "3x8")
    paper = str(request.field("paper") or "A4")
    cut_guides = request.field("cut_guides")
    options = {"cut_guides": True if cut_guides is None else config.to_bool(cut_guides)}

    items: list[dict[str, Any]] = []
    for raw_id in copy_ids[:500]:
        copy = ctx.catalog.get_copy(config.to_int(raw_id, -1))
        if copy is None:
            continue
        book = ctx.db.one("SELECT * FROM books WHERE id = ?", (int(copy["book_id"]),))
        if book is None:
            continue
        items.append({"copy": copy, "book": book})
    if not items:
        raise errors.ImriError(
            errors.LABEL_PAYLOAD_EMPTY, errors.status_for(errors.LABEL_PAYLOAD_EMPTY)
        )

    document = sheet_svg(items, layout=layout, paper=paper, options=options)
    stamp = config.now_utc().replace("-", "").replace(":", "").replace("T", "-")[:15]
    filename = "labels-%s.html" % stamp
    directory = config.exports_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(document, encoding="utf-8")

    columns, rows = (3, 8)
    parts = layout.lower().split("x")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        columns, rows = int(parts[0]), int(parts[1])
    per_page = max(1, columns * rows)
    pages = (len(items) + per_page - 1) // per_page

    return {
        "path": str(path),
        "filename": filename,
        "pages": pages,
        "size": os.path.getsize(path),
        "count": len(items),
    }


# --------------------------------------------------------------------------- #
# Backup
# --------------------------------------------------------------------------- #


@route("GET", "/api/backup", cap="backup.create")
def list_backups(ctx: Any, request: Request) -> dict[str, Any]:
    """Return every backup archive, newest first."""
    return {"items": ctx.backup.list(), "dir": str(config.backups_dir())}


@route("POST", "/api/backup", cap="backup.create")
def create_backup(ctx: Any, request: Request) -> dict[str, Any]:
    """Create a verified backup archive."""
    return ctx.backup.create(str(request.field("label") or ""), actor=request.actor)


@route("GET", "/api/backup/{name}/verify", cap="backup.create")
def verify_backup(ctx: Any, request: Request) -> dict[str, Any]:
    """Re-hash every archived file and report mismatches."""
    return ctx.backup.verify(str(request.param("name") or ""))


@route("POST", "/api/backup/restore", cap="backup.restore")
def restore_backup(ctx: Any, request: Request) -> dict[str, Any]:
    """Restore from a backup, after verifying it and snapshotting the current data."""
    name = str(request.field("name") or "")
    confirm = config.to_bool(request.field("confirm"))
    return ctx.backup.restore(name, actor=request.actor, confirm=confirm)


@route("DELETE", "/api/backup/{name}", cap="backup.restore")
def delete_backup(ctx: Any, request: Request) -> dict[str, Any]:
    """Delete one backup archive."""
    ctx.backup.delete(str(request.param("name") or ""), actor=request.actor)
    return {"deleted": True}
