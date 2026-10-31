"""Canonical error codes and the single exception type for IMRI.

The server never sends a sentence the UI displays: it sends a stable machine
token that the client maps to ``err.<code>`` in the active language
(ARCHITECTURE §8.2). ``message`` is a developer-facing English fallback and is
never rendered.

Domain modules raise :class:`ImriError`; only ``imri/server.py`` turns one into
an HTTP response.
"""

from __future__ import annotations

from typing import Any, Mapping

# --------------------------------------------------------------------------- #
# Canonical codes (ARCHITECTURE §8.2) — grouped exactly as the table groups them
# --------------------------------------------------------------------------- #

AUTH_REQUIRED = "auth_required"
INVALID_CREDENTIALS = "invalid_credentials"
WEAK_PASSWORD = "weak_password"
WRONG_PASSWORD = "wrong_password"
SESSION_EXPIRED = "session_expired"
ACCOUNT_DISABLED = "account_disabled"
RATE_LIMITED = "rate_limited"

LICENSE_REQUIRED = "license_required"
INVALID_LICENSE_CODE = "invalid_license_code"
LICENSE_COOLDOWN = "license_cooldown"
LICENSE_WRITE_FAILED = "license_write_failed"

FORBIDDEN = "forbidden"
CANNOT_DELETE_SELF = "cannot_delete_self"
CANNOT_DELETE_LAST_ADMIN = "cannot_delete_last_admin"
CANNOT_DEMOTE_LAST_ADMIN = "cannot_demote_last_admin"

NOT_FOUND = "not_found"
VALIDATION_FAILED = "validation_failed"
CONFLICT = "conflict"
INTERNAL = "internal"
DB_BUSY = "db_busy"
PAYLOAD_TOO_LARGE = "payload_too_large"
UNSUPPORTED_MEDIA = "unsupported_media"
DB_NEWER_THAN_APP = "db_newer_than_app"

DUPLICATE_ACCESSION = "duplicate_accession"
DUPLICATE_ISBN = "duplicate_isbn"
DUPLICATE_BARCODE = "duplicate_barcode"
INVALID_ISBN = "invalid_isbn"
INVALID_DEWEY = "invalid_dewey"
INVALID_CALL_NO = "invalid_call_no"
INVALID_YEAR = "invalid_year"
TITLE_REQUIRED = "title_required"
UNKNOWN_MATERIAL_TYPE = "unknown_material_type"
BOOK_HAS_ACTIVE_LOANS = "book_has_active_loans"
BOOK_HAS_RESERVATIONS = "book_has_reservations"
COPY_ON_LOAN = "copy_on_loan"
COPY_NOT_FOUND = "copy_not_found"
LAST_COPY = "last_copy"

DUPLICATE_MEMBER_CODE = "duplicate_member_code"
INVALID_IC = "invalid_ic"
INVALID_EMAIL = "invalid_email"
MEMBER_HAS_LOANS = "member_has_loans"
MEMBER_SUSPENDED = "member_suspended"
MEMBER_EXPIRED = "member_expired"
MEMBER_INACTIVE = "member_inactive"

NO_COPIES_AVAILABLE = "no_copies_available"
COPY_UNAVAILABLE = "copy_unavailable"
COPY_ON_HOLD = "copy_on_hold"
MAX_LOANS_REACHED = "max_loans_reached"
DUPLICATE_ACTIVE_LOAN = "duplicate_active_loan"
ALREADY_RETURNED = "already_returned"
LOAN_OVERDUE = "loan_overdue"
RENEWAL_LIMIT_REACHED = "renewal_limit_reached"
RENEWAL_BLOCKED_RESERVATION = "renewal_blocked_reservation"
RENEWAL_BLOCKED_HOLD = "renewal_blocked_hold"

OVERPAYMENT = "overpayment"
INVALID_AMOUNT = "invalid_amount"
REASON_REQUIRED = "reason_required"
NOTHING_TO_WAIVE = "nothing_to_waive"
RECEIPT_CONFLICT = "receipt_conflict"

RESERVATION_EXISTS = "reservation_exists"
TITLE_AVAILABLE = "title_available"
RESERVATION_NOT_OPEN = "reservation_not_open"
HOLD_EXPIRED = "hold_expired"
QUEUE_EMPTY = "queue_empty"

IMPORT_EXPIRED = "import_expired"
IMPORT_EMPTY = "import_empty"
IMPORT_BAD_ENCODING = "import_bad_encoding"
UNSUPPORTED_FORMAT = "unsupported_format"
NO_VALID_ROWS = "no_valid_rows"
EXPORT_FAILED = "export_failed"

