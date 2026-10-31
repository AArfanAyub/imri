"""Verify a released IMRI archive, independently of the tool that made it.

``release.py`` verifies the folder it staged. This verifies the *archive* — the
thing you actually hand over or download later — by extracting it to a fresh
directory and testing what came out. The two share no code on purpose: a bug in
the packer that also existed in its own checker would go unnoticed.

What it proves:

  · the zip is not corrupt, and extracts to a single top-level folder
  · every file matches the SHA-256 recorded in MANIFEST.txt, and nothing is
    missing from it — so the archive is the archive that was tested
  · the extracted tree contains no build junk, no test database, no licence file
  · the full test suite passes **from the extracted copy**
  · the bundled IMRI.exe passes its own ``--check`` from the extracted copy

Usage::

    python tools/verify_release.py release/IMRI-1.0.0.zip
    python tools/verify_release.py release/IMRI-1.0.0          # a folder

Exit codes: 0 verified, 1 failed, 2 could not start.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

PASS = 0
FAIL = 0

REQUIRED = (
    "main.py",
    "README.md",
    "CHANGELOG.md",
    "DEVELOPING.md",
    "MANIFEST.txt",
    "VERSION",
    "imri/config.py",
    "imri/server.py",
    "web/index.html",
    "web/js/app.js",
    "web/js/brand.js",
    "tests/run_all.py",
)

FORBIDDEN = ("data", ".tmpdata", "build", "dist", "reference", "__pycache__")


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))
    sys.stdout.flush()


def _run(command: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    try:
        done = subprocess.run(
            command, cwd=str(cwd), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=timeout,
        )
        return done.returncode, done.stdout or ""
    except subprocess.TimeoutExpired as exc:
        captured = exc.output or ""
        if isinstance(captured, bytes):
            captured = captured.decode(errors="replace")
        return 124, captured + "\n[timed out]"
    except OSError as exc:
        return 127, str(exc)


def _parse_manifest(root: Path) -> dict[str, str]:
    """Return ``{relative path: sha256}`` from MANIFEST.txt."""
    manifest = root / "MANIFEST.txt"
    entries: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        digest, _size, path = parts
        if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
            entries[path.strip()] = digest
    return entries


def _free_port() -> int:
    """Ask the OS for an unused loopback port, then let go of it."""
    import socket  # noqa: PLC0415 - only needed here

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _verify_source(root: Path) -> bool:
    """Boot the source app from the extracted copy and fetch its frontend.

    The test suite imports the package. This proves ``main.py`` itself runs and
    that ``web/`` is where the server expects it, by actually requesting the
    shell, the stylesheet and the brand module over HTTP. A release that ships
    the backend but loses a folder of the frontend passes every unit test and
    still shows a blank window — that is the failure this catches.
    """
    import urllib.error  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    print("\n== Source app, from the extracted copy")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    scratch = Path(tempfile.mkdtemp(prefix="imri-verify-src-"))
    server = None
    try:
        server = subprocess.Popen(
            [sys.executable, "main.py", "--no-window", "--port", str(port),
             "--data-dir", str(scratch)],
            cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

        deadline = time.time() + 40
        healthy = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/api/health", timeout=2) as response:
                    if response.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(0.25)
        check("source app starts and answers /api/health", healthy)
        if not healthy:
            if server.stdout:
                for line in server.stdout.read().strip().splitlines()[-6:]:
                    print(f"     {line}")
            return False

        def fetch(path: str) -> tuple[int, str]:
            try:
                with urllib.request.urlopen(base + path, timeout=10) as response:
                    return response.status, response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                return exc.code, ""
            except Exception:
                return 0, ""

        status, shell = fetch("/")
        check("serves the shell at /", status == 200, f"HTTP {status}")
        check("shell is the IMRI document",
              "<title>IMRI</title>" in shell and "brandmark" in shell,
              shell[:80].replace("\n", " "))

        status, script = fetch("/js/brand.js")
        check("serves the brand module", status == 200 and "export function mark" in script,
              f"HTTP {status}")

        status, css = fetch("/css/app.css")
        check("serves the stylesheet", status == 200 and ".brandmark" in css, f"HTTP {status}")

        status, _ = fetch("/js/pages/dashboard.js")
        check("serves a page module", status == 200, f"HTTP {status}")

        # The licence gate must be in force in the released build too.
        status, body = fetch("/api/books")
        check("licence gate is active before activation", status == 403,
              f"HTTP {status} {body[:60]}")
        return True
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except Exception:
                server.kill()
        shutil.rmtree(scratch, ignore_errors=True)


def verify(root: Path, run_tests: bool, run_exe: bool) -> bool:
    print(f"\nVerifying {root.name}\n")

    # -- shape ---------------------------------------------------------- #
    print("== Shape")
    for required in REQUIRED:
        check(f"{required} present", (root / required).is_file())
    for forbidden in FORBIDDEN:
        check(f"no {forbidden}/", not (root / forbidden).exists())

    junk = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.suffix in (".pyc", ".pyo", ".spec")
    ]
    check("no bytecode or spec files", not junk, ", ".join(junk[:3]))

    # -- manifest ------------------------------------------------------- #
    print("\n== Manifest")
    entries = _parse_manifest(root)
    check("manifest lists files", len(entries) >= 100, f"{len(entries)} entries")

    mismatched: list[str] = []
    for relative, digest in entries.items():
        target = root / relative
        if not target.is_file():
            mismatched.append(f"missing: {relative}")
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != digest:
            mismatched.append(f"hash: {relative}")
    check("every listed file matches its SHA-256", not mismatched,
          "; ".join(mismatched[:3]))

    on_disk = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "MANIFEST.txt"
    }
    unlisted = sorted(on_disk - set(entries))
    check("no file is missing from the manifest", not unlisted,
          ", ".join(unlisted[:3]))

    # -- tests ---------------------------------------------------------- #
    if run_tests:
        print("\n== Test suite, from the extracted copy")
        code, output = _run(
            [sys.executable, str(root / "tests" / "run_all.py"), "--quiet"],
            cwd=root, timeout=900,
        )
        for line in [ln for ln in output.strip().splitlines() if ln.strip()][-5:]:
            print(f"     {line}")
        check("test suite passes", code == 0, f"exit {code}")

    # -- source app ----------------------------------------------------- #
    if run_tests:
        _verify_source(root)

    # -- exe ------------------------------------------------------------ #
    exe = root / "IMRI.exe"
    if run_exe and exe.is_file():
        print("\n== Bundled exe, from the extracted copy")
        scratch = Path(tempfile.mkdtemp(prefix="imri-verify-"))
        try:
            code, output = _run(
                [str(exe), "--check", "--data-dir", str(scratch)],
                cwd=root, timeout=180,
            )
            start = output.find("{")
            try:
                payload = json.loads(output[start:]) if start >= 0 else {}
            except json.JSONDecodeError:
                payload = {}
            if not payload:
                for line in output.strip().splitlines()[-6:]:
                    print(f"     {line}")
                check("exe printed a JSON summary", False, f"exit {code}")
            else:
                objects = payload.get("objects") or {}
                check("exe exits cleanly", code == 0, f"exit {code}")
                check("exe reports a schema version", bool(payload.get("schema")),
                      payload.get("schema"))
                check("exe created its tables",
                      int(objects.get("tables") or 0) >= 18, objects.get("tables"))
                check("exe seeds the admin user",
                      int((payload.get("row_counts") or {}).get("users") or 0) == 1)
                check("exe starts unlicensed", payload.get("licensed") is False)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    elif run_exe:
        print("\n== Bundled exe\n     not present — source-only release")

    print(f"\n{PASS} passed, {FAIL} failed")
    return FAIL == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a released IMRI archive.")
    parser.add_argument("target", help="The .zip, or an extracted folder.")
    parser.add_argument("--no-tests", action="store_true", help="Skip the test suite.")
    parser.add_argument("--no-exe", action="store_true", help="Skip the exe check.")
    parser.add_argument("--keep", action="store_true", help="Keep the extraction folder.")
    args = parser.parse_args(argv)

    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        print(f"not found: {target}")
        return 2

    if target.is_dir():
        ok = verify(target, not args.no_tests, not args.no_exe)
        return 0 if ok else 1

    if not zipfile.is_zipfile(target):
        print(f"not a zip archive: {target}")
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="imri-unzip-"))
    try:
        with zipfile.ZipFile(target) as bundle:
            broken = bundle.testzip()
            if broken:
                print(f"archive is corrupt at {broken}")
                return 1
            names = bundle.namelist()
            tops = {name.split("/")[0] for name in names}
            print(f"\n== Archive\n  {len(names)} entries, {target.stat().st_size / 1_048_576:.1f} MB")
            check("extracts to a single top-level folder", len(tops) == 1, str(sorted(tops)))
            bundle.extractall(scratch)

        roots = [p for p in scratch.iterdir() if p.is_dir()]
        if len(roots) != 1:
            print(f"expected one folder after extraction, found {len(roots)}")
            return 1
        ok = verify(roots[0], not args.no_tests, not args.no_exe)
        return 0 if ok else 1
    finally:
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
