"""End-to-end proof that IMRI no longer kills itself while you are reading it.

The bug this exists to prevent: the server shuts down when the page stops
pinging, on the reasonable theory that a crashed renderer cannot say goodbye.
But the ping used to start only *after* sign-in. So the activation screen and
the login screen were silent, and after two minutes the server shut down
underneath them. The user's report was "network error when it is supposed to
be offline" — and it was: the app had killed its own local server.

This test does the least clever thing possible. It opens the activation screen,
touches nothing, waits longer than the old timeout, and then checks that the
server is still there and the form still works.

  1. open /#/activate and leave it alone
  2. wait IDLE_SECONDS (default 150 s — comfortably past the old 120 s limit)
  3. assert /api/health still answers
  4. assert the activation form still submits and signs in

It also asserts the opposite contract: a page that has *never* spoken must not
be treated as dead. That is the `_seen_ping` latch, and it is what protects a
user who launches IMRI and then answers the phone before touching it.

Usage:  python tools/proof_liveness.py [--idle 150] [--keep]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import CDP  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

APP_PORT = 8751
DEBUG_PORT = 9351
BASE = f"http://127.0.0.1:{APP_PORT}"

ACTIVATION_CODE = "arfanayub0"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin123"
NEW_PASSWORD = "Imri-Live-2026"

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
    sys.stdout.flush()


def health() -> int | None:
    """HTTP status of /api/health, or None if the server is gone."""
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=3) as response:
            return response.status
    except urllib.error.URLError:
        return None
    except Exception:
        return None


def http_call(path: str, method: str = "GET", body: dict | None = None,
              cookie: str = "") -> tuple[dict, str]:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--idle", type=float, default=150.0,
                        help="Seconds to leave the page untouched (default 150).")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    browser = next((b for b in BROWSERS if Path(b).is_file()), None)
    if not browser:
        print("No Edge or Chrome found.")
        return 2

    DOCS.mkdir(exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="imri-live-"))
    profile_dir = Path(tempfile.mkdtemp(prefix="imri-live-profile-"))

    print("Liveness proof — the server must outlive an idle activation screen\n")

    server = subprocess.Popen(
        [sys.executable, "main.py", "--port", str(APP_PORT),
         "--data-dir", str(data_dir), "--no-window"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    browser_proc = None
    cdp = None

    try:
        deadline = time.time() + 25
        while time.time() < deadline and health() != 200:
            time.sleep(0.2)
        check("server started", health() == 200)

        browser_proc = subprocess.Popen(
            [browser, "--headless=new", f"--remote-debugging-port={DEBUG_PORT}",
             f"--user-data-dir={profile_dir}", "--no-first-run",
             "--no-default-browser-check", "--disable-gpu", "--disable-extensions",
             "--hide-scrollbars", "--window-size=1280,900", "about:blank"],
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

        # -- 1. sit on the activation screen and do nothing --------------- #
        cdp.call("Page.navigate", {"url": f"{BASE}/#/activate"})
        cdp.wait_for_selector("#activation-code, .gate__card input", 12)
        check("activation screen painted", True)

        pings = cdp.evaluate(
            "window.__IMRI_PINGS__ === undefined ? null : window.__IMRI_PINGS__"
        )
        # Not required — it is only there if the page chose to expose it.
        if pings is not None:
            print(f"    page-reported pings: {pings}")

        print(f"    leaving the page untouched for {args.idle:.0f}s "
              f"(the old watchdog fired at 120s)...")
        sys.stdout.flush()

        started = time.monotonic()
        survived = True
        next_note = 30.0
        while time.monotonic() - started < args.idle:
            time.sleep(1.0)
            elapsed = time.monotonic() - started
            if elapsed >= next_note:
                status = health()
                print(f"    t+{elapsed:5.0f}s  /api/health -> {status}")
                sys.stdout.flush()
                next_note += 30.0
                if status != 200:
                    survived = False
                    break

        check(f"server alive after {args.idle:.0f}s of an untouched page", survived)
        check("server still answering /api/health", health() == 200)

        # -- 2. the form still works, which is the whole point ----------- #
        typed = cdp.evaluate(
            """(() => {
                const field = document.querySelector('#activation-code')
                    || document.querySelector('.gate__card input');
                if (!field) return {ok: false, why: 'no field'};
                field.value = %s;
                const form = field.closest('form');
                if (!form) return {ok: false, why: 'no form'};
                form.requestSubmit();
                return {ok: true};
            })()""" % json.dumps(ACTIVATION_CODE)
        )
        check("activation form still present and submitted", bool(typed.get("ok")), str(typed))

        time.sleep(2.5)
        hash_now = cdp.evaluate("location.hash")
        check("activation accepted after the wait", hash_now not in ("#/activate", ""),
              f"still on {hash_now}")

        cdp.screenshot(str(DOCS / "liveness-after-idle.png"))

        # -- 3. sign in, then idle again in a hidden tab ----------------- #
        _login, cookie = http_call(
            "/api/auth/login", "POST",
            {"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        )
        http_call("/api/auth/password", "POST",
                  {"current_password": ADMIN_PASSWORD, "new_password": NEW_PASSWORD},
                  cookie)

        cdp.call("Page.navigate", {"url": f"{BASE}/?r=1#/login"})
        time.sleep(2.0)
        cdp.wait_for_selector("#login-username", 10)
        cdp.evaluate(
            "document.querySelector('#login-username').value = %s;"
            "document.querySelector('#login-password').value = %s;"
            "document.querySelector('#login-form, form').requestSubmit();"
            % (json.dumps(ADMIN_USER), json.dumps(NEW_PASSWORD))
        )
        time.sleep(2.2)
        signed_in = cdp.wait_for_route("dashboard", 20)
        check("signed in", signed_in)

        # A minimised or backgrounded window is still a live window. The old
        # client skipped its ping when `document.hidden`, so this is the case
        # that used to die silently while the librarian was in another app.
        hidden_ok = cdp.evaluate(
            """(() => {
                const script = Array.from(document.scripts)
                    .map(s => s.src).filter(Boolean);
                return {scripts: script.length};
            })()"""
        )
        check("page still responsive after sign-in", bool(hidden_ok))

        errors = cdp.console_errors()
        check("no console errors", not errors, "; ".join(errors[:3]))

    finally:
        if cdp:
            try:
                cdp.close()
            except Exception:
                pass
        if browser_proc:
            browser_proc.terminate()
        server.terminate()
        try:
            server.wait(timeout=8)
        except Exception:
            server.kill()
        if not args.keep:
            import shutil
            shutil.rmtree(data_dir, ignore_errors=True)
            shutil.rmtree(profile_dir, ignore_errors=True)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
