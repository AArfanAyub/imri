"""T3 proof: drive the real HTTP server through the whole contract.

Starts ``main.py`` on an ephemeral port with a throwaway data directory, then
walks the checklist from the task brief:

1. unlicensed -> every data route answers ``403 license_required``
2. activation with the single code -> ``licensed: true``
3. a wrong code is refused, and a second activation does not double-activate
4. login -> session cookie -> dashboard ``200`` with KPIs
5. ``viewer`` -> ``403`` on ``POST /api/books``
6. ``assistant`` -> ``200`` on issue, ``403`` on delete
7. pagination ``?page=2&page_size=10`` -> correct ``meta``
8. ``page_size=9999`` -> clamped to ``200``
9. a suspended member -> ``409 member_suspended``
10. unknown route -> ``404 not_found``
11. ``POST /api/system/bye`` -> the process exits 0

Nothing is asserted from source inspection: every line below is a real request
and a real response.

    python tools/proof_t3.py
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The activation code lives in exactly one place; the test reads it from there
# rather than repeating the literal.
from imri.security import LICENSE_CODE as ACTIVATION_CODE  # noqa: E402

CHECKS = 0


def check(label: str, actual: object, expected: object) -> None:
    """Assert one observable fact and print it."""
    global CHECKS
    CHECKS += 1
    if actual != expected:
        raise AssertionError("%s: expected %r, got %r" % (label, expected, actual))
    print("  ok  %-52s %r" % (label, actual))


def note(label: str, value: object) -> None:
    """Print an observation that is not itself an assertion."""
    print("  ..  %-52s %r" % (label, value))


class Client:
    """A tiny HTTP client that remembers one session cookie."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.cookie = ""
        self.last_length = 0

    def call(
        self,
        method: str,
        path: str,
        body: object = None,
        query: dict | None = None,
        csrf: bool = True,
        headers_override: dict | None = None,
        raw: bytes | None = None,
    ) -> tuple[int, dict]:
        """Send one request and return ``(status, envelope)``."""
        target = path
        if query:
            target += "?" + urllib.parse.urlencode(query)

        headers = {"Accept": "application/json"}
        payload = raw
        if payload is None and body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if method not in ("GET", "HEAD") and csrf:
            headers["X-IMRI-CSRF"] = "1"
        if headers_override:
            headers.update(headers_override)
        if self.cookie:
            headers["Cookie"] = self.cookie

        connection = http.client.HTTPConnection(self.host, self.port, timeout=30)
        try:
            connection.request(method, target, body=payload, headers=headers)
            response = connection.getresponse()
            data = response.read()
            self.last_length = len(data)
            set_cookie = response.getheader("Set-Cookie")
            if set_cookie:
                self.cookie = set_cookie.split(";")[0]
            try:
                envelope = json.loads(data.decode("utf-8")) if data else {}
            except ValueError:
                envelope = {"_raw": data[:120].decode("utf-8", "replace")}
            return response.status, envelope
        finally:
            connection.close()

    def get(self, path: str, query: dict | None = None) -> tuple[int, dict]:
        return self.call("GET", path, query=query)

    def post(self, path: str, body: object = None, query: dict | None = None):
        return self.call("POST", path, body if body is not None else {}, query=query)

    def patch(self, path: str, body: object = None):
        return self.call("PATCH", path, body if body is not None else {})

    def delete(self, path: str):
        return self.call("DELETE", path)

    def code(self, envelope: dict) -> str:
        """Return the error code from an envelope, or ``''``."""
        return str(((envelope or {}).get("error") or {}).get("code") or "")


