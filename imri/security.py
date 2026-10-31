"""Password hashing, sessions, RBAC, login and the activation gate.

Cross-cutting concerns that every other layer depends on but none of them owns.

* **Passwords** are scrypt (n=2^14, r=8, p=1) with a 16-byte salt, stored as
  ``scrypt$ln=14,r=8,p=1$<salt_b64>$<dk_b64>`` so the parameters travel with the
  hash and old hashes keep verifying. A PBKDF2-HMAC-SHA256 fallback (210 000
  iterations) is written when ``hashlib.scrypt`` is unavailable, and any
  non-current format is transparently upgraded on a successful login.
* **Sessions** are 32 random bytes; the database stores only
  ``sha256(token)`` so a stolen database cannot be replayed as a cookie.
* **RBAC** lives in :data:`ROLE_CAPS` — one source of truth. There is no
  stored ``access_level`` column; the role is the role.
* **The activation gate** is a module constant compared in constant time. The
  reference implementation's HMAC-signed key format is deliberately dropped
  (ADR-01): a single literal cannot be forged if there is nothing to forge.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Iterable, Mapping

from . import config, errors
from .config import get_logger

_log = get_logger("security")

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"

SESSION_COOKIE = "imri_sid"
SESSION_TTL_SECONDS = 43200  # 12 hours
SESSION_COOKIE_HEADER = (
    "imri_sid={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age="
    + str(SESSION_TTL_SECONDS)
)

_SCRYPT_LOG_N = 14
_SCRYPT_N = 2 ** _SCRYPT_LOG_N
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_PREFIX = "scrypt$ln=%d,r=%d,p=%d" % (_SCRYPT_LOG_N, _SCRYPT_R, _SCRYPT_P)

_PBKDF2_ITERATIONS = 210_000
_PBKDF2_PREFIX = "pbkdf2_sha256$%d" % _PBKDF2_ITERATIONS

_MIN_PASSWORD_LEN = 8
_LOGIN_MAX_FAILURES = 5
_LOGIN_WINDOW_SECONDS = 300.0

LICENSE_CODE = "arfanayub0"
LICENSE_EDITION = "perpetual"
LICENSE_MAX_ATTEMPTS = 5
LICENSE_COOLDOWN_SECONDS = 30.0

# --------------------------------------------------------------------------- #
# Capability matrix (ARCHITECTURE §8.3) — the single source of truth
# --------------------------------------------------------------------------- #

_CAP_ROLES: dict[str, tuple[str, ...]] = {
    "dashboard.read": ("admin", "librarian", "assistant", "viewer"),
    "search.read": ("admin", "librarian", "assistant", "viewer"),
    "book.read": ("admin", "librarian", "assistant", "viewer"),
    "book.write": ("admin", "librarian"),
    "book.delete": ("admin", "librarian"),
    "copy.write": ("admin", "librarian"),
    "member.read": ("admin", "librarian", "assistant", "viewer"),
    "member.write": ("admin", "librarian", "assistant"),
    "member.delete": ("admin", "librarian"),
    "circ.read": ("admin", "librarian", "assistant", "viewer"),
    "circ.write": ("admin", "librarian", "assistant"),
    "circ.lost": ("admin", "librarian"),
    "fine.read": ("admin", "librarian", "assistant", "viewer"),
    "fine.pay": ("admin", "librarian", "assistant"),
    "fine.waive": ("admin",),
    "reservation.read": ("admin", "librarian", "assistant", "viewer"),
    "reservation.write": ("admin", "librarian", "assistant"),
    "report.read": ("admin", "librarian", "viewer"),
    "label.read": ("admin", "librarian", "assistant"),
    "nfc.read": ("admin", "librarian", "assistant"),
    "nfc.write": ("admin", "librarian"),
    "import.run": ("admin", "librarian"),
    "export.run": ("admin", "librarian", "viewer"),
    "settings.read": ("admin", "librarian"),
    "settings.write": ("admin",),
    "user.read": ("admin",),
    "user.write": ("admin",),
    "audit.read": ("admin",),
    "license.read": ("admin",),
    "backup.create": ("admin", "librarian"),
    "backup.restore": ("admin",),
    "system.diag": ("admin",),
    "system.reveal": ("admin", "librarian", "assistant", "viewer"),
}

#: ``{role: frozenset(capability)}`` — derived once, never hand-maintained.
ROLE_CAPS: dict[str, frozenset[str]] = {
    role: frozenset(cap for cap, roles in _CAP_ROLES.items() if role in roles)
    for role in config.USER_ROLES
}

#: Every capability token the router may declare.
ALL_CAPS: frozenset[str] = frozenset(_CAP_ROLES)


def caps_for(role: str) -> list[str]:
    """Return the sorted capability list granted to ``role``."""
    return sorted(ROLE_CAPS.get((role or "").strip().lower(), frozenset()))


def require(user: Mapping[str, Any] | None, cap: str) -> bool:
    """Return True when ``user`` holds ``cap``.

    Enforcement is server-side only (ADM-02); hiding a button is cosmetic.
    """
    if not user:
        return False
    return cap in ROLE_CAPS.get(str(user.get("role", "")).lower(), frozenset())


def caps_of(user: Mapping[str, Any] | None) -> list[str]:
    """Return the capability list of a session user dict."""
    if not user:
        return []
    return caps_for(str(user.get("role", "")))


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=False)


def _scrypt_available() -> bool:
    """Return True when ``hashlib.scrypt`` can be used on this runtime."""
    return hasattr(hashlib, "scrypt")


def hash_password(password: str) -> str:
    """Hash a password with the current scheme (scrypt, PBKDF2 fallback)."""
    raw = (password or "").encode("utf-8")
    salt = secrets.token_bytes(16)
    if _scrypt_available():
        try:
            derived = hashlib.scrypt(
                raw,
                salt=salt,
                n=_SCRYPT_N,
                r=_SCRYPT_R,
                p=_SCRYPT_P,
                dklen=_SCRYPT_DKLEN,
            )
            return "%s$%s$%s" % (_SCRYPT_PREFIX, _b64encode(salt), _b64encode(derived))
        except (ValueError, MemoryError):  # pragma: no cover - constrained runtime
            _log.warning("scrypt unavailable at runtime; falling back to PBKDF2")
    derived = hashlib.pbkdf2_hmac("sha256", raw, salt, _PBKDF2_ITERATIONS, _SCRYPT_DKLEN)
    return "%s$%s$%s" % (_PBKDF2_PREFIX, _b64encode(salt), _b64encode(derived))


def verify_password(password: str, stored: str) -> bool:
    """Verify ``password`` against ``stored`` in constant time.

    Accepts both the scrypt format and the PBKDF2 fallback, so a database
    written by a runtime without scrypt still opens.
    """
    if not stored:
        return False
    raw = (password or "").encode("utf-8")
    try:
        parts = stored.split("$")
        if len(parts) < 4:
            return False
        prefix = parts[0]
        if prefix == "scrypt":
            params, salt_b64, dk_b64 = parts[1], parts[2], parts[3]
            match = re.fullmatch(r"ln=(\d+),r=(\d+),p=(\d+)", params or "")
            if not match:
                return False
            log_n, r, p = (int(g) for g in match.groups())
            salt = _b64decode(salt_b64)
            expected = _b64decode(dk_b64)
            derived = hashlib.scrypt(
                raw, salt=salt, n=2 ** log_n, r=r, p=p, dklen=len(expected)
            )
            return hmac.compare_digest(derived, expected)
        if prefix == "pbkdf2_sha256":
            iterations, salt_b64, dk_b64 = parts[1], parts[2], parts[3]
            salt = _b64decode(salt_b64)
            expected = _b64decode(dk_b64)
            derived = hashlib.pbkdf2_hmac(
                "sha256", raw, salt, int(iterations), len(expected)
            )
            return hmac.compare_digest(derived, expected)
    except (ValueError, TypeError, IndexError):  # malformed hash must not raise
        return False
    return False


def needs_rehash(stored: str) -> bool:
    """True when a successful login should upgrade the stored hash."""
    if not stored:
        return False
    if not stored.startswith(_SCRYPT_PREFIX + "$"):
        return True
    if _scrypt_available():
        return False
    # Written as scrypt on a runtime that has since lost scrypt: keep it.
    return False


def validate_password(username: str, password: str) -> None:
    """Enforce the password policy (ADM-03). Raises ``weak_password``."""
    text = password or ""
    problems: list[str] = []
    if len(text) < _MIN_PASSWORD_LEN:
        problems.append("min_length")
    if text.strip().lower() == (username or "").strip().lower():
        problems.append("equals_username")
    if text.strip().lower() in ("admin123", "password", "12345678"):
        problems.append("too_common")
    if not problems:
        return
    raise errors.ImriError(
        errors.WEAK_PASSWORD,
        errors.status_for(errors.WEAK_PASSWORD),
        fields={"new_password": errors.WEAK_PASSWORD},
        details={"reasons": problems, "min_length": _MIN_PASSWORD_LEN},
    )


# --------------------------------------------------------------------------- #
# Session store
# --------------------------------------------------------------------------- #


def token_hash(token: str) -> str:
    """Return the stored form of a session token (``sha256`` hex)."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


