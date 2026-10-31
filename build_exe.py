"""Build a distributable IMRI.

Two modes, because the school's machines may or may not have PyInstaller:

``exe``  (default)
    A single ``dist/IMRI.exe`` built with PyInstaller: the Python runtime, the
    ``imri`` package and the ``web/`` client all in one file. ``data/`` is
    created beside the ``.exe`` on first run, never inside the bundle.

``portable``
    A folder you can copy to a USB stick: ``dist/IMRI-portable/`` holding the
    source tree, the web client and a launcher batch file. Requires nothing but
    a Python install on the target machine. This is the fallback when
    PyInstaller is unavailable, and it is also the mode to use while the
    librarian is still evaluating IMRI.

Neither mode needs the network. Neither mode writes into the source tree
except under ``dist/`` and ``build/``.

Usage::

    python build_exe.py                 # one-file exe, then a smoke test
    python build_exe.py --portable      # folder build, no PyInstaller needed
    python build_exe.py --clean         # wipe dist/ and build/ first
    python build_exe.py --no-verify     # skip the post-build smoke test

Exit codes: 0 built and verified, 1 build failed, 2 preflight failed.
"""

from __future__ import annotations

import argparse
import compileall
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _expected_schema() -> int:
    """Return ``config.SCHEMA_VERSION`` from the source tree being built."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from imri import config  # noqa: PLC0415 - deliberately late

        return int(config.SCHEMA_VERSION)
    except Exception:  # pragma: no cover - defensive
        return 1


DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_NAME = "IMRI"
ENTRY = ROOT / "main.py"
PACKAGE = ROOT / "imri"
WEB = ROOT / "web"

#: Files that make up a portable build, relative to the project root.
PORTABLE_FILES = (
    "main.py",
    "run.bat",
    "README.md",
    "LICENSE",
)

#: Directories copied wholesale into a portable build.
PORTABLE_DIRS = ("imri", "web")

#: Never copied: caches, the operator's data, previous builds.
IGNORE_PATTERNS = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".mypy_cache",
    ".ruff_cache",
    "data",
    "dist",
    "build",
    "*.spec",
)


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #


def step(message: str) -> None:
    """Print a build step header."""
    sys.stdout.write("\n== %s\n" % message)
    sys.stdout.flush()


def info(message: str) -> None:
    """Print an indented detail line."""
    sys.stdout.write("   %s\n" % message)
    sys.stdout.flush()


def fail(message: str, code: int = 1) -> int:
    """Print a failure and return the exit code."""
    sys.stderr.write("\n!! %s\n" % message)
    sys.stderr.flush()
    return code


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #


def preflight() -> list[str]:
    """Return a list of problems that must be fixed before building."""
    problems: list[str] = []

    if sys.version_info < (3, 11):
        problems.append(
            "Python 3.11 or newer is required (this is %d.%d)."
            % (sys.version_info.major, sys.version_info.minor)
        )

    if not ENTRY.is_file():
        problems.append("main.py is missing from %s" % ROOT)

    if not PACKAGE.is_dir():
        problems.append("the imri/ package is missing from %s" % ROOT)
    else:
        for module in (
            "config.py",
            "db.py",
            "errors.py",
            "security.py",
            "catalog.py",
            "members.py",
            "circulation.py",
            "reporting.py",
            "labels.py",
            "transfer.py",
            "nfc.py",
            "server.py",
        ):
            if not (PACKAGE / module).is_file():
                problems.append("imri/%s is missing" % module)

    if not WEB.is_dir():
        problems.append("the web/ client is missing from %s" % ROOT)
    else:
        # js/brand.js is on the critical path: app.js imports it at module
        # scope, so a missing copy fails the whole boot, not one screen.
        for asset in ("index.html", "css/app.css", "js/app.js", "js/api.js", "js/brand.js"):
            if not (WEB / asset).is_file():
                problems.append("web/%s is missing" % asset)

    return problems


def check_syntax() -> bool:
    """Byte-compile the package so a syntax error fails the build, not the user."""
    step("Checking syntax")
    ok = compileall.compile_dir(
        str(PACKAGE), quiet=1, force=True, legacy=False, optimize=0
    )
    ok = compileall.compile_file(str(ENTRY), quiet=1, force=True, legacy=False) and ok
    info("imri/ and main.py compile cleanly" if ok else "syntax errors found")
    return bool(ok)


# --------------------------------------------------------------------------- #
# Clean
# --------------------------------------------------------------------------- #


def clean() -> None:
    """Remove previous build output."""
    step("Cleaning")
    for target in (DIST, BUILD):
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            info("removed %s" % target.name)
    for spec in ROOT.glob("*.spec"):
        try:
            spec.unlink()
            info("removed %s" % spec.name)
        except OSError:
            pass
    for cache in PACKAGE.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


# --------------------------------------------------------------------------- #
# PyInstaller build
# --------------------------------------------------------------------------- #


def pyinstaller_available() -> bool:
    """Return True when PyInstaller can be imported."""
    try:
        import PyInstaller  # noqa: F401
    except Exception:
        return False
    return True


def build_exe() -> int:
    """Build ``dist/IMRI.exe`` with PyInstaller."""
    step("Building the one-file executable")

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        APP_NAME,
        # web/ is read-only and belongs inside the bundle; config.web_dir()
        # resolves it from sys._MEIPASS when frozen.
        "--add-data",
        "%s%sweb" % (WEB, os.pathsep),
        # Trim what the app never imports, which keeps the exe near 12 MB.
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "unittest",
        "--exclude-module",
        "pydoc",
        "--exclude-module",
        "test",
        "--exclude-module",
        "distutils",
        "--exclude-module",
        "lib2to3",
        "--exclude-module",
        "sqlite3.test",
        str(ENTRY),
    ]

    info(" ".join(command))
    started = time.time()
    result = subprocess.run(command, cwd=str(ROOT))
    elapsed = time.time() - started

    if result.returncode != 0:
        return fail("PyInstaller failed with exit code %d" % result.returncode)

    exe = DIST / (APP_NAME + (".exe" if os.name == "nt" else ""))
    if not exe.is_file():
        return fail("PyInstaller reported success but %s is missing" % exe.name)

    size_mb = exe.stat().st_size / (1024 * 1024)
    info("built %s (%.1f MB) in %.0fs" % (exe.name, size_mb, elapsed))
    return 0


# --------------------------------------------------------------------------- #
# Portable build
# --------------------------------------------------------------------------- #


def build_portable() -> int:
    """Assemble ``dist/IMRI-portable/`` — no PyInstaller required."""
    step("Building the portable folder")

    target = DIST / (APP_NAME + "-portable")
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    for name in PORTABLE_DIRS:
        source = ROOT / name
        if not source.is_dir():
            return fail("%s is missing" % name)
        shutil.copytree(source, target / name, ignore=IGNORE_PATTERNS)
        info("copied %s/" % name)

    for name in PORTABLE_FILES:
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, target / name)
            info("copied %s" % name)

    # A README that answers the only two questions a technician will have.
    (target / "HOW-TO-RUN.txt").write_text(
        "\r\n".join(
            [
                "IMRI - portable build",
                "",
                "1. Install Python 3.11 or newer (tick 'Add python.exe to PATH').",
                "2. Double-click run.bat.",
                "",
                "Your library lives in the 'data' folder created next to run.bat.",
                "Copy the whole folder to back everything up, or to move the",
                "library to another machine.",
                "",
                "Default login: admin / admin123  (you will be asked to change it)",
                "Activation code: see the licence card supplied with your copy.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    info("wrote HOW-TO-RUN.txt")

    total = sum(1 for _ in target.rglob("*") if _.is_file())
    info("assembled %d files in %s" % (total, target))
    return 0


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #


def verify_exe() -> int:
    """Run the built executable with ``--check`` against a throwaway data dir."""
    step("Verifying the build")

    exe = DIST / (APP_NAME + (".exe" if os.name == "nt" else ""))
    if not exe.is_file():
        return fail("nothing to verify at %s" % exe)

    with tempfile.TemporaryDirectory(prefix="imri-verify-") as sandbox:
        environment = dict(os.environ)
        environment["IMRI_DATA_DIR"] = sandbox
        command = [str(exe), "--check"]
        info(" ".join(command))
        result = subprocess.run(
            command,
            cwd=str(DIST),
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stdout or "")
            sys.stderr.write(result.stderr or "")
            return fail("the executable exited with %d" % result.returncode)

        summary = {}
        for line in (result.stdout or "").splitlines():
            text = line.strip()
            if text.startswith("{"):
                try:
                    summary = json.loads((result.stdout or "")[result.stdout.index("{") :])
                except ValueError:
                    summary = {}
                break

        if not summary:
            return fail("the executable printed no JSON summary")

        objects = summary.get("objects", {})
        info("schema v%s" % summary.get("schema"))
        info(
            "tables=%s indexes=%s triggers=%s"
            % (objects.get("tables"), objects.get("indexes"), objects.get("triggers"))
        )
        info("settings=%s licensed=%s" % (summary.get("settings"), summary.get("licensed")))

        problems: list[str] = []
        if int(objects.get("tables", 0)) < 18:
            problems.append("expected at least 18 tables, found %s" % objects.get("tables"))
        if int(objects.get("triggers", 0)) < 2:
            problems.append("expected 2 audit triggers, found %s" % objects.get("triggers"))
        # Read the expected version from the source rather than hardcoding it:
        # a migration bump must not silently break the build's own self-check.
        expected_schema = _expected_schema()
        if int(summary.get("schema", 0)) != expected_schema:
            problems.append(
                "expected schema version %s, found %s" % (expected_schema, summary.get("schema"))
            )
        if summary.get("licensed"):
            problems.append("a fresh data directory must start unlicensed")

        rows = summary.get("row_counts", {})
        if int(rows.get("users", 0)) != 1:
            problems.append("expected exactly 1 seeded user, found %s" % rows.get("users"))
        if int(summary.get("settings", 0)) != 19:
            problems.append("expected 19 seeded settings, found %s" % summary.get("settings"))

        if problems:
            for problem in problems:
                sys.stderr.write("   - %s\n" % problem)
            return fail("the build is not usable")

        # The data directory must have been created inside the sandbox, which
        # proves the frozen path resolution does not point at _MEIPASS.
        if not Path(sandbox, "imri.db").is_file():
            return fail("the database was not created in the supplied data directory")
        info("data directory honoured: %s" % sandbox)

    info("build verified")
    return 0


def verify_portable() -> int:
    """Run the portable copy's main.py against a throwaway data dir."""
    step("Verifying the portable build")
    target = DIST / (APP_NAME + "-portable")
    if not (target / "main.py").is_file():
        return fail("nothing to verify at %s" % target)

    with tempfile.TemporaryDirectory(prefix="imri-verify-") as sandbox:
        environment = dict(os.environ)
        environment["IMRI_DATA_DIR"] = sandbox
        result = subprocess.run(
            [sys.executable, str(target / "main.py"), "--check"],
            cwd=str(target),
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            sys.stderr.write(result.stdout or "")
            sys.stderr.write(result.stderr or "")
            return fail("the portable build exited with %d" % result.returncode)
        info("portable build starts and migrates cleanly")

    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the build options."""
    parser = argparse.ArgumentParser(
        prog="build_exe.py",
        description="Build a distributable IMRI.",
    )
    parser.add_argument(
        "--portable",
        action="store_true",
        help="Build a folder that runs from a Python install instead of a single exe.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete dist/ and build/ before starting.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip the post-build smoke test.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Fall back to --portable when PyInstaller is not installed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the build. Returns the process exit code."""
    args = parse_args(argv)

    step("IMRI build")
    info("python %s" % sys.version.split()[0])
    info("root   %s" % ROOT)

    problems = preflight()
    if problems:
        for problem in problems:
            sys.stderr.write("   - %s\n" % problem)
        return fail("preflight failed", 2)

    if args.clean:
        clean()

    if not check_syntax():
        return fail("the source does not compile", 2)

    if args.portable:
        code = build_portable()
        if code:
            return code
        return 0 if args.no_verify else verify_portable()

    if not pyinstaller_available():
        sys.stderr.write(
            "\n   PyInstaller is not installed, so a single .exe cannot be built.\n"
            "\n   Either install it once on a build machine:\n"
            "       python -m pip install pyinstaller\n"
            "\n   or build the portable folder, which needs nothing extra:\n"
            "       python build_exe.py --portable\n"
        )
        if args.force:
            info("--force given: building the portable folder instead")
            code = build_portable()
            if code:
                return code
            return 0 if args.no_verify else verify_portable()
        return fail("PyInstaller is unavailable", 1)

    code = build_exe()
    if code:
        return code

    if args.no_verify:
        info("verification skipped")
        return 0
    return verify_exe()


if __name__ == "__main__":
    sys.exit(main())
