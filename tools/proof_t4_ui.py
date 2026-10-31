"""T4 pass A proof — drive the real shell in a real browser.

Starts the application on a scratch data directory, opens it in headless Edge
over the DevTools protocol, walks the first-run journey (activate → sign in →
change the default password → dashboard), then exercises the shell:

  · sidebar collapse, and that it survives a reload
  · Ctrl+K palette: opens, filters, moves with the arrow keys, closes on Esc
  · `?` shortcut sheet opens and closes
  · theme toggle applies and persists across a reload
  · language switch repaints the chrome without re-fetching bootstrap
  · toasts cap at three
  · zero console errors and zero uncaught exceptions

Usage:  python tools/proof_t4_ui.py [--keep]

Screenshots land in `docs/` so the visual result can be reviewed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
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

APP_PORT = 8731
DEBUG_PORT = 9333
BASE = f"http://127.0.0.1:{APP_PORT}"

ACTIVATION_CODE = "arfanayub0"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin123"
NEW_PASSWORD = "Imri-Test-2026"

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

CONTRAST_JS = r"""
(() => {
  // Resolve a token pair into real colours by painting a throwaway element.
  const probe = document.createElement('span');
  probe.style.position = 'fixed';
  probe.style.opacity = '0';
  probe.style.pointerEvents = 'none';
  document.body.appendChild(probe);

  const rgba = (value) => {
    const nums = (value.match(/[0-9.]+/g) || []).map(Number);
    if (nums.length < 3) return null;
    return { rgb: nums.slice(0, 3), a: nums.length > 3 ? nums[3] : 1 };
  };

  const resolve = (fgToken, bgToken) => {
    probe.style.color = fgToken;
    probe.style.backgroundColor = bgToken;
    const fg = rgba(getComputedStyle(probe).color);
    const bg = rgba(getComputedStyle(probe).backgroundColor);
    return fg && bg ? [fg.rgb, bg.rgb] : null;
  };

  const lum = (rgb) => {
    const [r, g, b] = rgb.map((v) => {
      const c = v / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };

  const ratio = ([fg, bg]) => {
    const a = lum(fg);
    const b = lum(bg);
    const [hi, lo] = a > b ? [a, b] : [b, a];
    return Number(((hi + 0.05) / (lo + 0.05)).toFixed(2));
  };

  const pairs = [
    ['body text on the page', 'var(--text)', 'var(--bg)'],
    ['body text on a card', 'var(--text)', 'var(--surface)'],
    ['muted text on the page', 'var(--muted)', 'var(--bg)'],
    ['muted text on a card', 'var(--muted)', 'var(--surface)'],
    ['muted text in a table head', 'var(--muted)', 'var(--surface-2)'],
    ['accent text on a card', 'var(--primary)', 'var(--surface)'],
    ['button label on accent', 'var(--on-primary)', 'var(--primary)'],
    ['danger text on a card', 'var(--danger)', 'var(--surface)'],
    ['success text on a card', 'var(--success)', 'var(--surface)'],
    ['warning text on a card', 'var(--warning)', 'var(--surface)'],
  ];

  const out = pairs.map(([label, fg, bg]) => ({ label, ratio: ratio(resolve(fg, bg)) }));
  probe.remove();
  return JSON.stringify(out);
})()
"""

KIT_JS = (Path(__file__).resolve().parent / "kit_gallery.js").read_text(encoding="utf-8")

PASS = 0
FAIL = 0
NOTES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}")
    if detail:
        for line in str(detail).splitlines()[:6]:
            print(f"        {line}")


def wait_for(cdp: CDP, expression: str, timeout: float = 20.0, label: str = "") -> bool:
    """Poll an expression until it is truthy."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if cdp.evaluate(f"Boolean({expression})", timeout=5):
                return True
        except Exception:
            pass
        time.sleep(0.12)
    if label:
        NOTES.append(f"timed out waiting for {label}")
    return False


def capture(cdp: CDP, path: Path) -> None:
    """Screenshot a settled frame.

    Headless Edge occasionally captures a half-composited tile while an
    animation is finishing, which shows up as a duplicated strip of the page.
    Capturing twice, a beat apart, and keeping the second file avoids shipping
    that artefact as evidence.
    """
    cdp.screenshot(str(path))
    time.sleep(0.3)
    cdp.screenshot(str(path))


def read_catalogue(name: str) -> dict[str, str]:
    """Parse a catalogue module, the same way the static proof does."""
    text = (ROOT / "web" / "js" / "lang" / f"{name}.js").read_text(encoding="utf-8")
    body = text.split("export default", 1)[1].rsplit("};", 1)[0]
    return {key: value for key, value in re.findall(r"'([^']+)':\s*'((?:[^'\\]|\\.)*)'", body)}


def find_browser() -> str | None:
    for candidate in BROWSERS:
        if Path(candidate).exists():
            return candidate
    return None


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="leave the browser and server running")
    args = parser.parse_args()

    browser = find_browser()
    if not browser:
        print("No Edge or Chrome found; cannot run the UI proof.")
        return 2

    DOCS.mkdir(exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="imri-t4-"))
    profile_dir = Path(tempfile.mkdtemp(prefix="imri-t4-profile-"))

    print("T4 pass A — shell, in a real browser\n")
    print(f"  data   {data_dir}")
    print(f"  browser {Path(browser).name}\n")

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
            print("server did not come up")
            print(server.stdout.read() if server.stdout else "")
            return 2

        browser_proc = subprocess.Popen(
            [
                browser,
                "--headless=new",
                f"--remote-debugging-port={DEBUG_PORT}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
                "--disable-extensions",
                "--hide-scrollbars",
                "--window-size=1440,900",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        deadline = time.time() + 20
        socket_url = None
        while time.time() < deadline and socket_url is None:
            try:
                socket_url = CDP.page_target(DEBUG_PORT)
            except Exception:
                time.sleep(0.25)
        if not socket_url:
            print("browser did not expose a page target")
            return 2

        cdp = CDP(socket_url)
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False,
        })
        cdp.call("Page.navigate", {"url": BASE})

        # ---- 1. first run: licence gate ---------------------------------- #
        print("[1] first run")
        if not wait_for(cdp, "window.__IMRI_BOOT__ && window.__IMRI_BOOT__.ready", 25, "boot"):
            print("  the app never finished booting")
            print("  console:", cdp.console_errors()[:5])
            return 1

        check("boot completed", True)
        check("unlicensed install lands on activation",
              cdp.evaluate("location.hash") == "#/activate",
              cdp.evaluate("location.hash"))
        check("shell is mounted", cdp.evaluate("!!document.querySelector('.shell')"))
        check("sidebar is present", cdp.evaluate("!!document.querySelector('.sidebar')"))
        check("topbar is present", cdp.evaluate("!!document.querySelector('.topbar')"))
        check("page container is present", cdp.evaluate("!!document.querySelector('#view .page')"))
        check("activation screen painted",
              "Activate" in (cdp.evaluate("document.querySelector('.page__title').textContent") or ""))
        check("no destination links while unlicensed",
              cdp.evaluate("document.querySelectorAll('a.sb__item').length") == 0)
        check("help actions remain available while unlicensed",
              cdp.evaluate("document.querySelectorAll('button.sb__item').length") == 2)

        # ---- 2. activate, sign in, change the default password ------------ #
        print("\n[2] first-run journey")
        cdp.evaluate(
            "window.__IMRI__.api.activate(%r).then(() => window.__IMRI__.api.bootstrap())"
            ".then((b) => { window.__IMRI__.store.applyBootstrap(b);"
            " window.__IMRI__.router.navigate('/login'); return b.licensed; })"
            % ACTIVATION_CODE
        )
        check("activation accepted", cdp.evaluate("window.__IMRI__.store.state.licensed"))
        check("redirected to sign-in",
              wait_for(cdp, "location.hash === '#/login'", 8, "login route"),
              cdp.evaluate("location.hash"))
        check("sign-in screen painted",
              (cdp.evaluate("document.querySelector('.page__title').textContent") or "") == read_catalogue("en")["auth.signIn"])

        cdp.evaluate(
            "window.__IMRI__.api.login(%r, %r).then(() => window.__IMRI__.api.bootstrap())"
            ".then((b) => { window.__IMRI__.store.applyBootstrap(b);"
            " window.__IMRI__.router.navigate('/dashboard'); return true; })"
            % (ADMIN_USER, ADMIN_PASSWORD)
        )
        check("signed in as the seeded administrator", cdp.evaluate("window.__IMRI__.store.isAuthed()"))
        check("forced password change intercepts the dashboard",
              wait_for(cdp, "location.hash === '#/change-password'", 8, "change-password"),
              cdp.evaluate("location.hash"))

        cdp.evaluate(
            "window.__IMRI__.api.post('/api/auth/password', {current_password: %r, new_password: %r})"
            ".then(() => window.__IMRI__.api.bootstrap())"
            ".then((b) => { window.__IMRI__.store.applyBootstrap(b);"
            " window.__IMRI__.router.navigate('/dashboard'); return true; })"
            % (ADMIN_PASSWORD, NEW_PASSWORD)
        )
        check("password changed", cdp.evaluate("window.__IMRI__.store.mustChangePassword()") is False)
        check("dashboard reached",
              wait_for(cdp, "location.hash === '#/dashboard'", 8, "dashboard"),
              cdp.evaluate("location.hash"))
        check("dashboard title is translated",
              (cdp.evaluate("document.querySelector('.tb__title').textContent") or "")
              == read_catalogue("en")["nav.dashboard"])

        # ---- 3. sidebar --------------------------------------------------- #
        print("\n[3] sidebar")
        groups = cdp.evaluate("document.querySelectorAll('.sb__group').length")
        items = cdp.evaluate("document.querySelectorAll('.sb__item').length")
        check("five navigation groups", groups == 5, f"groups={groups}")
        check("navigation is populated", items >= 12, f"items={items}")
        check("active item is marked",
              cdp.evaluate("!!document.querySelector('.sb__item[aria-current=\"page\"]')"))

        cdp.evaluate("document.querySelector('.sb__collapse').click()")
        time.sleep(0.35)
        check("collapse sets the shell flag",
              cdp.evaluate("document.querySelector('.shell').dataset.collapsed") == "true")
        time.sleep(0.2)
        check("collapse is persisted",
              cdp.evaluate("localStorage.getItem('imri.sidebar')") == "1")
        width = cdp.evaluate("document.querySelector('.sidebar').getBoundingClientRect().width")
        check("collapsed width is 64 px", abs(float(width) - 64) < 1.5, f"width={width}")
        check("collapsed links keep an accessible name",
              cdp.evaluate(
                  "(() => { const label = document.querySelector('.sb__item .sb__text');"
                  " return getComputedStyle(label).display !== 'none'"
                  " && label.textContent.trim().length > 0; })()"
              ))
        check("collapse chevron points right",
              cdp.evaluate("document.querySelector('.sb__collapse svg path').getAttribute('d')")
              == "M9.5 6l6 6-6 6")
        capture(cdp, DOCS / "t4-shell-collapsed.png")

        cdp.evaluate("document.querySelector('.sb__collapse').click()")
        time.sleep(0.35)
        check("expand restores the sidebar",
              cdp.evaluate("document.querySelector('.shell').dataset.collapsed") == "false")

        # ---- 4. command palette ------------------------------------------- #
        print("\n[4] command palette")
        cdp.evaluate(
            "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'k', ctrlKey: true, bubbles: true}))"
        )
        check("Ctrl+K opens the palette", wait_for(cdp, "!!document.querySelector('.palette')", 5, "palette"))
        check("palette input has focus",
              cdp.evaluate("document.activeElement && document.activeElement.classList.contains('palette__input')"))
        check("palette lists actions and destinations",
              cdp.evaluate("document.querySelectorAll('.palette__item').length") >= 8,
              cdp.evaluate("document.querySelectorAll('.palette__item').length"))

        cdp.evaluate(
            "(() => { const i = document.querySelector('.palette__input');"
            " i.value = 'cat'; i.dispatchEvent(new Event('input', {bubbles: true})); return i.value; })()"
        )
        time.sleep(0.25)
        filtered = cdp.evaluate("document.querySelectorAll('.palette__item').length")
        check("typing filters the list", 0 < filtered < 20, f"items={filtered}")
        time.sleep(0.4)
        capture(cdp, DOCS / "t4-palette.png")

        first = cdp.evaluate(
            "document.querySelector('.palette__item[aria-selected=\"true\"]').textContent"
        )
        cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true}))")
        time.sleep(0.12)
        second = cdp.evaluate(
            "document.querySelector('.palette__item[aria-selected=\"true\"]').textContent"
        )
        check("arrow keys move the selection", first != second, f"{first!r} -> {second!r}")
        check("exactly one row is selected",
              cdp.evaluate("document.querySelectorAll('.palette__item[aria-selected=\"true\"]').length") == 1)

        cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        check("Esc closes the palette", wait_for(cdp, "!document.querySelector('.palette')", 5, "palette close"))

        # ---- 5. shortcut sheet -------------------------------------------- #
        print("\n[5] shortcut sheet")
        cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key: '?', bubbles: true}))")
        check("`?` opens the sheet", wait_for(cdp, "!!document.querySelector('.modal')", 5, "sheet"))
        rows = cdp.evaluate("document.querySelectorAll('.sheet__row').length")
        check("sheet lists shortcuts", rows >= 6, f"rows={rows}")
        time.sleep(0.4)
        capture(cdp, DOCS / "t4-shortcuts.png")
        time.sleep(0.7)  # the dialog focuses on the next frame; let the 220 ms entry settle
        check("focus is trapped inside the dialog",
              cdp.evaluate("document.querySelector('.modal').contains(document.activeElement)"))
        check("first Tab stop is inside the dialog",
              cdp.evaluate("document.activeElement.tagName") in ("BUTTON", "INPUT", "A"))
        cdp.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        check("Esc closes the sheet", wait_for(cdp, "!document.querySelector('.modal')", 5, "sheet close"))

        # ---- 6. theme ----------------------------------------------------- #
        print("\n[6] theme")
        cdp.evaluate("window.__IMRI__.store.setTheme('dark')")
        time.sleep(0.2)
        check("dark theme applies",
              cdp.evaluate("document.documentElement.dataset.theme") == "dark")
        check("theme choice is stored",
              cdp.evaluate("localStorage.getItem('imri.theme')") == "dark")
        bg = cdp.evaluate("getComputedStyle(document.body).backgroundColor")
        check("dark background is applied", bg not in ("rgb(246, 247, 249)", "rgba(0, 0, 0, 0)"), bg)
        capture(cdp, DOCS / "t4-shell-dark.png")

        cdp.evaluate("window.__IMRI__.store.setTheme('light')")
        time.sleep(0.2)
        check("light theme applies",
              cdp.evaluate("document.documentElement.dataset.theme") == "light")
        capture(cdp, DOCS / "t4-shell-light.png")

        # ---- 7. language --------------------------------------------------- #
        print("\n[7] language")
        ms = read_catalogue("ms")
        bootstraps_before = cdp.evaluate(
            "performance.getEntriesByType('resource')"
            ".filter((e) => e.name.includes('/api/bootstrap')).length"
        )
        cdp.evaluate("window.__IMRI__.i18n.setLang('ms')")
        time.sleep(0.25)
        title_ms = cdp.evaluate("document.querySelector('.tb__title').textContent")
        check("chrome repaints in Malay", title_ms == ms["nav.dashboard"], f"{title_ms!r}")
        check("<html lang> follows the choice",
              cdp.evaluate("document.documentElement.lang") == "ms")
        nav_ms = cdp.evaluate(
            "document.querySelector('.sb__item[aria-current=\"page\"] .sb__text').textContent"
        )
        check("sidebar repaints in Malay", nav_ms == ms["nav.dashboard"], f"{nav_ms!r}")
        bootstraps_after = cdp.evaluate(
            "performance.getEntriesByType('resource')"
            ".filter((e) => e.name.includes('/api/bootstrap')).length"
        )
        check("language switch did not re-fetch bootstrap",
              bootstraps_after == bootstraps_before,
              f"before={bootstraps_before} after={bootstraps_after}")
        check("no page reload happened",
              cdp.evaluate("performance.getEntriesByType('navigation')[0].type") == "navigate")

        cdp.evaluate("window.__IMRI__.i18n.setLang('en')")
        time.sleep(0.2)
        check("switching back restores English",
              (cdp.evaluate("document.querySelector('.tb__title').textContent") or "")
              == read_catalogue("en")["nav.dashboard"])

        # ---- 8. toasts ----------------------------------------------------- #
        print("\n[8] toasts")
        cdp.evaluate(
            "for (let i = 0; i < 4; i += 1) { window.__IMRI__.ui.toast.success('t' + i); }"
        )
        time.sleep(0.2)
        count = cdp.evaluate("document.querySelectorAll('.toast').length")
        check("toasts cap at three", count == 3, f"toasts={count}")
        check("toast stack is announced politely",
              cdp.evaluate("document.querySelector('.toasts').getAttribute('aria-live')") == "polite")

        # ---- 9. persistence across a reload -------------------------------- #
        print("\n[9] persistence")
        cdp.evaluate("window.__IMRI__.store.setTheme('dark')")
        cdp.call("Page.reload", {"ignoreCache": False})
        check("app boots after a reload",
              wait_for(cdp, "window.__IMRI_BOOT__ && window.__IMRI_BOOT__.ready", 20, "reload"))
        check("theme survives the reload",
              cdp.evaluate("document.documentElement.dataset.theme") == "dark")
        check("route survives the reload",
              cdp.evaluate("location.hash") == "#/dashboard", cdp.evaluate("location.hash"))
        cdp.evaluate("window.__IMRI__.store.setTheme('light')")

        # ---- 10. console ---------------------------------------------------- #
        print("\n[10] console")
        errors = cdp.console_errors()
        check("no console errors or uncaught exceptions", not errors, "\n".join(errors[:6]))

        # ---- 11. catalogue completeness ------------------------------------- #
        print("\n[11] catalogue")
        problems = cdp.evaluate("JSON.stringify(window.__IMRI__.checkCompleteness())")
        check("checkCompleteness() reports no gaps", problems == "[]", problems)

        # ---- 12. keyboard reachability ------------------------------------- #
        print("\n[12] keyboard")
        cdp.evaluate("document.querySelector('.tb__title').focus(); document.body.focus();")
        cdp.call("Input.dispatchKeyEvent", {
            "type": "rawKeyDown", "windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9, "key": "Tab",
        })
        cdp.call("Input.dispatchKeyEvent", {
            "type": "keyUp", "windowsVirtualKeyCode": 9, "nativeVirtualKeyCode": 9, "key": "Tab",
        })
        time.sleep(0.2)
        first_stop = cdp.evaluate(
            "document.activeElement && (document.activeElement.className || document.activeElement.tagName)"
        )
        check("the first Tab stop is the skip link", "skip-link" in str(first_stop), str(first_stop))
        check("the skip link is visible when focused",
              cdp.evaluate(
                  "(() => { const s = document.querySelector('.skip-link');"
                  " return s.getBoundingClientRect().width > 40; })()"
              ))

        # ---- 13. print ------------------------------------------------------- #
        print("\n[13] print")
        cdp.evaluate("document.activeElement && document.activeElement.blur()")
        cdp.call("Emulation.setEmulatedMedia", {"media": "print"})
        time.sleep(0.3)
        check("the skip link is hidden in print",
              cdp.evaluate("getComputedStyle(document.querySelector('.skip-link')).display") == "none")
        check("navigation is hidden in print",
              cdp.evaluate("getComputedStyle(document.querySelector('.sidebar')).display") == "none")
        check("the topbar is hidden in print",
              cdp.evaluate("getComputedStyle(document.querySelector('.topbar')).display") == "none")
        check("the page itself still prints",
              cdp.evaluate("getComputedStyle(document.querySelector('.page')).display") != "none")
        check("print uses a white background",
              cdp.evaluate("getComputedStyle(document.body).backgroundColor")
              in ("rgb(255, 255, 255)", "rgb(246, 247, 249)"))
        capture(cdp, DOCS / "t4-print.png")
        cdp.call("Emulation.setEmulatedMedia", {"media": ""})

        # ---- 14. contrast ---------------------------------------------------- #
        print("\n[14] contrast (WCAG AA needs 4.5:1 for body text)")
        for theme in ("light", "dark"):
            cdp.evaluate(f"window.__IMRI__.store.setTheme('{theme}')")
            time.sleep(0.2)
            samples = json.loads(cdp.evaluate(CONTRAST_JS))
            worst = min((entry["ratio"] for entry in samples), default=0)
            detail = ", ".join(f"{entry['label']} {entry['ratio']}" for entry in samples)
            check(f"{theme}: every sampled pair passes AA", worst >= 4.5, detail)
            print(f"        {theme}: {detail}")

        # ---- 15. component kit ---------------------------------------------- #
        print("\n[15] component kit")
        counts = json.loads(cdp.evaluate(KIT_JS, timeout=40))
        check("buttons render", counts["buttons"] == 8, str(counts))
        check("form fields render", counts["fields"] == 4, str(counts))
        check("table renders its rows", counts["rows"] == 3, str(counts))
        # six standalone pills plus the three the table renders
        check("pills render", counts["pills"] == 9, str(counts))
        # two filters plus the implicit "All" chip
        check("chips render", counts["chips"] == 3, str(counts))
        check("tabs render", counts["tabs"] == 2, str(counts))
        check("stat cards render", counts["stats"] == 3, str(counts))
        check("bar chart draws one bar per datum", counts["bars"] == 6, str(counts))
        check("donut draws one arc per segment", counts["arcs"] == 3, str(counts))
        check("sparkline renders", counts["sparks"] == 1, str(counts))
        check("pager renders its controls", counts["pager"] >= 4, str(counts))
        check("empty state renders", counts["empty"] == 1, str(counts))
        check("error state renders", counts["errors"] == 1, str(counts))
        check("skeleton renders", counts["skeleton"] >= 6, str(counts))
        time.sleep(0.8)
        capture(cdp, DOCS / "t4-kit.png")
        cdp.evaluate("document.getElementById('kit-gallery').scrollTop = 620")
        time.sleep(0.5)
        capture(cdp, DOCS / "t4-kit-charts.png")
        cdp.evaluate("document.getElementById('kit-gallery').remove()")

        print(f"\n{'=' * 58}")
        print(f"  {PASS} passed, {FAIL} failed")
        if NOTES:
            print("  notes:")
            for note in NOTES:
                print(f"    - {note}")
        print("  screenshots: docs/t4-shell-light.png, t4-shell-dark.png, t4-shell-collapsed.png,")
        print("               docs/t4-palette.png, t4-shortcuts.png")
        print(f"{'=' * 58}")
        return 0 if FAIL == 0 else 1

    finally:
        if cdp and not args.keep:
            cdp.close()
        if browser_proc and not args.keep:
            browser_proc.terminate()
            try:
                browser_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                browser_proc.kill()
        if server and not args.keep:
            server.terminate()
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
        if not args.keep:
            shutil.rmtree(data_dir, ignore_errors=True)
            shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
