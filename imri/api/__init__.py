"""Route table, request parsing, pagination and the composition root.

Handlers contain **no SQL and no business rules**: they parse, authorise,
delegate to a domain service and serialise the result. Everything they need is
on :class:`AppContext`.

The registry is a plain list of :class:`Route` objects built at import time by
the ``@route`` decorator. When two patterns match the same path (``/api/books``
vs ``/api/books/{id}`` vs ``/api/books/lookup``) the one with the fewest path
parameters wins, so a literal segment always beats a capture.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlsplit

from .. import config, errors
from ..catalog import CatalogService
from ..circulation import CirculationService
from ..config import get_logger
from ..labels import LabelService
from ..members import MemberService
from ..nfc import NfcBridge
from ..reporting import ReportingService
from ..security import LicenseGate, SecurityService
from ..transfer import BackupService, TransferService

_log = get_logger("api")

#: Paths reachable without a session (ARCHITECTURE §4.2 stage 5).
PUBLIC_PATHS = frozenset(
    {
        "/api/health",
        "/api/bootstrap",
        "/api/auth/login",
        "/api/auth/heartbeat",
        "/api/system/bye",
        "/api/license/activate",
    }
)

#: Paths reachable before activation (ARCHITECTURE §4.2 stage 4).
#:
#: ``/api/bootstrap`` lets the shell decide whether to paint the activation
#: screen. ``/api/license/activate`` is the way out of that state.
#: ``/api/system/bye`` must survive the gate too: a librarian who opens the app
#: and closes the activation window without activating still expects the
#: process to stop. ``/api/auth/heartbeat`` must survive it as well, or an
#: unactivated page could not keep its own server alive.
LICENSE_EXEMPT = frozenset(
    {"/api/bootstrap", "/api/license/activate", "/api/system/bye", "/api/auth/heartbeat"}
)

#: Mutating paths that cannot carry a custom header (``navigator.sendBeacon``).
CSRF_EXEMPT = frozenset({"/api/system/bye"})

#: Endpoints that take a raw file body instead of a JSON document. Everything
#: else rejects a non-JSON content type with ``415 unsupported_media`` rather
#: than silently treating the payload as empty.
RAW_BODY_PATHS = frozenset(
    {
        "/api/books/import/preview",
        "/api/members/import/preview",
        "/api/system/bye",
    }
)

MAX_PAGE_SIZE = 200
MIN_PAGE_SIZE = 1


# --------------------------------------------------------------------------- #
# Request / response primitives
# --------------------------------------------------------------------------- #


class Raw:
    """A non-JSON response body (SVG, CSV, file bytes)."""

    __slots__ = ("body", "content_type", "status", "headers")

    def __init__(
        self,
        body: bytes | str,
        content_type: str = "text/plain; charset=utf-8",
        status: int = 200,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.content_type = content_type
        self.status = status
        self.headers = dict(headers or {})


class Request:
    """One parsed HTTP request, already authenticated."""

    def __init__(
        self,
        method: str,
        path: str,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
        headers: Mapping[str, str] | None = None,
        client: str | None = None,
        raw_body: bytes = b"",
    ) -> None:
        self.method = method.upper()
        self.path = path
        self.query: dict[str, str] = {
            str(key): str(value) for key, value in (query or {}).items() if value is not None
        }
        self.body = body if isinstance(body, dict) else {}
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        self.client = client
        self.raw_body = raw_body
        self.params: dict[str, str] = {}
        self.user: dict[str, Any] | None = None
        self.token: str | None = None

    # -- query helpers ---------------------------------------------------- #
    def get(self, name: str, default: Any = None) -> Any:
        """Return a query parameter, or ``default``."""
        return self.query.get(name, default)

    def get_int(self, name: str, default: int = 0) -> int:
        """Return a query parameter coerced to ``int``."""
        return config.to_int(self.query.get(name), default)

    def get_bool(self, name: str, default: bool = False) -> bool:
        """Return a query parameter coerced to ``bool``."""
        if name not in self.query:
            return default
        return config.to_bool(self.query.get(name))

    # -- body helpers ----------------------------------------------------- #
    def field(self, name: str, default: Any = None) -> Any:
        """Return a body field, or ``default``."""
        return self.body.get(name, default)

    def require(self, name: str) -> Any:
        """Return a required body field or raise ``validation_failed``."""
        value = self.body.get(name)
        if value in (None, ""):
            raise errors.ImriError(
                errors.VALIDATION_FAILED,
                errors.status_for(errors.VALIDATION_FAILED),
                fields={name: errors.VALIDATION_FAILED},
            )
        return value

    def param(self, name: str, default: Any = None) -> Any:
        """Return a path parameter."""
        return self.params.get(name, default)

    def param_int(self, name: str) -> int:
        """Return a path parameter as an int, raising ``not_found`` when absent."""
        value = self.params.get(name)
        number = config.to_int(value, -1)
        if number < 0:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        return number

    @property
    def actor(self) -> str:
        """Return the acting username for audit rows."""
        return str((self.user or {}).get("username") or "system")

    @property
    def actor_id(self) -> int | None:
        """Return the acting user id, when there is one."""
        user = self.user or {}
        return int(user["id"]) if user.get("id") is not None else None


class Route:
    """One ``method + pattern -> handler`` binding."""

    __slots__ = ("method", "pattern", "cap", "handler", "regex", "param_count", "sorts")

    def __init__(
        self,
        method: str,
        pattern: str,
        handler: Callable[..., Any],
        cap: str | None = None,
        sorts: Iterable[str] | None = None,
    ) -> None:
        self.method = method.upper()
        self.pattern = pattern
        self.handler = handler
        self.cap = cap
        self.sorts = tuple(sorts or ())
        self.regex, self.param_count = _compile_pattern(pattern)

    def match(self, path: str) -> dict[str, str] | None:
        """Return the path parameters when ``path`` matches, else ``None``."""
        found = self.regex.match(path)
        if not found:
            return None
        return {key: unquote(value) for key, value in found.groupdict().items()}


_PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _compile_pattern(pattern: str) -> tuple[re.Pattern[str], int]:
    """Compile ``/api/books/{id}/copies`` into a regex with named groups.

    A placeholder may sit inside a longer segment (``{copy_id}.svg``), so the
    capture is non-greedy and the surrounding text stays literal.
    """
    parts: list[str] = []
    count = 0
    cursor = 0
    for found in _PARAM_RE.finditer(pattern):
        parts.append(re.escape(pattern[cursor : found.start()]))
        parts.append("(?P<%s>[^/]+?)" % found.group(1))
        count += 1
        cursor = found.end()
    parts.append(re.escape(pattern[cursor:]))
    return re.compile("^" + "".join(parts) + "$"), count


ROUTES: list[Route] = []


def route(
    method: str,
    pattern: str,
    cap: str | None = None,
    sorts: Iterable[str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a handler for ``method`` + ``pattern``."""

    def decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
        ROUTES.append(Route(method, pattern, handler, cap=cap, sorts=sorts))
        return handler

    return decorator


