"""HTTP transport: static files, the middleware chain, and the app window.

This is the only module that imports :mod:`http`. It owns three things that
decide whether IMRI feels like a desktop app or like a web page in a tab:

**The middleware chain** (ARCHITECTURE §4.2). The order is load-bearing. The
licence gate is stage 4, *before* the session stage, so an unlicensed build
cannot reach any route that touches data — a JS-side gate would be bypassable
from DevTools.

**The shutdown protocol** (§12.3). ``POST /api/system/bye`` arms a 3 s grace
window that any subsequent request cancels, so Ctrl+R does not kill the app;
a heartbeat timeout is the backstop for a browser crash; an ``inflight``
counter keeps the process alive through a request that is still running.

The heartbeat is a *backstop*, not a liveness check, and the distinction
matters. It used to be 120 s, which was actively harmful: the page did not
start pinging until after sign-in, so a librarian who opened IMRI and read the
activation screen for two minutes came back to "the service is not responding"
— the server had exited underneath the form. It is now 1800 s, and silence
before the page's first ping is never read as death.

**The window launch** (§12.2). Edge, then Chrome, then ``webbrowser``, always
with ``--app`` and a dedicated ``--user-data-dir`` inside ``data/``.
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

from . import api, config, errors
from .api import Raw, Request
from .config import get_logger
from .db import Error as DbError
from .security import SESSION_COOKIE, parse_cookie

_log = get_logger("server")

#: Request bodies larger than this are refused with 413 (imports stream).
MAX_BODY_BYTES = 8 * 1024 * 1024

#: Static extensions the server will ever hand out.
STATIC_EXTENSIONS = frozenset(
    {".html", ".css", ".js", ".mjs", ".svg", ".png", ".ico", ".woff2", ".json", ".webmanifest"}
)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
}

CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "form-action 'none'; "
    "object-src 'none'"
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Backstop only — NOT a liveness check.
#:
#: The real "the window is gone" signal is the browser process exiting, which
#: ``main.py`` detects directly. This timer exists for the one case that signal
#: cannot cover: a browser that is still running but has hung or lost its page.
#:
#: It must therefore be long enough never to fire on a real person. It used to
#: be 120 s, which was actively harmful: the page did not start pinging until
#: after sign-in, so a librarian who opened IMRI and spent two minutes reading
#: the activation screen came back to "Cannot reach the IMRI service" — the
#: server had shut itself down underneath them. Thirty minutes cannot be reached
#: by reading, thinking, or a lunch break, and a closed lid is caught by the
#: visibility ping on wake.
HEARTBEAT_TIMEOUT_SECONDS = 1800.0
BYE_GRACE_SECONDS = 3.0


# --------------------------------------------------------------------------- #
# App window
# --------------------------------------------------------------------------- #


class AppWindow:
    """Launch and track the chromeless browser window."""

    WINDOW_SIZE = "1440,900"
    COMMON_FLAGS = (
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate",
        "--disable-extensions",
        "--disable-sync",
    )

    @staticmethod
    def _candidate_paths() -> list[tuple[str, str]]:
        """Return ``(kind, absolute_path)`` for every browser worth probing."""
        roots: list[str] = []
        for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            value = os.environ.get(variable)
            if value:
                roots.append(value)

        relative = {
            "edge": [
                ("Microsoft", "Edge", "Application", "msedge.exe"),
            ],
            "chrome": [
                ("Google", "Chrome", "Application", "chrome.exe"),
                ("Chromium", "Application", "chrome.exe"),
            ],
        }
        found: list[tuple[str, str]] = []
        for kind, patterns in relative.items():
            for root in roots:
                for parts in patterns:
                    candidate = os.path.join(root, *parts)
                    if os.path.isfile(candidate):
                        found.append((kind, candidate))
        return found

    @classmethod
    def _find_browser(cls) -> tuple[str, str] | None:
        """Return the first available ``(kind, path)``, or ``None``."""
        candidates = cls._candidate_paths()
        for kind in ("edge", "chrome"):
            for candidate_kind, path in candidates:
                if candidate_kind == kind:
                    return candidate_kind, path
        return None

    @classmethod
    def launch(cls, url: str, profile_dir: str | os.PathLike[str]) -> subprocess.Popen[Any] | None:
        """Open ``url`` in an app window, falling back to the default browser."""
        profile = str(profile_dir)
        try:
            os.makedirs(profile, exist_ok=True)
        except OSError:  # pragma: no cover - defensive
            profile = ""

        found = cls._find_browser()
        if found is not None:
            _kind, executable = found
            command = [executable, "--app=" + url, "--window-size=" + cls.WINDOW_SIZE]
            if profile:
                command.append("--user-data-dir=" + profile)
            command.extend(cls.COMMON_FLAGS)
            try:
                process = subprocess.Popen(command, close_fds=True)
                _log.info("launched %s for %s", executable, url)
                return process
            except OSError as exc:
                _log.warning("could not launch %s: %s", executable, exc)

        try:
            webbrowser.open(url)
            _log.info("opened %s in the default browser", url)
        except Exception as exc:  # pragma: no cover - defensive
            _log.error("no browser could be opened: %s", exc)
        return None

    @classmethod
    def wait_for_close(cls, server: "HttpServer", process: subprocess.Popen[Any] | None) -> str:
        """Block until the app should exit, and return the reason.

        Three independent signals are polled every 250 ms: the armed ``bye``
        window, the heartbeat timeout, and the launcher process exiting.
        """
        while True:
            time.sleep(0.25)
            reason = server.should_exit()
            if reason and server.inflight == 0 and not server.db.busy:
                _log.info("shutting down: %s", reason)
                return reason
            if process is not None and process.poll() is not None:
                # The launcher handed off to an already-running browser, so
                # this is not proof of a closed window; let the heartbeat decide.
                process = None


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #


class HttpServer:
    """Owns the socket, the lifecycle counters and the request handler."""

    def __init__(self, db: Any, host: str = config.HOST, port: int = 0) -> None:
        self.db = db
        self.ctx = api.AppContext(db)
        self.ctx.register()
        self.ctx.server = self

        self._lock = threading.RLock()
        self._inflight = 0
        self.last_ping = time.monotonic()
        self._seen_ping = False
        self.bye_at: float | None = None

        handler = _make_handler(self.ctx, self)
        self.httpd = ThreadingHTTPServer((host, int(port)), handler)
        self.httpd.daemon_threads = True
        self.host, self.port = self.httpd.server_address[0], int(self.httpd.server_address[1])
        _log.info("listening on http://%s:%d", self.host, self.port)

    # -- lifecycle counters ----------------------------------------------- #
    @property
    def inflight(self) -> int:
        """Number of requests currently being served."""
        return self._inflight

    def begin_request(self) -> None:
        """Mark a request as in flight."""
        with self._lock:
            self._inflight += 1

    def end_request(self) -> None:
        """Mark a request as finished."""
        with self._lock:
            self._inflight = max(0, self._inflight - 1)

    def touch_ping(self) -> None:
        """Record page activity and cancel any armed shutdown."""
        self.last_ping = time.monotonic()
        self._seen_ping = True
        self.cancel_bye()

    def last_ping_age(self) -> float:
        """Seconds since the last page activity."""
        return time.monotonic() - self.last_ping

    def arm_bye(self, grace: float = BYE_GRACE_SECONDS) -> None:
        """Arm the shutdown grace window (a reload cancels it)."""
        self.bye_at = time.monotonic() + float(grace)
        _log.info("shutdown armed with a %.1fs grace window", grace)

    def cancel_bye(self) -> None:
        """Cancel an armed shutdown."""
        if self.bye_at is not None:
            _log.info("shutdown cancelled by a fresh request")
        self.bye_at = None

    def should_exit(self) -> str | None:
        """Return the exit reason, or ``None`` to keep running."""
        if self.bye_at is not None and time.monotonic() >= self.bye_at:
            return "bye"
        # Silence only means "the page died" once the page has spoken at least
        # once. Before that it means "nobody has loaded the page yet", which is
        # the normal state for the first seconds of a launch.
        if self._seen_ping and self.last_ping_age() > HEARTBEAT_TIMEOUT_SECONDS:
            return "heartbeat_timeout"
        return None

    # -- socket ----------------------------------------------------------- #
    @property
    def url(self) -> str:
        """Return the URL the window should open."""
        return "http://%s:%d/#/" % (self.host, self.port)

    def serve_forever(self) -> None:
        """Run the accept loop (call on a daemon thread)."""
        try:
            self.httpd.serve_forever(poll_interval=0.2)
        except Exception:  # pragma: no cover - defensive
            _log.exception("server loop stopped")

    def start_background(self) -> threading.Thread:
        """Start the accept loop on a daemon thread and return it."""
        thread = threading.Thread(target=self.serve_forever, name="imri-http", daemon=True)
        thread.start()
        return thread

    def shutdown(self) -> None:
        """Stop the accept loop and close the socket."""
        try:
            self.httpd.shutdown()
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            self.httpd.server_close()
        except Exception:  # pragma: no cover - defensive
            pass

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "HttpServer(%s:%d)" % (self.host, self.port)


# --------------------------------------------------------------------------- #
# Request handler
# --------------------------------------------------------------------------- #


def _make_handler(ctx: api.AppContext, server: HttpServer) -> type[BaseHTTPRequestHandler]:
    """Build the request handler bound to one context and one server."""

    class ImriHandler(BaseHTTPRequestHandler):
        """One thread per request; the domain layer serialises writes."""

        protocol_version = "HTTP/1.1"
        server_version = "IMRI/" + config.APP_VERSION
        sys_version = ""

        # -- entry points ------------------------------------------------ #
        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._dispatch("PUT")

        def do_PATCH(self) -> None:  # noqa: N802
            self._dispatch("PATCH")

        def do_DELETE(self) -> None:  # noqa: N802
            self._dispatch("DELETE")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send_json(204, None)

        def log_message(self, fmt: str, *args: Any) -> None:
            """Route the stdlib access log into our logger, never stdout."""
            _log.debug("%s - %s", self.address_string(), fmt % args)

        # -- middleware chain -------------------------------------------- #
        def _dispatch(self, method: str) -> None:
            started = time.monotonic()
            server.begin_request()
            status = 500
            code = ""
            try:
                path, query = api.split_target(self.path)
                if not path.startswith("/api"):
                    status = self._serve_static(path)
                    return

                if path != "/api/system/bye":
                    server.touch_ping()

                # Stage 2 — health answers before anything else can fail.
                if path == "/api/health":
                    status = self._send_json(
                        200,
                        api.envelope(
                            {
                                "status": "ok",
                                "version": config.APP_VERSION,
                                "schema": config.SCHEMA_VERSION,
                                "uptime_s": ctx.uptime_seconds(),
                            }
                        ),
                    )
                    return

                # Stage 3 — body size guard.
                length = config.to_int(self.headers.get("Content-Length"), 0)
                if length < 0 or length > MAX_BODY_BYTES:
                    raise errors.ImriError(
                        errors.PAYLOAD_TOO_LARGE,
                        errors.status_for(errors.PAYLOAD_TOO_LARGE),
                        details={"limit": MAX_BODY_BYTES},
                    )
                raw_body = self.rfile.read(length) if length else b""

                # Stage 4 — licence gate.
                if path not in api.LICENSE_EXEMPT:
                    ctx.license.require()

                # Route resolution.
                matched, params = api.find_route(method, path)
                if matched is None:
                    if api.known_path(path):
                        raise errors.ImriError(
                            errors.FORBIDDEN,
                            errors.status_for(errors.FORBIDDEN),
                            details={"allowed": api.allowed_methods(path)},
                        )
                    raise errors.ImriError(
                        errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND)
                    )

                # Stage 5 — session.
                token = self._session_token()
                user = ctx.sessions.lookup(token) if token else None
                if path not in api.PUBLIC_PATHS and user is None:
                    # A cookie that no longer resolves means the session lapsed
                    # or was revoked; saying "session expired" lets the client
                    # explain itself instead of looking like a fresh visitor.
                    failure = errors.SESSION_EXPIRED if token else errors.AUTH_REQUIRED
                    raise errors.ImriError(failure, errors.status_for(failure))

                # Stage 6 — CSRF / origin.
                if method in MUTATING_METHODS and path not in api.CSRF_EXEMPT:
                    self._check_csrf()

                # Stage 7 — capability.
                if matched.cap and not _has_capability(user, matched.cap):
                    raise errors.ImriError(
                        errors.FORBIDDEN,
                        errors.status_for(errors.FORBIDDEN),
                        details={"capability": matched.cap},
                    )

                # Stage 8 — handler.
                body = self._decode_body(raw_body)
                request = Request(
                    method=method,
                    path=path,
                    query=query,
                    body=body,
                    headers=dict(self.headers.items()),
                    client=self.address_string(),
                    raw_body=raw_body,
                )
                request.user = user
                request.token = token
                request.params = params or {}

                data, meta = api.call(ctx, request)
                extra: dict[str, str] = {}
                if meta:
                    cookie = meta.get("set_cookie")
                    if cookie:
                        extra["Set-Cookie"] = cookie
                if isinstance(data, Raw):
                    status = self._send_raw(data, extra)
                else:
                    status = self._send_json(200, api.envelope(data, meta), extra)

            except errors.ImriError as exc:
                status = exc.status
                code = exc.code
                if exc.status >= 500:
                    _log.error("%s %s -> %s %s", method, self.path, exc.status, exc.code)
                status = self._send_json(exc.status, api.error_envelope(exc))
            except DbError as exc:
                # A write that could not take the lock is temporary: the client
                # should retry, so it gets 503 db_busy rather than a 500 that
                # reads like a bug.
                message = str(exc).lower()
                failure = errors.ImriError(
                    errors.DB_BUSY if ("locked" in message or "busy" in message) else errors.INTERNAL,
                    errors.status_for(
                        errors.DB_BUSY if ("locked" in message or "busy" in message) else errors.INTERNAL
                    ),
                    details={"error": type(exc).__name__},
                )
                if failure.status >= 500:
                    _log.error("%s %s -> %s %s", method, self.path, failure.status, failure.code)
                status = failure.status
                code = failure.code
                status = self._send_json(failure.status, api.error_envelope(failure))
            except Exception as exc:  # pragma: no cover - last-resort guard
                _log.exception("unhandled error on %s %s", method, self.path)
                failure = errors.ImriError(
                    errors.INTERNAL,
                    errors.status_for(errors.INTERNAL),
                    details={"error": type(exc).__name__},
                )
                status = self._send_json(failure.status, api.error_envelope(failure))
            finally:
                server.end_request()
                _log.info(
                    "%s %s -> %d %s (%.0f ms)",
                    method,
                    self.path,
                    status,
                    code,
                    (time.monotonic() - started) * 1000.0,
                )

        # -- helpers ------------------------------------------------------ #
        def _session_token(self) -> str | None:
            """Read the session token from the cookie or the header."""
            header = self.headers.get("X-IMRI-Token")
            if header:
                return header.strip()
            return parse_cookie(self.headers.get("Cookie"), SESSION_COOKIE)

        def _check_csrf(self) -> None:
            """Require the custom header and a same-origin signal."""
            if self.headers.get("X-IMRI-CSRF") != "1":
                raise errors.ImriError(
                    errors.FORBIDDEN,
                    errors.status_for(errors.FORBIDDEN),
                    details={"reason": "missing_csrf_header"},
                )
            origin = (self.headers.get("Origin") or "").strip()
            if origin:
                allowed = {
                    "http://%s:%d" % (server.host, server.port),
                    "http://127.0.0.1:%d" % server.port,
                    "http://localhost:%d" % server.port,
                }
                if origin not in allowed:
                    raise errors.ImriError(
                        errors.FORBIDDEN,
                        errors.status_for(errors.FORBIDDEN),
                        details={"reason": "cross_origin"},
                    )
                return
            site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
            if site and site not in ("same-origin", "none"):
                raise errors.ImriError(
                    errors.FORBIDDEN,
                    errors.status_for(errors.FORBIDDEN),
                    details={"reason": "cross_site"},
                )

        def _decode_body(self, raw: bytes) -> dict[str, Any]:
            """Decode a JSON body; upload endpoints keep their raw bytes."""
            if not raw:
                return {}
            content_type = (self.headers.get("Content-Type") or "").lower()
            if content_type and "application/json" not in content_type:
                # Imports post a file with its own media type, and ``bye`` may
                # arrive from ``sendBeacon`` as text/plain. Anywhere else a
                # non-JSON body is a client bug worth naming, not silently
                # turning into an empty dict and a confusing validation error.
                path = api.split_target(self.path)[0]
                if path not in api.RAW_BODY_PATHS:
                    raise errors.ImriError(
                        errors.UNSUPPORTED_MEDIA,
                        errors.status_for(errors.UNSUPPORTED_MEDIA),
                        details={"received": content_type.split(";")[0]},
                    )
                return {}
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    details={"reason": "malformed_json"},
                ) from exc
            return payload if isinstance(payload, dict) else {"value": payload}

        # -- static files -------------------------------------------------- #
        def _serve_static(self, path: str) -> int:
            """Serve one file from ``web_dir()`` with a traversal guard."""
            relative = path.lstrip("/") or "index.html"
            if relative.endswith("/"):
                relative += "index.html"
            normalised = posixpath.normpath(relative)
            if (
                normalised.startswith("..")
                or "\\" in normalised
                or "\x00" in normalised
                or normalised.startswith("/")
            ):
                return self._send_json(
                    404,
                    api.error_envelope(
                        errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
                    ),
                )

            suffix = Path(normalised).suffix.lower()
            root = config.web_dir().resolve()
            target = (root / normalised).resolve()

            if suffix and suffix not in STATIC_EXTENSIONS:
                return self._send_json(
                    404,
                    api.error_envelope(
                        errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
                    ),
                )
            try:
                inside = target.is_relative_to(root)
            except (OSError, ValueError):  # pragma: no cover - defensive
                inside = False
            if not inside:
                return self._send_json(
                    404,
                    api.error_envelope(
                        errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
                    ),
                )
            if not target.is_file():
                # Hash routing means every deep link is "/", but a bare path
                # without a suffix still deserves the shell rather than a 404.
                if not suffix:
                    target = root / "index.html"
                else:
                    return self._send_json(
                        404,
                        api.error_envelope(
                            errors.ImriError(
                                errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND)
                            )
                        ),
                    )

            try:
                with open(target, "rb") as handle:
                    body = handle.read()
            except OSError:
                return self._send_json(
                    404,
                    api.error_envelope(
                        errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
                    ),
                )

            content_type = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return 200

        # -- responses ----------------------------------------------------- #
        def _send_json(
            self,
            status: int,
            payload: Mapping[str, Any] | None,
            extra_headers: Mapping[str, str] | None = None,
        ) -> int:
            """Send a JSON envelope (or an empty 204)."""
            if status == 204 or payload is None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                for key, value in SECURITY_HEADERS.items():
                    self.send_header(key, value)
                self.send_header("Cache-Control", "no-store")
                for key, value in (extra_headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                return status

            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return status

        def _send_raw(self, raw: Raw, extra_headers: Mapping[str, str] | None = None) -> int:
            """Send a non-JSON body (SVG, CSV, file bytes)."""
            body = raw.body or b""
            self.send_response(raw.status)
            if raw.content_type:
                self.send_header("Content-Type", raw.content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in SECURITY_HEADERS.items():
                self.send_header(key, value)
            self.send_header("Cache-Control", "no-store")
            for key, value in raw.headers.items():
                self.send_header(key, value)
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)
            return raw.status

    return ImriHandler


def _has_capability(user: Mapping[str, Any] | None, cap: str) -> bool:
    """Return True when ``user`` holds ``cap`` (server-side, always)."""
    from .security import require

    return require(user, cap)


def free_port_hint() -> int:
    """Return a free ephemeral port without keeping the socket (diagnostics)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((config.HOST, 0))
        return int(probe.getsockname()[1])


def which_browser() -> str | None:
    """Return the browser executable IMRI would launch, if any."""
    found = AppWindow._find_browser()  # noqa: SLF001 - same module
    return found[1] if found else None


def describe_environment() -> dict[str, Any]:
    """Return a small environment snapshot for the log at startup."""
    return {
        "python": sys.version.split()[0],
        "frozen": bool(getattr(sys, "frozen", False)),
        "data_dir": str(config.data_dir()),
        "web_dir": str(config.web_dir()),
        "browser": which_browser() or shutil.which("msedge") or shutil.which("chrome") or "",
    }