QR_DATA_TOO_LONG = "qr_data_too_long"
CODE39_UNSUPPORTED_CHAR = "code39_unsupported_char"
LABEL_PAYLOAD_EMPTY = "label_payload_empty"

NFC_UNAVAILABLE = "nfc_unavailable"
NFC_NO_CARD = "nfc_no_card"
NFC_TIMEOUT = "nfc_timeout"
NFC_PAYLOAD_TOO_LONG = "nfc_payload_too_long"

RESTORE_NO_FILE = "restore_no_file"
RESTORE_NO_MANIFEST = "restore_no_manifest"
RESTORE_INTEGRITY_FAILED = "restore_integrity_failed"
UNSAFE_PATH = "unsafe_path"
BACKUP_FAILED = "backup_failed"

PATH_OUTSIDE_DATA = "path_outside_data"
REVEAL_FAILED = "reveal_failed"
DIAGNOSTICS_FAILED = "diagnostics_failed"

#: Codes used by the API layer but not named in the §8.2 table.
OUT_OF_RANGE = "out_of_range"
DUPLICATE_USERNAME = "duplicate_username"

# --------------------------------------------------------------------------- #
# Code -> default HTTP status
# --------------------------------------------------------------------------- #

_401 = frozenset({AUTH_REQUIRED, SESSION_EXPIRED, INVALID_CREDENTIALS})
_403 = frozenset(
    {
        FORBIDDEN,
        ACCOUNT_DISABLED,
        LICENSE_REQUIRED,
        INVALID_LICENSE_CODE,
    }
)
_404 = frozenset({NOT_FOUND, COPY_NOT_FOUND})
_409 = frozenset(
    {
        CONFLICT,
        DUPLICATE_ACCESSION,
        DUPLICATE_ISBN,
        DUPLICATE_BARCODE,
        BOOK_HAS_ACTIVE_LOANS,
        BOOK_HAS_RESERVATIONS,
        COPY_ON_LOAN,
        LAST_COPY,
        DUPLICATE_MEMBER_CODE,
        MEMBER_HAS_LOANS,
        MEMBER_SUSPENDED,
        MEMBER_EXPIRED,
        MEMBER_INACTIVE,
        NO_COPIES_AVAILABLE,
        COPY_UNAVAILABLE,
        COPY_ON_HOLD,
        MAX_LOANS_REACHED,
        DUPLICATE_ACTIVE_LOAN,
        ALREADY_RETURNED,
        LOAN_OVERDUE,
        RENEWAL_LIMIT_REACHED,
        RENEWAL_BLOCKED_RESERVATION,
        RENEWAL_BLOCKED_HOLD,
        RESERVATION_EXISTS,
        TITLE_AVAILABLE,
        RESERVATION_NOT_OPEN,
        HOLD_EXPIRED,
        QUEUE_EMPTY,
        RECEIPT_CONFLICT,
        DUPLICATE_USERNAME,
        CANNOT_DELETE_SELF,
        CANNOT_DELETE_LAST_ADMIN,
        CANNOT_DEMOTE_LAST_ADMIN,
        NFC_UNAVAILABLE,
        NFC_NO_CARD,
        NFC_TIMEOUT,
        UNSAFE_PATH,
        REVEAL_FAILED,
        DIAGNOSTICS_FAILED,
        BACKUP_FAILED,
        EXPORT_FAILED,
    }
)
_422 = frozenset(
    {
        VALIDATION_FAILED,
        INVALID_ISBN,
        INVALID_DEWEY,
        INVALID_CALL_NO,
        INVALID_YEAR,
        TITLE_REQUIRED,
        UNKNOWN_MATERIAL_TYPE,
        INVALID_IC,
        INVALID_EMAIL,
        WEAK_PASSWORD,
        WRONG_PASSWORD,
        OVERPAYMENT,
        INVALID_AMOUNT,
        REASON_REQUIRED,
        NOTHING_TO_WAIVE,
        IMPORT_EMPTY,
        IMPORT_BAD_ENCODING,
        UNSUPPORTED_FORMAT,
        NO_VALID_ROWS,
        QR_DATA_TOO_LONG,
        CODE39_UNSUPPORTED_CHAR,
        LABEL_PAYLOAD_EMPTY,
        NFC_PAYLOAD_TOO_LONG,
        RESTORE_NO_FILE,
        RESTORE_NO_MANIFEST,
        RESTORE_INTEGRITY_FAILED,
        PATH_OUTSIDE_DATA,
        OUT_OF_RANGE,
    }
)
_429 = frozenset({RATE_LIMITED, LICENSE_COOLDOWN})
_500 = frozenset({INTERNAL, DB_NEWER_THAN_APP, LICENSE_WRITE_FAILED})
_413 = frozenset({PAYLOAD_TOO_LARGE})
_415 = frozenset({UNSUPPORTED_MEDIA})
_410 = frozenset({IMPORT_EXPIRED})
_503 = frozenset({DB_BUSY})


