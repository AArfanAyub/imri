"""Paths, constants, time/money helpers and logging for IMRI.

Everything that needs to know *where* a file lives or *what* "today" means
goes through this module, so the frozen-vs-dev distinction exists in exactly
one place.

The single rule that matters most: :func:`data_dir` always resolves beside the
executable, never inside ``sys._MEIPASS``. A one-file PyInstaller bundle
extracts to a temporary directory that is deleted on exit, so writing the
librarian's database there would silently destroy the whole library.
"""

from __future__ import annotations

import datetime as _dt
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

APP_NAME = "IMRI"
APP_VERSION = "1.0.0"
PRODUCT = "IMRI"

#: Schema version. Bump it whenever :data:`imri.db.Migrator.STEPS` gains a step.
#:
#: * 1 — the original schema.
#: * 2 — the FTS5 catalogue search index and the eleven read-path indexes
#:   measured in ``docs/LOAD-TEST.md``. Applied in place to an existing file;
#:   a fresh file reaches the same shape through the same step.
SCHEMA_VERSION = 2

HOST = "127.0.0.1"
DEFAULT_PORT = 0  # 0 -> the OS picks a free ephemeral port

#: Environment override used by tests and scenario scripts so they never touch
#: the operator's real ``data/`` folder.
DATA_DIR_ENV = "IMRI_DATA_DIR"

#: Sub-directories created under ``data/`` on every start (ARCHITECTURE §12.1).
DATA_SUBDIRS = (
    "backups",
    "covers",
    "photos",
    "exports",
    "logs",
    "browser-profile",
)

#: Settings seeded on first run (ARCHITECTURE §3.4) — exactly 19 keys.
DEFAULT_SETTINGS: dict[str, str] = {
    "school_name": "My School Resource Centre",
    "currency": "RM",
    "currency_space": "1",
    "fine_per_day": "0.20",
    "loan_days": "14",
    "max_loans": "3",
    "max_renewals": "1",
    "hold_days": "3",
    "label_prefix": "IMRI",
    "default_locale": "en",
    "theme": "system",
    "license_key": "",
    "license_edition": "",
    "license_activated_at": "",
    "license_school": "",
    "backup_on_exit": "0",
    "backup_keep": "10",
    "page_size_default": "50",
    "idle_lock_minutes": "0",
}

#: Settings the API refuses outside these ranges (``out_of_range``, 422).
#: ``kind`` is one of ``int``, ``float``, ``enum``, ``bool``.
SETTING_RULES: dict[str, dict[str, Any]] = {
    "school_name": {"kind": "text", "max_len": 120},
    "currency": {"kind": "text", "max_len": 8},
    "currency_space": {"kind": "bool"},
    "fine_per_day": {"kind": "float", "min": 0.0, "max": 100.0},
    "loan_days": {"kind": "int", "min": 1, "max": 365},
    "max_loans": {"kind": "int", "min": 1, "max": 50},
    "max_renewals": {"kind": "int", "min": 0, "max": 10},
    "hold_days": {"kind": "int", "min": 1, "max": 90},
    "label_prefix": {"kind": "text", "max_len": 16},
    "default_locale": {"kind": "enum", "values": ("en", "ms", "zh")},
    "theme": {"kind": "enum", "values": ("system", "light", "dark")},
    "backup_on_exit": {"kind": "bool"},
    "backup_keep": {"kind": "int", "min": 1, "max": 100},
    "page_size_default": {"kind": "int", "min": 10, "max": 200},
    "idle_lock_minutes": {"kind": "int", "min": 0, "max": 240},
}

#: Settings the API must never let a client write directly.
READ_ONLY_SETTINGS = frozenset(
    {
        "license_key",
        "license_edition",
        "license_activated_at",
        "license_school",
    }
)

#: Material types (CAT-07) — the enum is enforced by the schema CHECK as well.
MATERIAL_TYPES = (
    "book",
    "textbook",
    "reference",
    "magazine",
    "av",
    "kit",
    "thesis",
    "other",
)

CLASSIFICATIONS = ("ddc", "lcc", "local")
BOOK_STATUSES = ("available", "on_loan", "unavailable", "lost", "withdrawn")
COPY_STATUSES = ("available", "on_loan", "reserved", "lost", "damaged", "withdrawn")
COPY_CONDITIONS = ("new", "good", "fair", "poor")
MEMBER_TYPES = ("student", "teacher", "patron")
MEMBER_STATUSES = ("active", "suspended", "expired")
USER_ROLES = ("admin", "librarian", "assistant", "viewer")
LOAN_STATUSES = ("on_loan", "returned", "lost", "damaged")
RESERVATION_STATUSES = ("open", "ready", "fulfilled", "expired", "cancelled")

