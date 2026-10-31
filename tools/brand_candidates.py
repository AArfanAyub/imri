"""Render candidate brand marks side by side, for a visual decision.

Not part of the product. Writes ``docs/brand-candidates.png`` and prints
nothing useful beyond the count, so the choice is made by looking.

Usage:  python tools/brand_candidates.py
"""

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
APP_PORT = 8749
DEBUG_PORT = 9349
BASE = f"http://127.0.0.1:{APP_PORT}"

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

BODY = """
<rect x="2" y="2" width="44" height="44" rx="13" fill="url(#bg)"/>
<rect x="2" y="2" width="44" height="44" rx="13" fill="url(#shine)"/>
<rect x="3.4" y="3.4" width="41.2" height="41.2" rx="11.8" fill="none"
      stroke="#ffffff" stroke-opacity="0.16" stroke-width="1.2"/>
"""

DEFS = """
<defs>
  <linearGradient id="bg" x1="8" y1="0" x2="40" y2="48" gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#4F46E5"/><stop offset="0.52" stop-color="#6D5BE8"/>
    <stop offset="1" stop-color="#8B5CF6"/>
  </linearGradient>
  <linearGradient id="shine" x1="24" y1="2" x2="24" y2="28" gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#ffffff" stop-opacity="0.30"/>
    <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="rib" x1="29" y1="16" x2="29" y2="43" gradientUnits="userSpaceOnUse">
    <stop offset="0" stop-color="#FCD34D"/><stop offset="1" stop-color="#F59E0B"/>
  </linearGradient>
</defs>
"""

VARIANTS = {
    # A — the drawing currently shipped: gentle dome, mild pinch.
    "A current": """
      <path d="M26.9 18H29.9V42L28.4 39L26.9 42Z" fill="url(#rib)"/>
      <path d="M23 17.6Q14 12.6 9.6 14.6Q8.4 15.1 8.4 16.6V30.2Q8.4 31.7 9.6 32.1Q14 34.1 23 35.5Z" fill="#fff" fill-opacity=".88"/>
      <path d="M25 17.6Q34 12.6 38.4 14.6Q39.6 15.1 39.6 16.6V30.2Q39.6 31.7 38.4 32.1Q34 34.1 25 35.5Z" fill="#fff"/>
    """,
    # B — exaggerated pinch: the spine edge is much taller than the outer edge,
    #     so the pages visibly fan open.
    "B pinch": """
      <path d="M26.4 17.4H30.2V42.2L28.3 39.6L26.4 42.2Z" fill="url(#rib)"/>
      <path d="M22.9 17.2Q13.2 12.2 9.6 14.4Q8.2 15.1 8.2 16.8V28.8Q8.2 30.5 9.6 31.2Q13.2 33.4 22.9 36Z" fill="#fff" fill-opacity=".88"/>
      <path d="M25.1 17.2Q34.8 12.2 38.4 14.4Q39.8 15.1 39.8 16.8V28.8Q39.8 30.5 38.4 31.2Q34.8 33.4 25.1 36Z" fill="#fff"/>
    """,
    # C — curled pages: the outer edge bulges out and the top/bottom edges
    #     sag toward the spine, the way paper actually sits.
    "C curl": """
      <path d="M26.7 17.8H30.1V42.4L28.4 39.8L26.7 42.4Z" fill="url(#rib)"/>
      <path d="M23 17.8C17.4 14.4 11.6 14 8.8 15.6C7.9 16.1 7.5 16.9 7.5 17.8V28.6C7.5 29.6 7.9 30.4 8.8 30.9C11.6 32.5 17.4 32.1 23 35.4Z" fill="#fff" fill-opacity=".88"/>
      <path d="M25 17.8C30.6 14.4 36.4 14 39.2 15.6C40.1 16.1 40.5 16.9 40.5 17.8V28.6C40.5 29.6 40.1 30.4 39.2 30.9C36.4 32.5 30.6 32.1 25 35.4Z" fill="#fff"/>
    """,
    # D — book with a cover: a darker silhouette behind the white pages reads
    #     as a hardback binding and gives the white a place to sit.
    "D cover": """
      <path d="M23.2 15.4C17.6 12.4 11.4 12 8.2 13.6V33.2C11.4 31.6 17.6 32 23.2 35Z" fill="#312E81"/>
      <path d="M24.8 15.4C30.4 12.4 36.6 12 39.8 13.6V33.2C36.6 31.6 30.4 32 24.8 35Z" fill="#312E81"/>
      <path d="M26.6 17.6H29.8V41.6L28.2 39.2L26.6 41.6Z" fill="url(#rib)"/>
      <path d="M23 17.2Q14 13.4 10.4 15.2Q9.4 15.7 9.4 16.8V29.6Q9.4 30.7 10.4 31.2Q14 33 23 35.2Z" fill="#fff" fill-opacity=".9"/>
      <path d="M25 17.2Q34 13.4 37.6 15.2Q38.6 15.7 38.6 16.8V29.6Q38.6 30.7 37.6 31.2Q34 33 25 35.2Z" fill="#fff"/>
    """,
}


