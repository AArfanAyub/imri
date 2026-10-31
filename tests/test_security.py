"""Security: password hashing, sessions, and the single activation code."""

from __future__ import annotations

import unittest

from helpers import ServiceTestCase

from imri import errors
from imri.security import caps_for, hash_password, needs_rehash, verify_password


class PasswordTests(unittest.TestCase):
    def test_hash_and_verify_round_trip(self) -> None:
        stored = hash_password("correct horse battery")
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertTrue(verify_password("correct horse battery", stored))
        self.assertFalse(verify_password("wrong password", stored))

    def test_hash_is_salted(self) -> None:
        first = hash_password("same password")
        second = hash_password("same password")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("same password", first))
        self.assertTrue(verify_password("same password", second))

    def test_plaintext_is_never_embedded(self) -> None:
        self.assertNotIn("hunter2", hash_password("hunter2"))

    def test_pbkdf2_fallback_still_verifies(self) -> None:
        """A database written by a runtime without scrypt must still open."""
        import base64
        import hashlib

        salt = b"0123456789abcdef"
        digest = hashlib.pbkdf2_hmac("sha256", b"legacy-pass", salt, 120_000)
        stored = "pbkdf2_sha256$120000$%s$%s" % (
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )
        self.assertTrue(verify_password("legacy-pass", stored))
        self.assertFalse(verify_password("nope", stored))

    def test_malformed_hashes_are_rejected_without_raising(self) -> None:
        for junk in ("", "garbage", "scrypt$bad", "scrypt$ln=x,r=8,p=1$aa$bb", "$$$$"):
            self.assertFalse(verify_password("anything", junk), junk)

    def test_needs_rehash_flags_weak_settings(self) -> None:
        self.assertTrue(needs_rehash("pbkdf2_sha256$120000$aa$bb"))
        self.assertFalse(needs_rehash(hash_password("strong enough password")))
        self.assertFalse(needs_rehash(""))


class LicenseTests(ServiceTestCase):
    def test_starts_unlicensed(self) -> None:
        self.assertFalse(self.license.status()["licensed"])

    def test_the_one_code_activates(self) -> None:
        result = self.license.activate("arfanayub0")
        self.assertTrue(result["licensed"])
        self.assertTrue(self.license.status()["licensed"])
        self.assertEqual(result["edition"], "perpetual")

    def test_code_is_case_and_space_insensitive(self) -> None:
        for variant in ("ARFANAYUB0", "  arfanayub0  ", "ArfanAyub0"):
            self.license = type(self.license)(self.db)  # fresh gate
            self.license.activate(variant)
            self.assertTrue(self.license.status()["licensed"], variant)

    def test_any_other_code_is_refused(self) -> None:
        for wrong in ("arfanayub", "arfanayub01", "arfanayubo", "", "admin", "IMRI-2026"):
            gate = type(self.license)(self.db)
            with self.assertRaises(errors.ImriError):
                gate.activate(wrong)
            self.assertFalse(gate.status()["licensed"], wrong)

    def test_failed_attempts_trigger_a_cooldown(self) -> None:
        gate = type(self.license)(self.db)
        for _ in range(5):
            with self.assertRaises(errors.ImriError):
                gate.activate("wrong")
        with self.assertRaises(errors.ImriError) as caught:
            gate.activate("arfanayub0")
        # The cooldown must block even the correct code.
        self.assertIsNotNone(caught.exception)

    def test_activation_is_persisted_across_instances(self) -> None:
        self.license.activate("arfanayub0")
        reopened = type(self.license)(self.db)
        self.assertTrue(reopened.status()["licensed"])

    def test_activation_is_audited(self) -> None:
        self.license.activate("arfanayub0")
        rows = self.db.query("SELECT action FROM audit_log WHERE action LIKE 'license%'")
        self.assertTrue(rows)


class SessionTests(ServiceTestCase):
    def test_session_lifecycle(self) -> None:
        session = self.security.login("admin", "admin123", client="127.0.0.1")
        token = session["token"]
        self.assertTrue(self.security.sessions.lookup(token))
        self.security.sessions.revoke(token)
        self.assertIsNone(self.security.sessions.lookup(token))

    def test_wrong_password_is_rejected(self) -> None:
        with self.assertRaises(errors.ImriError):
            self.security.login("admin", "not-the-password")

    def test_role_capabilities(self) -> None:
        self.assertIn("book.write", caps_for("admin"))
        self.assertNotIn("book.write", caps_for("viewer"))
        self.assertIn("circ.write", caps_for("assistant"))
        self.assertNotIn("book.delete", caps_for("assistant"))


if __name__ == "__main__":
    unittest.main()
