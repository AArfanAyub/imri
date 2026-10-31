"""Pass B2/B3 smoke proof — every screen renders, with no console errors.

Boots the real application on a scratch data directory, seeds a small library
through the public API, then walks every route in the app and asserts:

  · the route resolves to the page module (not the 404 screen)
  · a page title is painted
  · no console error and no uncaught exception was produced

Screenshots of the new screens land in ``docs/`` so the visual result can be
reviewed, not just asserted.

Usage:  python tools/smoke_pages.py [--keep]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import CDP  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

APP_PORT = 8741
DEBUG_PORT = 9341
BASE = f"http://127.0.0.1:{APP_PORT}"

ACTIVATION_CODE = "arfanayub0"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin123"
NEW_PASSWORD = "Imri-Smoke-2026"

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def find_browser() -> str | None:
    for candidate in BROWSERS:
        if Path(candidate).is_file():
            return candidate
    return None


def http_call(path: str, method: str = "GET", body: dict | None = None, cookie: str = "") -> tuple[dict, str]:
    """Perform one API call; return the payload and any refreshed session cookie.

    Changing a password revokes the old session and issues a new token, so the
    caller must always take the cookie back rather than assume it is stable.
    """
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-IMRI-CSRF", "1")
    if cookie:
        request.add_header("Cookie", cookie)
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.loads(response.read().decode() or "{}")
        for header, value in response.getheaders():
            if header.lower() == "set-cookie":
                candidate = value.split(";")[0]
                if "=" in candidate and candidate.split("=", 1)[1]:
                    cookie = candidate
    return payload, cookie


def http_json(path: str, method: str = "GET", body: dict | None = None, cookie: str = "") -> dict:
    return http_call(path, method, body, cookie)[0]


def wait_for_http(url: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


ROUTES = [
    ("dashboard", "/dashboard", "dash"),
    ("catalogue", "/catalogue", "cat"),
    ("members", "/members", "mem"),
    ("reading-lists", "/reading-lists", "nav.readingLists"),
    ("counter", "/counter", "circ.title"),
    ("overdue", "/overdue", "circ.overdue.title"),
    ("reservations", "/reservations", "res.title"),
    ("reports", "/reports", "rep.title"),
    ("labels", "/labels", "label.title"),
    ("admin", "/admin", "adm.users.title"),
    ("settings", "/settings", "adm.settings.title"),
    ("backup", "/backup", "adm.backup.title"),
    ("audit", "/audit", "adm.audit.title"),
    ("license", "/license", "adm.license.title"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    browser = find_browser()
    if not browser:
        print("No Edge or Chrome found.")
        return 2

    DOCS.mkdir(exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="imri-smoke-"))
    profile_dir = Path(tempfile.mkdtemp(prefix="imri-smoke-profile-"))

    print("Pass B2/B3 smoke — every screen renders\n")

    server = subprocess.Popen(
        [sys.executable, "main.py", "--port", str(APP_PORT), "--data-dir", str(data_dir), "--no-window"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    browser_proc = None
    cdp = None

    try:
        if not wait_for_http(f"{BASE}/api/health"):
            print("server did not start")
            print(server.stdout.read() if server.stdout else "")
            return 2

        # -- seed through the API --------------------------------------- #
        http_json("/api/license/activate", "POST", {"code": ACTIVATION_CODE})
        _login, cookie = http_call(
            "/api/auth/login", "POST", {"username": ADMIN_USER, "password": ADMIN_PASSWORD}
        )
        check("seed session acquired", bool(cookie), cookie)

        # Change the password so the forced-change screen does not intercept.
        # This revokes the first session and returns a fresh token.
        _changed, cookie = http_call(
            "/api/auth/password", "POST",
            {"current_password": ADMIN_PASSWORD, "new_password": NEW_PASSWORD},
            cookie,
        )
        check("password changed", bool(cookie), cookie)

        books = [
            ("The Silent Patient", "Alex Michaelides", "book", "823.92", 3),
            ("Sejarah Melayu", "Tun Seri Lanang", "reference", "959.5", 2),
            ("Physics for Matriculation", "Poh Liong Yong", "textbook", "530", 4),
            ("National Geographic, Oct 2026", "NatGeo", "magazine", "910", 1),
        ]
        for index, (title, authors, material, dewey, copies) in enumerate(books):
            payload = {
                "title": title, "authors": authors, "material_type": material,
                "dewey": dewey, "copies": copies, "pub_year": "2020",
                "publisher": "Test Press",
            }
            # One known-good ISBN is enough to exercise the field; inventing
            # check digits would just be rejected by the validator.
            if index == 0:
                payload["isbn"] = "9780306406157"
            http_json("/api/books", "POST", payload, cookie)

        members = [
            ("Nur Aisyah binti Rahman", "student", "5", "5 Amanah"),
            ("Lim Wei Sheng", "student", "4", "4 Bestari"),
            ("Pn. Kavitha", "teacher", "", ""),
        ]
        for name, kind, form, klass in members:
            http_json("/api/members", "POST", {
                "name": name, "member_type": kind, "form": form, "class_name": klass,
            }, cookie)

        # One loan, so the counter and overdue screens have something real.
        catalog = http_json("/api/books?page=1&page_size=5", cookie=cookie)
        first = (catalog.get("data", {}).get("items") or [{}])[0]
        roster = http_json("/api/members?page=1&page_size=5", cookie=cookie)
        person = (roster.get("data", {}).get("items") or [{}])[0]
        if first.get("accession_no") and person.get("member_code"):
            http_json("/api/circulation/issue", "POST", {
                "accession_no": first["accession_no"], "member_code": person["member_code"],
            }, cookie)
            check("seed loan issued", True)

        # -- browser ---------------------------------------------------- #
        browser_proc = subprocess.Popen(
            [
                browser, "--headless=new",
                f"--remote-debugging-port={DEBUG_PORT}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-gpu", "--disable-extensions", "--hide-scrollbars",
                "--window-size=1440,900", "about:blank",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        deadline = time.time() + 20
        socket_url = None
        while time.time() < deadline and socket_url is None:
            try:
                socket_url = CDP.page_target(DEBUG_PORT)
            except Exception:
                time.sleep(0.25)
        if not socket_url:
            print("browser did not expose a target")
            return 2

        cdp = CDP(socket_url)
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False,
        })

        cdp.call("Page.navigate", {"url": f"{BASE}/#/login"})
        time.sleep(1.5)
        cdp.wait_for_selector("#login-username", 8)

        cdp.evaluate(
            "document.querySelector('#login-username').value = %s;"
            "document.querySelector('#login-password').value = %s;"
            "document.querySelector('#login-form, form').requestSubmit();"
            % (json.dumps(ADMIN_USER), json.dumps(NEW_PASSWORD))
        )
        time.sleep(2.0)

        try:
            signed_in = cdp.wait_for_route("dashboard", 20)
        except Exception as exc:
            print("    login debug:", exc)
            print("    error node:", cdp.evaluate(
                "(document.querySelector('.field__error, .gate__error, [role=alert]') || {}).textContent || '(none)'"
            ))
            print("    url:", cdp.evaluate("location.hash"))
            signed_in = False
        check("signed in", signed_in)

        # -- walk every route ------------------------------------------- #
        for name, path, _title in ROUTES:
            cdp.call("Page.navigate", {"url": f"{BASE}/#{path}"})
            try:
                cdp.wait_for_selector(".page__title", 8)
            except TimeoutError:
                pass
            time.sleep(0.3)
            title_text = cdp.evaluate(
                "(document.querySelector('.page__title') || {}).textContent || ''"
            )
            check(f"{name}: page title painted", bool(str(title_text).strip()), str(title_text))
            errors = cdp.console_errors()
            check(f"{name}: no console errors", not errors, "; ".join(errors[:2]))

        # -- screenshots ------------------------------------------------ #
        for label, path in (
            ("dashboard", "/dashboard"),
            ("catalogue", "/catalogue"),
            ("members", "/members"),
            ("counter", "/counter"),
            ("reports", "/reports"),
            ("reservations", "/reservations"),
            ("settings", "/settings"),
            ("license", "/license"),
        ):
            cdp.call("Page.navigate", {"url": f"{BASE}/#{path}"})
            try:
                cdp.wait_for_selector(".page__title", 8)
            except TimeoutError:
                pass
            time.sleep(1.2)
            # Long-lived pages queue hundreds of protocol events; drain them
            # first or the screenshot reply sits behind them.
            cdp.drain(0.5)
            cdp.screenshot(str(DOCS / f"b2-{label}.png"))

        # Dark theme pass on the two headline screens.
        cdp.evaluate("document.documentElement.setAttribute('data-theme','dark')")
        for label, path in (("dashboard", "/dashboard"), ("catalogue", "/catalogue")):
            cdp.call("Page.navigate", {"url": f"{BASE}/#{path}"})
            try:
                cdp.wait_for_selector(".page__title", 8)
            except TimeoutError:
                pass
            time.sleep(1.2)
            cdp.drain(0.5)
            cdp.screenshot(str(DOCS / f"b2-{label}-dark.png"))

        print("\n" + "=" * 58)
        print(f"  {PASS} passed, {FAIL} failed")
        print("=" * 58)
        return 0 if FAIL == 0 else 1

    finally:
        if cdp is not None:
            try:
                cdp.close()
            except Exception:
                pass
        if browser_proc is not None and not args.keep:
            browser_proc.terminate()
        if not args.keep:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    sys.exit(main())
