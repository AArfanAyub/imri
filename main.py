"""IMRI entry point — the process lifecycle.

One process, one window, one SQLite connection. This module is the only place
that decides *when* things happen; every rule about *what* happens lives in the
domain layer.

The startup order is load-bearing (ARCHITECTURE §12.1):

1. ``ensure_dirs()`` — ``data/`` must exist before logging, because the log
   file lives in it.
2. ``setup_logging()`` — so every later failure is diagnosable.
3. Single-instance lock — a second double-click must refuse politely instead of
   opening a second window on the same database.
4. ``Database()`` — opens, migrates, seeds settings and the admin account.
5. ``consume_reset_admin()`` — the ``RESET-ADMIN`` escape hatch.
6. ``expire_holds()`` — stale holds become available copies before the first
   page renders, so the dashboard never shows a copy as reserved that has in
   fact expired overnight.
7. ``HttpServer`` on an ephemeral loopback port.
8. ``AppWindow.launch()`` then the wait loop.

The wait loop is the shutdown protocol (§12.3). ``server.should_exit()``
returns ``"bye"`` three seconds after ``POST /api/system/bye`` (a reload
cancels the window) or ``"heartbeat_timeout"`` after 120 s of silence (the
browser crashed). Either way the loop refuses to break while a request is still
in flight or a write transaction is open, then shuts down cleanly.

Run it directly for development::

    python main.py --no-window --port 8765

``--no-window`` prints the URL instead of launching a browser, which is what
the test scripts use.
"""

from __future__ import annotations

import argparse
import atexit
import errno
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from imri import config, security
from imri.config import get_logger
from imri.db import Database
from imri.server import AppWindow, HttpServer, describe_environment

_log = get_logger("main")

#: How long the wait loop sleeps between lifecycle checks.
POLL_SECONDS = 0.25

#: Exit codes. Kept distinct so ``run.bat`` can tell the cases apart.
EXIT_OK = 0
EXIT_ALREADY_RUNNING = 2
EXIT_STARTUP_FAILED = 3


# --------------------------------------------------------------------------- #
# Single-instance lock
# --------------------------------------------------------------------------- #


