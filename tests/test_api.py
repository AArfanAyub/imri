"""HTTP layer: the licence gate, authentication, CSRF, RBAC and hostile requests.

These tests talk to a real socket through a real ``ThreadingHTTPServer``, because
the things being tested — cookie flags, the order of the middleware chain, the
status code a browser will actually receive — cannot be observed by calling a
Python function.

The most important test here is :meth:`LicenseGateTests.test_data_routes_are_blocked_when_unlicensed`.
The licence check is server middleware precisely so that opening DevTools and
calling the API by hand cannot get past it.
"""

from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.parse
import urllib.request

from helpers import ServiceTestCase

from imri import errors
from imri.server import HttpServer

LICENCE_CODE = "arfanayub0"
GOOD_PASSWORD = "Imri-Test-Pass-9"


class ApiTestCase(ServiceTestCase):
    """Boots the real server on an ephemeral port and speaks HTTP to it."""

    def setUp(self) -> None:
        super().setUp()
        self.server = HttpServer(self.db, host="127.0.0.1", port=0)
        self.server.start_background()
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.port
        self.cookie = ""

    # -- transport ------------------------------------------------------- #
    def request(self, method, path, body=None, headers=None, csrf=True, raw=None):
        """Send one request. Returns ``(status, payload, headers)``."""
        url = self.base + path
        data = None
        merged = {"Accept": "application/json"}
        if raw is not None:
            data = raw
            merged["Content-Type"] = "text/csv"
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            merged["Content-Type"] = "application/json"
        if csrf and method in ("POST", "PUT", "PATCH", "DELETE"):
            merged["X-IMRI-CSRF"] = "1"
        if self.cookie:
            merged["Cookie"] = self.cookie
        merged.update(headers or {})

        request = urllib.request.Request(url, data=data, method=method, headers=merged)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
                status = response.status
                response_headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            status = exc.code
            response_headers = dict(exc.headers.items())

        set_cookie = response_headers.get("Set-Cookie")
        if set_cookie:
            self.cookie = set_cookie.split(";", 1)[0]
        try:
            return status, json.loads(payload.decode("utf-8")), response_headers
        except (ValueError, UnicodeDecodeError):
            return status, {"_raw": payload}, response_headers

    def activate(self):
        return self.request("POST", "/api/license/activate", {"code": LICENCE_CODE})

    def sign_in(self, username="admin", password="admin123"):
        """Activate, log in and clear the forced password change."""
        self.activate()
        status, payload, _ = self.request(
            "POST", "/api/auth/login", {"username": username, "password": password}
        )
        self.assertEqual(status, 200, payload)
        if payload["data"].get("must_change_password"):
            status, payload, _ = self.request(
                "POST",
                "/api/auth/password",
                {"current_password": password, "new_password": GOOD_PASSWORD},
            )
            self.assertEqual(status, 200, payload)
        return payload

    def error_code(self, payload):
        return (payload.get("error") or {}).get("code")


class LicenseGateTests(ApiTestCase):
    def test_only_the_escape_hatches_work_before_activation(self) -> None:
        """Health, bootstrap and the activate route — nothing else."""
        self.assertEqual(self.request("GET", "/api/health")[0], 200)
        # Bootstrap must answer so the shell can decide to paint the activation
        # screen; it must say "not licensed" rather than refuse.
        status, payload, _ = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200, payload)
        self.assertFalse(payload["data"]["licensed"])
        # Everything else is behind the gate, including the version endpoint.
        for path in ("/api/version", "/api/license", "/api/auth/session"):
            status, payload, _ = self.request("GET", path)
            self.assertEqual(status, 403, path)

    def test_data_routes_are_blocked_when_unlicensed(self) -> None:
        """The gate is middleware: no browser, no session, no way around it."""
        for path in ("/api/books", "/api/members", "/api/dashboard", "/api/settings",
                     "/api/reports/collection", "/api/users", "/api/audit"):
            status, payload, _ = self.request("GET", path)
            self.assertEqual(status, 403, path)
            self.assertEqual(self.error_code(payload), errors.LICENSE_REQUIRED, path)

    def test_writes_are_blocked_when_unlicensed(self) -> None:
        status, payload, _ = self.request("POST", "/api/books", {"title": "Sneaky"})
        self.assertEqual(status, 403)
        self.assertEqual(self.error_code(payload), errors.LICENSE_REQUIRED)

    def test_the_wrong_code_is_refused_and_grants_nothing(self) -> None:
        for wrong in ("arfanayub", "IMRI-2026", "admin", ""):
            status, payload, _ = self.request(
                "POST", "/api/license/activate", {"code": wrong}
            )
            self.assertGreaterEqual(status, 400, wrong)
            self.assertNotEqual(status, 200, wrong)
        status, payload, _ = self.request("GET", "/api/books")
        self.assertEqual(status, 403)

    def test_the_one_code_unlocks_the_product(self) -> None:
        status, payload, _ = self.activate()
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["data"]["licensed"])
        status, _, _ = self.request("GET", "/api/books")
        self.assertEqual(status, 401)  # now it wants a session, not a licence


