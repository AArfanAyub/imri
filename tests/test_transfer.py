"""Import, export and backup: the three places untrusted bytes enter the product.

Every test here feeds the service something a librarian could actually receive
by email — a CSV from another school, a spreadsheet exported from Excel, a zip
someone renamed — and asserts that a bad file fails loudly instead of corrupting
the library.
"""

from __future__ import annotations

import csv
import io
import json
import os
import zipfile

from helpers import ServiceTestCase

from imri import config, errors

BOOK_HEADER = ["title", "authors", "isbn", "copies"]


def csv_bytes(rows: list[list[str]], header: list[str] | None = None) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header or BOOK_HEADER)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def code_of(callable_, *args, **kwargs) -> str:
    try:
        callable_(*args, **kwargs)
    except errors.ImriError as exc:
        return exc.code
    raise AssertionError("expected an ImriError, but the call succeeded")


class ImportPreviewTests(ServiceTestCase):
    def test_dry_run_writes_no_books(self) -> None:
        raw = csv_bytes([["Sulalatus Salatin", "A. Samad", "", "2"]])
        result = self.transfer.preview_books(raw, "books.csv", actor="tester")
        self.assertEqual(result["ok_count"], 1)
        self.assertEqual(result["error_count"], 0)
        # The whole point of a dry run: the catalogue is untouched.
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], 0)

    def test_dry_run_still_records_the_batch(self) -> None:
        raw = csv_bytes([["One", "A", "", "1"]])
        result = self.transfer.preview_books(raw, "books.csv", actor="tester")
        batch = self.db.one("SELECT * FROM import_batches WHERE id = ?", (result["batch_id"],))
        self.assertEqual(batch["status"], "preview")
        self.assertEqual(batch["row_count"], 1)

    def test_a_bad_row_is_reported_without_blocking_the_good_ones(self) -> None:
        raw = csv_bytes(
            [
                ["Good Book", "A. Author", "", "1"],
                ["", "No Title", "", "1"],
                ["Bad ISBN", "A. Author", "9780306406150", "1"],
                ["Another Good", "B. Author", "", "1"],
            ]
        )
        result = self.transfer.preview_books(raw, "books.csv", actor="tester")
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["ok_count"], 2)
        self.assertEqual(result["error_count"], 2)
        codes = {item["code"] for item in result["errors"]}
        self.assertIn(errors.TITLE_REQUIRED, codes)
        self.assertIn(errors.INVALID_ISBN, codes)

    def test_duplicate_inside_the_file_is_caught(self) -> None:
        raw = csv_bytes(
            [
                ["Same", "A", "9780306406157", "1"],
                ["Same", "A", "9780306406157", "1"],
            ]
        )
        result = self.transfer.preview_books(raw, "books.csv", actor="tester")
        self.assertEqual(result["ok_count"], 1)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["errors"][0]["code"], errors.DUPLICATE_ISBN)

    def test_two_books_with_the_same_title_are_both_imported(self) -> None:
        """Same title, no ISBN: a school may own two different editions."""
        raw = csv_bytes([["Same", "A", "", "1"], ["Same", "B", "", "1"]])
        result = self.transfer.preview_books(raw, "books.csv", actor="tester")
        self.assertEqual(result["ok_count"], 2)

    def test_duplicate_against_the_live_catalogue_is_caught(self) -> None:
        self.make_book(title="Already Here", isbn="9780306406157")
        raw = csv_bytes([["Already Here", "A", "9780306406157", "1"]])
        result = self.transfer.preview_books(raw, "books.csv", actor="tester")
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["errors"][0]["code"], errors.DUPLICATE_ISBN)

    def test_empty_file_is_refused(self) -> None:
        self.assertEqual(
            code_of(self.transfer.preview_books, b"", "books.csv"), errors.IMPORT_EMPTY
        )

    def test_header_only_file_is_refused(self) -> None:
        self.assertEqual(
            code_of(self.transfer.preview_books, csv_bytes([]), "books.csv"),
            errors.IMPORT_EMPTY,
        )

    def test_binary_garbage_does_not_crash_the_server(self) -> None:
        code = code_of(
            self.transfer.preview_books, b"\x00\xff\xfe\x00binary", "books.csv"
        )
        self.assertIn(code, (errors.IMPORT_BAD_ENCODING, errors.IMPORT_EMPTY))

    def test_unknown_extension_is_refused(self) -> None:
        self.assertEqual(
            code_of(self.transfer.preview_books, b"title\nx\n", "books.pdf"),
            errors.UNSUPPORTED_FORMAT,
        )

    def test_headers_are_matched_case_and_space_insensitively(self) -> None:
        raw = b"  TITLE , Authors ,Copies\nMesin Waktu,B. Author,1\n"
        result = self.transfer.preview_books(raw, "books.csv", actor="tester")
        self.assertEqual(result["ok_count"], 1)

    def test_utf8_bom_is_tolerated(self) -> None:
        """Excel writes a BOM; refusing it would reject every real spreadsheet."""
        raw = b"\xef\xbb\xbf" + csv_bytes([["Bom Book", "A", "", "1"]])
        result = self.transfer.preview_books(raw, "books.csv", actor="tester")
        self.assertEqual(result["ok_count"], 1)