class SessionStore:
    """Server-side sessions keyed by ``sha256(token)``.

    The raw token exists only in the client cookie, so revoking a row really
    revokes the session — needed for "admin reset password kills sessions".
    """

    def __init__(self, db: Any) -> None:
        self.db = db

    def create(self, user_id: int, client: str | None = None) -> str:
        """Create a session and return the raw token (never stored)."""
        token = secrets.token_urlsafe(32)
        now = config.now_utc()
        expiry = (
            _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=SESSION_TTL_SECONDS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.db.execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, last_seen_at, expires_at, client) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (token_hash(token), int(user_id), now, now, expiry, (client or "")[:200] or None),
        )
        return token

    def lookup(self, token: str, touch: bool = True) -> dict[str, Any] | None:
        """Return the session's user dict, or ``None`` when invalid/expired."""
        if not token:
            return None
        row = self.db.one(
            "SELECT s.token_hash, s.expires_at, u.id AS user_id, u.username, "
            "       u.display_name, u.role, u.is_active, u.must_change_password "
            "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (token_hash(token),),
        )
        if not row:
            return None
        if str(row["expires_at"] or "") <= config.now_utc():
            self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
            return None
        if not config.to_bool(row["is_active"]):
            return None
        if touch:
            self.touch(row["token_hash"])
        return {
            "id": int(row["user_id"]),
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "caps": caps_for(row["role"]),
            "must_change_password": config.to_bool(row["must_change_password"]),
        }

    def touch(self, stored_hash: str) -> None:
        """Refresh ``last_seen_at`` for a live session."""
        self.db.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
            (config.now_utc(), stored_hash),
        )

    def revoke(self, token: str) -> int:
        """Delete one session by its raw token."""
        return self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

    def revoke_user(self, user_id: int) -> int:
        """Delete every session belonging to ``user_id``; returns the count."""
        return self.db.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))

    def purge_expired(self) -> int:
        """Delete sessions past ``expires_at``; returns the count."""
        return self.db.execute("DELETE FROM sessions WHERE expires_at <= ?", (config.now_utc(),))