class AuthTests(ApiTestCase):
    def test_a_data_route_needs_a_session(self) -> None:
        self.activate()
        status, payload, _ = self.request("GET", "/api/books")
        self.assertEqual(status, 401)
        self.assertEqual(self.error_code(payload), errors.AUTH_REQUIRED)

    def test_login_sets_a_hardened_cookie(self) -> None:
        self.activate()
        status, payload, headers = self.request(
            "POST", "/api/auth/login", {"username": "admin", "password": "admin123"}
        )
        self.assertEqual(status, 200, payload)
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("imri_sid=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("admin123", json.dumps(payload))
        self.assertNotIn("password_hash", json.dumps(payload))

    def test_wrong_password_is_rejected(self) -> None:
        self.activate()
        status, payload, _ = self.request(
            "POST", "/api/auth/login", {"username": "admin", "password": "wrong-one"}
        )
        self.assertEqual(status, 401)
        self.assertEqual(self.error_code(payload), errors.INVALID_CREDENTIALS)

    def test_repeated_failures_are_rate_limited(self) -> None:
        self.activate()
        codes = []
        for _ in range(12):
            status, payload, _ = self.request(
                "POST", "/api/auth/login", {"username": "admin", "password": "nope"}
            )
            codes.append(status)
        self.assertIn(429, codes, "a brute-force run must eventually be throttled")

    def test_logout_revokes_the_session(self) -> None:
        self.sign_in()
        self.request("POST", "/api/auth/logout", {})
        status, _, _ = self.request("GET", "/api/books")
        self.assertEqual(status, 401)

    def test_password_change_revokes_the_old_session(self) -> None:
        self.activate()
        self.request(
            "POST", "/api/auth/login", {"username": "admin", "password": "admin123"}
        )
        stolen = self.cookie
        self.request(
            "POST",
            "/api/auth/password",
            {"current_password": "admin123", "new_password": GOOD_PASSWORD},
        )
        self.cookie = stolen
        status, _, _ = self.request("GET", "/api/books")
        self.assertEqual(status, 401)

    def test_a_weak_password_is_refused(self) -> None:
        self.activate()
        self.request(
            "POST", "/api/auth/login", {"username": "admin", "password": "admin123"}
        )
        status, payload, _ = self.request(
            "POST",
            "/api/auth/password",
            {"current_password": "admin123", "new_password": "123"},
        )
        self.assertIn(status, (400, 422))
        self.assertEqual(self.error_code(payload), errors.WEAK_PASSWORD)


class CsrfTests(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sign_in()

    def test_a_mutation_without_the_csrf_header_is_refused(self) -> None:
        status, payload, _ = self.request(
            "POST", "/api/books", {"title": "No Header"}, csrf=False
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.error_code(payload), errors.FORBIDDEN)

    def test_a_mutation_with_the_header_is_allowed(self) -> None:
        status, payload, _ = self.request(
            "POST", "/api/books", {"title": "With Header", "copies": 1}
        )
        self.assertEqual(status, 200, payload)

    def test_a_cross_site_origin_is_refused(self) -> None:
        status, payload, _ = self.request(
            "POST",
            "/api/books",
            {"title": "Cross Site"},
            headers={"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        self.assertEqual(status, 403)

    def test_reads_need_no_csrf_header(self) -> None:
        status, _, _ = self.request("GET", "/api/books", csrf=False)
        self.assertEqual(status, 200)


class RbacTests(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sign_in()
        self.security.create_user("viewer1", "Viewer-Pass-99", "Vera Viewer", "viewer")
        self.security.create_user("assist1", "Assist-Pass-99", "Asif Assistant", "assistant")

    def as_user(self, username, password):
        self.cookie = ""
        status, payload, _ = self.request(
            "POST", "/api/auth/login", {"username": username, "password": password}
        )
        self.assertEqual(status, 200, payload)

    def test_a_viewer_can_read_but_not_write(self) -> None:
        self.as_user("viewer1", "Viewer-Pass-99")
        self.assertEqual(self.request("GET", "/api/books")[0], 200)
        status, payload, _ = self.request(
            "POST", "/api/books", {"title": "Viewer Book", "copies": 1}
        )
        self.assertEqual(status, 403)
        self.assertEqual(self.error_code(payload), errors.FORBIDDEN)

    def test_a_viewer_cannot_reach_admin_areas(self) -> None:
        self.as_user("viewer1", "Viewer-Pass-99")
        for path in ("/api/users", "/api/audit", "/api/settings"):
            status, _, _ = self.request("GET", path)
            self.assertEqual(status, 403, path)

    def test_an_assistant_can_run_the_counter_but_not_delete_books(self) -> None:
        book = self.make_book(title="Assistant Target", copies=2)["book"]
        self.as_user("assist1", "Assist-Pass-99")
        self.assertEqual(self.request("GET", "/api/circulation/active")[0], 200)
        status, _, _ = self.request("DELETE", "/api/books/%d" % book["id"])
        self.assertEqual(status, 403)

    def test_a_viewer_cannot_issue_a_loan(self) -> None:
        self.as_user("viewer1", "Viewer-Pass-99")
        status, payload, _ = self.request(
            "POST",
            "/api/circulation/issue",
            {"copy_barcode": "X-1", "member_code": "S00001"},
        )
        self.assertEqual(status, 403)

    def test_session_endpoint_reports_capabilities(self) -> None:
        self.as_user("viewer1", "Viewer-Pass-99")
        status, payload, _ = self.request("GET", "/api/auth/session")
        self.assertEqual(status, 200)
        caps = payload["data"]["user"]["caps"]
        self.assertIn("book.read", caps)
        self.assertNotIn("book.write", caps)


class HostileRequestTests(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sign_in()

    def test_an_unknown_route_is_404(self) -> None:
        status, _, _ = self.request("GET", "/api/does-not-exist")
        self.assertEqual(status, 404)

    def test_the_wrong_method_is_405_or_404(self) -> None:
        status, _, _ = self.request("DELETE", "/api/dashboard")
        self.assertIn(status, (403, 404, 405))

    def test_static_traversal_is_refused(self) -> None:
        for path in (
            "/../imri/config.py",
            "/%2e%2e/%2e%2e/imri/db.py",
            "/js/../../imri/db.py",
            "/..%5cimri%5cdb.py",
        ):
            status, _, _ = self.request("GET", path)
            self.assertIn(status, (400, 403, 404), path)

    def test_sql_injection_in_a_query_parameter_is_inert(self) -> None:
        payload = "'; DROP TABLE books; --"
        status, body, _ = self.request("GET", "/api/books?q=" + urllib.parse.quote(payload))
        self.assertEqual(status, 200)
        self.assertEqual(body["meta"]["total"], 0)
        self.assertTrue(self.db.integrity_check()["ok"])
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], 0)

    def test_an_oversized_body_is_refused(self) -> None:
        blob = {"title": "x" * (9 * 1024 * 1024), "copies": 1}
        status, payload, _ = self.request("POST", "/api/books", blob)
        self.assertEqual(status, 413)
        self.assertEqual(self.error_code(payload), errors.PAYLOAD_TOO_LARGE)

    def test_a_non_json_body_is_415(self) -> None:
        status, payload, _ = self.request(
            "POST", "/api/books", headers={"Content-Type": "text/plain"}, raw=b"title=x"
        )
        self.assertEqual(status, 415)

    def test_malformed_json_is_a_clean_400(self) -> None:
        status, payload, _ = self.request(
            "POST",
            "/api/books",
            headers={"Content-Type": "application/json"},
            raw=b"{not json at all",
        )
        self.assertIn(status, (400, 422))

    def test_an_unknown_field_is_ignored_not_stored(self) -> None:
        status, payload, _ = self.request(
            "POST",
            "/api/books",
            {"title": "Field Test", "copies": 1, "is_admin": True, "role": "admin"},
        )
        self.assertEqual(status, 200, payload)
        book = self.db.one("SELECT * FROM books WHERE title = 'Field Test'")
        self.assertIsNotNone(book)
        self.assertFalse(hasattr(book, "is_admin"))

    def test_security_headers_are_present(self) -> None:
        _, _, headers = self.request("GET", "/api/version")
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")

    def test_the_shell_is_served_without_a_session(self) -> None:
        """The login screen has to load before anyone is signed in."""
        request = urllib.request.Request(self.base + "/")
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
        self.assertIn("<html", body.lower())

    def test_ids_that_are_not_numbers_are_rejected(self) -> None:
        for path in ("/api/books/abc", "/api/loans/abc", "/api/users/abc"):
            status, _, _ = self.request("GET", path)
            self.assertIn(status, (400, 403, 404, 422), path)

    def test_a_negative_page_does_not_crash_the_server(self) -> None:
        status, payload, _ = self.request("GET", "/api/books?page=-5&page_size=-10")
        self.assertEqual(status, 200)
        self.assertEqual(payload["meta"]["page"], 1)
        self.assertGreaterEqual(payload["meta"]["page_size"], 1)

    def test_a_huge_page_size_is_clamped(self) -> None:
        status, payload, _ = self.request("GET", "/api/books?page_size=100000")
        self.assertEqual(status, 200)
        self.assertLessEqual(payload["meta"]["page_size"], 200)


class LiveDataTests(ApiTestCase):
    """The documented contract, exercised end to end over HTTP."""

    def setUp(self) -> None:
        super().setUp()
        self.sign_in()

    def test_create_book_then_read_it_back(self) -> None:
        status, payload, _ = self.request(
            "POST",
            "/api/books",
            {"title": "Live Book", "authors": "A. Author", "copies": 2},
        )
        self.assertEqual(status, 200, payload)
        book_id = payload["data"]["book"]["id"]

        status, payload, _ = self.request("GET", "/api/books/%d" % book_id)
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["book"]["title"], "Live Book")
        self.assertEqual(len(payload["data"]["copies"]), 2)

    def test_validation_errors_come_back_per_field(self) -> None:
        status, payload, _ = self.request(
            "POST", "/api/books", {"title": "", "isbn": "9780306406150"}
        )
        self.assertEqual(status, 422)
        self.assertEqual(self.error_code(payload), errors.TITLE_REQUIRED)
        self.assertIn("title", (payload["error"].get("fields") or {}))

    def test_the_issue_and_return_round_trip(self) -> None:
        book = self.make_book(title="Round Trip", copies=1)["book"]
        member = self.make_member("Round Trip Reader")["member"]
        copy = self.copies_of(book["id"])[0]

        status, payload, _ = self.request(
            "POST",
            "/api/circulation/issue",
            {"copy_barcode": copy["barcode"], "member_code": member["member_code"]},
        )
        self.assertEqual(status, 200, payload)
        loan_id = payload["data"]["loan"]["id"]

        status, payload, _ = self.request(
            "POST", "/api/circulation/return", {"loan_id": loan_id}
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["loan"]["status"], "returned")

    def test_pagination_meta_is_the_documented_shape(self) -> None:
        for index in range(3):
            self.make_book(title="Meta %d" % index, copies=1)
        status, payload, _ = self.request("GET", "/api/books?page=1&page_size=2")
        self.assertEqual(status, 200)
        meta = payload["meta"]
        for key in ("page", "page_size", "total", "pages", "sort", "order"):
            self.assertIn(key, meta)
        self.assertEqual(meta["total"], 3)
        self.assertEqual(len(payload["data"]["items"]), 2)

    def test_csv_import_preview_reports_without_writing(self) -> None:
        raw = b"title,authors,copies\nPreview Only,A,1\n"
        status, payload, _ = self.request(
            "POST", "/api/books/import/preview", raw=raw
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["ok_count"], 1)
        self.assertEqual(self.db.one("SELECT COUNT(*) AS n FROM books")["n"], 0)

    def test_an_export_downloads(self) -> None:
        self.make_book(title="Exportable", copies=1)
        status, payload, _ = self.request("GET", "/api/books/export?format=csv")
        self.assertEqual(status, 200, payload)
        filename = payload["data"]["filename"]
        status, body, headers = self.request("GET", "/api/exports/" + filename)
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers.get("Content-Type", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
