"""Database layer: schema shape, idempotence, migration and invariants."""

from __future__ import annotations

import os
import tempfile
import unittest

from helpers import ServiceTestCase, rollup_mismatches

from imri import config
from imri.db import Database


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="imri-db-")
        self.path = os.path.join(self.dir, "imri.db")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def test_schema_creates_expected_objects(self) -> None:
        db = Database(self.path)
        try:
            counts = db.object_counts()
            self.assertEqual(counts["tables"], 18)
            self.assertGreaterEqual(counts["indexes"], 30)
            self.assertEqual(counts["triggers"], 2)
            self.assertEqual(db.schema_version(), config.SCHEMA_VERSION)
        finally:
            db.close()

    def test_opening_twice_is_idempotent(self) -> None:
        first = Database(self.path)
        first.close()
        second = Database(self.path)  # must not raise
        try:
            self.assertEqual(second.object_counts()["tables"], 18)
        finally:
            second.close()

    def test_settings_are_seeded(self) -> None:
        db = Database(self.path)
        try:
            settings = db.all_settings()
            for key in ("school_name", "currency", "loan_days", "max_loans", "fine_per_day"):
                self.assertIn(key, settings)
            self.assertEqual(settings["loan_days"], "14")
        finally:
            db.close()

    def test_default_admin_is_seeded_and_must_change(self) -> None:
        db = Database(self.path)
        try:
            admin = db.one("SELECT * FROM users WHERE username = 'admin'")
            self.assertIsNotNone(admin)
            self.assertEqual(admin["role"], "admin")
            self.assertEqual(admin["must_change_password"], 1)
            self.assertTrue(admin["password_hash"].startswith("scrypt$"))
            # The plaintext default must never be stored.
            self.assertNotIn("admin123", admin["password_hash"])
        finally:
            db.close()

    def test_newer_schema_is_refused(self) -> None:
        db = Database(self.path)
        db.execute("UPDATE schema_meta SET version = 99")
        db.close()
        with self.assertRaises(Exception):
            Database(self.path)

    def test_foreign_keys_are_on(self) -> None:
        db = Database(self.path)
        try:
            self.assertTrue(db.integrity_check()["ok"])
        finally:
            db.close()


class AuditTriggerTests(ServiceTestCase):
    def test_audit_rows_cannot_be_updated(self) -> None:
        self.db.audit("tester", "unit.test", "target", "detail")
        row = self.db.one("SELECT id FROM audit_log ORDER BY id DESC LIMIT 1")
        with self.assertRaises(Exception):
            self.db.execute("UPDATE audit_log SET action = 'tampered' WHERE id = ?", (row["id"],))

    def test_audit_rows_cannot_be_deleted(self) -> None:
        self.db.audit("tester", "unit.test", "target", "detail")
        with self.assertRaises(Exception):
            self.db.execute("DELETE FROM audit_log")


class RollupTests(ServiceTestCase):
    def test_rollup_matches_copies_after_creation(self) -> None:
        book = self.make_book(copies=3)["book"]
        self.assertEqual(rollup_mismatches(self.db), [])
        self.assertEqual(book["copies"], 3)

    def test_rollup_repairs_a_corrupted_count(self) -> None:
        book = self.make_book(copies=2)["book"]
        self.db.execute("UPDATE books SET copies = 9 WHERE id = ?", (book["id"],))
        self.assertTrue(rollup_mismatches(self.db))

        self.catalog.refresh_book_rollup(book["id"])
        self.assertEqual(rollup_mismatches(self.db), [])


if __name__ == "__main__":
    unittest.main()
