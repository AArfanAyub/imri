"""Core endpoints: health, bootstrap, licence, auth, dashboard, admin, system.

Handlers parse, authorise, delegate and serialise. No SQL, no business rules.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

from .. import config, errors
from ..config import get_logger
from ..db import rollup_mismatches
from ..security import caps_for, clear_cookie_header, cookie_header
from ..transfer import safe_cell, write_csv
from . import Raw, Request, parse_page, route

_log = get_logger("api.core")

_SETTING_EXPORT_COLUMNS = ("key", "value")


def _settings_payload(ctx: Any) -> dict[str, str]:
    """Return every setting, with the defaults filled in."""
    merged = dict(config.DEFAULT_SETTINGS)
    merged.update(ctx.db.all_settings())
    return merged


def _client_settings(settings: dict[str, str]) -> dict[str, str]:
    """Return only the settings the client needs to boot."""
    keys = (
        "school_name",
        "currency",
        "currency_space",
        "default_locale",
        "theme",
        "page_size_default",
        "fine_per_day",
        "loan_days",
        "max_loans",
        "max_renewals",
        "hold_days",
    )
    return {key: settings.get(key, "") for key in keys}


# --------------------------------------------------------------------------- #
# Health and bootstrap
# --------------------------------------------------------------------------- #


@route("GET", "/api/health")
def health(ctx: Any, request: Request) -> dict[str, Any]:
    """Liveness probe. Answers before the licence gate, always 200."""
    return {
        "status": "ok",
        "version": config.APP_VERSION,
        "schema": config.SCHEMA_VERSION,
        "uptime_s": ctx.uptime_seconds(),
    }


@route("GET", "/api/bootstrap")
def bootstrap(ctx: Any, request: Request) -> dict[str, Any]:
    """Everything the client needs to decide which screen to paint."""
    status = ctx.license.status()
    settings = _settings_payload(ctx)
    session = None
    if request.user:
        session = {
            "user": {
                "id": request.user.get("id"),
                "username": request.user.get("username"),
                "display_name": request.user.get("display_name"),
                "role": request.user.get("role"),
            },
            "role": request.user.get("role"),
            "caps": caps_for(str(request.user.get("role") or "")),
            "must_change_password": bool(request.user.get("must_change_password")),
        }
    return {
        "licensed": bool(status.get("licensed")),
        "license": {
            "edition": status.get("edition", ""),
            "activated_at": status.get("activated_at", ""),
            "school_name": status.get("school_name", ""),
        },
        "session": session,
        "settings": _client_settings(settings),
        "version": config.APP_VERSION,
        "schema": config.SCHEMA_VERSION,
    }


# --------------------------------------------------------------------------- #
# Licence
# --------------------------------------------------------------------------- #


@route("POST", "/api/license/activate")
def license_activate(ctx: Any, request: Request) -> dict[str, Any]:
    """Validate the activation code and persist the activation."""
    code = request.field("code")
    if code in (None, ""):
        raise errors.ImriError(
            errors.INVALID_LICENSE_CODE,
            errors.status_for(errors.INVALID_LICENSE_CODE),
            fields={"code": errors.INVALID_LICENSE_CODE},
        )
    return ctx.license.activate(str(code), client=request.client)


@route("GET", "/api/license", cap="license.read")
def license_status(ctx: Any, request: Request) -> dict[str, Any]:
    """Return the licence record shown on Administration -> Licence & About."""
    status = ctx.license.status()
    return {
        "product": config.PRODUCT,
        "edition": status.get("edition") or "perpetual",
        "activated_at": status.get("activated_at", ""),
        "school_name": status.get("school_name", ""),
        "version": config.APP_VERSION,
        "schema": config.SCHEMA_VERSION,
        "licensed": bool(status.get("licensed")),
    }


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #


@route("POST", "/api/auth/login")
def login(ctx: Any, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    """Authenticate and open a session; the token goes out as a cookie."""
    username = str(request.field("username") or "")
    password = str(request.field("password") or "")
    result = ctx.security.login(username, password, client=request.client)
    token = str(result.pop("token"))
    request.token = token
    return result, {"set_cookie": cookie_header(token)}


@route("POST", "/api/auth/logout")
def logout(ctx: Any, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    """Revoke the current session and clear the cookie."""
    ctx.security.logout(request.token or "", user=request.user, client=request.client)
    return {"logged_out": True}, {"set_cookie": clear_cookie_header()}


@route("GET", "/api/auth/session")
def session_info(ctx: Any, request: Request) -> dict[str, Any]:
    """Return the caller's identity and capabilities."""
    if not request.user:
        raise errors.ImriError(errors.AUTH_REQUIRED, errors.status_for(errors.AUTH_REQUIRED))
    user = request.user
    return {
        "user": {
            "id": user.get("id"),
            "username": user.get("username"),
            "display_name": user.get("display_name"),
            "role": user.get("role"),
            "caps": caps_for(str(user.get("role") or "")),
        },
        "must_change_password": bool(user.get("must_change_password")),
    }