# --------------------------------------------------------------------------- #
# Security service (login, rate limit, users)
# --------------------------------------------------------------------------- #


class SecurityService:
    """Authentication, session lifecycle and user administration."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self.sessions = SessionStore(db)
        self._lock = threading.RLock()
        self._failures: dict[str, list[float]] = {}

    # -- rate limiting ---------------------------------------------------- #
    def _key(self, username: str) -> str:
        return (username or "").strip().lower()

    def _prune(self, key: str, now: float) -> list[float]:
        stamps = [t for t in self._failures.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
        if stamps:
            self._failures[key] = stamps
        else:
            self._failures.pop(key, None)
        return stamps

    def is_rate_limited(self, username: str) -> int:
        """Return seconds to wait, or ``0`` when the caller may try again."""
        now = time.monotonic()
        with self._lock:
            stamps = self._prune(self._key(username), now)
            if len(stamps) < _LOGIN_MAX_FAILURES:
                return 0
            oldest = min(stamps)
            remaining = _LOGIN_WINDOW_SECONDS - (now - oldest)
            return max(1, int(remaining) + 1)

    def record_failure(self, username: str) -> int:
        """Record one failed attempt; returns the remaining attempt count."""
        now = time.monotonic()
        with self._lock:
            key = self._key(username)
            stamps = self._prune(key, now)
            stamps.append(now)
            self._failures[key] = stamps
            return max(0, _LOGIN_MAX_FAILURES - len(stamps))

    def clear_failures(self, username: str) -> None:
        """Forget the failure history for ``username`` (called on success)."""
        with self._lock:
            self._failures.pop(self._key(username), None)

    # -- login ------------------------------------------------------------ #
    def login(self, username: str, password: str, client: str | None = None) -> dict[str, Any]:
        """Authenticate and open a session.

        Raises ``rate_limited`` (429) after 5 failures in 5 minutes and
        ``invalid_credentials`` (401) for a bad pair. Both failures are audited;
        the password is never written anywhere.
        """
        wait = self.is_rate_limited(username)
        if wait:
            self.db.audit(
                username or "anonymous",
                "auth.login_failed",
                target=username,
                detail={"reason": "rate_limited"},
                ip=client,
            )
            raise errors.ImriError(
                errors.RATE_LIMITED,
                errors.status_for(errors.RATE_LIMITED),
                details={"retry_after_s": wait},
            )

        row = self.db.one(
            "SELECT id, username, password_hash, display_name, role, is_active, "
            "       must_change_password FROM users WHERE username = ?",
            ((username or "").strip(),),
        )

        if row is None:
            # Constant-ish work so a missing user is not detectably faster.
            verify_password(password or "", hash_password("imri-padding-value"))
            self.record_failure(username)
            self.db.audit(
                username or "anonymous",
                "auth.login_failed",
                target=username,
                detail={"reason": "unknown_user"},
                ip=client,
            )
            raise errors.ImriError(
                errors.INVALID_CREDENTIALS, errors.status_for(errors.INVALID_CREDENTIALS)
            )

        if not verify_password(password or "", row["password_hash"]):
            remaining = self.record_failure(username)
            self.db.audit(
                username,
                "auth.login_failed",
                target=username,
                detail={"reason": "bad_password", "attempts_left": remaining},
                ip=client,
            )
            raise errors.ImriError(
                errors.INVALID_CREDENTIALS, errors.status_for(errors.INVALID_CREDENTIALS)
            )

        if not config.to_bool(row["is_active"]):
            self.db.audit(
                username, "auth.login_failed", target=username, detail={"reason": "disabled"}, ip=client
            )
            raise errors.ImriError(
                errors.ACCOUNT_DISABLED, errors.status_for(errors.ACCOUNT_DISABLED)
            )

        self.clear_failures(username)
        self.sessions.purge_expired()

        now = config.now_utc()
        with self.db.transaction():
            if needs_rehash(row["password_hash"]):
                self.db.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                    (hash_password(password or ""), now, row["id"]),
                )
            self.db.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (now, now, row["id"]),
            )
            token = self.sessions.create(row["id"], client)
            self.db.audit(
                row["username"], "auth.login", target=row["username"], ip=client, actor_id=row["id"]
            )

        user = {
            "id": int(row["id"]),
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "caps": caps_for(row["role"]),
        }
        must_change = config.to_bool(row["must_change_password"])
        return {
            "user": user,
            "token": token,
            "must_change_password": must_change,
            "default_password_warning": must_change,
        }

    def logout(self, token: str, user: Mapping[str, Any] | None = None, client: str | None = None) -> bool:
        """Revoke the session behind ``token``."""
        if not token:
            return False
        removed = self.sessions.revoke(token)
        if removed and user:
            self.db.audit(user.get("username", "system"), "auth.logout", target=user.get("username"), ip=client)
        return bool(removed)

    def session(self, token: str) -> dict[str, Any] | None:
        """Return the user dict for ``token`` (``None`` when invalid)."""
        return self.sessions.lookup(token)

    # -- password change -------------------------------------------------- #
    def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
        client: str | None = None,
    ) -> dict[str, Any]:
        """Change a user's own password, then revoke their other sessions."""
        row = self.db.one("SELECT * FROM users WHERE id = ?", (int(user_id),))
        if row is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        if not verify_password(current_password or "", row["password_hash"]):
            raise errors.ImriError(
                errors.WRONG_PASSWORD,
                errors.status_for(errors.WRONG_PASSWORD),
                fields={"current_password": errors.WRONG_PASSWORD},
            )
        validate_password(row["username"], new_password)
        now = config.now_utc()
        with self.db.transaction():
            self.db.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0, "
                "password_changed_at = ?, updated_at = ? WHERE id = ?",
                (hash_password(new_password), now, now, int(user_id)),
            )
            revoked = self.sessions.revoke_user(int(user_id))
            self.db.audit(
                row["username"],
                "auth.password_changed",
                target=row["username"],
                detail={"sessions_revoked": revoked},
                ip=client,
                actor_id=int(user_id),
            )
        return {"changed": True, "sessions_revoked": revoked}

    # -- user administration ---------------------------------------------- #
    def list_users(self) -> list[dict[str, Any]]:
        """Return every account, minus hashes."""
        return self.db.query(
            "SELECT id, username, display_name, role, is_active, must_change_password, "
            "       last_login_at, created_at, updated_at FROM users ORDER BY username"
        )

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        """Return one account, minus the hash."""
        return self.db.one(
            "SELECT id, username, display_name, role, is_active, must_change_password, "
            "last_login_at, created_at, updated_at FROM users WHERE id = ?",
            (int(user_id),),
        )

    def _admin_count(self, exclude_id: int | None = None) -> int:
        if exclude_id is None:
            return int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1", default=0
                )
            )
        return int(
            self.db.scalar(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND id <> ?",
                (int(exclude_id),),
                default=0,
            )
        )

    def create_user(
        self,
        username: str,
        password: str,
        display_name: str = "",
        role: str = "assistant",
        actor: str = "system",
    ) -> dict[str, Any]:
        """Create an account. Raises ``duplicate_username`` / ``weak_password``."""
        name = (username or "").strip()
        if not name:
            raise errors.ImriError(
                errors.VALIDATION_FAILED,
                errors.status_for(errors.VALIDATION_FAILED),
                fields={"username": errors.VALIDATION_FAILED},
            )
        role_value = (role or "assistant").strip().lower()
        if role_value not in config.USER_ROLES:
            raise errors.ImriError(
                errors.VALIDATION_FAILED,
                errors.status_for(errors.VALIDATION_FAILED),
                fields={"role": errors.VALIDATION_FAILED},
                details={"allowed": list(config.USER_ROLES)},
            )
        if self.db.one("SELECT id FROM users WHERE username = ?", (name,)):
            raise errors.ImriError(
                errors.DUPLICATE_USERNAME,
                errors.status_for(errors.DUPLICATE_USERNAME),
                fields={"username": errors.DUPLICATE_USERNAME},
            )
        validate_password(name, password)
        now = config.now_utc()
        with self.db.transaction():
            user_id = self.db.execute(
                "INSERT INTO users(username, password_hash, display_name, role, is_active, "
                "must_change_password, created_at, updated_at) VALUES (?, ?, ?, ?, 1, 0, ?, ?)",
                (name, hash_password(password), (display_name or name).strip(), role_value, now, now),
            )
            self.db.audit(
                actor, "user.create", target=name, detail={"role": role_value}, target_type="user"
            )
        return self.get_user(user_id) or {}

    def update_user(
        self,
        user_id: int,
        fields: Mapping[str, Any],
        actor: str = "system",
        actor_id: int | None = None,
    ) -> dict[str, Any]:
        """Patch ``display_name`` / ``role`` / ``is_active``."""
        row = self.db.one("SELECT * FROM users WHERE id = ?", (int(user_id),))
        if row is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))

        sets: list[str] = []
        params: list[Any] = []
        changes: dict[str, Any] = {}

        if "display_name" in fields:
            value = config.clean_text(fields.get("display_name")) or ""
            sets.append("display_name = ?")
            params.append(value)
            changes["display_name"] = value

        if "role" in fields:
            role_value = str(fields.get("role") or "").strip().lower()
            if role_value not in config.USER_ROLES:
                raise errors.ImriError(
                    errors.VALIDATION_FAILED,
                    errors.status_for(errors.VALIDATION_FAILED),
                    fields={"role": errors.VALIDATION_FAILED},
                    details={"allowed": list(config.USER_ROLES)},
                )
            if row["role"] == "admin" and role_value != "admin" and self._admin_count(user_id) == 0:
                raise errors.ImriError(
                    errors.CANNOT_DEMOTE_LAST_ADMIN,
                    errors.status_for(errors.CANNOT_DEMOTE_LAST_ADMIN),
                )
            sets.append("role = ?")
            params.append(role_value)
            changes["role"] = role_value

        if "is_active" in fields:
            active = 1 if config.to_bool(fields.get("is_active")) else 0
            if not active and row["role"] == "admin" and self._admin_count(user_id) == 0:
                raise errors.ImriError(
                    errors.CANNOT_DEMOTE_LAST_ADMIN,
                    errors.status_for(errors.CANNOT_DEMOTE_LAST_ADMIN),
                )
            sets.append("is_active = ?")
            params.append(active)
            changes["is_active"] = bool(active)

        if not sets:
            return self.get_user(user_id) or {}

        sets.append("updated_at = ?")
        params.append(config.now_utc())
        params.append(int(user_id))
        with self.db.transaction():
            self.db.execute("UPDATE users SET %s WHERE id = ?" % ", ".join(sets), params)
            self.db.audit(
                actor, "user.update", target=row["username"], detail=changes, target_type="user"
            )
            if changes.get("is_active") is False:
                self.sessions.revoke_user(int(user_id))
        return self.get_user(user_id) or {}

    def delete_user(self, user_id: int, actor: str = "system", actor_id: int | None = None) -> None:
        """Delete an account, refusing self-deletion and the last admin."""
        row = self.db.one("SELECT * FROM users WHERE id = ?", (int(user_id),))
        if row is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        if actor_id is not None and int(actor_id) == int(user_id):
            raise errors.ImriError(
                errors.CANNOT_DELETE_SELF, errors.status_for(errors.CANNOT_DELETE_SELF)
            )
        if row["role"] == "admin" and self._admin_count(user_id) == 0:
            raise errors.ImriError(
                errors.CANNOT_DELETE_LAST_ADMIN,
                errors.status_for(errors.CANNOT_DELETE_LAST_ADMIN),
            )
        with self.db.transaction():
            self.db.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))
            self.db.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
            self.db.audit(
                actor, "user.delete", target=row["username"], target_type="user"
            )

    def reset_password(
        self, user_id: int, new_password: str, actor: str = "system", client: str | None = None
    ) -> dict[str, Any]:
        """Administrator password reset; revokes every session of that user."""
        row = self.db.one("SELECT * FROM users WHERE id = ?", (int(user_id),))
        if row is None:
            raise errors.ImriError(errors.NOT_FOUND, errors.status_for(errors.NOT_FOUND))
        validate_password(row["username"], new_password)
        now = config.now_utc()
        with self.db.transaction():
            self.db.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0, "
                "password_changed_at = ?, updated_at = ? WHERE id = ?",
                (hash_password(new_password), now, now, int(user_id)),
            )
            revoked = self.sessions.revoke_user(int(user_id))
            self.db.audit(
                actor,
                "user.password_reset",
                target=row["username"],
                detail={"sessions_revoked": revoked},
                ip=client,
                target_type="user",
            )
        return {"reset": True, "sessions_revoked": revoked}


