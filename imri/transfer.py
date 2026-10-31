"""Import, export and backup/restore.

Import is two-phase (ADR-14): ``preview`` parses and validates with **zero
writes** to ``books``/``members`` and caches the normalised rows server-side
under a ``batch_token``; ``commit`` inserts only the rows that passed. A bad
file therefore never half-loads, and the client never gets to send back a row
list the server would have to trust.

Backup is a zip with a SHA-256 manifest, verified after writing and again
before restoring. Restore refuses a tampered archive, takes a pre-restore
snapshot, and guards every extracted member against zip-slip.
"""

from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
import xml.etree.ElementTree as ElementTree
from typing import Any, Iterable, Mapping, Sequence

from . import config, errors
from .catalog import CatalogService
from .config import get_logger
from .members import MemberService

_log = get_logger("transfer")

_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

MANIFEST_NAME = "MANIFEST.json"
BACKUP_FORMAT = "imri-backup/1"
BACKUP_SUFFIX = ".imribackup"

#: Files/directories never included in a backup archive.
_BACKUP_EXCLUDES = ("backups", "browser-profile", "logs", ".lock")
_BACKUP_SKIP_SUFFIXES = (".db-wal", ".db-shm", ".log", ".tmp")

#: Canonical field -> accepted header aliases (English, Bahasa Melayu, 中文).
HEADER_ALIASES: dict[str, set[str]] = {
    # --- books -----------------------------------------------------------
    "accession_no": {
        "accession no", "accession_no", "accession", "accession number", "acc no",
        "accno", "item no", "item number", "no. akses", "nombor akses", "登录号", "登录号 ",
    },
    "isbn": {"isbn", "isbn13", "isbn10", "isbn 13", "isbn 10"},
    "title": {"title", "book title", "tajuk", "judul", "题名", "书名", "标题"},
    "subtitle": {"subtitle", "sub title", "subtajuk", "副题名"},
    "authors": {"authors", "author", "author(s)", "pengarang", "penulis", "作者"},
    "publisher": {"publisher", "penerbit", "出版社", "pub"},
    "pub_year": {"year", "pub year", "publication year", "pub_year", "tahun", "年份"},
    "edition": {"edition", "edisi", "版次"},
    "language": {"language", "lang", "bahasa", "语言"},
    "dewey": {"dewey", "dewey decimal", "ddc", "dewey classification", "nombor dewey"},
    "classification": {"classification", "klasifikasi", "分类法"},
    "call_no": {"call no", "call_no", "call number", "shelfmark", "no panggilan", "索书号"},
    "subjects": {"subjects", "subject", "subjek", "主题", "topics", "topic"},
    "material_type": {"material type", "material_type", "material", "bahan", "载体", "type"},
    "location": {"location", "shelf", "section", "lokasi", "馆藏地点", "rack"},
    "copies": {"copies", "copy", "number of copies", "naskah", "复本数", "qty"},
    "price": {"price", "cost", "harga", "价格", "amount"},
    "synopsis": {"synopsis", "description", "notes", "sinopsis", "内容简介", "remark"},
    "status": {"status", "stat", "status buku", "状态"},
    # --- members ---------------------------------------------------------
    "member_code": {
        "member code", "member_code", "code", "kod ahli", "no. ahli", "会员编号", "编号",
    },
    "name": {"name", "member name", "nama", "nama penuh", "姓名", "名字"},
    "member_type": {"member type", "member_type", "type of member", "jenis ahli", "会员类型"},
    "ic_number": {"ic", "ic no", "ic number", "ic_number", "mykad", "no kp", "身份证"},
    "form": {"form", "tingkatan", "年级"},
    "class_name": {"class", "class name", "class_name", "kelas", "班级"},
    "address": {"address", "alamat", "地址"},
    "phone": {"phone", "telephone", "tel", "no telefon", "电话", "手机"},
    "email": {"email", "e-mail", "emel", "邮箱"},
    "membership_date": {
        "membership date", "membership_date", "joined", "tarikh ahli", "入会日期",
    },
    "expiry_date": {"expiry", "expiry date", "expiry_date", "tarikh luput", "到期日"},
    "note": {"note", "notes", "catatan", "备注"},
}

#: Canonical export columns per entity.
BOOK_EXPORT_COLUMNS: tuple[str, ...] = (
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
    "status",
    "barcode",
)

MEMBER_EXPORT_COLUMNS: tuple[str, ...] = (
    "member_code",
    "name",
    "member_type",
    "ic_number",
    "form",
    "class_name",
    "address",
    "phone",
    "email",
    "membership_date",
    "expiry_date",
    "status",
    "note",
)

#: ``batch_token`` -> ``{kind, batch_id, rows, expires_at}`` (10 minute TTL).
_IMPORT_TTL_SECONDS = 600.0

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def safe_cell(value: Any) -> Any:
    """Neutralise spreadsheet formula injection in one cell."""
    if isinstance(value, str) and value[:1] in _FORMULA_PREFIXES:
        return "'" + value
    return value