#: Language vocabulary offered by ``GET /api/languages``.
LANGUAGES = (
    ("en", "English"),
    ("ms", "Bahasa Melayu"),
    ("zh", "中文"),
    ("ta", "தமிழ்"),
    ("ar", "العربية"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("id", "Bahasa Indonesia"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("other", "Other"),
)

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


def base_dir() -> Path:
    """Return the writable directory that owns ``data/``.

    Frozen: the folder holding ``IMRI.exe``. Development: the repository root
    (the parent of the ``imri`` package).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Return ``base_dir()/data`` — the only directory IMRI ever writes to."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return base_dir() / "data"


def web_dir() -> Path:
    """Return the read-only client root (inside the one-file bundle when frozen)."""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass) / "web"
        return base_dir() / "web"
    return base_dir() / "web"


def db_path() -> Path:
    """Return the SQLite file path."""
    return data_dir() / "imri.db"


def license_path() -> Path:
    """Return the primary activation record path."""
    return data_dir() / "license.json"


def lock_path() -> Path:
    """Return the single-instance lock file path."""
    return data_dir() / ".lock"


def reset_admin_path() -> Path:
    """Return the recovery-marker path consumed on startup."""
    return data_dir() / "RESET-ADMIN"


def logs_dir() -> Path:
    """Return the log directory."""
    return data_dir() / "logs"


def exports_dir() -> Path:
    """Return the directory reports and exports are written to (ADR-12)."""
    return data_dir() / "exports"


def backups_dir() -> Path:
    """Return the backup archive directory."""
    return data_dir() / "backups"


def browser_profile_dir() -> Path:
    """Return the dedicated browser profile used by the app window.

    A private profile matters for two reasons: ``--app`` on a profile that is
    already running would just open a tab in the user's everyday browser, and
    the localStorage state (theme, last route) must survive between sessions
    without touching the librarian's normal browsing data.
    """
    return data_dir() / "browser-profile"


def ensure_dirs() -> Path:
    """Create ``data/`` and every sub-directory. Idempotent."""
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    for name in DATA_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


# --------------------------------------------------------------------------- #
# Time and money
# --------------------------------------------------------------------------- #


def now_utc() -> str:
    """Return the current UTC timestamp as ``YYYY-MM-DDTHH:MM:SSZ``.

    Used for every ``created_at`` / ``updated_at`` / ``paid_at`` / audit ``ts``.
    """
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_local() -> str:
    """Return *local* today as ``YYYY-MM-DD`` (ADR-09).

    Business dates follow the librarian's wall clock. Using UTC here would make
    a 00:30 return in Kuala Lumpur count as "yesterday" and put a fine on the
    wrong day every evening.
    """
    return _dt.date.today().isoformat()


def parse_date(value: Any) -> _dt.date | None:
    """Parse ``YYYY-MM-DD`` into a :class:`datetime.date`, or return ``None``."""
    if isinstance(value, _dt.date):
        return value
    if not value:
        return None
    try:
        return _dt.date.fromisoformat(str(value).strip()[:10])
    except (ValueError, TypeError):
        return None


def date_add(value: Any, days: int) -> str:
    """Return ``value + days`` as ``YYYY-MM-DD``; ``value`` defaults to today."""
    base = parse_date(value) or _dt.date.today()
    return (base + _dt.timedelta(days=int(days))).isoformat()


def days_between(start: Any, end: Any) -> int:
    """Return whole days from ``start`` to ``end`` (``end - start``)."""
    a = parse_date(start)
    b = parse_date(end)
    if a is None or b is None:
        return 0
    return (b - a).days


def money(value: Any) -> float:
    """Round a monetary value to 2 decimal places, half-up (ADR-08)."""
    try:
        return round(float(value) + 1e-9, 2)
    except (TypeError, ValueError):
        return 0.0


def money_eq(a: Any, b: Any) -> bool:
    """Compare two monetary values with a 0.005 tolerance (never use ``==``)."""
    return abs(money(a) - money(b)) < 0.005


def to_int(value: Any, default: int = 0) -> int:
    """Best-effort integer coercion that never raises."""
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    """Best-effort float coercion that never raises."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def clean_text(value: Any) -> str | None:
    """Normalise optional text: blank becomes ``None`` so "empty" has one form."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def like_term(value: Any) -> str:
    """Wrap a user term for a ``LIKE`` comparison, escaping its wildcards.

    Always pair the placeholder with ``ESCAPE '\\'`` in the SQL. Without this a
    reader who types ``%`` into a search box matches the whole catalogue and
    ``_`` matches any single character, which is both wrong and slow: SQLite
    cannot use an index for a pattern that starts with a wildcard, so a stray
    ``%`` turns every search into a full scan of a 200,000-row table.
    """
    text = clean_text(value) or ""
    text = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + text + "%"


def page_offset(page: Any, limit: int) -> int:
    """Return the SQL ``OFFSET`` for a page mapping.

    ``offset`` wins when the caller supplies it. Otherwise the offset is derived
    from ``page``, so ``{"page": 4000}`` really means page 4000. Without this a
    caller that passes only ``page`` silently gets page 1 while ``meta.page``
    reports 4000 — which quietly invalidates any deep-pagination measurement.
    """
    source = page if isinstance(page, dict) or hasattr(page, "get") else {}
    raw = source.get("offset")
    if raw not in (None, ""):
        return max(0, to_int(raw, 0))
    number = max(1, to_int(source.get("page"), 1))
    return (number - 1) * max(1, int(limit))


def to_bool(value: Any) -> bool:
    """Coerce a SQLite 0/1, a JSON bool or a string into a real boolean."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_configured = False


def setup_logging(level: int = logging.INFO, force: bool = False) -> logging.Logger:
    """Configure the rotating file log at ``data/logs/imri.log``.

    There is no ``print()`` anywhere in IMRI: under ``--windowed`` the process
    has no stdout at all and a stray write can raise. Everything is logged.

    Returns the root ``imri`` logger.
    """
    global _configured
    logger = logging.getLogger("imri")
    if _configured and not force:
        return logger

    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover - defensive
            pass

    formatter = logging.Formatter(_LOG_FORMAT)

    try:
        logs = logs_dir()
        logs.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            str(logs / "imri.log"),
            maxBytes=1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        # A read-only or missing data folder must not stop the app from
        # starting; it just runs without a file log.
        pass

    if not getattr(sys, "frozen", False) and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        stream.setLevel(logging.WARNING)
        logger.addHandler(stream)

    _configured = True
    return logger


def get_logger(name: str = "imri") -> logging.Logger:
    """Return a child logger of the ``imri`` namespace."""
    if not name.startswith("imri"):
        name = "imri." + name
    return logging.getLogger(name)