def status_for(code: str, default: int = 400) -> int:
    """Map an error code onto its canonical HTTP status."""
    if code in _401:
        return 401
    if code in _403:
        return 403
    if code in _404:
        return 404
    if code in _409:
        return 409
    if code in _422:
        return 422
    if code in _429:
        return 429
    if code in _410:
        return 410
    if code in _413:
        return 413
    if code in _415:
        return 415
    if code in _500:
        return 500
    if code in _503:
        return 503
    return default


#: Every code this build can emit. Used by the i18n completeness test.
ALL_CODES: tuple[str, ...] = tuple(
    sorted(
        _401
        | _403
        | _404
        | _409
        | _422
        | _429
        | _500
        | _413
        | _415
        | _410
        | _503
    )
)


class ImriError(Exception):
    """A domain or transport failure carrying a stable machine code.

    Args:
        code: one of the canonical tokens above.
        status: HTTP status; defaults to the canonical mapping for ``code``.
        fields: per-field validation errors, ``{"isbn": "invalid_isbn"}``,
            which the client renders inline under the matching input.
        details: free-form extra context (never secrets).
        message: developer-facing English fallback.
    """

    __slots__ = ("code", "status", "fields", "details", "message")

    def __init__(
        self,
        code: str,
        status: int | None = None,
        fields: Mapping[str, Any] | None = None,
        details: Mapping[str, Any] | None = None,
        message: str | None = None,
    ) -> None:
        self.code = code or INTERNAL
        self.status = int(status) if status is not None else status_for(self.code)
        self.fields = dict(fields) if fields else {}
        self.details = dict(details) if details else {}
        self.message = message or _default_message(self.code)
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the ``{"ok": false, "error": {...}}`` payload."""
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.fields:
            payload["fields"] = self.fields
        if self.details:
            payload["details"] = self.details
        return payload

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "ImriError(%r, status=%d)" % (self.code, self.status)


_MESSAGES = {
    AUTH_REQUIRED: "Authentication required.",
    INVALID_CREDENTIALS: "Username or password is incorrect.",
    WEAK_PASSWORD: "Password must be at least 8 characters.",
    WRONG_PASSWORD: "Current password is incorrect.",
    SESSION_EXPIRED: "Session has expired.",
    ACCOUNT_DISABLED: "This account is disabled.",
    RATE_LIMITED: "Too many attempts. Please wait.",
    LICENSE_REQUIRED: "IMRI has not been activated.",
    INVALID_LICENSE_CODE: "That activation code is not valid.",
    LICENSE_COOLDOWN: "Too many failed activation attempts.",
    LICENSE_WRITE_FAILED: "Could not write the activation record.",
    FORBIDDEN: "Your role does not allow this action.",
    NOT_FOUND: "Record not found.",
    VALIDATION_FAILED: "One or more fields are invalid.",
    CONFLICT: "That action conflicts with the current state.",
    INTERNAL: "An unexpected error occurred.",
    DB_BUSY: "The database is busy. Try again.",
    PAYLOAD_TOO_LARGE: "Request body is too large.",
    UNSUPPORTED_MEDIA: "Unsupported content type.",
    DB_NEWER_THAN_APP: "This database was written by a newer version of IMRI.",
    TITLE_REQUIRED: "Title is required.",
    INVALID_ISBN: "ISBN check digit is invalid.",
    INVALID_DEWEY: "Dewey number must look like 823 or 823.914.",
    INVALID_CALL_NO: "Call number contains unsupported characters.",
    INVALID_YEAR: "Publication year must be four digits.",
    DUPLICATE_ACCESSION: "That accession number already exists.",
    DUPLICATE_ISBN: "A title with that ISBN already exists.",
    DUPLICATE_BARCODE: "That barcode already exists.",
    UNKNOWN_MATERIAL_TYPE: "Unknown material type.",
    BOOK_HAS_ACTIVE_LOANS: "This title still has copies on loan.",
    BOOK_HAS_RESERVATIONS: "This title still has live reservations.",
    COPY_ON_LOAN: "That copy is currently on loan.",
    COPY_NOT_FOUND: "Copy not found.",
    LAST_COPY: "A title must keep at least one copy.",
    DUPLICATE_MEMBER_CODE: "That member code already exists.",
    INVALID_IC: "IC number must be 12 digits.",
    INVALID_EMAIL: "Email address is not valid.",
    MEMBER_HAS_LOANS: "This member still has loans.",
    MEMBER_SUSPENDED: "This member is suspended.",
    MEMBER_EXPIRED: "This membership has expired.",
    MEMBER_INACTIVE: "This member is not active.",
    NO_COPIES_AVAILABLE: "No copy of this title is available.",
    COPY_UNAVAILABLE: "That copy is not available for loan.",
    COPY_ON_HOLD: "That copy is being held for another member.",
    MAX_LOANS_REACHED: "This member has reached the loan limit.",
    DUPLICATE_ACTIVE_LOAN: "That copy already has an active loan.",
    ALREADY_RETURNED: "That loan has already been returned.",
    LOAN_OVERDUE: "An overdue loan cannot be renewed.",
    RENEWAL_LIMIT_REACHED: "Renewal limit reached.",
    RENEWAL_BLOCKED_RESERVATION: "Someone is waiting for this title.",
    RENEWAL_BLOCKED_HOLD: "This copy is on the hold shelf.",
    OVERPAYMENT: "The amount is more than the outstanding balance.",
    INVALID_AMOUNT: "Amount must be greater than zero.",
    REASON_REQUIRED: "A reason is required.",
    NOTHING_TO_WAIVE: "There is nothing left to waive.",
    RECEIPT_CONFLICT: "Receipt number collision; retry.",
    RESERVATION_EXISTS: "This member already has a live reservation.",
    TITLE_AVAILABLE: "A copy is available, so a reservation is not needed.",
    RESERVATION_NOT_OPEN: "That reservation is no longer open.",
    HOLD_EXPIRED: "That hold has expired.",
    QUEUE_EMPTY: "Nobody is waiting for this title.",
    IMPORT_EXPIRED: "The import preview expired. Upload the file again.",
    IMPORT_EMPTY: "The file contains no data rows.",
    IMPORT_BAD_ENCODING: "The file could not be decoded as UTF-8.",
    UNSUPPORTED_FORMAT: "Only CSV and XLSX files are supported.",
    NO_VALID_ROWS: "No row passed validation.",
    EXPORT_FAILED: "Could not write the export file.",
    QR_DATA_TOO_LONG: "That data is too long for a QR code.",
    CODE39_UNSUPPORTED_CHAR: "Code 39 cannot encode that character.",
    LABEL_PAYLOAD_EMPTY: "Nothing to print.",
    NFC_UNAVAILABLE: "No card reader is available.",
    NFC_NO_CARD: "No card is on the reader.",
    NFC_TIMEOUT: "The card reader timed out.",
    NFC_PAYLOAD_TOO_LONG: "Payload exceeds 48 bytes.",
    RESTORE_NO_FILE: "That backup file does not exist.",
    RESTORE_NO_MANIFEST: "That backup has no manifest.",
    RESTORE_INTEGRITY_FAILED: "Backup integrity check failed.",
    UNSAFE_PATH: "That path is not allowed.",
    BACKUP_FAILED: "Could not create the backup.",
    PATH_OUTSIDE_DATA: "That path is outside the data folder.",
    REVEAL_FAILED: "Could not open the folder.",
    DIAGNOSTICS_FAILED: "Diagnostics could not run.",
    OUT_OF_RANGE: "Value is outside the allowed range.",
    DUPLICATE_USERNAME: "That username already exists.",
    CANNOT_DELETE_SELF: "You cannot delete your own account.",
    CANNOT_DELETE_LAST_ADMIN: "The last administrator cannot be deleted.",
    CANNOT_DEMOTE_LAST_ADMIN: "The last administrator cannot be demoted.",
}


def _default_message(code: str) -> str:
    """Return the developer-facing English fallback for ``code``."""
    return _MESSAGES.get(code, code.replace("_", " ").capitalize())


def fail(code: str, **kwargs: Any) -> ImriError:
    """Shorthand constructor: ``raise fail(TITLE_REQUIRED, fields={...})``."""
    return ImriError(code, **kwargs)
