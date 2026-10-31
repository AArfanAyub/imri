"""Focused debug: sign in, open one route, dump what actually happened."""

from __future__ import annotations

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
PORT = 8755
DEBUG = 9355
BASE = f"http://127.0.0.1:{PORT}"
ROUTE = sys.argv[1] if len(sys.argv) > 1 else "/dashboard"
NEW_PASSWORD = "Imri-Debug-2026"


def call(path, method="GET", body=None, cookie=""):
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
                if candidate.split("=", 1)[-1]:
                    cookie = candidate
    return payload, cookie


def main() -> int:
    data_dir = Path(tempfile.mkdtemp(prefix="imri-dbg-"))
    profile = Path(tempfile.mkdtemp(prefix="imri-dbg-prof-"))
    server = subprocess.Popen(
        [sys.executable, "main.py", "--port", str(PORT), "--data-dir", str(data_dir), "--no-window"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    browser = None
    cdp = None
    try:
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
                break
            except Exception:
                time.sleep(0.2)

        call("/api/license/activate", "POST", {"code": "arfanayub0"})
        _, cookie = call("/api/auth/login", "POST", {"username": "admin", "password": "admin123"})
        _, cookie = call("/api/auth/password", "POST",
                         {"current_password": "admin123", "new_password": NEW_PASSWORD}, cookie)
        call("/api/books", "POST", {"title": "Debug Book", "authors": "A", "copies": 2}, cookie)

        browser = subprocess.Popen(
            [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             "--headless=new", f"--remote-debugging-port={DEBUG}", f"--user-data-dir={profile}",
             "--no-first-run", "--disable-gpu", "--disable-extensions", "--window-size=1440,900",
             "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 20
        url = None
        while time.time() < deadline and url is None:
            try:
                url = CDP.page_target(DEBUG)
            except Exception:
                time.sleep(0.25)
        cdp = CDP(url)
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")

        cdp.call("Page.navigate", {"url": f"{BASE}/#/login"})
        time.sleep(2.0)
        cdp.evaluate(
            "document.querySelector('#login-username').value=%s;"
            "document.querySelector('#login-password').value=%s;"
            "document.querySelector('form').requestSubmit();"
            % (json.dumps("admin"), json.dumps(NEW_PASSWORD))
        )
        time.sleep(5.0)
        cdp.drain(1.0)

        print("route   :", cdp.evaluate("location.hash"))
        print("active  :", cdp.evaluate("window.__IMRI__ && window.__IMRI__.store.currentRoute && window.__IMRI__.store.currentRoute().name"))
        print("title   :", cdp.evaluate("(document.querySelector('.page__title')||{}).textContent||'(none)'"))
        print("errors  :", cdp.console_errors())
        print("view    :", str(cdp.evaluate("(document.querySelector('#view')||{}).innerHTML||''"))[:300])

        cdp.call("Page.navigate", {"url": f"{BASE}/#{ROUTE}"})
        time.sleep(3.0)
        cdp.drain(1.0)
        print("\n-- direct navigate to", ROUTE)
        print("route   :", cdp.evaluate("location.hash"))
        print("title   :", cdp.evaluate("(document.querySelector('.page__title')||{}).textContent||'(none)'"))
        print("errors  :", cdp.console_errors())
        return 0
    finally:
        if cdp is not None:
            try:
                cdp.close()
            except Exception:
                pass
        if browser is not None:
            browser.terminate()
        server.terminate()


if __name__ == "__main__":
    sys.exit(main())