# --------------------------------------------------------------------------- #
# Licence gate
# --------------------------------------------------------------------------- #


def _fingerprint(code: str) -> str:
    """Return the truncated SHA-256 fingerprint stored in ``license.json``."""
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()[:16]


class LicenseGate:
    """The single activation constant and its two persisted records.

    ``data/license.json`` is primary; ``settings.license_key`` is the mirror.
    Either one restores activation (LIC-07) — deleting one file must not
    deactivate a licensed copy.
    """

    #: The one and only activation code in the entire product (ADR-01).
    CODE = LICENSE_CODE
    EDITION = LICENSE_EDITION

    def __init__(self, db: Any) -> None:
        self.db = db
        self._lock = threading.RLock()
        self._attempts = 0
        self._cooldown_until = 0.0

    # -- persistence ------------------------------------------------------ #
    def _record_from_settings(self) -> dict[str, Any] | None:
        """Rebuild an activation record from the settings mirror."""
        key = (self.db.get_setting("license_key", "") or "").strip()
        if not key:
            return None
        fingerprint = key.split(":", 1)[1].strip() if ":" in key else key
        if not hmac.compare_digest(fingerprint, _fingerprint(self.CODE)):
            return None
        return {
            "product": config.PRODUCT,
            "edition": self.db.get_setting("license_edition", "") or self.EDITION,
            "code_fingerprint": fingerprint,
            "activated_at": self.db.get_setting("license_activated_at", "") or "",
            "school_name": self.db.get_setting("license_school", "")
            or self.db.get_setting("school_name", ""),
            "schema": config.SCHEMA_VERSION,
        }

    def _read_file(self) -> dict[str, Any] | None:
        """Read and validate ``data/license.json``; ``None`` when unusable."""
        path = config.license_path()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict):
            return None
        fingerprint = str(record.get("code_fingerprint") or "")
        if not fingerprint or not hmac.compare_digest(fingerprint, _fingerprint(self.CODE)):
            return None
        record.setdefault("product", config.PRODUCT)
        record.setdefault("edition", self.EDITION)
        record.setdefault("schema", config.SCHEMA_VERSION)
        return record

    def _write_file(self, record: Mapping[str, Any]) -> None:
        """Write ``data/license.json`` atomically."""
        path = config.license_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(dict(record), handle, ensure_ascii=False, indent=2)
            os_replace(tmp, path)
        except OSError as exc:
            _log.error("could not write license.json: %s", exc)
            raise errors.ImriError(
                errors.LICENSE_WRITE_FAILED, errors.status_for(errors.LICENSE_WRITE_FAILED)
            ) from exc

    def _mirror(self, record: Mapping[str, Any]) -> None:
        """Mirror the activation record into ``settings`` (LIC-03)."""
        with self.db.transaction():
            self.db.set_setting("license_key", "PERPETUAL:" + str(record["code_fingerprint"]))
            self.db.set_setting("license_edition", str(record.get("edition") or self.EDITION))
            self.db.set_setting("license_activated_at", str(record.get("activated_at") or ""))
            self.db.set_setting("license_school", str(record.get("school_name") or ""))

    # -- cooldown --------------------------------------------------------- #
    def _check_cooldown(self) -> int:
        """Return remaining cooldown seconds, or ``0``."""
        with self._lock:
            remaining = self._cooldown_until - time.monotonic()
            return max(0, int(remaining) + 1) if remaining > 0 else 0

    def _record_failure(self) -> tuple[int, int]:
        """Count one failure; returns ``(attempts_left, retry_after_s)``."""
        with self._lock:
            self._attempts += 1
            if self._attempts >= LICENSE_MAX_ATTEMPTS:
                self._cooldown_until = time.monotonic() + LICENSE_COOLDOWN_SECONDS
                return 0, int(LICENSE_COOLDOWN_SECONDS)
            return max(0, LICENSE_MAX_ATTEMPTS - self._attempts), 0

    def _reset_attempts(self) -> None:
        with self._lock:
            self._attempts = 0
            self._cooldown_until = 0.0

    # -- public API ------------------------------------------------------- #
    def status(self) -> dict[str, Any]:
        """Return the current activation state, repairing the file if needed."""
        record = self._read_file()
        if record is None:
            record = self._record_from_settings()
            if record is not None:
                # LIC-07: the mirror is valid, so silently rewrite the file.
                try:
                    self._write_file(record)
                except errors.ImriError:
                    _log.warning("license mirror present but license.json is not writable")
        if record is None:
            return {
                "licensed": False,
                "edition": "",
                "activated_at": "",
                "school_name": "",
                "product": config.PRODUCT,
                "version": config.APP_VERSION,
                "schema": config.SCHEMA_VERSION,
            }
        return {
            "licensed": True,
            "edition": str(record.get("edition") or self.EDITION),
            "activated_at": str(record.get("activated_at") or ""),
            "school_name": str(record.get("school_name") or ""),
            "product": config.PRODUCT,
            "version": config.APP_VERSION,
            "schema": config.SCHEMA_VERSION,
        }

    def is_licensed(self) -> bool:
        """Convenience predicate used by the middleware."""
        return bool(self.status().get("licensed"))

    def require(self) -> None:
        """Raise ``license_required`` (403) when IMRI is not activated."""
        if not self.is_licensed():
            raise errors.ImriError(
                errors.LICENSE_REQUIRED, errors.status_for(errors.LICENSE_REQUIRED)
            )

    def activate(self, code: str, client: str | None = None) -> dict[str, Any]:
        """Validate ``code`` and persist the activation.

        Comparison is ``strip().lower()`` then :func:`hmac.compare_digest`
        against the single module constant. Nothing is written on failure.
        """
        wait = self._check_cooldown()
        if wait:
            raise errors.ImriError(
                errors.LICENSE_COOLDOWN,
                errors.status_for(errors.LICENSE_COOLDOWN),
                details={"retry_after_s": wait},
            )

        candidate = (code or "").strip().lower()
        matched = False
        try:
            matched = hmac.compare_digest(candidate, self.CODE)
        except TypeError:  # non-ASCII input cannot equal an ASCII constant
            matched = False

        if not matched:
            attempts_left, retry_after = self._record_failure()
            self.db.audit(
                "anonymous",
                "license_activate_failed",
                target="license",
                detail={"attempts_left": attempts_left},
                ip=client,
            )
            raise errors.ImriError(
                errors.INVALID_LICENSE_CODE,
                errors.status_for(errors.INVALID_LICENSE_CODE),
                fields={"code": errors.INVALID_LICENSE_CODE},
                details={"attempts_left": attempts_left, "retry_after_s": retry_after},
            )

        school = self.db.get_setting("school_name", config.DEFAULT_SETTINGS["school_name"])
        record = {
            "product": config.PRODUCT,
            "edition": self.EDITION,
            "code_fingerprint": _fingerprint(self.CODE),
            "activated_at": config.now_utc(),
            "school_name": school,
            "schema": config.SCHEMA_VERSION,
        }
        self._write_file(record)
        self._mirror(record)
        self._reset_attempts()
        self.db.audit(
            "anonymous",
            "license_activated",
            target="license",
            detail={"edition": self.EDITION, "school_name": school},
            ip=client,
        )
        return {
            "licensed": True,
            "activated_at": record["activated_at"],
            "school_name": school,
            "edition": self.EDITION,
        }