class ImportCommitTests(ServiceTestCase):
    def test_commit_inserts_only_the_valid_rows(self) -> None:
        raw = csv_bytes(
            [
                ["Commit Me", "A", "", "2"],
                ["", "No Title", "", "1"],
            ]
        )
        preview = self.transfer.preview_books(raw, "books.csv", actor="tester")
        result = self.transfer.commit(preview["batch_token"], actor="tester")

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], 1)

        book = self.db.one("SELECT * FROM books WHERE title = 'Commit Me'")
        self.assertEqual(book["copies"], 2)
        self.assertEqual(
            self.db.one("SELECT COUNT(*) AS n FROM book_copies WHERE book_id = ?", (book["id"],))["n"],
            2,
        )

    def test_commit_marks_the_batch_done(self) -> None:
        preview = self.transfer.preview_books(csv_bytes([["X", "A", "", "1"]]), "b.csv")
        self.transfer.commit(preview["batch_token"], actor="tester")
        batch = self.db.one("SELECT * FROM import_batches WHERE id = ?", (preview["batch_id"],))
        self.assertEqual(batch["status"], "committed")

    def test_a_token_cannot_be_used_twice(self) -> None:
        preview = self.transfer.preview_books(csv_bytes([["Once", "A", "", "1"]]), "b.csv")
        self.transfer.commit(preview["batch_token"], actor="tester")
        self.assertEqual(
            code_of(self.transfer.commit, preview["batch_token"]),
            errors.IMPORT_EXPIRED,
        )
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], 1)

    def test_an_unknown_token_is_refused(self) -> None:
        self.assertEqual(
            code_of(self.transfer.commit, "deadbeef" * 4), errors.IMPORT_EXPIRED
        )

    def test_cancel_drops_the_batch(self) -> None:
        preview = self.transfer.preview_books(csv_bytes([["Nope", "A", "", "1"]]), "b.csv")
        self.assertTrue(self.transfer.cancel(preview["batch_token"], actor="tester"))
        self.assertEqual(
            code_of(self.transfer.commit, preview["batch_token"]), errors.IMPORT_EXPIRED
        )
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], 0)

    def test_commit_is_atomic(self) -> None:
        """If one row fails late, the batch must not leave a half-imported catalogue."""
        rows = [["Row %d" % i, "A", "", "1"] for i in range(30)]
        preview = self.transfer.preview_books(csv_bytes(rows), "b.csv", actor="tester")
        result = self.transfer.commit(preview["batch_token"], actor="tester")
        self.assertEqual(result["imported"], 30)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], 30)
        self.assertTrue(self.db.integrity_check()["ok"])