def cell(name: str, body: str, size: int) -> str:
    return (
        f'<div class="cell"><svg viewBox="0 0 48 48" width="{size}" height="{size}">'
        f"{DEFS}{BODY}{body}</svg><b>{name}</b></div>"
    )


def main() -> int:
    browser = next((b for b in BROWSERS if Path(b).is_file()), None)
    if not browser:
        print("No Edge or Chrome found.")
        return 2

    cells_large = "".join(cell(k, v, 168) for k, v in VARIANTS.items())
    cells_small = "".join(cell(k, v, 28) for k, v in VARIANTS.items())
    cells_tiny = "".join(cell(k, v, 16) for k, v in VARIANTS.items())

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
      body {{ margin:0; background:#0b1020; color:#94a3b8; font-family:system-ui;
             height:100vh; display:flex; flex-direction:column; align-items:center;
             justify-content:center; gap:34px; }}
      .row {{ display:flex; align-items:flex-end; gap:34px; }}
      .cell {{ text-align:center; }}
      .cell b {{ display:block; margin-top:10px; font-size:11px; font-weight:500;
                 letter-spacing:.08em; }}
    </style></head><body>
      <div class="row">{cells_large}</div>
      <div class="row">{cells_small}</div>
      <div class="row">{cells_tiny}</div>
    </body></html>"""

    data_dir = Path(tempfile.mkdtemp(prefix="imri-cand-"))
    profile_dir = Path(tempfile.mkdtemp(prefix="imri-cand-p-"))

    server = subprocess.Popen(
        [sys.executable, "main.py", "--port", str(APP_PORT),
         "--data-dir", str(data_dir), "--no-window"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    browser_proc = None
    cdp = None
    try:
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{BASE}/api/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.2)

        browser_proc = subprocess.Popen(
            [browser, "--headless=new", f"--remote-debugging-port={DEBUG_PORT}",
             f"--user-data-dir={profile_dir}", "--no-first-run",
             "--no-default-browser-check", "--disable-gpu", "--disable-extensions",
             "--hide-scrollbars", "--window-size=1000,780", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

        deadline = time.time() + 20
        socket_url = None
        while time.time() < deadline and socket_url is None:
            try:
                socket_url = CDP.page_target(DEBUG_PORT)
            except Exception:
                time.sleep(0.25)

        cdp = CDP(socket_url)
        cdp.call("Runtime.enable")
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1000, "height": 780, "deviceScaleFactor": 2, "mobile": False,
        })
        cdp.call("Page.navigate", {"url": f"{BASE}/"})
        time.sleep(1.0)
        # Write the comparison into the page directly. Doing it through a
        # fetched file would mean dropping a stray document in web/.
        cdp.evaluate(
            "((html) => { document.open(); document.write(html); document.close();"
            "  return true; })(%s)" % json.dumps(html)
        )
        time.sleep(0.8)
        cdp.screenshot(str(ROOT / "docs" / "brand-candidates.png"))
        print("wrote docs/brand-candidates.png")
        return 0
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
        import shutil
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
