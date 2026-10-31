"""Reporting: five report builders, the dashboard aggregate, CSV and PDF.

Two things here are worth reading before changing anything:

* :meth:`ReportingService.dashboard` is **one** handler issuing ~10 aggregate
  queries. No per-row loops, no N+1 — the dashboard has a 400 ms budget at
  10 000 titles (ARCHITECTURE §10).
* :class:`PdfWriter` is a real PDF 1.4 writer built on :mod:`zlib` alone. It
  emits a valid cross-reference table, Helvetica base-14 fonts, a header with
  the school name and report title, and "Page N of M" footers.
"""

from __future__ import annotations

import csv
import os
import zlib
from typing import Any, Iterable, Mapping, Sequence

from . import config, errors
from .config import get_logger
# The formula-injection guard has one implementation, in the module that owns
# every import/export format. Reports reuse it so a CSV opened from a report
# behaves exactly like one opened from the catalogue export.
from .transfer import safe_cell

_log = get_logger("reporting")


def _month_key(value: Any) -> str:
    """Return the ``YYYY-MM`` prefix of an ISO date string."""
    text = str(value or "")
    return text[:7] if len(text) >= 7 else ""


def month_range(start: str, end: str) -> list[str]:
    """Return every ``YYYY-MM`` between two ``YYYY-MM-DD`` dates inclusive."""
    first = config.parse_date(start)
    last = config.parse_date(end)
    if first is None or last is None or last < first:
        return []
    months: list[str] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append("%04d-%02d" % (year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
        if len(months) > 240:  # hard stop; a report is never 20 years long
            break
    return months


def default_range(days: int = 30) -> tuple[str, str]:
    """Return ``(from, to)`` covering the last ``days`` days, inclusive of today."""
    end = config.today_local()
    start = config.date_add(end, -(int(days) - 1))
    return start, end


class PdfWriter:
    """Minimal, valid PDF 1.4 writer (A4, Helvetica, paginated)."""

    PAGE_WIDTH = 595.0
    PAGE_HEIGHT = 842.0
    MARGIN = 50.0
    BODY_TOP_OFFSET = 78.0
    BODY_BOTTOM_OFFSET = 52.0
    LEADING = 14.0
    MAX_CHARS = 118

    def __init__(self, school_name: str = "", subtitle: str = "") -> None:
        self.school_name = school_name or ""
        self.subtitle = subtitle or ""

    @staticmethod
    def _escape(text: Any) -> str:
        """Escape a string for a PDF literal string."""
        return (
            str(text)
            .replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("\r", " ")
            .replace("\n", " ")
        )

    def _truncate(self, text: str) -> str:
        """Clamp a line so it cannot run past the right margin."""
        clean = text.replace("\t", "    ")
        if len(clean) > self.MAX_CHARS:
            return clean[: self.MAX_CHARS - 1] + "\u2026"
        return clean

    def _normalise(self, blocks: Iterable[Any]) -> list[tuple[float, bool, str]]:
        """Flatten report blocks into ``(size, bold, text)`` triples."""
        lines: list[tuple[float, bool, str]] = []
        for block in blocks or []:
            if isinstance(block, Mapping):
                lines.append(
                    (
                        float(block.get("size", 10)),
                        bool(block.get("bold", False)),
                        self._truncate(str(block.get("text", ""))),
                    )
                )
            elif isinstance(block, (list, tuple)):
                lines.append(
                    (float(block[0]) if block else 10.0, False, self._truncate(str(block[-1])))
                )
            else:
                lines.append((10.0, False, self._truncate(str(block))))
        return lines

    def _paginate(
        self, lines: Sequence[tuple[float, bool, str]]
    ) -> list[list[tuple[float, float, bool, str]]]:
        """Split lines into pages, each entry ``(y, size, bold, text)``."""
        body_top = self.PAGE_HEIGHT - self.MARGIN - self.BODY_TOP_OFFSET
        body_bottom = self.MARGIN + self.BODY_BOTTOM_OFFSET
        pages: list[list[tuple[float, float, bool, str]]] = []
        current: list[tuple[float, float, bool, str]] = []
        y = body_top
        for size, bold, text in lines:
            step = self.LEADING if size <= 10 else size + 6.0
            if y - step < body_bottom and current:
                pages.append(current)
                current = []
                y = body_top
            y -= step
            current.append((y, size, bold, text))
        if current or not pages:
            pages.append(current)
        return pages

    def write(
        self,
        title: str,
        blocks: Iterable[Any],
        path: str | os.PathLike[str],
        meta: Mapping[str, Any] | None = None,
    ) -> str:
        """Render ``blocks`` to a PDF file and return its path."""
        info = dict(meta or {})
        school = str(info.get("school_name") or self.school_name or config.APP_NAME)
        generated = str(info.get("generated") or config.today_local())
        lines = self._normalise(blocks)
        pages = self._paginate(lines)
        total_pages = len(pages)

        # Object numbering: 1 catalog, 2 pages, 3 Helvetica, 4 Helvetica-Bold,
        # then two objects (page, content) per page.
        page_object_numbers: list[int] = []
        content_object_numbers: list[int] = []
        next_number = 5
        for _ in range(total_pages):
            page_object_numbers.append(next_number)
            next_number += 1
            content_object_numbers.append(next_number)
            next_number += 1

        objects: list[bytes | None] = [None] * next_number
        objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
        kids = " ".join("%d 0 R" % number for number in page_object_numbers)
        objects[2] = (
            "<< /Type /Pages /Count %d /Kids [%s] >>" % (total_pages, kids)
        ).encode("latin-1")
        objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        objects[4] = (
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
        )

        for index, page_lines in enumerate(pages):
            page_number = index + 1
            commands: list[str] = []

            # --- header -----------------------------------------------------
            header_y = self.PAGE_HEIGHT - self.MARGIN - 6
            commands.append(
                "BT /F2 13 Tf 1 0 0 1 %.1f %.1f Tm (%s) Tj ET"
                % (self.MARGIN, header_y, self._escape(title))
            )
            commands.append(
                "BT /F1 9 Tf 1 0 0 1 %.1f %.1f Tm (%s) Tj ET"
                % (
                    self.MARGIN,
                    header_y - 15,
                    self._escape("%s  ·  generated %s" % (school, generated)),
                )
            )
            commands.append(
                "0.85 w 0.6 G %.1f %.1f m %.1f %.1f l S 0 G"
                % (
                    self.MARGIN,
                    header_y - 24,
                    self.PAGE_WIDTH - self.MARGIN,
                    header_y - 24,
                )
            )

            # --- body -------------------------------------------------------
            for y, size, bold, text in page_lines:
                font = "F2" if bold else "F1"
                commands.append(
                    "BT /%s %.1f Tf 1 0 0 1 %.1f %.1f Tm (%s) Tj ET"
                    % (font, size, self.MARGIN, y, self._escape(text))
                )

            # --- footer -----------------------------------------------------
            commands.append(
                "BT /F1 8 Tf 1 0 0 1 %.1f %.1f Tm (%s) Tj ET"
                % (
                    self.MARGIN,
                    self.MARGIN - 18,
                    self._escape("%s  ·  %s" % (config.APP_NAME, config.APP_VERSION)),
                )
            )
            footer = "Page %d of %d" % (page_number, total_pages)
            commands.append(
                "BT /F1 8 Tf 1 0 0 1 %.1f %.1f Tm (%s) Tj ET"
                % (
                    self.PAGE_WIDTH - self.MARGIN - 7.0 * len(footer),
                    self.MARGIN - 18,
                    self._escape(footer),
                )
            )

            stream = "\n".join(commands).encode("latin-1", "replace")
            compressed = zlib.compress(stream, 9)
            content_number = content_object_numbers[index]
            objects[content_number] = (
                b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(compressed)
            ) + compressed + b"\nendstream"
            objects[page_object_numbers[index]] = (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.0f %.0f] "
                "/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>"
                % (self.PAGE_WIDTH, self.PAGE_HEIGHT, content_number)
            ).encode("latin-1")

        buffer = bytearray()
        buffer += b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        offsets = [0] * len(objects)
        for number in range(1, len(objects)):
            payload = objects[number]
            if payload is None:  # pragma: no cover - defensive
                payload = b"null"
            offsets[number] = len(buffer)
            buffer += ("%d 0 obj\n" % number).encode("latin-1")
            buffer += payload
            buffer += b"\nendobj\n"

        xref_position = len(buffer)
        buffer += ("xref\n0 %d\n" % len(objects)).encode("latin-1")
        buffer += b"0000000000 65535 f \n"
        for number in range(1, len(objects)):
            buffer += ("%010d 00000 n \n" % offsets[number]).encode("latin-1")
        buffer += (
            "trailer\n<< /Size %d /Root 1 0 R /Producer (%s %s) >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects), config.APP_NAME, config.APP_VERSION, xref_position)
        ).encode("latin-1")

        target = str(path)
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(buffer)
        return target


class ReportingService:
    """The five reports, the dashboard aggregate and the file exporters."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # -- helpers ---------------------------------------------------------- #
    def _school_name(self) -> str:
        return self.db.get_setting("school_name", config.DEFAULT_SETTINGS["school_name"])

    def _currency(self) -> str:
        return self.db.get_setting("currency", "RM") or "RM"

    def _export_path(self, stem: str, suffix: str) -> tuple[str, str]:
        """Return ``(absolute_path, filename)`` inside ``data/exports``."""
        stamp = config.now_utc().replace("-", "").replace(":", "").replace("T", "-")[:15]
        safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:60]
        filename = "%s-%s.%s" % (safe_stem, stamp, suffix)
        directory = config.exports_dir()
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory / filename), filename

    # -- 1. collection ---------------------------------------------------- #
    def collection(self, filters: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Collection statistics: titles, copies, value and four breakdowns.

        One statement, one pass. The previous version ran two correlated
        ``COUNT`` sub-queries per title over 200,000 titles and then read the
        same rows three more times to build the breakdowns — 9.9 s against a
        900 ms budget. Here:

        * ``books.copies`` supplies the copy total. It is the denormalised
          rollup that :func:`imri.catalog.refresh_book_rollup` is the only
          writer of, so it is already the number the rest of the UI shows.
        * Only the *available* count still needs ``book_copies``, so exactly
          one correlated aggregate remains instead of two.
        * All four breakdowns are accumulated from the same streamed rows, so
          ``by_material`` no longer costs a second scan with its own sub-query.
        * ``iter_tuples`` streams plain tuples instead of materialising 200,000
          dicts, and is read positionally: ``sqlite3.Row``'s named access is a
          linear scan of the column list per lookup, which cost 1.6 s here.
        """
        criteria = dict(filters or {})
        clauses: list[str] = []
        params: list[Any] = []
        for column, key in (
            ("b.material_type", "material_type"),
            ("b.language", "language"),
            ("b.classification", "classification"),
            ("b.status", "status"),
        ):
            value = config.clean_text(criteria.get(key))
            if value and value.lower() != "all":
                clauses.append("%s = ?" % column)
                params.append(value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        # Column order is mirrored by the positional reads below.
        sql = (
            "SELECT b.accession_no, b.title, b.authors, b.material_type, b.language, "
            "       b.dewey, b.call_no, b.status, b.copies, b.price, "
            "       (SELECT COUNT(*) FROM book_copies c WHERE c.book_id = b.id "
            "        AND c.status = 'available') AS available_copies "
            "FROM books b" + where + " ORDER BY b.dewey, b.title COLLATE NOCASE"
        )

        money = config.money
        table: list[list[Any]] = []
        append = table.append
        total_copies = 0
        total_value = 0.0
        by_material: dict[str, list[int]] = {}
        by_language: dict[str, int] = {}
        by_status: dict[str, int] = {}
        buckets: dict[str, int] = {}
        # A 200,000-title catalogue holds about a thousand distinct Dewey
        # numbers, so the 3-digit shelf bucket is worth computing once per
        # distinct value instead of splitting and reformatting every row.
        dewey_buckets: dict[str, str] = {}

        for row in self.db.iter_tuples(sql, params):
            (
                accession_no,
                title,
                authors,
                material,
                language,
                dewey,
                call_no,
                shelf,
                copies,
                price,
                available,
            ) = row
            copies = int(copies or 0)
            value = money((price or 0) * copies)
            total_copies += copies
            total_value += value
            append(
                [
                    accession_no,
                    title,
                    authors or "",
                    material,
                    dewey or "",
                    call_no or "",
                    shelf,
                    copies,
                    int(available or 0),
                    value,
                ]
            )

            slot = by_material.get(material)
            if slot is None:
                by_material[material] = [1, copies]
            else:
                slot[0] += 1
                slot[1] += copies
            language = language or ""
            by_language[language] = by_language.get(language, 0) + 1
            shelf = shelf or ""
            by_status[shelf] = by_status.get(shelf, 0) + 1
            bucket = dewey_buckets.get(dewey)
            if bucket is None:
                whole = (dewey or "").split(".")[0]
                bucket = ("%03d" % int(whole)) if whole.isdigit() else "000"
                dewey_buckets[dewey] = bucket
            buckets[bucket] = buckets.get(bucket, 0) + 1

        return {
            "title": "Collection report",
            "columns": [
                "accession_no",
                "title",
                "authors",
                "material_type",
                "dewey",
                "call_no",
                "status",
                "copies",
                "available",
                "value",
            ],
            "rows": table,
            "totals": {
                "titles": len(table),
                "copies": total_copies,
                "value": config.money(total_value),
            },
            "by_material": [
                {"key": key, "titles": counts[0], "copies": counts[1]}
                for key, counts in sorted(
                    by_material.items(), key=lambda item: (-item[1][0], item[0])
                )
            ],
            "by_language": [
                {"key": key, "titles": count}
                for key, count in sorted(by_language.items(), key=lambda item: (-item[1], item[0]))
            ],
            "by_dewey": [{"key": key, "titles": buckets[key]} for key in sorted(buckets)],
            "by_status": [
                {"key": key, "titles": count}
                for key, count in sorted(by_status.items(), key=lambda item: (-item[1], item[0]))
            ],
        }

    # -- 2. circulation --------------------------------------------------- #
    def circulation(self, from_date: Any = None, to_date: Any = None) -> dict[str, Any]:
        """Circulation statistics over a date range."""
        default_from, default_to = default_range(30)
        start = (config.clean_text(from_date) or default_from)[:10]
        end = (config.clean_text(to_date) or default_to)[:10]

        # ~20,000 loans in a busy month. ``iter_tuples`` avoids materialising
        # 20,000 intermediate dicts; the row list below is the payload and
        # cannot be avoided. Column order is mirrored by the positional read.
        issued_sql = (
            "SELECT l.issue_date, l.due_date, l.return_date, l.accession_no, "
            "       b.title, l.member_code, m.name, l.status "
            "FROM loans l LEFT JOIN books b ON b.id = l.book_id "
            "LEFT JOIN members m ON m.id = l.member_id "
            "WHERE l.issue_date BETWEEN ? AND ? ORDER BY l.issue_date, l.id"
        )
        rows: list[list[Any]] = []
        append = rows.append
        for row in self.db.iter_tuples(issued_sql, (start, end)):
            append(
                [
                    row[0],
                    row[1],
                    row[2] or "",
                    row[3],
                    row[4] or "",
                    row[5],
                    row[6] or "",
                    row[7],
                ]
            )
        issued_count = len(rows)

        returned = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans WHERE return_date BETWEEN ? AND ?",
                (start, end),
                default=0,
            )
        )
        average = self.db.scalar(
            "SELECT AVG(julianday(return_date) - julianday(issue_date)) FROM loans "
            "WHERE return_date IS NOT NULL AND return_date BETWEEN ? AND ?",
            (start, end),
            default=0,
        )
        # Both leaderboards are the same issue_date range grouped by a single
        # key; ``ix_loans_issue_book`` / ``ix_loans_issue_member`` make the
        # grouping pass index-only instead of re-reading the range from the
        # table twice more.
        top_titles = self.db.query(
            "SELECT b.title AS title, COUNT(*) AS count FROM loans l "
            "LEFT JOIN books b ON b.id = l.book_id "
            "WHERE l.issue_date BETWEEN ? AND ? GROUP BY l.book_id "
            "ORDER BY count DESC, title LIMIT 10",
            (start, end),
        )
        top_borrowers = self.db.query(
            "SELECT m.name AS name, m.member_code AS member_code, COUNT(*) AS count "
            "FROM loans l LEFT JOIN members m ON m.id = l.member_id "
            "WHERE l.issue_date BETWEEN ? AND ? GROUP BY l.member_id "
            "ORDER BY count DESC, name LIMIT 10",
            (start, end),
        )

        return {
            "title": "Circulation report",
            "from": start,
            "to": end,
            "columns": [
                "issue_date",
                "due_date",
                "return_date",
                "accession_no",
                "title",
                "member_code",
                "member_name",
                "status",
            ],
            "rows": rows,
            "summary": {
                "issued": issued_count,
                "returned": returned,
                "avg_loan_days": round(float(average or 0), 1),
            },
            "top_titles": top_titles,
            "top_borrowers": top_borrowers,
        }

    # -- 3. membership ---------------------------------------------------- #
    def membership(self, from_date: Any = None, to_date: Any = None) -> dict[str, Any]:
        """Membership statistics: counts, breakdowns and new members per month."""
        default_from, default_to = default_range(365)
        start = (config.clean_text(from_date) or default_from)[:10]
        end = (config.clean_text(to_date) or default_to)[:10]

        rows = self.db.query(
            "SELECT member_code, name, member_type, form, class_name, status, "
            "       membership_date, expiry_date, phone, email FROM members "
            "WHERE membership_date <= ? ORDER BY membership_date DESC, name COLLATE NOCASE",
            (end,),
        )
        table = [
            [
                row["member_code"],
                row["name"],
                row["member_type"],
                row["form"] or "",
                row["class_name"] or "",
                row["status"],
                row["membership_date"],
                row["expiry_date"] or "",
            ]
            for row in rows
        ]
        by_form = self.db.query(
            "SELECT COALESCE(NULLIF(form, ''), '-') AS key, COUNT(*) AS count "
            "FROM members GROUP BY key ORDER BY count DESC"
        )
        by_class = self.db.query(
            "SELECT COALESCE(NULLIF(class_name, ''), '-') AS key, COUNT(*) AS count "
            "FROM members GROUP BY key ORDER BY count DESC LIMIT 30"
        )
        by_status = self.db.query(
            "SELECT status AS key, COUNT(*) AS count FROM members GROUP BY status ORDER BY count DESC"
        )
        by_type = self.db.query(
            "SELECT member_type AS key, COUNT(*) AS count FROM members "
            "GROUP BY member_type ORDER BY count DESC"
        )
        new_per_month = self.db.query(
            "SELECT substr(membership_date, 1, 7) AS month, COUNT(*) AS count "
            "FROM members WHERE membership_date BETWEEN ? AND ? "
            "GROUP BY month ORDER BY month",
            (start, end),
        )
        return {
            "title": "Membership report",
            "from": start,
            "to": end,
            "columns": [
                "member_code",
                "name",
                "member_type",
                "form",
                "class_name",
                "status",
                "membership_date",
                "expiry_date",
            ],
            "rows": table,
            "by_form": by_form,
            "by_class": by_class,
            "by_status": by_status,
            "by_type": by_type,
            "new_per_month": new_per_month,
        }

    # -- 4. overdue and fines --------------------------------------------- #
    def overdue_fines(self, as_of: Any = None) -> dict[str, Any]:
        """Overdue and fine totals per member — matches the dashboard KPI."""
        reference = (config.clean_text(as_of) or config.today_local())[:10]
        fine_per_day = config.money(self.db.get_setting_float("fine_per_day", 0.20))

        rows = self.db.query(
            "SELECT m.id, m.member_code, m.name, m.class_name, m.phone, "
            "       COUNT(l.id) AS loans, "
            "       COALESCE(SUM(CASE WHEN l.status = 'on_loan' THEN "
            "         MAX(0, julianday(?) - julianday(l.due_date)) ELSE 0 END), 0) AS days_late, "
            "       COALESCE(SUM(l.fine_amount), 0) AS fine, "
            "       COALESCE(SUM(l.paid_amount), 0) AS paid, "
            "       COALESCE(SUM(l.waived_amount), 0) AS waived "
            "FROM loans l JOIN members m ON m.id = l.member_id "
            "WHERE (l.status = 'on_loan' AND l.due_date < ?) "
            "   OR l.fine_amount > 0 "
            "GROUP BY m.id ORDER BY fine DESC, m.name COLLATE NOCASE",
            (reference, reference),
        )

        table: list[list[Any]] = []
        totals = {"members": 0, "loans": 0, "fine": 0.0, "paid": 0.0, "waived": 0.0, "balance": 0.0}
        detail: list[dict[str, Any]] = []
        for row in rows:
            fine = config.money(row["fine"] or 0)
            paid = config.money(row["paid"] or 0)
            waived = config.money(row["waived"] or 0)
            accrued = config.money(float(row["days_late"] or 0) * fine_per_day)
            balance = config.money(max(0.0, fine - paid - waived))
            if balance <= 0.004 and int(row["loans"] or 0) == 0:
                continue
            totals["members"] += 1
            totals["loans"] += int(row["loans"] or 0)
            totals["fine"] += fine
            totals["paid"] += paid
            totals["waived"] += waived
            totals["balance"] += balance
            table.append(
                [
                    row["member_code"],
                    row["name"],
                    row["class_name"] or "",
                    int(row["loans"] or 0),
                    fine,
                    paid,
                    waived,
                    balance,
                ]
            )
            detail.append(
                {
                    "member_code": row["member_code"],
                    "name": row["name"],
                    "class_name": row["class_name"],
                    "loans": int(row["loans"] or 0),
                    "fine": fine,
                    "paid": paid,
                    "waived": waived,
                    "balance": balance,
                    "accrued": accrued,
                    "phone": row["phone"],
                }
            )

        return {
            "title": "Overdue and fines report",
            "as_of": reference,
            "fine_per_day": fine_per_day,
            "columns": [
                "member_code",
                "name",
                "class_name",
                "loans",
                "fine",
                "paid",
                "waived",
                "balance",
            ],
            "rows": table,
            "items": detail,
            "totals": {
                "members": totals["members"],
                "loans": totals["loans"],
                "fine": config.money(totals["fine"]),
                "paid": config.money(totals["paid"]),
                "waived": config.money(totals["waived"]),
                "balance": config.money(totals["balance"]),
            },
        }

    # -- 5. trend --------------------------------------------------------- #
    def trend(
        self, from_date: Any = None, to_date: Any = None, compare: bool = False
    ) -> dict[str, Any]:
        """Monthly issued/returned series, optionally against the prior period."""
        default_from, default_to = default_range(365)
        start = (config.clean_text(from_date) or default_from)[:10]
        end = (config.clean_text(to_date) or default_to)[:10]
        months = month_range(start, end)

        issued_rows = self.db.query(
            "SELECT substr(issue_date, 1, 7) AS month, COUNT(*) AS count FROM loans "
            "WHERE issue_date BETWEEN ? AND ? GROUP BY month",
            (start, end),
        )
        returned_rows = self.db.query(
            "SELECT substr(return_date, 1, 7) AS month, COUNT(*) AS count FROM loans "
            "WHERE return_date IS NOT NULL AND return_date BETWEEN ? AND ? GROUP BY month",
            (start, end),
        )
        issued_map = {row["month"]: int(row["count"] or 0) for row in issued_rows}
        returned_map = {row["month"]: int(row["count"] or 0) for row in returned_rows}
        series = [
            {
                "month": month,
                "issued": issued_map.get(month, 0),
                "returned": returned_map.get(month, 0),
            }
            for month in months
        ]

        previous: dict[str, Any] = {}
        deltas: dict[str, Any] = {}
        if compare and months:
            span = len(months)
            previous_end = config.date_add(start, -1)
            previous_start = config.date_add(previous_end, -(span * 30 - 1))
            prev_issued = int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM loans WHERE issue_date BETWEEN ? AND ?",
                    (previous_start, previous_end),
                    default=0,
                )
            )
            prev_returned = int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM loans WHERE return_date BETWEEN ? AND ?",
                    (previous_start, previous_end),
                    default=0,
                )
            )
            previous = {
                "from": previous_start,
                "to": previous_end,
                "issued": prev_issued,
                "returned": prev_returned,
            }
            current_issued = sum(item["issued"] for item in series)
            current_returned = sum(item["returned"] for item in series)
            deltas = {
                "issued": current_issued - prev_issued,
                "returned": current_returned - prev_returned,
                "issued_pct": round(
                    ((current_issued - prev_issued) / prev_issued * 100.0) if prev_issued else 0.0,
                    1,
                ),
                "returned_pct": round(
                    ((current_returned - prev_returned) / prev_returned * 100.0)
                    if prev_returned
                    else 0.0,
                    1,
                ),
            }

        return {
            "title": "Circulation trend",
            "from": start,
            "to": end,
            "series": series,
            "previous": previous,
            "deltas": deltas,
        }

    # -- dashboard helpers ------------------------------------------------ #
    def _members_over_limit(self, max_loans: int) -> list[dict[str, Any]]:
        """Return the ten members holding the most open loans, at or above ``max_loans``.

        Aggregated from ``loans`` alone and joined to ``members`` only for the
        ten rows that survive the ``LIMIT``. Joining first made SQLite group all
        25,000 live loans through a temp B-tree *and* re-read ``members`` for
        every one of them.
        """
        ranked = self.db.query(
            "SELECT l.member_id AS id, COUNT(*) AS loans FROM loans l "
            "WHERE l.status = 'on_loan' GROUP BY l.member_id "
            "HAVING COUNT(*) >= ? ORDER BY loans DESC, l.member_id LIMIT 10",
            (int(max_loans),),
        )
        if not ranked:
            return []
        ids = [int(row["id"]) for row in ranked]
        placeholders = ",".join("?" for _ in ids)
        labels = {
            int(row["id"]): row
            for row in self.db.query(
                "SELECT id, member_code, name FROM members WHERE id IN (%s)" % placeholders,
                ids,
            )
        }
        return [
            {
                "id": int(row["id"]),
                "member_code": labels.get(int(row["id"]), {}).get("member_code", ""),
                "name": labels.get(int(row["id"]), {}).get("name", ""),
                "loans": int(row["loans"] or 0),
            }
            for row in ranked
        ]

    # -- dashboard -------------------------------------------------------- #
    def dashboard(self, as_of: Any = None) -> dict[str, Any]:
        """Six KPI cards, two panels, two chart series and an alerts strip.

        Roughly ten aggregate queries, no per-row loops (DSH-01…05).
        """
        reference = (config.clean_text(as_of) or config.today_local())[:10]
        cutoff = config.date_add(reference, -30)
        cutoff60 = config.date_add(reference, -60)
        fine_per_day = config.money(self.db.get_setting_float("fine_per_day", 0.20))
        max_loans = self.db.get_setting_int("max_loans", 3)

        titles = int(self.db.scalar("SELECT COUNT(*) FROM books", default=0))
        titles_delta = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM books WHERE created_at >= ?", (cutoff,), default=0
            )
        )
        copies = int(self.db.scalar("SELECT COUNT(*) FROM book_copies", default=0))
        copies_delta = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM book_copies WHERE created_at >= ?", (cutoff,), default=0
            )
        )
        active_members = int(
            self.db.scalar("SELECT COUNT(*) FROM members WHERE status = 'active'", default=0)
        )
        members_delta = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM members WHERE membership_date >= ?", (cutoff,), default=0
            )
        )
        on_loan = int(
            self.db.scalar("SELECT COUNT(*) FROM loans WHERE status = 'on_loan'", default=0)
        )
        issued_30 = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans WHERE issue_date BETWEEN ? AND ?",
                (cutoff, reference),
                default=0,
            )
        )
        returned_30 = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans WHERE return_date BETWEEN ? AND ?",
                (cutoff, reference),
                default=0,
            )
        )
        overdue = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans WHERE status = 'on_loan' AND due_date < ?",
                (reference,),
                default=0,
            )
        )
        overdue_then = int(
            self.db.scalar(
                "SELECT COUNT(*) FROM loans WHERE issue_date <= ? AND due_date < ? "
                "AND (return_date IS NULL OR return_date > ?)",
                (cutoff, cutoff, cutoff),
                default=0,
            )
        )
        fines_outstanding = config.money(
            self.db.scalar(
                "SELECT COALESCE(SUM(CASE WHEN fine_amount - paid_amount - waived_amount > 0 "
                "THEN fine_amount - paid_amount - waived_amount ELSE 0 END), 0) FROM loans",
                default=0,
            )
        )
        fines_30 = config.money(
            self.db.scalar(
                "SELECT COALESCE(SUM(fine_amount), 0) FROM loans WHERE due_date >= ?",
                (cutoff,),
                default=0,
            )
        )
        del cutoff60

        due_today = self.db.query(
            "SELECT l.id, l.due_date, l.accession_no, l.member_code, "
            "       b.title AS book_title, m.name AS member_name "
            "FROM loans l LEFT JOIN books b ON b.id = l.book_id "
            "LEFT JOIN members m ON m.id = l.member_id "
            "WHERE l.status = 'on_loan' AND l.due_date = ? "
            "ORDER BY l.id LIMIT 5",
            (reference,),
        )
        overdue_rows = self.db.query(
            "SELECT l.id, l.due_date, l.accession_no, l.member_code, "
            "       b.title AS book_title, m.name AS member_name, m.phone, "
            "       CAST(julianday(?) - julianday(l.due_date) AS INTEGER) AS overdue_days "
            "FROM loans l LEFT JOIN books b ON b.id = l.book_id "
            "LEFT JOIN members m ON m.id = l.member_id "
            "WHERE l.status = 'on_loan' AND l.due_date < ? "
            "ORDER BY l.due_date LIMIT 5",
            (reference, reference),
        )
        for row in overdue_rows:
            row["overdue_days"] = max(0, int(row.get("overdue_days") or 0))
            row["accrued_fine"] = config.money(row["overdue_days"] * fine_per_day)

        chart_start = config.date_add(reference[:8] + "01", -330)
        loans_by_month_rows = self.db.query(
            "SELECT substr(issue_date, 1, 7) AS month, COUNT(*) AS count FROM loans "
            "WHERE issue_date >= ? AND issue_date <= ? GROUP BY month ORDER BY month",
            (chart_start, reference),
        )
        counts = {row["month"]: int(row["count"] or 0) for row in loans_by_month_rows}
        months = month_range(chart_start, reference)[-12:]
        loans_by_month = [{"month": month, "count": counts.get(month, 0)} for month in months]

        collection_by_type = self.db.query(
            "SELECT material_type AS type, COUNT(*) AS count FROM books "
            "GROUP BY material_type ORDER BY count DESC"
        )

        no_available_with_holds = self.db.query(
            "SELECT b.id, b.accession_no, b.title, COUNT(r.id) AS holds FROM books b "
            "JOIN reservations r ON r.book_id = b.id AND r.status IN ('open','ready') "
            "WHERE NOT EXISTS (SELECT 1 FROM book_copies c WHERE c.book_id = b.id "
            "                  AND c.status = 'available') "
            "GROUP BY b.id ORDER BY holds DESC LIMIT 10"
        )
        over_limit = self._members_over_limit(max_loans)
        stale_holds = self.db.query(
            "SELECT r.id, r.accession_no, r.expires_at, m.member_code FROM reservations r "
            "JOIN members m ON m.id = r.member_id "
            "WHERE r.status = 'ready' AND r.expires_at IS NOT NULL AND r.expires_at < ? "
            "ORDER BY r.expires_at LIMIT 10",
            (reference,),
        )

        alerts = [
            {
                "kind": "no_available_with_holds",
                "count": len(no_available_with_holds),
                "items": no_available_with_holds,
            },
            {
                "kind": "over_loan_limit",
                "count": len(over_limit),
                "items": over_limit,
            },
            {
                "kind": "stale_holds",
                "count": len(stale_holds),
                "items": stale_holds,
            },
        ]

        return {
            "as_of": reference,
            "kpis": {
                "titles": {"value": titles, "delta_30d": titles_delta},
                "copies": {"value": copies, "delta_30d": copies_delta},
                "active_members": {"value": active_members, "delta_30d": members_delta},
                "on_loan": {"value": on_loan, "delta_30d": issued_30 - returned_30},
                "overdue": {"value": overdue, "delta_30d": overdue - overdue_then},
                "fines_outstanding": {
                    "value": fines_outstanding,
                    "delta_30d": fines_30,
                    "currency": self._currency(),
                },
            },
            "due_today": due_today,
            "overdue": overdue_rows,
            "charts": {
                "loans_by_month": loans_by_month,
                "collection_by_type": collection_by_type,
            },
            "alerts": alerts,
        }

    # -- exporters -------------------------------------------------------- #
    def to_csv(self, report: Mapping[str, Any], path: str | os.PathLike[str]) -> str:
        """Write a report to CSV with a UTF-8 BOM and the injection guard."""
        target = str(path)
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        columns = list(report.get("columns") or [])
        rows = list(report.get("rows") or [])
        with open(target, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([safe_cell(str(report.get("title") or "Report"))])
            if report.get("from") or report.get("to"):
                writer.writerow(
                    [
                        "period",
                        "%s .. %s" % (report.get("from", ""), report.get("to", "")),
                    ]
                )
            writer.writerow([safe_cell(column) for column in columns])
            for row in rows:
                writer.writerow([safe_cell(value) for value in row])
        return target

    def to_pdf(
        self,
        report: Mapping[str, Any],
        path: str | os.PathLike[str],
        meta: Mapping[str, Any] | None = None,
    ) -> str:
        """Write a report to PDF using the built-in writer."""
        columns = [str(c) for c in (report.get("columns") or [])]
        rows = list(report.get("rows") or [])
        blocks: list[dict[str, Any]] = []

        subtitle_bits: list[str] = []
        if report.get("from") or report.get("to"):
            subtitle_bits.append("Period %s to %s" % (report.get("from", ""), report.get("to", "")))
        if report.get("as_of"):
            subtitle_bits.append("As of %s" % report.get("as_of"))
        if subtitle_bits:
            blocks.append({"text": "  ·  ".join(subtitle_bits), "size": 9})
            blocks.append({"text": "", "size": 9})

        totals = report.get("totals") or {}
        if totals:
            summary = ", ".join("%s: %s" % (key, value) for key, value in totals.items())
            blocks.append({"text": summary, "size": 9})
            blocks.append({"text": "", "size": 9})

        if columns:
            blocks.append({"text": " | ".join(columns), "size": 9, "bold": True})
            blocks.append({"text": "-" * 110, "size": 9})
            for row in rows:
                blocks.append(
                    {"text": " | ".join("" if v is None else str(v) for v in row), "size": 9}
                )
            if not rows:
                blocks.append({"text": "No rows matched this report.", "size": 9})
        else:
            blocks.append({"text": "This report has no tabular section.", "size": 9})

        for key in ("top_titles", "top_borrowers", "series", "by_material", "by_class"):
            section = report.get(key)
            if not section:
                continue
            blocks.append({"text": "", "size": 9})
            blocks.append({"text": key.replace("_", " ").title(), "size": 10, "bold": True})
            for item in section[:20]:
                if isinstance(item, Mapping):
                    blocks.append(
                        {
                            "text": "  " + "  ".join("%s=%s" % (k, v) for k, v in item.items()),
                            "size": 9,
                        }
                    )

        writer = PdfWriter(school_name=self._school_name())
        info = {"school_name": self._school_name(), "generated": config.today_local()}
        info.update(dict(meta or {}))
        return writer.write(str(report.get("title") or "Report"), blocks, str(path), info)

    def export_csv(
        self, report: Mapping[str, Any], stem: str, actor: str = "system"
    ) -> dict[str, Any]:
        """Write ``report`` into ``data/exports`` and describe the file."""
        path, filename = self._export_path(stem, "csv")
        try:
            self.to_csv(report, path)
        except OSError as exc:
            raise errors.ImriError(
                errors.EXPORT_FAILED, errors.status_for(errors.EXPORT_FAILED), details={"error": str(exc)}
            ) from exc
        self.db.audit(
            actor, "export.run", target=filename, detail={"format": "csv", "rows": len(report.get("rows") or [])}
        )
        return {
            "path": path,
            "filename": filename,
            "size": os.path.getsize(path),
            "rows": len(report.get("rows") or []),
        }

    def export_pdf(
        self, report: Mapping[str, Any], stem: str, actor: str = "system"
    ) -> dict[str, Any]:
        """Write ``report`` as a PDF into ``data/exports``."""
        path, filename = self._export_path(stem, "pdf")
        try:
            self.to_pdf(report, path)
        except OSError as exc:
            raise errors.ImriError(
                errors.EXPORT_FAILED, errors.status_for(errors.EXPORT_FAILED), details={"error": str(exc)}
            ) from exc
        self.db.audit(
            actor, "export.run", target=filename, detail={"format": "pdf", "rows": len(report.get("rows") or [])}
        )
        return {
            "path": path,
            "filename": filename,
            "size": os.path.getsize(path),
            "rows": len(report.get("rows") or []),
        }