def find_route(method: str, path: str) -> tuple[Route, dict[str, str]] | tuple[None, None]:
    """Return the most specific route for ``method`` + ``path``."""
    best: Route | None = None
    best_params: dict[str, str] | None = None
    for candidate in ROUTES:
        if candidate.method != method.upper():
            continue
        params = candidate.match(path)
        if params is None:
            continue
        if best is None or candidate.param_count < best.param_count:
            best = candidate
            best_params = params
    return best, best_params


def known_path(path: str) -> bool:
    """Return True when any method is registered for ``path``."""
    return any(candidate.match(path) is not None for candidate in ROUTES)


def allowed_methods(path: str) -> list[str]:
    """Return the HTTP methods registered for ``path``."""
    return sorted(
        {candidate.method for candidate in ROUTES if candidate.match(path) is not None}
    )


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #


def parse_page(
    query: Mapping[str, Any],
    sorts: Sequence[str] | None = None,
    default_sort: str | None = None,
    default_order: str = "asc",
    default_size: int = 50,
) -> dict[str, Any]:
    """Parse and clamp pagination/sort parameters.

    ``page_size`` is clamped to ``[1, 200]`` (ARCHITECTURE §4.1); an unknown
    ``sort`` falls back to ``default_sort`` instead of reaching SQL.
    """
    page = max(1, config.to_int(query.get("page"), 1))
    size = config.to_int(query.get("page_size"), default_size)
    size = max(MIN_PAGE_SIZE, min(MAX_PAGE_SIZE, size))

    sort = str(query.get("sort") or "").strip()
    allowed = list(sorts or ())
    if allowed and sort not in allowed:
        sort = default_sort or allowed[0]
    elif not allowed:
        sort = default_sort or ""

    order = "desc" if str(query.get("order") or default_order).lower() == "desc" else "asc"
    return {
        "page": page,
        "page_size": size,
        "offset": (page - 1) * size,
        "sort": sort,
        "order": order,
    }


