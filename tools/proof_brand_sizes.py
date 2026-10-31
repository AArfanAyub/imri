"""Render the real brand mark at every size it is used, for visual review.

Deliberately imports ``/js/brand.js`` from the running app rather than copying
the path data here — a copy would drift, and then this proof would be
reviewing a drawing the product no longer ships.

Writes ``docs/brand-sizes.png``.
"""

import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import CDP  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APP_PORT = 8747
DEBUG_PORT = 9347
BASE = f"http://127.0.0.1:{APP_PORT}"

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]

RENDER = """(async () => {
  const brand = await import('/js/brand.js');
  const sizes = [192, 96, 56, 32, 28, 20, 16];
  document.documentElement.innerHTML = `
    <head><meta charset="utf-8"><style>
      body { margin:0; background:#0b1020; color:#94a3b8; height:100vh;
             display:flex; align-items:center; justify-content:center;
             font-family: system-ui, sans-serif; }
      .row { display:flex; align-items:flex-end; gap:40px; }
      .cell { text-align:center; font-size:11px; letter-spacing:.08em; }
      .cell > div { margin-top:10px; }
    </style></head>
    <body><div class="row" id="row"></div></body>`;
  const row = document.getElementById('row');
  for (const size of sizes) {
    const cell = document.createElement('div');
    cell.className = 'cell';
    cell.textContent = size + ' px';
    const holder = document.createElement('div');
    holder.appendChild(brand.mark(size, { label: null }));
    cell.appendChild(holder);
    row.appendChild(cell);
  }
  return document.querySelectorAll('svg.brandmark').length;
})()"""


def main() -> int:
    browser = next((b for b in BROWSERS if Path(b).is_file()), None)
    if not browser:
        print("No Edge or Chrome found.")
        return 2

    data_dir = Path(tempfile.mkdtemp(prefix="imri-sizes-"))
    profile_dir = Path(tempfile.mkdtemp(prefix="imri-sizes-profile-"))

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
        else:
            print("server did not start")
            return 2

        browser_proc = subprocess.Popen(
            [browser, "--headless=new", f"--remote-debugging-port={DEBUG_PORT}",
             f"--user-data-dir={profile_dir}", "--no-first-run",
             "--no-default-browser-check", "--disable-gpu", "--disable-extensions",
             "--hide-scrollbars", "--window-size=1180,420", "about:blank"],
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
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 1180, "height": 420, "deviceScaleFactor": 2, "mobile": False,
        })
        cdp.call("Page.navigate", {"url": f"{BASE}/"})
        time.sleep(1.2)

        drawn = cdp.evaluate(RENDER)
        print(f"marks drawn: {drawn}")
        time.sleep(0.4)
        cdp.screenshot(str(ROOT / "docs" / "brand-sizes.png"))
        print("wrote docs/brand-sizes.png")
        return 0 if drawn == 7 else 1

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