def _normalise_header(header: Any) -> str:
    """Lower-case, collapse whitespace and strip a header cell."""
    text = str(header or "").strip().lower()
    text = re.sub(r"[\s_]+", " ", text)
    return text.strip()


def canonical_key(header: Any) -> str:
    """Map a source header onto a canonical field name, or return it unchanged."""
    normalised = _normalise_header(header)
    if not normalised:
        return ""
    for canonical, aliases in HEADER_ALIASES.items():
        if normalised == canonical or normalised in aliases:
            return canonical
    return normalised


# --------------------------------------------------------------------------- #
# Table reading
# --------------------------------------------------------------------------- #


def _col_to_index(letters: str) -> int:
    """Convert an Excel column label (``AB``) to a zero-based index."""
    value = 0
    for char in letters:
        value = value * 26 + (ord(char.upper()) - 64)
    return value


def _index_to_col(index: int) -> str:
    """Convert a zero-based index into an Excel column label."""
    letters = ""
    position = index + 1
    while position:
        position, remainder = divmod(position - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def read_csv_bytes(raw: bytes) -> list[dict[str, Any]]:
    """Parse CSV bytes into canonical-keyed dicts (UTF-8 BOM tolerated)."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise errors.ImriError(
            errors.IMPORT_BAD_ENCODING, errors.status_for(errors.IMPORT_BAD_ENCODING)
        ) from exc
    reader = csv.reader(io.StringIO(text, newline=""))
    rows = [row for row in reader if any(str(cell).strip() for cell in row)]
    if not rows:
        raise errors.ImriError(errors.IMPORT_EMPTY, errors.status_for(errors.IMPORT_EMPTY))
    headers = [canonical_key(cell) for cell in rows[0]]
    records: list[dict[str, Any]] = []
    for raw_row in rows[1:]:
        record: dict[str, Any] = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            record[header] = raw_row[index] if index < len(raw_row) else ""
        records.append(record)
    return records


def read_xlsx_bytes(raw: bytes) -> list[dict[str, Any]]:
    """Parse an XLSX workbook's first sheet using ``zipfile`` + ``ElementTree``."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise errors.ImriError(
            errors.UNSUPPORTED_FORMAT, errors.status_for(errors.UNSUPPORTED_FORMAT)
        ) from exc

    with archive:
        names = archive.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.iter(_XLSX_NS + "si"):
                shared.append("".join(node.text or "" for node in item.iter(_XLSX_NS + "t")))

        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in names:
            candidates = sorted(
                name
                for name in names
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
            if not candidates:
                raise errors.ImriError(
                    errors.UNSUPPORTED_FORMAT, errors.status_for(errors.UNSUPPORTED_FORMAT)
                )
            sheet_name = candidates[0]
        sheet = ElementTree.fromstring(archive.read(sheet_name))

    grid: list[dict[str, Any]] = []
    for row in sheet.iter(_XLSX_NS + "row"):
        cells: dict[str, Any] = {}
        for cell in row.iter(_XLSX_NS + "c"):
            reference = cell.get("r") or ""
            letters = "".join(char for char in reference if char.isalpha())
            cell_type = cell.get("t")
            value: Any = None
            if cell_type == "s":
                node = cell.find(_XLSX_NS + "v")
                if node is not None and node.text is not None:
                    try:
                        value = shared[int(node.text)]
                    except (ValueError, IndexError):
                        value = None
            elif cell_type == "inlineStr":
                inline = cell.find(_XLSX_NS + "is")
                if inline is not None:
                    value = "".join(node.text or "" for node in inline.iter(_XLSX_NS + "t"))
            else:
                node = cell.find(_XLSX_NS + "v")
                value = node.text if node is not None else None
            cells[letters] = value
        grid.append(cells)

    if not grid:
        raise errors.ImriError(errors.IMPORT_EMPTY, errors.status_for(errors.IMPORT_EMPTY))

    header_cells = grid[0]
    header_columns = sorted(header_cells.keys(), key=_col_to_index)
    headers = [canonical_key(header_cells.get(column) or "") for column in header_columns]
    records: list[dict[str, Any]] = []
    for row in grid[1:]:
        record: dict[str, Any] = {}
        for index, column in enumerate(header_columns):
            if not headers[index]:
                continue
            record[headers[index]] = row.get(column)
        if any(str(value or "").strip() for value in record.values()):
            records.append(record)
    return records


def read_bytes(raw: bytes, filename: str = "") -> list[dict[str, Any]]:
    """Parse uploaded bytes as CSV or XLSX depending on the file name."""
    suffix = os.path.splitext(str(filename or ""))[1].lower()
    if suffix in (".xlsx", ".xlsm"):
        return read_xlsx_bytes(raw)
    if suffix in (".csv", ".txt", ""):
        return read_csv_bytes(raw)
    raise errors.ImriError(
        errors.UNSUPPORTED_FORMAT,
        errors.status_for(errors.UNSUPPORTED_FORMAT),
        details={"extension": suffix},
    )


def read_table(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read a CSV or XLSX file from disk into canonical-keyed dicts."""
    target = str(path)
    with open(target, "rb") as handle:
        return read_bytes(handle.read(), target)


# --------------------------------------------------------------------------- #
# Table writing
# --------------------------------------------------------------------------- #


def write_csv(
    headers: Sequence[str], rows: Iterable[Mapping[str, Any]], path: str | os.PathLike[str]
) -> str:
    """Write CSV with a UTF-8 BOM and the formula-injection guard."""
    target = str(path)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([safe_cell(header) for header in headers])
        for row in rows:
            writer.writerow([safe_cell(row.get(header, "")) for header in headers])
    return target


def write_xlsx(
    headers: Sequence[str], rows: Iterable[Mapping[str, Any]], path: str | os.PathLike[str]
) -> str:
    """Write a minimal but Excel-compatible XLSX workbook."""
    target = str(path)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

    def escape(value: Any) -> str:
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    sheet_rows: list[str] = []
    header_cells: list[str] = []
    for index, header in enumerate(headers):
        column = _index_to_col(index)
        header_cells.append(
            '<c r="%s1" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
            % (column, escape(safe_cell(header)))
        )
    sheet_rows.append('<row r="1">%s</row>' % "".join(header_cells))

    for row_index, row in enumerate(rows, start=2):
        cells: list[str] = []
        for index, header in enumerate(headers):
            column = _index_to_col(index)
            value = row.get(header, "")
            if value is None:
                value = ""
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cells.append('<c r="%s%d"><v>%s</v></c>' % (column, row_index, value))
            else:
                cells.append(
                    '<c r="%s%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                    % (column, row_index, escape(safe_cell(value)))
                )
        sheet_rows.append('<row r="%d">%s</row>' % (row_index, "".join(cells)))

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>'
        % (namespace, "".join(sheet_rows))
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="%s" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>' % namespace
    )
    workbook_relationships = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_relationships)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return target


# --------------------------------------------------------------------------- #
# TransferService
# --------------------------------------------------------------------------- #


class TransferService:
    """CSV/XLSX import and export with a two-phase dry run."""

    def __init__(self, db: Any, catalog: CatalogService | None = None) -> None:
        self.db = db
        self.catalog = catalog or CatalogService(db)
        self.members = MemberService(db, self.catalog)
        self._cache: dict[str, dict[str, Any]] = {}

    # -- validation (read-only) ------------------------------------------- #
    def _validate_book_row(
        self, row: Mapping[str, Any], seen: dict[str, set[str]] | None = None
    ) -> tuple[dict[str, Any], str | None]:
        """Validate one book row without writing anything.

        ``seen`` carries the ISBNs and accessions already accepted earlier in
        the same file. Checking only the database would let a file that lists
        the same book twice pass the dry run and then fail halfway through the
        commit — the exact surprise a dry run exists to prevent.
        """
        from .catalog import (
            isbn_normalize,
            to_isbn10,
            to_isbn13,
            validate_call_no,
            validate_dewey,
            validate_isbn,
            validate_year,
        )

        clean: dict[str, Any] = {}
        title = config.clean_text(row.get("title"))
        if not title:
            return clean, errors.TITLE_REQUIRED
        if len(title) > 512:
            return clean, errors.VALIDATION_FAILED
        clean["title"] = title

        marked = seen if seen is not None else {"isbn": set(), "accession_no": set()}

        isbn_raw = config.clean_text(row.get("isbn"))
        if isbn_raw:
            if not validate_isbn(isbn_raw):
                return clean, errors.INVALID_ISBN
            normalised = to_isbn13(isbn_raw) or isbn_normalize(isbn_raw)
            clean["isbn"] = normalised
            clean["isbn10"] = to_isbn10(normalised)
            if normalised in marked.setdefault("isbn", set()):
                return clean, errors.DUPLICATE_ISBN
            if self.db.one("SELECT id FROM books WHERE isbn = ?", (normalised,)):
                return clean, errors.DUPLICATE_ISBN
            marked["isbn"].add(normalised)

        accession = config.clean_text(row.get("accession_no"))
        if accession:
            if accession in marked.setdefault("accession_no", set()):
                return clean, errors.DUPLICATE_ACCESSION
            if self.db.one("SELECT id FROM books WHERE accession_no = ?", (accession,)):
                return clean, errors.DUPLICATE_ACCESSION
            marked["accession_no"].add(accession)
            clean["accession_no"] = accession

        dewey = config.clean_text(row.get("dewey"))
        if dewey and not validate_dewey(dewey):
            return clean, errors.INVALID_DEWEY
        clean["dewey"] = dewey

        call_no = config.clean_text(row.get("call_no"))
        if call_no and not validate_call_no(call_no):
            return clean, errors.INVALID_CALL_NO
        clean["call_no"] = call_no

        year = config.clean_text(row.get("pub_year"))
        if year and not validate_year(year):
            return clean, errors.INVALID_YEAR
        clean["pub_year"] = year

        material = (config.clean_text(row.get("material_type")) or "book").lower()
        if material not in config.MATERIAL_TYPES:
            return clean, errors.UNKNOWN_MATERIAL_TYPE
        clean["material_type"] = material

        for field in (
            "subtitle",
            "authors",
            "publisher",
            "edition",
            "location",
            "synopsis",
        ):
            clean[field] = config.clean_text(row.get(field))

        language = config.clean_text(row.get("language"))
        clean["language"] = (language or "en").lower()

        copies_raw = row.get("copies")
        copies = config.to_int(copies_raw, 1) if str(copies_raw or "").strip() else 1
        if copies < 0 or copies > 500:
            return clean, errors.VALIDATION_FAILED
        clean["copies"] = copies

        price_raw = row.get("price")
        price = config.to_float(price_raw, 0.0) if str(price_raw or "").strip() else 0.0
        if price < 0:
            return clean, errors.VALIDATION_FAILED
        clean["price"] = config.money(price)

        subjects = config.clean_text(row.get("subjects"))
        if subjects:
            clean["subjects"] = [part.strip() for part in str(subjects).split(",") if part.strip()]

        status = (config.clean_text(row.get("status")) or "").lower()
        if status and status not in config.BOOK_STATUSES:
            clean["status"] = "available"
        return clean, None

    def _validate_member_row(
        self, row: Mapping[str, Any], seen: dict[str, set[str]] | None = None
    ) -> tuple[dict[str, Any], str | None]:
        """Validate one member row without writing anything."""
        from .members import validate_email, validate_ic

        clean: dict[str, Any] = {}
        name = config.clean_text(row.get("name"))
        if not name:
            return clean, errors.VALIDATION_FAILED
        clean["name"] = name

        code = config.clean_text(row.get("member_code"))
        if code:
            marked = seen if seen is not None else {"member_code": set()}
            if code in marked.setdefault("member_code", set()):
                return clean, errors.DUPLICATE_MEMBER_CODE
            if self.db.one("SELECT id FROM members WHERE member_code = ?", (code,)):
                return clean, errors.DUPLICATE_MEMBER_CODE
            marked["member_code"].add(code)
            clean["member_code"] = code

        member_type = (config.clean_text(row.get("member_type")) or "student").lower()
        if member_type not in config.MEMBER_TYPES:
            member_type = "student"
        clean["member_type"] = member_type

        ic = config.clean_text(row.get("ic_number"))
        if ic and not validate_ic(ic):
            return clean, errors.INVALID_IC
        clean["ic_number"] = ic

        email = config.clean_text(row.get("email"))
        if email and not validate_email(email):
            return clean, errors.INVALID_EMAIL
        clean["email"] = email

        for field in ("form", "class_name", "address", "phone", "note"):
            clean[field] = config.clean_text(row.get(field))

        membership = config.clean_text(row.get("membership_date"))
        clean["membership_date"] = (
            membership[:10]
            if membership and config.parse_date(membership)
            else config.today_local()
        )
        expiry = config.clean_text(row.get("expiry_date"))
        clean["expiry_date"] = expiry[:10] if expiry and config.parse_date(expiry) else None

        status = (config.clean_text(row.get("status")) or "active").lower()
        clean["status"] = status if status in config.MEMBER_STATUSES else "active"
        return clean, None

    # -- preview ---------------------------------------------------------- #
    def _preview(
        self, kind: str, raw: bytes, filename: str, actor: str
    ) -> dict[str, Any]:
        """Parse, validate, record the batch and cache the rows. No data writes."""
        if not raw:
            raise errors.ImriError(errors.IMPORT_EMPTY, errors.status_for(errors.IMPORT_EMPTY))
        rows = read_bytes(raw, filename)
        if not rows:
            raise errors.ImriError(errors.IMPORT_EMPTY, errors.status_for(errors.IMPORT_EMPTY))

        validator = self._validate_book_row if kind == "books" else self._validate_member_row
        key_field = "accession_no" if kind == "books" else "member_code"
        seen: dict[str, set[str]] = {"isbn": set(), "accession_no": set(), "member_code": set()}

        prepared: list[dict[str, Any]] = []
        errors_out: list[dict[str, Any]] = []
        ok_count = 0
        warning_count = 0

        batch_id = self.db.execute(
            "INSERT INTO import_batches(filename, kind, row_count, status, actor, created_at) "
            "VALUES (?, ?, ?, 'preview', ?, ?)",
            (filename or "", kind, len(rows), actor, config.now_utc()),
        )

        for index, row in enumerate(rows, start=1):
            clean, code = validator(row, seen)
            if code:
                prepared.append({"row_no": index, "status": "error", "code": code, "row": clean})
                errors_out.append(
                    {
                        "row_no": index,
                        "code": code,
                        "message": errors.ImriError(code).message,
                        "fields": {code: code},
                    }
                )
                self.db.execute(
                    "INSERT INTO import_batch_items(batch_id, row_no, key_value, status, code, "
                    "message) VALUES (?, ?, ?, 'error', ?, ?)",
                    (
                        batch_id,
                        index,
                        config.clean_text(row.get(key_field)),
                        code,
                        errors.ImriError(code).message,
                    ),
                )
                continue
            ok_count += 1
            prepared.append({"row_no": index, "status": "ok", "code": None, "row": clean})
            self.db.execute(
                "INSERT INTO import_batch_items(batch_id, row_no, key_value, status) "
                "VALUES (?, ?, ?, 'ok')",
                (batch_id, index, config.clean_text(clean.get(key_field))),
            )

        token = uuid.uuid4().hex
        self._cache[token] = {
            "kind": kind,
            "batch_id": batch_id,
            "rows": prepared,
            "expires_at": _dt.datetime.now().timestamp() + _IMPORT_TTL_SECONDS,
            "actor": actor,
        }
        self._purge_cache()

        columns = {
            header: canonical_key(header)
            for header in (rows[0].keys() if rows else [])
        }
        return {
            "batch_token": token,
            "kind": kind,
            "batch_id": batch_id,
            "total": len(rows),
            "ok_count": ok_count,
            "error_count": len(errors_out),
            "warning_count": warning_count,
            "columns": columns,
            "preview": [item["row"] for item in prepared if item["status"] == "ok"][:20],
            "errors": errors_out[:50],
        }

    def preview_books(self, raw: bytes, filename: str, actor: str = "system") -> dict[str, Any]:
        """Dry-run a books file. Performs zero writes to ``books``."""
        return self._preview("books", raw, filename, actor)

    def preview_members(self, raw: bytes, filename: str, actor: str = "system") -> dict[str, Any]:
        """Dry-run a members file. Performs zero writes to ``members``."""
        return self._preview("members", raw, filename, actor)

    def _purge_cache(self) -> None:
        """Drop expired batch tokens."""
        now = _dt.datetime.now().timestamp()
        for token in [t for t, entry in self._cache.items() if entry["expires_at"] <= now]:
            self._cache.pop(token, None)

    # -- commit ----------------------------------------------------------- #
    def commit(self, token: str, actor: str = "system") -> dict[str, Any]:
        """Insert only the rows that passed validation, in one transaction."""
        self._purge_cache()
        entry = self._cache.get(str(token or ""))
        if entry is None:
            raise errors.ImriError(
                errors.IMPORT_EXPIRED, errors.status_for(errors.IMPORT_EXPIRED)
            )
        if not any(item["status"] == "ok" for item in entry["rows"]):
            raise errors.ImriError(errors.NO_VALID_ROWS, errors.status_for(errors.NO_VALID_ROWS))

        kind = entry["kind"]
        batch_id = entry["batch_id"]
        imported = 0
        rejected = 0
        problems: list[dict[str, Any]] = []

        for item in entry["rows"]:
            if item["status"] != "ok":
                rejected += 1
                continue
            try:
                if kind == "books":
                    self.catalog.create_book(item["row"], actor=actor)
                else:
                    self.members.create_member(item["row"], actor=actor)
                imported += 1
            except errors.ImriError as exc:
                rejected += 1
                problems.append(
                    {
                        "row_no": item["row_no"],
                        "code": exc.code,
                        "message": exc.message,
                        "fields": exc.fields,
                    }
                )
                self.db.execute(
                    "UPDATE import_batch_items SET status = 'error', code = ?, message = ? "
                    "WHERE batch_id = ? AND row_no = ?",
                    (exc.code, exc.message, batch_id, item["row_no"]),
                )

        with self.db.transaction():
            self.db.execute(
                "UPDATE import_batches SET imported = ?, rejected = ?, status = 'committed', "
                "completed_at = ? WHERE id = ?",
                (imported, rejected, config.now_utc(), batch_id),
            )
            self.db.audit(
                actor,
                "import.commit",
                target="%s#%d" % (kind, batch_id),
                detail={"imported": imported, "rejected": rejected, "filename": kind},
                target_type="import",
            )

        self._cache.pop(str(token), None)
        return {
            "batch_id": batch_id,
            "imported": imported,
            "rejected": rejected,
            "errors": problems,
        }

    def commit_books(self, token: str, actor: str = "system") -> dict[str, Any]:
        """Commit a cached books preview."""
        return self.commit(token, actor)

    def commit_members(self, token: str, actor: str = "system") -> dict[str, Any]:
        """Commit a cached members preview."""
        return self.commit(token, actor)

    def cancel(self, token: str, actor: str = "system") -> bool:
        """Drop a cached preview and mark its batch cancelled."""
        entry = self._cache.pop(str(token or ""), None)
        if entry is None:
            raise errors.ImriError(
                errors.IMPORT_EXPIRED, errors.status_for(errors.IMPORT_EXPIRED)
            )
        self.db.execute(
            "UPDATE import_batches SET status = 'cancelled', completed_at = ? WHERE id = ?",
            (config.now_utc(), entry["batch_id"]),
        )
        return True

    # -- export ----------------------------------------------------------- #
    def _export_path(self, stem: str, suffix: str) -> tuple[str, str]:
        stamp = config.now_utc().replace("-", "").replace(":", "").replace("T", "-")[:15]
        filename = "%s-%s.%s" % (stem, stamp, suffix)
        directory = config.exports_dir()
        directory.mkdir(parents=True, exist_ok=True)
        return str(directory / filename), filename

    def export_books(
        self,
        filters: Mapping[str, Any] | None,
        fmt: str = "csv",
        actor: str = "system",
    ) -> dict[str, Any]:
        """Export the caller's filtered catalogue view."""
        books = self.catalog.iter_books(filters)
        records = [{column: book.get(column) for column in BOOK_EXPORT_COLUMNS} for book in books]
        suffix = "xlsx" if str(fmt).lower() == "xlsx" else "csv"
        path, filename = self._export_path("books", suffix)
        if suffix == "xlsx":
            write_xlsx(BOOK_EXPORT_COLUMNS, records, path)
        else:
            write_csv(BOOK_EXPORT_COLUMNS, records, path)
        self.db.audit(
            actor, "export.run", target=filename, detail={"kind": "books", "rows": len(records)}
        )
        return {
            "path": path,
            "filename": filename,
            "size": os.path.getsize(path),
            "rows": len(records),
        }

    def export_members(
        self,
        filters: Mapping[str, Any] | None,
        fmt: str = "csv",
        actor: str = "system",
    ) -> dict[str, Any]:
        """Export the caller's filtered member view."""
        criteria = dict(filters or {})
        where, params = self.members._where_from_filters(criteria)  # noqa: SLF001 - internal reuse
        rows = self.db.query("SELECT m.* FROM members m" + where + " ORDER BY m.name COLLATE NOCASE", params)
        records = [{column: row.get(column) for column in MEMBER_EXPORT_COLUMNS} for row in rows]
        suffix = "xlsx" if str(fmt).lower() == "xlsx" else "csv"
        path, filename = self._export_path("members", suffix)
        if suffix == "xlsx":
            write_xlsx(MEMBER_EXPORT_COLUMNS, records, path)
        else:
            write_csv(MEMBER_EXPORT_COLUMNS, records, path)
        self.db.audit(
            actor, "export.run", target=filename, detail={"kind": "members", "rows": len(records)}
        )
        return {
            "path": path,
            "filename": filename,
            "size": os.path.getsize(path),
            "rows": len(records),
        }


# --------------------------------------------------------------------------- #
# BackupService
# --------------------------------------------------------------------------- #


class BackupService:
    """Zip backups with a verified SHA-256 manifest, plus a guarded restore."""

    MANIFEST = MANIFEST_NAME

    def __init__(self, db: Any) -> None:
        self.db = db

    # -- helpers ---------------------------------------------------------- #
    @staticmethod
    def _sha256(path: str | os.PathLike[str]) -> str:
        """Return the hex SHA-256 of a file, streamed in 64 KB chunks."""
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _data_dir(self) -> str:
        return str(config.data_dir())

    def _iter_files(self) -> Iterable[tuple[str, str]]:
        """Yield ``(relative_path, absolute_path)`` for every backup-worthy file."""
        root = self._data_dir()
        for current, directories, files in os.walk(root):
            directories[:] = [d for d in directories if d not in _BACKUP_EXCLUDES]
            for name in files:
                if name in _BACKUP_EXCLUDES:
                    continue
                if name.endswith(_BACKUP_SKIP_SUFFIXES):
                    continue
                full = os.path.join(current, name)
                relative = os.path.relpath(full, root).replace(os.sep, "/")
                if relative.startswith(".."):
                    continue
                yield relative, full

    def _backup_path(self, name: str) -> str:
        """Resolve a backup name to a path inside ``data/backups``."""
        candidate = str(name or "").strip()
        if not candidate or "/" in candidate or "\\" in candidate or ".." in candidate:
            raise errors.ImriError(errors.UNSAFE_PATH, errors.status_for(errors.UNSAFE_PATH))
        directory = config.backups_dir()
        path = directory / candidate
        try:
            if not path.resolve().is_relative_to(directory.resolve()):
                raise errors.ImriError(errors.UNSAFE_PATH, errors.status_for(errors.UNSAFE_PATH))
        except (OSError, ValueError):
            raise errors.ImriError(errors.UNSAFE_PATH, errors.status_for(errors.UNSAFE_PATH))
        return str(path)

    # -- create / list / verify ------------------------------------------- #
    def create(self, label: str = "", actor: str = "system") -> dict[str, Any]:
        """Write a verified backup archive and return its description."""
        directory = config.backups_dir()
        directory.mkdir(parents=True, exist_ok=True)
        self.db.checkpoint("TRUNCATE")

        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_label = ""
        if label:
            safe_label = "-" + "".join(c if c.isalnum() else "_" for c in str(label))[:30]
        filename = "imri-backup-%s%s%s" % (stamp, safe_label, BACKUP_SUFFIX)
        target = str(directory / filename)

        manifest: dict[str, Any] = {
            "format": BACKUP_FORMAT,
            "product": config.PRODUCT,
            "version": config.APP_VERSION,
            "schema": config.SCHEMA_VERSION,
            "created": config.now_utc(),
            "label": label or "",
            "files": [],
        }

        try:
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
                for relative, full in self._iter_files():
                    manifest["files"].append(
                        {
                            "rel": relative,
                            "size": os.path.getsize(full),
                            "sha256": self._sha256(full),
                        }
                    )
                    archive.write(full, relative)
                manifest["manifest_sha256"] = self._manifest_digest(manifest["files"])
                archive.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, ensure_ascii=False))
        except OSError as exc:
            raise errors.ImriError(
                errors.BACKUP_FAILED, errors.status_for(errors.BACKUP_FAILED), details={"error": str(exc)}
            ) from exc

        verification = self.verify(filename)
        if not verification.get("ok"):
            raise errors.ImriError(
                errors.RESTORE_INTEGRITY_FAILED,
                errors.status_for(errors.RESTORE_INTEGRITY_FAILED),
                details={"bad": verification.get("bad", [])},
            )

        self.db.audit(
            actor,
            "backup.create",
            target=filename,
            detail={"files": len(manifest["files"]), "label": label or ""},
            target_type="backup",
        )
        return {
            "name": filename,
            "path": target,
            "size": os.path.getsize(target),
            "sha256_manifest": manifest["manifest_sha256"],
            "verified": True,
            "files": len(manifest["files"]),
            "created": manifest["created"],
        }

    @staticmethod
    def _manifest_digest(entries: Sequence[Mapping[str, Any]]) -> str:
        """Return one digest over the sorted ``rel:sha256`` pairs."""
        digest = hashlib.sha256()
        for entry in sorted(entries, key=lambda item: str(item.get("rel"))):
            digest.update(("%s:%s\n" % (entry.get("rel"), entry.get("sha256"))).encode("utf-8"))
        return digest.hexdigest()

    def list(self) -> list[dict[str, Any]]:
        """Return every backup, newest first."""
        directory = config.backups_dir()
        if not directory.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for name in sorted(os.listdir(directory)):
            if not name.endswith(BACKUP_SUFFIX):
                continue
            full = directory / name
            entry: dict[str, Any] = {
                "name": name,
                "created": "",
                "size": 0,
                "label": "",
                "files": 0,
                "verified": False,
            }
            try:
                entry["size"] = os.path.getsize(full)
            except OSError:
                pass
            try:
                with zipfile.ZipFile(full) as archive:
                    if MANIFEST_NAME in archive.namelist():
                        manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
                        entry["created"] = manifest.get("created", "")
                        entry["label"] = manifest.get("label", "")
                        entry["files"] = len(manifest.get("files", []))
                        entry["verified"] = (
                            manifest.get("manifest_sha256")
                            == self._manifest_digest(manifest.get("files", []))
                        )
            except (OSError, ValueError, zipfile.BadZipFile):
                entry["verified"] = False
            out.append(entry)
        out.sort(
            key=lambda item: (str(item.get("created") or ""), str(item.get("name") or "")),
            reverse=True,
        )
        return out

    def verify(self, name: str) -> dict[str, Any]:
        """Re-hash every archived file and report any mismatch."""
        path = self._backup_path(name)
        if not os.path.isfile(path):
            raise errors.ImriError(
                errors.RESTORE_NO_FILE, errors.status_for(errors.RESTORE_NO_FILE)
            )
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if MANIFEST_NAME not in names:
                    raise errors.ImriError(
                        errors.RESTORE_NO_MANIFEST,
                        errors.status_for(errors.RESTORE_NO_MANIFEST),
                    )
                manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
                entries = manifest.get("files", [])
                bad: list[str] = []
                for entry in entries:
                    relative = entry.get("rel")
                    if relative not in names:
                        bad.append(str(relative))
                        continue
                    digest = hashlib.sha256(archive.read(relative)).hexdigest()
                    if digest != entry.get("sha256"):
                        bad.append(str(relative))
                manifest_ok = manifest.get("manifest_sha256") == self._manifest_digest(entries)
                return {
                    "ok": not bad and manifest_ok,
                    "files": len(entries),
                    "bad": bad,
                    "manifest_ok": manifest_ok,
                }
        except zipfile.BadZipFile as exc:
            raise errors.ImriError(
                errors.RESTORE_INTEGRITY_FAILED,
                errors.status_for(errors.RESTORE_INTEGRITY_FAILED),
                details={"error": "bad_zip"},
            ) from exc

    # -- restore ---------------------------------------------------------- #
    @staticmethod
    def _safe_extract(archive: zipfile.ZipFile, destination: str) -> None:
        """Extract only members whose resolved path stays inside ``destination``.

        A member that resolves outside the data folder is refused outright
        rather than skipped: the archive's own manifest listed it, so either the
        file is corrupt or it was crafted to write outside the library. Quietly
        ignoring it would let a tampered backup restore "successfully" while
        silently dropping data.
        """
        root = os.path.abspath(destination)
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if name in ("", "/", MANIFEST_NAME):
                continue
            target = os.path.abspath(os.path.join(root, name))
            if target != root and not target.startswith(root + os.sep):
                raise errors.ImriError(
                    errors.UNSAFE_PATH,
                    errors.status_for(errors.UNSAFE_PATH),
                    details={"member": info.filename},
                )
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as handle:
                for chunk in iter(lambda: source.read(65536), b""):
                    handle.write(chunk)

    def _snapshot(self) -> str:
        """Copy the current data files into a timestamped safety folder."""
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        safety = config.backups_dir() / ("pre-restore-%s" % stamp)
        safety.mkdir(parents=True, exist_ok=True)
        for relative, full in self._iter_files():
            destination = safety / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with open(full, "rb") as source, open(destination, "wb") as handle:
                for chunk in iter(lambda: source.read(65536), b""):
                    handle.write(chunk)
        return str(safety)

    def restore(self, name: str, actor: str = "system", confirm: bool = False) -> dict[str, Any]:
        """Verify, snapshot, extract and re-check a backup archive."""
        if not confirm:
            raise errors.ImriError(
                errors.VALIDATION_FAILED,
                errors.status_for(errors.VALIDATION_FAILED),
                fields={"confirm": errors.VALIDATION_FAILED},
                details={"reason": "confirmation_required"},
            )
        path = self._backup_path(name)
        if not os.path.isfile(path):
            raise errors.ImriError(
                errors.RESTORE_NO_FILE, errors.status_for(errors.RESTORE_NO_FILE)
            )
        verification = self.verify(name)
        if not verification.get("ok"):
            raise errors.ImriError(
                errors.RESTORE_INTEGRITY_FAILED,
                errors.status_for(errors.RESTORE_INTEGRITY_FAILED),
                details={"bad": verification.get("bad", [])},
            )

        data_root = self._data_dir()
        with self.db.exclusive():
            safety_path = self._snapshot()

            for name_to_remove in ("imri.db", "imri.db-wal", "imri.db-shm", "license.json"):
                try:
                    os.remove(os.path.join(data_root, name_to_remove))
                except OSError:
                    pass

            try:
                with zipfile.ZipFile(path) as archive:
                    self._safe_extract(archive, data_root)
            except zipfile.BadZipFile as exc:
                raise errors.ImriError(
                    errors.RESTORE_INTEGRITY_FAILED,
                    errors.status_for(errors.RESTORE_INTEGRITY_FAILED),
                ) from exc

        # The connection was reopened by ``exclusive()``; re-verify the restored
        # database before declaring success.
        integrity = self.db.integrity_check()
        if not integrity.get("ok"):
            raise errors.ImriError(
                errors.RESTORE_INTEGRITY_FAILED,
                errors.status_for(errors.RESTORE_INTEGRITY_FAILED),
                details={"integrity": integrity.get("result"), "safety_path": safety_path},
            )

        self.db.audit(
            actor,
            "backup.restore",
            target=name,
            detail={"safety_path": safety_path},
            target_type="backup",
        )
        return {
            "restored": True,
            "safety_path": safety_path,
            "restart_required": True,
        }

    def delete(self, name: str, actor: str = "system") -> None:
        """Delete one backup archive."""
        path = self._backup_path(name)
        if not os.path.isfile(path):
            raise errors.ImriError(
                errors.RESTORE_NO_FILE, errors.status_for(errors.RESTORE_NO_FILE)
            )
        os.remove(path)
        self.db.audit(actor, "backup.delete", target=name, target_type="backup")

    def prune(self, keep: int = 10) -> int:
        """Keep the newest ``keep`` archives, deleting the rest."""
        limit = max(1, int(keep))
        backups = self.list()
        removed = 0
        for entry in backups[limit:]:
            try:
                os.remove(self._backup_path(entry["name"]))
                removed += 1
            except (OSError, errors.ImriError):  # pragma: no cover - defensive
                continue
        return removed

    def last_backup(self) -> dict[str, Any] | None:
        """Return the newest backup description, or ``None``."""
        entries = self.list()
        return entries[0] if entries else None