def _pid_alive(pid: int) -> bool:
    """Return True when ``pid`` names a live process on this machine.

    ``os.kill(pid, 0)`` is the portable form, but on Windows it raises
    ``OSError`` with ``EINVAL`` for a handle the caller may not open, which
    would look like "dead" for a live process owned by another user. The
    ``ctypes`` probe below asks the kernel directly and treats "access denied"
    as alive, which is the safe answer: refusing to start is recoverable,
    corrupting a database with two writers is not.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                error = ctypes.get_last_error()
                # 5 = ERROR_ACCESS_DENIED -> the process exists, we just cannot
                # ask it anything. Treat it as alive.
                return error == 5
            try:
                code = ctypes.c_ulong(0)
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # pragma: no cover - defensive
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM


class InstanceLock:
    """A PID file that refuses a second instance and recovers from a crash.

    The file is JSON so a future version can add fields without breaking the
    stale-check, and so a support engineer can read it by hand.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else config.lock_path()
        self.acquired = False
        self.stale_pid: int | None = None

    def read(self) -> dict[str, Any]:
        """Return the lock contents, or ``{}`` when absent/unreadable."""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            payload = json.loads(raw)
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def acquire(self) -> bool:
        """Take the lock, clearing a stale one left by a crashed process.

        Returns ``False`` when another live IMRI owns the database. The caller
        is responsible for telling the user; this method only logs.
        """
        existing = self.read()
        pid = config.to_int(existing.get("pid"), 0)
        if pid and pid != os.getpid() and _pid_alive(pid):
            _log.warning("another instance is already running (pid %d)", pid)
            return False
        if pid and pid != os.getpid():
            self.stale_pid = pid
            _log.info("reclaiming stale lock from pid %d", pid)

        payload = {
            "pid": os.getpid(),
            "started_at": config.now_utc(),
            "version": config.APP_VERSION,
            "data_dir": str(config.data_dir()),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            _log.exception("could not write the lock file")
            return False

        self.acquired = True
        atexit.register(self.release)
        return True

    def release(self) -> None:
        """Remove the lock if we still own it."""
        if not self.acquired:
            return
        self.acquired = False
        try:
            current = self.read()
            if config.to_int(current.get("pid"), 0) == os.getpid():
                self.path.unlink()
        except OSError:
            # A leftover lock is harmless: the next start reclaims it.
            _log.debug("could not remove the lock file", exc_info=True)


# --------------------------------------------------------------------------- #
# Startup
# --------------------------------------------------------------------------- #


def prepare_database() -> Database:
    """Open the database, run the recovery hooks and return it."""
    db = Database()

    if security.consume_reset_admin(db):
        _log.warning(
            "RESET-ADMIN marker consumed: admin password reset to the default "
            "and flagged for change"
        )

    version = db.schema_version()
    _log.info("database ready at %s (schema v%d)", db.path, version)

    integrity = db.integrity_check()
    if not integrity.get("ok", False):
        _log.error("integrity check reported a problem: %s", integrity)

    mismatches = _rollup_mismatches(db)
    if mismatches:
        _log.error("%d book(s) disagree with their copies: %s", len(mismatches), mismatches[:10])

    return db


def _rollup_mismatches(db: Database) -> list[int]:
    """Return book ids whose rollup columns drifted from the copy table.

    Read-only on purpose: startup never repairs silently, because a mismatch
    means some code path bypassed ``refresh_book_rollup``, and hiding that
    would hide the bug. The nightly ``verify`` job in Administration reports
    the same list and offers an explicit repair.
    """
    from imri.db import rollup_mismatches

    try:
        return list(rollup_mismatches(db))
    except Exception:  # pragma: no cover - defensive
        _log.exception("rollup check failed")
        return []


def expire_stale_holds(db: Database) -> None:
    """Release holds that lapsed while the app was closed.

    Runs before the server starts so the very first dashboard render already
    shows the correct copy states.
    """
    from imri.circulation import CirculationService

    try:
        result = CirculationService(db).expire_holds()
    except Exception:  # pragma: no cover - defensive
        _log.exception("could not expire stale holds")
        return
    expired = int(result.get("expired", 0) or 0)
    promoted = int(result.get("promoted", 0) or 0)
    if expired or promoted:
        _log.info("startup housekeeping: %d hold(s) expired, %d promoted", expired, promoted)


def build_server(db: Database, port: int) -> HttpServer:
    """Bind the HTTP server on loopback."""
    server = HttpServer(db, host=config.HOST, port=port)
    _log.info("IMRI %s listening on %s", config.APP_VERSION, server.url)
    return server


def install_signal_handlers(server: HttpServer) -> None:
    """Turn Ctrl+C / SIGTERM into a graceful shutdown instead of a traceback."""

    def _handler(signum: int, frame: Any) -> None:
        _log.info("signal %s received, shutting down", signum)
        server.arm_bye(grace=0.0)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (OSError, ValueError):  # pragma: no cover - non-main thread
            pass


def wait_loop(server: HttpServer, process: Any | None) -> str:
    """Run until the shutdown protocol fires, then return the reason.

    The loop refuses to exit while a request is in flight or a write
    transaction is open. That guard is what makes ``bye`` safe: the page can
    close the instant after it fires the request, and the export that is
    halfway through writing a CSV still completes.

    ``process`` is the browser we launched, if any. Its exit is *not* treated
    as proof the window closed — a second launch hands off to the already
    running browser and exits immediately — so the heartbeat stays in charge.
    """
    while True:
        reason = server.should_exit()
        if reason is not None:
            busy = server.inflight > 0 or server.db.busy
            if not busy:
                _log.info("shutdown reason: %s", reason)
                return reason
            # Something is still running; give it a moment and re-check.
            _log.debug("shutdown deferred: inflight=%d busy=%s", server.inflight, server.db.busy)
        if process is not None and process.poll() is not None:
            _log.debug("launcher exited; the heartbeat now decides when to stop")
            process = None
        time.sleep(POLL_SECONDS)


def shutdown(server: HttpServer, db: Database) -> None:
    """Close the socket, checkpoint the WAL and close the database."""
    _log.info("stopping the HTTP server")
    try:
        server.shutdown()
    except Exception:  # pragma: no cover - defensive
        _log.exception("server shutdown raised")

    try:
        db.checkpoint("TRUNCATE")
    except Exception:  # pragma: no cover - defensive
        _log.exception("WAL checkpoint failed")

    try:
        db.close()
    except Exception:  # pragma: no cover - defensive
        _log.exception("database close failed")

    _log.info("IMRI stopped cleanly")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the small command line ``run.bat`` and the tests use."""
    parser = argparse.ArgumentParser(
        prog="imri",
        description="IMRI — offline library management for school resource centres.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.DEFAULT_PORT,
        help="TCP port on 127.0.0.1 (0 = pick a free one, the default).",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Print the URL instead of launching the app window.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Alias of --no-window, kept for the packaging scripts.",
    )
    parser.add_argument(
        "--data-dir",
        default="",
        help="Override the data directory (also honours IMRI_DATA_DIR).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Start, verify health, print a JSON summary and exit.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log at DEBUG level.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Start IMRI. Returns the process exit code."""
    args = parse_args(argv)

    if args.data_dir:
        os.environ[config.DATA_DIR_ENV] = args.data_dir

    config.ensure_dirs()
    config.setup_logging(force=True)
    _log.info("starting %s %s", config.APP_NAME, config.APP_VERSION)
    _log.info("environment: %s", json.dumps(describe_environment(), ensure_ascii=False))

    lock = InstanceLock()
    if not lock.acquire():
        existing = lock.read()
        message = (
            "IMRI is already running (pid %s). Close the existing window first."
            % (existing.get("pid") or "unknown")
        )
        _log.error(message)
        # Written to stderr so run.bat can surface it even with logging off.
        sys.stderr.write(message + "\n")
        return EXIT_ALREADY_RUNNING

    db: Database | None = None
    server: HttpServer | None = None
    process: Any | None = None

    try:
        db = prepare_database()
        expire_stale_holds(db)
        server = build_server(db, args.port)
        install_signal_handlers(server)
        server.start_background()

        if args.check:
            summary = {
                "url": server.url,
                "port": server.port,
                "data_dir": str(config.data_dir()),
                "schema": db.schema_version(),
                "objects": db.object_counts(),
                "row_counts": db.row_counts(),
                "settings": db.scalar("SELECT COUNT(*) FROM settings"),
                "licensed": security.LicenseGate(db).is_licensed(),
            }
            sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
            shutdown(server, db)
            return EXIT_OK

        if args.no_window or args.no_browser:
            sys.stdout.write(server.url + "\n")
            sys.stdout.flush()
        else:
            process = AppWindow.launch(server.url, config.browser_profile_dir())

        wait_loop(server, process)
        return EXIT_OK
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        _log.info("interrupted")
        return EXIT_OK
    except Exception:
        _log.exception("startup failed")
        return EXIT_STARTUP_FAILED
    finally:
        if server is not None and db is not None:
            shutdown(server, db)
        elif db is not None:
            db.close()
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