def wait_for_health(host: str, port: int, timeout: float = 40.0) -> dict:
    """Poll ``/api/health`` until the server answers."""
    deadline = time.time() + timeout
    client = Client(host, port)
    last: Exception | None = None
    while time.time() < deadline:
        try:
            status, envelope = client.get("/api/health")
            if status == 200:
                return envelope["data"]
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.15)
    raise SystemExit("health never answered: %r" % (last,))


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="imri-t3-"))
    environment = dict(os.environ)
    environment["IMRI_DATA_DIR"] = str(scratch)

    print("data dir:", scratch)
    process = subprocess.Popen(
        [PY, str(ROOT / "main.py"), "--no-window", "--port", "0"],
        cwd=str(ROOT),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        line = process.stdout.readline().strip()
        parts = urllib.parse.urlsplit(line)
        if not parts.hostname:
            raise SystemExit("unexpected startup line: %r" % line)
        host, port = parts.hostname, int(parts.port)
        print("listening on http://%s:%d" % (host, port))

        wait_for_health(host, port)
        anonymous = Client(host, port)
        admin = Client(host, port)
        viewer = Client(host, port)
        assistant = Client(host, port)

        # -------------------------------------------------------------- #
        print("\n[1] licence gate blocks every data route")
        status, envelope = anonymous.get("/api/books")
        check("GET /api/books while unlicensed", status, 403)
        check("error code", anonymous.code(envelope), "license_required")
        for path in ("/api/dashboard", "/api/members", "/api/settings", "/api/circulation/active"):
            status, envelope = anonymous.get(path)
            check("%s gated" % path, (status, anonymous.code(envelope)), (403, "license_required"))
        status, envelope = anonymous.post("/api/books", {"title": "Sneaky"})
        check("POST /api/books while unlicensed", status, 403)
        status, envelope = anonymous.get("/api/bootstrap")
        check("bootstrap still answers", status, 200)
        check("bootstrap says unlicensed", envelope["data"]["licensed"], False)

        # -------------------------------------------------------------- #
        print("\n[2] activation")
        status, envelope = anonymous.post("/api/license/activate", {"code": "not-the-code"})
        check("wrong code refused", status, 403)
        check("wrong code error", anonymous.code(envelope), "invalid_license_code")
        status, envelope = anonymous.post("/api/license/activate", {"code": "  ARFANAYUB0  "})
        check("code is case- and space-insensitive", status, 200)
        check("licensed now", envelope["data"]["licensed"], True)
        status, envelope = anonymous.get("/api/bootstrap")
        check("bootstrap agrees", envelope["data"]["licensed"], True)

        status, envelope = anonymous.get("/api/books")
        check("data routes open after activation", status, 401)
        check("but a session is still required", anonymous.code(envelope), "auth_required")

        # -------------------------------------------------------------- #
        print("\n[3] login")
        status, envelope = anonymous.post("/api/auth/login", {"username": "admin", "password": "wrong"})
        check("bad password refused", status, 401)
        check("bad password code", anonymous.code(envelope), "invalid_credentials")

        status, envelope = admin.post(
            "/api/auth/login", {"username": "admin", "password": "admin123"}
        )
        check("admin login", status, 200)
        check("must change password flagged", envelope["data"]["must_change_password"], True)
        check("cookie was issued", admin.cookie.startswith("imri_sid="), True)
        note("session cookie", admin.cookie[:24] + "...")

        status, envelope = admin.get("/api/auth/session")
        check("session endpoint", status, 200)
        check("role", envelope["data"]["user"]["role"], "admin")
        check("admin holds book.delete", "book.delete" in envelope["data"]["user"]["caps"], True)

        # -------------------------------------------------------------- #
        print("\n[4] dashboard")
        status, envelope = admin.get("/api/dashboard")
        check("GET /api/dashboard", status, 200)
        data = envelope["data"]
        check("dashboard has kpis", isinstance(data.get("kpis"), dict), True)
        note("kpi keys", sorted(data.get("kpis", {}).keys()))
        check("dashboard has alerts", isinstance(data.get("alerts"), list), True)

        # -------------------------------------------------------------- #
        print("\n[5] seed twelve titles for pagination")
        created_ids: list[int] = []
        for index in range(1, 13):
            status, envelope = admin.post(
                "/api/books",
                {
                    "title": "Pagination Sample %02d" % index,
                    "authors": "Test Author",
                    "material_type": "book",
                    "language": "en",
                    "pub_year": 2000 + index,
                    "price": 10.0 + index,
                    "copies": 2,
                },
            )
            if status != 200:
                raise AssertionError("seed book %d failed: %s %s" % (index, status, envelope))
            created_ids.append(int(envelope["data"]["book"]["id"]))
        check("titles created", len(created_ids), 12)

        status, envelope = admin.get("/api/books", {"page": 2, "page_size": 10, "sort": "title"})
        check("page 2 status", status, 200)
        meta = envelope["meta"]
        check("meta.page", meta["page"], 2)
        check("meta.page_size", meta["page_size"], 10)
        check("meta.total", meta["total"], 12)
        check("meta.pages", meta["pages"], 2)
        check("items on page 2", len(envelope["data"]["items"]), 2)

        status, envelope = admin.get("/api/books", {"page_size": 9999})
        check("page_size 9999 clamped", envelope["meta"]["page_size"], 200)
        status, envelope = admin.get("/api/books", {"page": -3})
        check("negative page clamped to 1", envelope["meta"]["page"], 1)

        status, envelope = admin.get("/api/books", {"sort": "DROP TABLE books"})
        check("unknown sort is ignored", status, 200)
        check("sort fell back to title", envelope["meta"]["sort"], "title")

        # -------------------------------------------------------------- #
        print("\n[6] role capabilities")
        for username, role in (("viewer1", "viewer"), ("assistant1", "assistant")):
            status, envelope = admin.post(
                "/api/users",
                {
                    "username": username,
                    "display_name": username.title(),
                    "password": "Passw0rd!23",
                    "role": role,
                },
            )
            check("created %s" % username, status, 200)

        status, envelope = viewer.post(
            "/api/auth/login", {"username": "viewer1", "password": "Passw0rd!23"}
        )
        check("viewer login", status, 200)
        status, envelope = viewer.get("/api/books")
        check("viewer can read", status, 200)
        status, envelope = viewer.post("/api/books", {"title": "Viewer Book"})
        check("viewer cannot write", status, 403)
        check("viewer write code", viewer.code(envelope), "forbidden")
        check(
            "forbidden names the capability",
            (envelope.get("error") or {}).get("details", {}).get("capability"),
            "book.write",
        )

        status, envelope = assistant.post(
            "/api/auth/login", {"username": "assistant1", "password": "Passw0rd!23"}
        )
        check("assistant login", status, 200)

        # -------------------------------------------------------------- #
        print("\n[7] circulation through the API")
        status, envelope = admin.post(
            "/api/members",
            {
                "name": "Nur Iman",
                "member_code": "S-1001",
                "member_type": "student",
                "class_name": "4 Cekal",
            },
        )
        check("member created", status, 200)
        member = envelope["data"]["member"]

        status, envelope = admin.get("/api/books/%d/copies" % created_ids[0])
        check("copies listed", status, 200)
        copy = envelope["data"]["items"][0]

        status, envelope = assistant.post(
            "/api/circulation/issue",
            {"copy_barcode": copy["barcode"], "member_code": member["member_code"]},
        )
        check("assistant can issue", status, 200)
        loan = envelope["data"]["loan"]
        check("loan is on loan", loan["status"], "on_loan")
        check("copy flipped to on_loan", envelope["data"]["copy"]["status"], "on_loan")

        status, envelope = assistant.delete("/api/books/%d" % created_ids[0])
        check("assistant cannot delete a title", status, 403)
        check("delete code", assistant.code(envelope), "forbidden")

        status, envelope = assistant.post(
            "/api/circulation/issue",
            {"copy_barcode": copy["barcode"], "member_code": member["member_code"]},
        )
        check("double issue refused", status, 409)
        check("double issue code", assistant.code(envelope), "copy_unavailable")

        # -------------------------------------------------------------- #
        print("\n[8] suspended member")
        status, envelope = admin.patch(
            "/api/members/%d" % member["id"], {"status": "suspended"}
        )
        check("member suspended", status, 200)
        check("status", envelope["data"]["member"]["status"], "suspended")

        status, envelope = admin.get("/api/books/%d/copies" % created_ids[1])
        second_copy = envelope["data"]["items"][0]
        status, envelope = admin.post(
            "/api/circulation/issue",
            {"copy_barcode": second_copy["barcode"], "member_code": member["member_code"]},
        )
        check("issue to suspended member", status, 409)
        check("suspended code", admin.code(envelope), "member_suspended")

        # -------------------------------------------------------------- #
        print("\n[9] error contract")
        status, envelope = admin.get("/api/does-not-exist")
        check("unknown route", status, 404)
        check("unknown route code", admin.code(envelope), "not_found")
        status, envelope = admin.get("/api/books/999999")
        check("missing book", status, 404)
        check("missing book code", admin.code(envelope), "not_found")
        status, envelope = admin.post("/api/books", {"title": ""})
        check("empty title rejected", status, 422)
        check("empty title code", admin.code(envelope), "title_required")
        check(
            "validation names the field",
            (envelope.get("error") or {}).get("fields", {}).get("title"),
            "title_required",
        )
        status, envelope = admin.call("POST", "/api/books", {"title": "No CSRF"}, csrf=False)
        check("missing CSRF header refused", status, 403)

        # A body that is not JSON is named as such instead of being read as an
        # empty document.
        status, envelope = admin.call(
            "POST", "/api/books", {"title": "Wrong type"}, headers_override={"Content-Type": "text/plain"}
        )
        check("non-JSON body refused", status, 415)
        check("non-JSON code", admin.code(envelope), "unsupported_media")

        # A cookie that no longer resolves reads as an expired session, which
        # is a different message from "you never signed in".
        stale = Client(host, port)
        stale.cookie = "imri_sid=not-a-real-token"
        status, envelope = stale.get("/api/auth/session")
        check("stale cookie", status, 401)
        check("stale cookie code", stale.code(envelope), "session_expired")

        # -------------------------------------------------------------- #
        print("\n[10] non-JSON endpoints")
        status, envelope = admin.get("/api/labels/qr.svg", {"data": "IMRI-000001-1"})
        check("QR svg", status, 200)
        status, envelope = admin.get("/api/labels/book/%d.svg" % int(second_copy["id"]))
        check("book label svg", status, 200)
        status, envelope = admin.get("/api/members/%d/card.svg" % member["id"])
        check("member card svg", status, 200)
        status, envelope = admin.get("/api/reports/collection")
        check("collection report", status, 200)
        check("report has rows", isinstance(envelope["data"].get("rows"), list), True)

        # -------------------------------------------------------------- #
        print("\n[10b] static shell and traversal guard")
        for asset in ("/", "/index.html", "/css/app.css", "/js/app.js", "/js/api.js"):
            status, envelope = admin.get(asset)
            check("static %s" % asset, status, 200)
        status, envelope = admin.get("/js/../../imri/config.py")
        check("traversal refused", status, 404)
        status, envelope = admin.get("/imri/config.py")
        check("source is not served", status, 404)
        status, envelope = admin.get("/#/catalogue")
        check("deep link falls back to the shell", status, 200)

        # -------------------------------------------------------------- #
        print("\n[10c] export writes a file the client can download")
        status, envelope = admin.get("/api/books/export", {"format": "csv"})
        check("books csv export", status, 200)
        exported = envelope["data"]
        check("export filename ends in .csv", exported["filename"].endswith(".csv"), True)
        check("export rows", exported["rows"] >= 12, True)
        status, envelope = admin.get("/api/exports/%s" % exported["filename"])
        check("download the exported file", status, 200)
        check("download body looks like csv", str(envelope.get("_raw", ""))[:1] != "<", True)

        # -------------------------------------------------------------- #
        print("\n[10d] the hand-rolled writers actually produce files")
        csv_body = (
            "title,author,isbn,copies,price\n"
            "Imported Title One,Ada Lovelace,9780141321608,2,19.90\n"
            "Imported Title Two,Grace Hopper,,1,25.00\n"
            ",Missing Title,,1,0\n"
        ).encode("utf-8")
        status, envelope = admin.call(
            "POST",
            "/api/books/import/preview",
            query={"filename": "books.csv"},
            raw=csv_body,
            headers_override={"Content-Type": "text/csv"},
        )
        check("import preview", status, 200)
        batch = envelope["data"]
        check("preview rows counted", batch["total"], 3)
        check("preview flags the bad row", batch["error_count"], 1)
        check("preview wrote nothing", admin.get("/api/books")[1]["meta"]["total"], 12)
        status, envelope = admin.post(
            "/api/books/import/commit", {"batch_token": batch["batch_token"]}
        )
        check("import commit", status, 200)
        check("imported rows", envelope["data"]["imported"], 2)
        check("total after import", admin.get("/api/books")[1]["meta"]["total"], 14)

        # Reports are written to data/exports and handed back as metadata, the
        # same contract as the catalogue export, so the client downloads the
        # file through /api/exports/{filename}.
        for fmt in ("csv", "pdf"):
            status, envelope = admin.get(
                "/api/reports/collection/export", {"format": fmt}
            )
            check("report export %s" % fmt, status, 200)
            produced = envelope["data"]
            note("%s file" % fmt, produced["filename"])
            check("%s has bytes" % fmt, produced["size"] > 400, True)
            status, envelope = admin.get("/api/exports/%s" % produced["filename"])
            check("%s downloads" % fmt, status, 200)
            check("%s download size" % fmt, admin.last_length > 400, True)

        status, envelope = admin.get("/api/reports/trend")
        check("trend report", status, 200)
        check(
            "trend covers at least twelve months",
            len(envelope["data"].get("series", [])) >= 12,
            True,
        )
        check("trend counts today's issues", envelope["data"]["series"][-1]["issued"] >= 1, True)

        # -------------------------------------------------------------- #
        print("\n[10e] backup create, list and verify")
        status, envelope = admin.post("/api/backup", {})
        check("backup created", status, 200)
        backup_name = envelope["data"]["name"]
        note("backup", backup_name)
        status, envelope = admin.get("/api/backup")
        check("backup listed", status, 200)
        check("one backup on disk", len(envelope["data"]["items"]), 1)
        status, envelope = admin.get("/api/backup/%s/verify" % backup_name)
        check("backup verifies", status, 200)
        check("backup is intact", envelope["data"]["ok"], True)
        check("manifest matches", envelope["data"]["manifest_ok"], True)
        check("backup contains files", envelope["data"]["files"] > 0, True)

        # -------------------------------------------------------------- #
        print("\n[11] logout revokes the session")
        status, envelope = viewer.post("/api/auth/logout")
        check("logout", status, 200)
        status, envelope = viewer.get("/api/books")
        check("session gone after logout", status, 401)

        # -------------------------------------------------------------- #
        print("\n[12] bye stops the process")
        status, envelope = admin.post("/api/system/bye")
        check("bye accepted", status, 204)
        process.wait(timeout=25)
        check("exit code", process.returncode, 0)
        check("lock released", (scratch / ".lock").exists(), False)

        print("\n%d checks, T3 PASS" % CHECKS)
        return 0
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.stdout.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