def envelope(data: Any, meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Wrap a payload in the success envelope."""
    payload: dict[str, Any] = {"ok": True, "data": data}
    if meta:
        payload["meta"] = dict(meta)
    return payload


def error_envelope(exc: errors.ImriError) -> dict[str, Any]:
    """Wrap an :class:`~imri.errors.ImriError` in the failure envelope."""
    return {"ok": False, "error": exc.to_dict()}


# --------------------------------------------------------------------------- #
# Composition root
# --------------------------------------------------------------------------- #


class AppContext:
    """Every service the handlers need, built once per process."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self.security = SecurityService(db)
        self.sessions = self.security.sessions
        self.license = LicenseGate(db)
        self.catalog = CatalogService(db)
        self.members = MemberService(db, self.catalog)
        self.circulation = CirculationService(db, self.catalog)
        self.reporting = ReportingService(db)
        self.labels = LabelService()
        self.transfer = TransferService(db, self.catalog)
        self.backup = BackupService(db)
        self.nfc = NfcBridge()
        self.started_at = time.monotonic()
        self.server: Any = None
        self._registered = False

    def register(self) -> None:
        """Import the handler modules so their ``@route`` calls run."""
        if self._registered:
            return
        from . import circulation as _circulation  # noqa: F401
        from . import core as _core  # noqa: F401
        from . import insights as _insights  # noqa: F401
        from . import library as _library  # noqa: F401

        self._registered = True

    def uptime_seconds(self) -> int:
        """Return how long this process has been serving."""
        return int(time.monotonic() - self.started_at)


def call(ctx: AppContext, request: Request) -> tuple[Any, dict[str, Any] | None]:
    """Invoke the handler for ``request`` and normalise its return value."""
    matched, params = find_route(request.method, request.path)
    if matched is None:
        raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
    request.params = params or {}
    result = matched.handler(ctx, request)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        return result[0], result[1]
    return result, None


def parse_query_string(raw: str) -> dict[str, str]:
    """Flatten a query string into ``{name: last_value}``."""
    if not raw:
        return {}
    parsed = parse_qs(raw, keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def split_target(target: str) -> tuple[str, dict[str, str]]:
    """Split a request target into ``(path, query)``."""
    parts = urlsplit(target)
    return unquote(parts.path or "/"), parse_query_string(parts.query)


def decode_json(raw: bytes) -> Any:
    """Decode a JSON request body, raising ``validation_failed`` on junk."""
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise errors.ImriError(
            errors.VALIDATION_FAILED,
            errors.status_for(errors.VALIDATION_FAILED),
            details={"reason": "malformed_json"},
        ) from exc