class ImportHostileInputTests(ServiceTestCase):
    def test_sql_injection_in_a_cell_is_stored_as_text(self) -> None:
        payload = "Robert'); DROP TABLE books;--"
        raw = csv_bytes([[payload, "A", "", "1"]])
        preview = self.transfer.preview_books(raw, "books.csv", actor="tester")
        self.transfer.commit(preview["batch_token"], actor="tester")
        book = self.db.one("SELECT * FROM books WHERE title = ?", (payload,))
        self.assertIsNotNone(book)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], 1)

    def test_an_over_long_title_is_rejected_not_stored(self) -> None:
        raw = csv_bytes([["A" * 5000, "A", "", "1"]])
        result = self.transfer.preview_books(raw, "books.csv", actor="tester")
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["errors"][0]["code"], errors.VALIDATION_FAILED)

    def test_a_long_but_legal_title_is_accepted(self) -> None:
        raw = csv_bytes([["A" * 400, "A", "", "1"]])
        result = self.transfer.preview_books(raw, "books.csv", actor="tester")
        self.assertEqual(result["ok_count"], 1)

    def test_xlsx_export_neutralises_formula_injection(self) -> None:
        """A title starting with '=' must not become a live Excel formula."""
        self.make_book(title="=cmd|' /C calc'!A0", copies=1)
        export = self.transfer.export_books({}, fmt="xlsx", actor="tester")
        with zipfile.ZipFile(export["path"]) as archive:
            sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("'=cmd", sheet)
        self.assertNotIn("<f>", sheet)

    def test_csv_export_neutralises_formula_injection(self) -> None:
        self.make_book(title="+SUM(1,1)", copies=1)
        export = self.transfer.export_books({}, fmt="csv", actor="tester")
        with open(export["path"], encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        flat = [cell for row in rows for cell in row]
        self.assertIn("'+SUM(1,1)", flat)


class ExportTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        for index in range(3):
            self.make_book(title="Export %d" % index, authors="A", copies=2)

    def test_csv_export_has_a_header_and_every_row(self) -> None:
        export = self.transfer.export_books({}, fmt="csv", actor="tester")
        self.assertEqual(export["rows"], 3)
        self.assertGreater(export["size"], 0)
        self.assertTrue(os.path.exists(export["path"]))
        with open(export["path"], encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(len(rows), 4)  # header + 3
        self.assertIn("title", rows[0])

    def test_export_honours_the_filter(self) -> None:
        export = self.transfer.export_books({"q": "Export 1"}, fmt="csv", actor="tester")
        self.assertEqual(export["rows"], 1)

    def test_export_is_audited(self) -> None:
        self.transfer.export_books({}, fmt="csv", actor="tester")
        rows = self.db.query("SELECT action FROM audit_log WHERE action = 'export.run'")
        self.assertEqual(len(rows), 1)

    def test_export_round_trips_through_import(self) -> None:
        """Re-importing our own export must not create duplicates."""
        export = self.transfer.export_books({}, fmt="csv", actor="tester")
        with open(export["path"], "rb") as handle:
            raw = handle.read()
        preview = self.transfer.preview_books(raw, "books.csv", actor="tester")
        self.assertEqual(preview["ok_count"], 0)
        self.assertEqual(preview["error_count"], 3)


class BackupTests(ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_book(title="Backed Up", copies=2)

    def test_create_writes_a_zip_with_a_manifest(self) -> None:
        result = self.backup.create("nightly", actor="tester")
        self.assertTrue(os.path.exists(result["path"]))
        with zipfile.ZipFile(result["path"]) as archive:
            names = archive.namelist()
        self.assertIn("MANIFEST.json", names)
        self.assertTrue(any(name.endswith(".db") for name in names))
        self.assertIn("nightly", result["name"])
        self.assertTrue(result["verified"])

    def test_verify_accepts_an_untouched_backup(self) -> None:
        created = self.backup.create("nightly", actor="tester")
        report = self.backup.verify(created["name"])
        self.assertTrue(report["ok"])
        self.assertEqual(report["bad"], [])

    def test_verify_rejects_a_tampered_backup(self) -> None:
        created = self.backup.create("nightly", actor="tester")
        with zipfile.ZipFile(created["path"]) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
        payload = contents.pop("MANIFEST.json")
        # Rewrite the database payload but keep the old manifest.
        target = next(name for name in contents if name.endswith(".db"))
        contents[target] = contents[target] + b"tampered"
        with zipfile.ZipFile(created["path"], "w") as archive:
            for name, blob in contents.items():
                archive.writestr(name, blob)
            archive.writestr("MANIFEST.json", payload)
        report = self.backup.verify(created["name"])
        self.assertFalse(report["ok"])

    def test_restore_requires_explicit_confirmation(self) -> None:
        created = self.backup.create("nightly", actor="tester")
        self.make_book(title="Added Later", copies=1)
        self.assertEqual(
            code_of(self.backup.restore, created["name"], confirm=False),
            errors.VALIDATION_FAILED,
        )
        # Nothing was destroyed by the refusal.
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], 2)

    def test_restore_puts_the_old_data_back(self) -> None:
        created = self.backup.create("nightly", actor="tester")
        self.make_book(title="Added Later", copies=1)
        result = self.backup.restore(created["name"], actor="tester", confirm=True)
        self.assertTrue(result["restored"])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], 1)

    def test_restore_refuses_a_missing_manifest(self) -> None:
        created = self.backup.create("nightly", actor="tester")
        with zipfile.ZipFile(created["path"]) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
        contents.pop("MANIFEST.json")
        with zipfile.ZipFile(created["path"], "w") as archive:
            for name, blob in contents.items():
                archive.writestr(name, blob)
        self.assertEqual(
            code_of(self.backup.restore, created["name"], confirm=True),
            errors.RESTORE_NO_MANIFEST,
        )

    def test_zip_slip_is_refused(self) -> None:
        """A backup containing ``../../evil.txt`` must never be extracted."""
        created = self.backup.create("nightly", actor="tester")
        with zipfile.ZipFile(created["path"]) as archive:
            contents = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(contents["MANIFEST.json"].decode("utf-8"))
        evil = b"pwned"
        contents["../../evil.txt"] = evil
        files = manifest.get("files") or manifest.get("entries") or {}
        if isinstance(files, dict):
            files["../../evil.txt"] = {"sha256": __import__("hashlib").sha256(evil).hexdigest(),
                                       "size": len(evil)}
        manifest["files"] = files
        contents["MANIFEST.json"] = json.dumps(manifest).encode("utf-8")
        with zipfile.ZipFile(created["path"], "w") as archive:
            for name, blob in contents.items():
                archive.writestr(name, blob)

        code = code_of(self.backup.restore, created["name"], confirm=True)
        self.assertIn(code, (errors.UNSAFE_PATH, errors.RESTORE_INTEGRITY_FAILED))
        self.assertFalse(
            os.path.exists(os.path.join(config.data_dir().parent, "evil.txt"))
        )

    def test_path_traversal_in_the_name_is_refused(self) -> None:
        for name in ("../imri.db", "..\\imri.db", "/etc/passwd", "C:\\Windows\\system32\\x.zip"):
            code = code_of(self.backup.restore, name, confirm=True)
            self.assertIn(
                code,
                (errors.NOT_FOUND, errors.UNSAFE_PATH, errors.RESTORE_NO_FILE),
                name,
            )

    def test_delete_removes_the_file_and_the_entry(self) -> None:
        created = self.backup.create("nightly", actor="tester")
        self.backup.delete(created["name"], actor="tester")
        self.assertFalse(os.path.exists(created["path"]))
        self.assertNotIn(created["name"], [item["name"] for item in self.backup.list()])

    def test_prune_keeps_the_newest(self) -> None:
        for index in range(4):
            self.backup.create("nightly-%d" % index, actor="tester")
        removed = self.backup.prune(keep=2)
        self.assertEqual(removed, 2)
        self.assertEqual(len(self.backup.list()), 2)

    def test_last_backup_reports_the_newest(self) -> None:
        self.backup.create("first", actor="tester")
        newest = self.backup.create("second", actor="tester")
        self.assertEqual(self.backup.last_backup()["name"], newest["name"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
