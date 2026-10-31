"""Shared fixtures for the IMRI test suite (standard library only).

Every test gets a private database in a temporary directory. Nothing touches the
real ``data/`` folder, so a test run can never damage a live library.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from imri import config  # noqa: E402
from imri.catalog import CatalogService  # noqa: E402
from imri.circulation import CirculationService  # noqa: E402
from imri.db import Database  # noqa: E402
from imri.members import MemberService  # noqa: E402
from imri.reporting import ReportingService  # noqa: E402
from imri.security import LicenseGate, SecurityService  # noqa: E402
from imri.transfer import BackupService, TransferService  # noqa: E402


class ServiceTestCase(unittest.TestCase):
    """A test case with a fresh database and every service wired up."""

    def setUp(self) -> None:
        self._dir = tempfile.mkdtemp(prefix="imri-test-")
        # ``data/license.json`` is install-global, not per-database, so it has
        # to be redirected too or one test's activation leaks into the next.
        self._prev_data_dir = os.environ.get(config.DATA_DIR_ENV)
        os.environ[config.DATA_DIR_ENV] = self._dir
        self.addCleanup(self._cleanup)
        self.db = Database(os.path.join(self._dir, "imri.db"))
        self.catalog = CatalogService(self.db)
        self.members = MemberService(self.db, self.catalog)
        self.circulation = CirculationService(self.db, self.catalog)
        self.reporting = ReportingService(self.db)
        self.transfer = TransferService(self.db, self.catalog)
        self.backup = BackupService(self.db)
        self.security = SecurityService(self.db)
        self.license = LicenseGate(self.db)

    def _cleanup(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass
        if self._prev_data_dir is None:
            os.environ.pop(config.DATA_DIR_ENV, None)
        else:
            os.environ[config.DATA_DIR_ENV] = self._prev_data_dir
        import shutil

        shutil.rmtree(self._dir, ignore_errors=True)

    # -- convenience ---------------------------------------------------- #
    def make_book(self, title: str = "Test Book", copies: int = 1, **extra):
        fields = {
            "title": title,
            "authors": "A. Author",
            "material_type": "book",
            "copies": copies,
        }
        fields.update(extra)
        return self.catalog.create_book(fields, actor="tester")

    def make_member(self, name: str = "Test Reader", **extra):
        fields = {"name": name, "member_type": "student"}
        fields.update(extra)
        return self.members.create_member(fields, actor="tester")

    def copies_of(self, book_id: int):
        return self.db.query(
            "SELECT * FROM book_copies WHERE book_id = ? ORDER BY copy_no", (book_id,)
        )


def rollup_mismatches(db: Database) -> list[dict]:
    """Books whose denormalised copy count disagrees with the copy rows."""
    return db.query(
        "SELECT b.id, b.copies, "
        "       (SELECT COUNT(*) FROM book_copies c WHERE c.book_id = b.id) AS actual "
        "FROM books b "
        "WHERE b.copies <> (SELECT COUNT(*) FROM book_copies c WHERE c.book_id = b.id)"
    )


__all__ = ["ServiceTestCase", "rollup_mismatches", "config"]