def os_replace(src: Any, dst: Any) -> None:
    """``os.replace`` with a Windows retry (a virus scanner may hold the file)."""
    last_error: OSError | None = None
    for _ in range(5):
        try:
            os.replace(str(src), str(dst))
            return
        except OSError as exc:  # pragma: no cover - Windows file locking
            last_error = exc
            time.sleep(0.05)
    if last_error is not None:
        raise last_error


# --------------------------------------------------------------------------- #
# Recovery marker (ADM-05)
# --------------------------------------------------------------------------- #


def consume_reset_admin(db: Any, actor: str = "system") -> bool:
    """Consume ``data/RESET-ADMIN`` on startup.

    Resets ``admin`` to the documented default with ``must_change_password=1``,
    writes one audit row and deletes the marker. This is the only password
    escape hatch: there is no email reset and no network.
    """
    marker = config.reset_admin_path()
    if not marker.exists():
        return False

    now = config.now_utc()
    new_hash = hash_password(DEFAULT_ADMIN_PASSWORD)
    with db.transaction():
        row = db.one("SELECT id FROM users WHERE username = ?", (DEFAULT_ADMIN_USERNAME,))
        if row:
            db.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 1, "
                "is_active = 1, role = 'admin', updated_at = ? WHERE id = ?",
                (new_hash, now, row["id"]),
            )
            target_id = row["id"]
        else:
            target_id = db.execute(
                "INSERT INTO users(username, password_hash, display_name, role, is_active, "
                "must_change_password, created_at, updated_at) VALUES (?, ?, ?, 'admin', 1, 1, ?, ?)",
                (DEFAULT_ADMIN_USERNAME, new_hash, "Administrator", now, now),
            )
        db.execute("DELETE FROM sessions WHERE user_id = ?", (target_id,))
        db.audit(
            actor,
            "admin_password_reset",
            target=DEFAULT_ADMIN_USERNAME,
            detail={"source": "RESET-ADMIN marker"},
            target_type="user",
        )
    try:
        marker.unlink()
    except OSError:  # pragma: no cover - defensive
        _log.warning("could not delete the RESET-ADMIN marker")
    _log.warning("RESET-ADMIN marker consumed; admin password restored to default")
    return True


def cookie_header(token: str) -> str:
    """Return the ``Set-Cookie`` value for a session token."""
    return SESSION_COOKIE_HEADER.format(token=token)


def clear_cookie_header() -> str:
    """Return the ``Set-Cookie`` value that clears the session cookie."""
    return "imri_sid=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"


def parse_cookie(header: str | None, name: str = SESSION_COOKIE) -> str | None:
    """Extract one cookie value from a raw ``Cookie`` header."""
    if not header:
        return None
    for part in header.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() == name:
            return value.strip() or None
    return None


def iter_caps() -> Iterable[str]:
    """Return every declared capability (used by tests and diagnostics)."""
    return tuple(sorted(ALL_CAPS))