@route("POST", "/api/auth/password")
def change_password(ctx: Any, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    """Change the caller's own password, revoking their other sessions."""
    if not request.user:
        raise errors.ImriError(errors.AUTH_REQUIRED, errors.status_for(errors.AUTH_REQUIRED))
    current = str(request.field("current_password") or "")
    new = str(request.field("new_password") or "")
    result = ctx.security.change_password(
        int(request.user["id"]), current, new, client=request.client
    )
    # The change revoked every session including this one; re-issue a token.
    login_result = ctx.security.login(
        str(request.user["username"]), new, client=request.client
    )
    token = str(login_result.pop("token"))
    request.token = token
    result["token_reissued"] = True
    return result, {"set_cookie": cookie_header(token)}


@route("POST", "/api/auth/heartbeat")
def heartbeat(ctx: Any, request: Request) -> dict[str, Any]:
    """Keep-alive from the page; also cancels an armed ``bye``."""
    if ctx.server is not None:
        ctx.server.touch_ping()
    return {"alive": True, "server_time": config.now_utc()}


# --------------------------------------------------------------------------- #
# Dashboard and search
# --------------------------------------------------------------------------- #


@route("GET", "/api/dashboard", cap="dashboard.read")
def dashboard(ctx: Any, request: Request) -> dict[str, Any]:
    """Six KPI cards, two panels, two chart series and the alerts strip."""
    return ctx.reporting.dashboard(as_of=request.get("as_of"))


@route("GET", "/api/search", cap="search.read")
def global_search(ctx: Any, request: Request) -> dict[str, Any]:
    """Grouped global search: books, members, loans, reservations (UX-03)."""
    term = str(request.get("q") or "").strip()
    limit = max(1, min(50, request.get_int("limit", 5)))
    if not term:
        empty = {"count": 0, "items": []}
        return {
            "books": dict(empty),
            "members": dict(empty),
            "loans": dict(empty),
            "reservations": dict(empty),
        }

    like = config.like_term(term)
    books = ctx.db.query(
        "SELECT id, accession_no, title, authors, call_no, material_type, status "
        "FROM books WHERE title LIKE ? ESCAPE '\\' OR authors LIKE ? ESCAPE '\\' "
        "OR accession_no LIKE ? ESCAPE '\\' OR isbn LIKE ? ESCAPE '\\' "
        "ORDER BY title COLLATE NOCASE LIMIT ?",
        (like, like, like, like, limit),
    )
    members = ctx.db.query(
        "SELECT id, member_code, name, class_name, member_type, status FROM members "
        "WHERE name LIKE ? ESCAPE '\\' OR member_code LIKE ? ESCAPE '\\' "
        "OR ic_number LIKE ? ESCAPE '\\' "
        "ORDER BY name COLLATE NOCASE LIMIT ?",
        (like, like, like, limit),
    )
    circulation = ctx.circulation.search(term, limit=limit)
    return {
        "books": {"count": len(books), "items": books},
        "members": {"count": len(members), "items": members},
        "loans": circulation["loans"],
        "reservations": circulation["reservations"],
    }


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


@route("GET", "/api/settings", cap="settings.read")
def get_settings(ctx: Any, request: Request) -> dict[str, Any]:
    """Return every setting plus the validation ranges the form needs."""
    return {
        "settings": _settings_payload(ctx),
        "validation": {
            key: dict(rule) for key, rule in config.SETTING_RULES.items()
        },
        "read_only": sorted(config.READ_ONLY_SETTINGS),
    }


def _coerce_setting(key: str, raw: Any) -> str:
    """Validate one setting against its rule and return the stored string."""
    rule = config.SETTING_RULES.get(key)
    if rule is None:
        raise errors.ImriError(
            errors.OUT_OF_RANGE,
            errors.status_for(errors.OUT_OF_RANGE),
            fields={key: errors.OUT_OF_RANGE},
            details={"reason": "unknown_setting"},
        )
    kind = rule.get("kind")
    text = "" if raw is None else str(raw).strip()

    if kind == "int":
        value = config.to_int(text, -10 ** 9)
        if value < int(rule.get("min", 0)) or value > int(rule.get("max", 0)):
            raise errors.ImriError(
                errors.OUT_OF_RANGE,
                errors.status_for(errors.OUT_OF_RANGE),
                fields={key: errors.OUT_OF_RANGE},
                details={"min": rule.get("min"), "max": rule.get("max")},
            )
        return str(value)
    if kind == "float":
        value = config.to_float(text, -10 ** 9)
        if value < float(rule.get("min", 0.0)) or value > float(rule.get("max", 0.0)):
            raise errors.ImriError(
                errors.OUT_OF_RANGE,
                errors.status_for(errors.OUT_OF_RANGE),
                fields={key: errors.OUT_OF_RANGE},
                details={"min": rule.get("min"), "max": rule.get("max")},
            )
        return ("%.2f" % value).rstrip("0").rstrip(".") if value else "0"
    if kind == "enum":
        if text.lower() not in tuple(rule.get("values") or ()):
            raise errors.ImriError(
                errors.OUT_OF_RANGE,
                errors.status_for(errors.OUT_OF_RANGE),
                fields={key: errors.OUT_OF_RANGE},
                details={"allowed": list(rule.get("values") or ())},
            )
        return text.lower()
    if kind == "bool":
        return "1" if config.to_bool(text) else "0"
    # text
    if len(text) > int(rule.get("max_len", 200)):
        raise errors.ImriError(
            errors.OUT_OF_RANGE,
            errors.status_for(errors.OUT_OF_RANGE),
            fields={key: errors.OUT_OF_RANGE},
            details={"max_len": rule.get("max_len")},
        )
    return text


@route("PUT", "/api/settings", cap="settings.write")
def put_settings(ctx: Any, request: Request) -> dict[str, Any]:
    """Apply validated settings immediately (ADM-01)."""
    payload = request.body or {}
    if not isinstance(payload, dict) or not payload:
        raise errors.ImriError(
            errors.VALIDATION_FAILED, errors.status_for(errors.VALIDATION_FAILED)
        )
    for key in payload:
        if key in config.READ_ONLY_SETTINGS:
            raise errors.ImriError(
                errors.OUT_OF_RANGE,
                errors.status_for(errors.OUT_OF_RANGE),
                fields={key: errors.OUT_OF_RANGE},
                details={"reason": "read_only"},
            )

    applied: list[str] = []
    coerced: dict[str, str] = {}
    for key, raw in payload.items():
        coerced[key] = _coerce_setting(str(key), raw)

    with ctx.db.transaction():
        for key, value in coerced.items():
            ctx.db.set_setting(key, value)
            applied.append(key)
        ctx.db.audit(
            request.actor,
            "settings.update",
            target="settings",
            detail={key: coerced[key] for key in applied},
            ip=request.client,
        )
    return {"settings": _settings_payload(ctx), "applied": applied}


# --------------------------------------------------------------------------- #
# Users and roles
# --------------------------------------------------------------------------- #


@route("GET", "/api/users", cap="user.read")
def list_users(ctx: Any, request: Request) -> dict[str, Any]:
    """Return every account (no hashes, ever)."""
    return {"items": ctx.security.list_users()}


@route("POST", "/api/users", cap="user.write")
def create_user(ctx: Any, request: Request) -> dict[str, Any]:
    """Create an account."""
    user = ctx.security.create_user(
        str(request.field("username") or ""),
        str(request.field("password") or ""),
        display_name=str(request.field("display_name") or ""),
        role=str(request.field("role") or "assistant"),
        actor=request.actor,
    )
    return {"user": user}


@route("PATCH", "/api/users/{id}", cap="user.write")
def update_user(ctx: Any, request: Request) -> dict[str, Any]:
    """Patch display name, role or active flag."""
    user = ctx.security.update_user(
        request.param_int("id"),
        request.body or {},
        actor=request.actor,
        actor_id=request.actor_id,
    )
    return {"user": user}


@route("DELETE", "/api/users/{id}", cap="user.write")
def delete_user(ctx: Any, request: Request) -> dict[str, Any]:
    """Delete an account, refusing self-deletion and the last admin."""
    ctx.security.delete_user(
        request.param_int("id"), actor=request.actor, actor_id=request.actor_id
    )
    return {"deleted": True}


@route("POST", "/api/users/{id}/reset-password", cap="user.write")
def reset_password(ctx: Any, request: Request) -> dict[str, Any]:
    """Administrator password reset, revoking every session of that user."""
    new_password = str(request.field("new_password") or "")
    return ctx.security.reset_password(
        request.param_int("id"), new_password, actor=request.actor, client=request.client
    )


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def _audit_filters(request: Request) -> tuple[str, list[Any]]:
    """Build the audit WHERE clause from the request query."""
    clauses: list[str] = []
    params: list[Any] = []
    for column, key in (
        ("actor", "actor"),
        ("action", "action"),
        ("target", "target"),
    ):
        value = str(request.get(key) or "").strip()
        if value:
            clauses.append("%s = ?" % column)
            params.append(value)
    date_from = str(request.get("from") or "").strip()
    if date_from:
        clauses.append("ts >= ?")
        params.append(date_from[:10])
    date_to = str(request.get("to") or "").strip()
    if date_to:
        clauses.append("ts <= ?")
        params.append(date_to[:10] + "T23:59:59Z")
    term = str(request.get("q") or "").strip()
    if term:
        like = config.like_term(term)
        clauses.append(
            "(action LIKE ? ESCAPE '\\' OR target LIKE ? ESCAPE '\\' "
            " OR detail LIKE ? ESCAPE '\\' OR actor LIKE ? ESCAPE '\\')"
        )
        params.extend([like] * 4)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


@route("GET", "/api/audit", cap="audit.read", sorts=("ts", "id", "actor", "action"))
def list_audit(ctx: Any, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the append-only audit log, paginated and filtered."""
    page = parse_page(request.query, sorts=("ts", "id", "actor", "action"), default_sort="ts", default_order="desc")
    where, params = _audit_filters(request)
    total = int(ctx.db.scalar("SELECT COUNT(*) FROM audit_log" + where, params, default=0))
    items = ctx.db.query(
        "SELECT id, ts, actor, actor_id, action, target, target_type, detail, ip "
        "FROM audit_log" + where + " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
        params + [page["page_size"], page["offset"]],
    )
    pages = (total + page["page_size"] - 1) // page["page_size"] if page["page_size"] else 0
    return {"items": items}, {
        "page": page["page"],
        "page_size": page["page_size"],
        "total": total,
        "pages": pages,
        "sort": page["sort"],
        "order": page["order"],
    }


@route("GET", "/api/audit/export", cap="audit.read")
def export_audit(ctx: Any, request: Request) -> dict[str, Any]:
    """Export the filtered audit log to CSV."""
    where, params = _audit_filters(request)
    rows = ctx.db.query(
        "SELECT ts, actor, action, target, target_type, detail, ip FROM audit_log"
        + where
        + " ORDER BY ts DESC, id DESC LIMIT 100000",
        params,
    )
    columns = ("ts", "actor", "action", "target", "target_type", "detail", "ip")
    stamp = config.now_utc().replace("-", "").replace(":", "").replace("T", "-")[:15]
    filename = "audit-%s.csv" % stamp
    path = str(config.exports_dir() / filename)
    config.exports_dir().mkdir(parents=True, exist_ok=True)
    write_csv(columns, rows, path)
    ctx.db.audit(request.actor, "export.run", target=filename, detail={"kind": "audit"})
    return {
        "path": path,
        "filename": filename,
        "size": os.path.getsize(path),
        "rows": len(rows),
    }


# --------------------------------------------------------------------------- #
# Exports download
# --------------------------------------------------------------------------- #

_EXPORT_CONTENT_TYPES = {
    ".csv": "text/csv; charset=utf-8",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
}


@route("GET", "/api/exports/{filename}", cap="export.run")
def download_export(ctx: Any, request: Request) -> Raw:
    """Stream a previously generated export file from ``data/exports``."""
    filename = str(request.param("filename") or "")
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise errors.ImriError(errors.UNSAFE_PATH, errors.status_for(errors.UNSAFE_PATH))
    directory = config.exports_dir().resolve()
    target = (directory / filename).resolve()
    try:
        inside = target.is_relative_to(directory)
    except (OSError, ValueError):  # pragma: no cover - defensive
        inside = False
    if not inside:
        raise errors.ImriError(errors.UNSAFE_PATH, errors.status_for(errors.UNSAFE_PATH))
    if not target.is_file():
        raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))

    suffix = target.suffix.lower()
    content_type = _EXPORT_CONTENT_TYPES.get(suffix, "application/octet-stream")
    with open(target, "rb") as handle:
        body = handle.read()
    return Raw(
        body,
        content_type=content_type,
        headers={"Content-Disposition": 'attachment; filename="%s"' % filename},
    )


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #


@route("POST", "/api/system/bye")
def bye(ctx: Any, request: Request) -> Raw:
    """Arm the 3 s shutdown grace window (a reload cancels it)."""
    if ctx.server is not None:
        ctx.server.arm_bye()
    return Raw(b"", content_type="application/json", status=204)


@route("POST", "/api/system/reveal", cap="system.reveal")
def reveal(ctx: Any, request: Request) -> dict[str, Any]:
    """Open Explorer with the given file selected, restricted to ``data/``."""
    raw_path = str(request.field("path") or "").strip()
    if not raw_path:
        raise errors.ImriError(errors.UNSAFE_PATH, errors.status_for(errors.UNSAFE_PATH))

    root = config.data_dir().resolve()
    candidate = os.path.abspath(raw_path)
    if not os.path.isabs(candidate):
        candidate = os.path.join(str(root), candidate)
    target = os.path.abspath(candidate)
    try:
        inside = os.path.commonpath([root, os.path.abspath(target)]) == str(root)
    except ValueError:
        inside = False
    if not inside:
        raise errors.ImriError(
            errors.PATH_OUTSIDE_DATA, errors.status_for(errors.PATH_OUTSIDE_DATA)
        )
    allowed = (".csv", ".pdf", ".xlsx", ".html", ".svg", ".json", ".db")
    if os.path.splitext(target)[1].lower() not in allowed:
        raise errors.ImriError(errors.UNSAFE_PATH, errors.status_for(errors.UNSAFE_PATH))

    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", target])
        else:  # pragma: no cover - development on non-Windows
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, os.path.dirname(target) or target])
    except OSError as exc:
        raise errors.ImriError(
            errors.REVEAL_FAILED, errors.status_for(errors.REVEAL_FAILED), details={"error": str(exc)}
        ) from exc
    return {"revealed": True}


@route("GET", "/api/system/info", cap="system.diag")
def system_info(ctx: Any, request: Request) -> dict[str, Any]:
    """Return build, path and row-count diagnostics."""
    return {
        "version": config.APP_VERSION,
        "schema": ctx.db.schema_version(),
        "python": sys.version.split()[0],
        "db_path": str(config.db_path()),
        "data_dir": str(config.data_dir()),
        "row_counts": ctx.db.row_counts(),
        "objects": ctx.db.object_counts(),
        "license": ctx.license.status(),
        "uptime_s": ctx.uptime_seconds(),
    }


@route("GET", "/api/system/diagnostics", cap="system.diag")
def system_diagnostics(ctx: Any, request: Request) -> dict[str, Any]:
    """Integrity, orphan and rollup-mismatch report (ADM-07 backend)."""
    try:
        integrity = ctx.db.integrity_check()
        mismatches = rollup_mismatches(ctx.db)
        orphan_loans = ctx.db.query(
            "SELECT l.id FROM loans l "
            "LEFT JOIN books b ON b.id = l.book_id "
            "LEFT JOIN members m ON m.id = l.member_id "
            "LEFT JOIN book_copies c ON c.id = l.copy_id "
            "WHERE b.id IS NULL OR m.id IS NULL "
            "   OR (l.copy_id IS NOT NULL AND c.id IS NULL)"
        )
        orphan_copies = ctx.db.query(
            "SELECT c.id FROM book_copies c LEFT JOIN books b ON b.id = c.book_id "
            "WHERE b.id IS NULL"
        )
        orphan_reservations = ctx.db.query(
            "SELECT r.id FROM reservations r "
            "LEFT JOIN books b ON b.id = r.book_id "
            "LEFT JOIN members m ON m.id = r.member_id "
            "WHERE b.id IS NULL OR m.id IS NULL"
        )
        backups = ctx.backup.list()
        return {
            "integrity": integrity,
            "orphans": {
                "loans": len(orphan_loans),
                "copies": len(orphan_copies),
                "reservations": len(orphan_reservations),
            },
            "rollup_mismatches": len(mismatches),
            "rollup_detail": mismatches[:20],
            "backup_count": len(backups),
            "last_backup": backups[0] if backups else None,
            "foreign_key_violations": len(ctx.db.foreign_key_check()),
            "settings": _settings_payload(ctx),
        }
    except errors.ImriError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        _log.exception("diagnostics failed")
        raise errors.ImriError(
            errors.DIAGNOSTICS_FAILED,
            errors.status_for(errors.DIAGNOSTICS_FAILED),
            details={"error": str(exc)},
        ) from exc


@route("GET", "/api/version")
def version_info(ctx: Any, request: Request) -> dict[str, Any]:
    """Cheap version probe used by the client to detect a stale page."""
    return {
        "product": config.PRODUCT,
        "version": config.APP_VERSION,
        "schema": config.SCHEMA_VERSION,
        "server_time": config.now_utc(),
        "uptime_s": ctx.uptime_seconds(),
        "pid": os.getpid(),
        "monotonic": time.monotonic(),
    }
