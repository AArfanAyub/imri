"""Proof that the IMRI brand mark renders, everywhere it is used.

The mark is drawn once, in ``web/js/brand.js``, and injected into five places.
That design has one failure mode worth testing: the five places silently
diverge, or two marks on the same page collide on their gradient ids and the
second one renders black. Neither shows up in a unit test, because the bug is
in the browser's paint.

So this script boots the real application, looks at the real DOM, and asserts:

  · the activation gate shows a 56 px mark, painted with a gradient
  · the sidebar shows a 28 px mark once signed in
  · two marks on one page carry different gradient ids
  · every ``<path>`` and ``<rect>`` the drawing expects is present
  · the tab icon is the same drawing, as a ``data:`` URI
  · no console error was logged at any point

Screenshots land in ``docs/`` so the result can be looked at, not just
asserted.

Usage:  python tools/proof_brand.py [--keep]
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

APP_PORT = 8743
DEBUG_PORT = 9343
BASE = f"http://127.0.0.1:{APP_PORT}"

ACTIVATION_CODE = "arfanayub0"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin123"
NEW_PASSWORD = "Imri-Brand-2026"

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


def http_json(path: str, method: str = "GET", body: dict | None = None,
              cookie: str = "") -> dict:
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


# The shape of the drawing, as the DOM should report it. Kept as numbers
# rather than a string compare so a cosmetic tweak to a path does not fail
# the test, while a missing shape does.
EXPECTED_RECTS = 3   # body, top shine, inner hairline ring
EXPECTED_PATHS = 3   # ribbon, left page, right page


def mark_report(cdp: CDP, selector: str) -> dict:
    """Measure one mark: size, shape census, and its gradient ids."""
    return cdp.evaluate(
        """(() => {
            const host = document.querySelector(%s);
            if (!host) return {found: false};
            const svg = host.matches('svg') ? host : host.querySelector('svg.brandmark');
            if (!svg) return {found: true, svg: false};
            const box = svg.getBoundingClientRect();
            const ids = Array.from(svg.querySelectorAll('linearGradient')).map(n => n.id);
            const stops = Array.from(svg.querySelectorAll('linearGradient stop'))
                .map(n => n.getAttribute('stop-color'));
            const bg = svg.querySelector('rect');
            return {
                found: true,
                svg: true,
                width: Math.round(box.width),
                height: Math.round(box.height),
                rects: svg.querySelectorAll('rect').length,
                paths: svg.querySelectorAll('path').length,
                ids,
                uniqueIds: new Set(ids).size === ids.length,
                stopCount: stops.length,
                hasViolet: stops.some(s => (s || '').toUpperCase() === '#4F46E5'),
                hasAmber: stops.some(s => (s || '').toUpperCase() === '#FCD34D'),
                fill: bg ? bg.getAttribute('fill') : null,
                label: svg.getAttribute('aria-label'),
                hidden: svg.getAttribute('aria-hidden'),
            };
        })()""" % json.dumps(selector)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    browser = find_browser()
    if not browser:
        print("No Edge or Chrome found.")
        return 2

    DOCS.mkdir(exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="imri-brand-"))
    profile_dir = Path(tempfile.mkdtemp(prefix="imri-brand-profile-"))

    print("Brand mark proof\n")

    server = subprocess.Popen(
        [sys.executable, "main.py", "--port", str(APP_PORT),
         "--data-dir", str(data_dir), "--no-window"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    browser_proc = None
    cdp = None

    try:
        if not wait_for_http(f"{BASE}/api/health"):
            print("server did not start")
            print(server.stdout.read() if server.stdout else "")
            return 2

        browser_proc = subprocess.Popen(
            [browser, "--headless=new", f"--remote-debugging-port={DEBUG_PORT}",
             f"--user-data-dir={profile_dir}", "--no-first-run",
             "--no-default-browser-check", "--disable-gpu", "--disable-extensions",
             "--hide-scrollbars", "--window-size=1440,900", "about:blank"],
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

        # -- 1. the gate, on a fresh unlicensed install ------------------ #
        cdp.call("Page.navigate", {"url": f"{BASE}/#/activate"})
        time.sleep(2.0)
        cdp.wait_for_selector(".gate__mark", 10)

        gate = mark_report(cdp, ".gate__mark")
        check("gate: mark present", gate.get("found") and gate.get("svg"), str(gate))
        if gate.get("svg"):
            check("gate: drawn at 56 px", gate["width"] == 56 and gate["height"] == 56,
                  f"{gate['width']}x{gate['height']}")
            check("gate: 3 rects (body, shine, ring)", gate["rects"] == EXPECTED_RECTS,
                  str(gate["rects"]))
            check("gate: 3 paths (ribbon, two pages)", gate["paths"] == EXPECTED_PATHS,
                  str(gate["paths"]))
            check("gate: body filled by its own gradient",
                  (gate["fill"] or "").startswith("url(#imri-bg-"), str(gate["fill"]))
            check("gate: indigo stop present", gate["hasViolet"], str(gate["stopCount"]))
            check("gate: amber ribbon stop present", gate["hasAmber"], str(gate["stopCount"]))
            check("gate: gradient ids unique", gate["uniqueIds"], str(gate["ids"]))
            check("gate: decorative (wordmark is beside it)",
                  gate["hidden"] == "true" and gate["label"] is None,
                  f"hidden={gate['hidden']} label={gate['label']}")

        # -- 2. the tab icon is the same drawing ------------------------ #
        icon = cdp.evaluate(
            """(() => {
                const link = document.querySelector('link[rel="icon"]');
                if (!link) return {found: false};
                const href = link.getAttribute('href') || '';
                let decoded = '';
                try { decoded = decodeURIComponent(href.replace(/^data:image\\/svg\\+xml,/, '')); }
                catch (e) { decoded = ''; }
                return {
                    found: true,
                    isData: href.startsWith('data:image/svg+xml,'),
                    hasViewBox: decoded.includes('viewBox="0 0 48 48"'),
                    hasRibbon: decoded.toUpperCase().includes('#FCD34D'),
                    length: href.length,
                };
            })()"""
        )
        check("tab icon: present as data: SVG", icon.get("isData"), str(icon))
        check("tab icon: 48-unit viewBox", icon.get("hasViewBox"), str(icon))
        check("tab icon: carries the amber ribbon", icon.get("hasRibbon"), str(icon))

        cdp.screenshot(str(DOCS / "brand-gate.png"))

        # -- 3. sign in, look at the sidebar ---------------------------- #
        http_json("/api/license/activate", "POST", {"code": ACTIVATION_CODE})
        _login, cookie = http_call(
            "/api/auth/login", "POST",
            {"username": ADMIN_USER, "password": ADMIN_PASSWORD},
        )
        http_call("/api/auth/password", "POST",
                  {"current_password": ADMIN_PASSWORD, "new_password": NEW_PASSWORD},
                  cookie)

        # The page in the browser booted against an unlicensed install, so its
        # store still says `licensed: false` and the guard would bounce /login
        # straight back to /activate. A query-string change forces a real
        # document load, which re-runs boot() and re-reads bootstrap.
        cdp.call("Page.navigate", {"url": f"{BASE}/?r=1#/login"})
        time.sleep(2.2)
        cdp.wait_for_selector("#login-username", 10)
        cdp.evaluate(
            "document.querySelector('#login-username').value = %s;"
            "document.querySelector('#login-password').value = %s;"
            "document.querySelector('#login-form, form').requestSubmit();"
            % (json.dumps(ADMIN_USER), json.dumps(NEW_PASSWORD))
        )
        time.sleep(2.2)
        try:
            signed_in = cdp.wait_for_route("dashboard", 20)
        except Exception:
            signed_in = False
        check("signed in", signed_in)

        sidebar = mark_report(cdp, ".sb__mark")
        check("sidebar: mark present", sidebar.get("found") and sidebar.get("svg"), str(sidebar))
        if sidebar.get("svg"):
            check("sidebar: drawn at 28 px",
                  sidebar["width"] == 28 and sidebar["height"] == 28,
                  f"{sidebar['width']}x{sidebar['height']}")
            check("sidebar: full shape census",
                  sidebar["rects"] == EXPECTED_RECTS and sidebar["paths"] == EXPECTED_PATHS,
                  f"rects={sidebar['rects']} paths={sidebar['paths']}")
            check("sidebar: gradient ids unique", sidebar["uniqueIds"], str(sidebar["ids"]))

        # Two marks in one document must not share a gradient id, or the
        # second silently borrows the first's paint and renders black.
        both = cdp.evaluate(
            """(() => {
                const all = Array.from(document.querySelectorAll('svg.brandmark'));
                const ids = all.flatMap(s =>
                    Array.from(s.querySelectorAll('linearGradient')).map(n => n.id));
                return {marks: all.length, ids, unique: new Set(ids).size === ids.length};
            })()"""
        )
        check("marks on one page: gradient ids do not collide",
              bool(both.get("unique")), f"{both.get('marks')} marks, ids={both.get('ids')}")

        cdp.screenshot(str(DOCS / "brand-sidebar.png"))

        # -- 4. the mark survives a theme flip -------------------------- #
        # Headless Chrome reports prefers-color-scheme: dark, so the page is
        # already dark here. Forcing *light* is therefore the informative
        # direction: it is the theme the mark has not been seen against.
        cdp.evaluate(
            "document.documentElement.setAttribute('data-theme', 'light');"
            "document.documentElement.setAttribute('data-theme-choice', 'light');"
        )
        time.sleep(0.4)
        themed = cdp.evaluate(
            "document.documentElement.getAttribute('data-theme')"
        )
        check("theme actually flipped to light", themed == "light", str(themed))
        light = mark_report(cdp, ".sb__mark")
        check("light theme: mark still present",
              light.get("found") and light.get("svg") and light.get("paths") == EXPECTED_PATHS,
              str(light.get("paths")))
        # A theme flip repaints the whole tree; capture too early and the
        # screenshot catches a half-composited frame. The assertions above are
        # the real check, this is only so the picture is worth looking at.
        time.sleep(1.5)
        cdp.screenshot(str(DOCS / "brand-sidebar-light.png"))

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
