"""PC/SC bridge to a USB contactless reader, via ``ctypes`` and ``winscard.dll``.

There is no browser NFC API, so the reader is reached through Python. The
contract that makes this safe to call from a UI: **every entry point returns
``(ok, value_or_reason)`` and never raises**. A machine with no reader is a
normal state, not an error — the counter screen simply renders a disabled
"Scan card" ghost button and the librarian types the code instead (NFC-01).
"""

from __future__ import annotations

import ctypes
import struct
import time
from typing import Any

from .config import get_logger

_log = get_logger("nfc")

# PC/SC return codes and constants
SCARD_S_SUCCESS = 0
SCARD_E_NO_SMARTCARD = 0x8010000C
SCARD_E_READER_UNAVAILABLE = 0x80100017
SCARD_E_TIMEOUT = 0x8010000A
SCARD_SCOPE_USER = 0
SCARD_SHARE_SHARED = 2
SCARD_PROTOCOL_T0 = 1
SCARD_PROTOCOL_T1 = 2
SCARD_LEAVE_CARD = 0

#: MIFARE Ultralight / NTAG geometry used for the NDEF text record.
_NDEF_START_PAGE = 4
_MAX_NDEF_PAGES = 12
MAX_PAYLOAD_BYTES = 48

#: Reason tokens returned to the API layer.
REASON_OK = "ok"
REASON_NO_DLL = "no_dll"
REASON_NO_READER = "no_reader"
REASON_NO_CARD = "no_card"
REASON_TIMEOUT = "timeout"
REASON_READ_FAILED = "read_failed"
REASON_WRITE_FAILED = "write_failed"
REASON_NO_RECORD = "no_record"
REASON_TOO_LONG = "payload_too_long"

_loaded: Any = None
_load_attempted = False


def _load_winscard() -> Any:
    """Load and prototype ``winscard.dll`` once; return ``None`` when absent."""
    global _loaded, _load_attempted
    if _load_attempted:
        return _loaded
    _load_attempted = True
    try:
        library = ctypes.windll.winscard  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        _loaded = None
        return None
    try:
        library.SCardEstablishContext.restype = ctypes.c_long
        library.SCardEstablishContext.argtypes = [
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        library.SCardReleaseContext.restype = ctypes.c_long
        library.SCardReleaseContext.argtypes = [ctypes.c_void_p]
        library.SCardListReadersW.restype = ctypes.c_long
        library.SCardListReadersW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
        ]
        library.SCardConnectW.restype = ctypes.c_long
        library.SCardConnectW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        library.SCardDisconnect.restype = ctypes.c_long
        library.SCardDisconnect.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        library.SCardTransmit.restype = ctypes.c_long
        library.SCardTransmit.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
    except (AttributeError, TypeError):  # pragma: no cover - unusual DLL
        _loaded = None
        return None
    _loaded = library
    return library


def _list_readers(library: Any) -> list[str]:
    """Return the reader names reported by PC/SC (empty when none)."""
    handle = ctypes.c_void_p()
    code = library.SCardEstablishContext(SCARD_SCOPE_USER, None, None, ctypes.byref(handle))
    if code != SCARD_S_SUCCESS or not handle:
        return []
    try:
        length = ctypes.c_ulong(0)
        code = library.SCardListReadersW(handle, None, None, ctypes.byref(length))
        if code != SCARD_S_SUCCESS or length.value <= 2:
            return []
        buffer = ctypes.create_unicode_buffer("\x00" * (length.value // 2 + 1))
        code = library.SCardListReadersW(handle, None, buffer, ctypes.byref(length))
        if code != SCARD_S_SUCCESS or not buffer.value:
            return []
        return [name for name in buffer.value.split("\x00") if name.strip()]
    finally:
        try:
            library.SCardReleaseContext(handle)
        except Exception:  # pragma: no cover - defensive
            pass


def status() -> dict[str, Any]:
    """Return ``{available, reader, reason}``. Never raises."""
    try:
        library = _load_winscard()
        if library is None:
            return {"available": False, "reader": None, "reason": REASON_NO_DLL}
        readers = _list_readers(library)
        if not readers:
            return {"available": False, "reader": None, "reason": REASON_NO_READER}
        return {"available": True, "reader": readers[0], "reason": REASON_OK}
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("nfc status failed: %s", exc)
        return {"available": False, "reader": None, "reason": REASON_NO_DLL}


class _Connection:
    """A short-lived PC/SC connection to the first reader."""

    def __init__(self, library: Any) -> None:
        self.library = library
        self.context = ctypes.c_void_p()
        self.card = ctypes.c_void_p()
        self.protocol = 0

    def open(self, reader: str) -> str | None:
        """Connect to ``reader``; returns a reason token on failure."""
        code = self.library.SCardEstablishContext(
            SCARD_SCOPE_USER, None, None, ctypes.byref(self.context)
        )
        if code != SCARD_S_SUCCESS or not self.context:
            return REASON_NO_READER
        protocol = ctypes.c_ulong()
        code = self.library.SCardConnectW(
            self.context,
            reader,
            SCARD_SHARE_SHARED,
            SCARD_PROTOCOL_T0 | SCARD_PROTOCOL_T1,
            ctypes.byref(self.card),
            ctypes.byref(protocol),
        )
        if code != SCARD_S_SUCCESS or not self.card:
            self.close()
            return REASON_NO_CARD
        self.protocol = int(protocol.value)
        return None

    def transmit(self, command: list[int]) -> tuple[int, bytes] | None:
        """Send one APDU; returns ``(status_word, data)`` or ``None``."""
        payload = (ctypes.c_ubyte * len(command))(*command)
        receive = (ctypes.c_ubyte * 261)()
        receive_length = ctypes.c_ulong(261)
        io_request = ctypes.create_string_buffer(
            struct.pack("8B", 0x6C, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00), 8
        )
        code = self.library.SCardTransmit(
            self.card,
            ctypes.byref(io_request),
            ctypes.byref(payload),
            len(command),
            None,
            ctypes.byref(receive),
            ctypes.byref(receive_length),
        )
        if code != SCARD_S_SUCCESS:
            return None
        data = bytes(receive[: receive_length.value])
        if len(data) < 2:
            return None
        status_word = (data[-2] << 8) | data[-1]
        return status_word, data[:-2]

    def close(self) -> None:
        """Disconnect the card and release the context, swallowing errors."""
        try:
            if self.card:
                self.library.SCardDisconnect(self.card, SCARD_LEAVE_CARD)
        except Exception:  # pragma: no cover - defensive
            pass
        try:
            if self.context:
                self.library.SCardReleaseContext(self.context)
        except Exception:  # pragma: no cover - defensive
            pass
        self.card = ctypes.c_void_p()
        self.context = ctypes.c_void_p()

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def _connect_with_deadline(library: Any, deadline: float) -> tuple[Any, str | None]:
    """Try to connect until ``deadline``; returns ``(connection, reason)``."""
    readers = _list_readers(library)
    if not readers:
        return None, REASON_NO_READER
    last_reason = REASON_NO_CARD
    while True:
        connection = _Connection(library)
        reason = connection.open(readers[0])
        if reason is None:
            return connection, None
        connection.close()
        last_reason = reason
        if time.monotonic() >= deadline:
            return None, REASON_TIMEOUT if last_reason == REASON_NO_CARD else last_reason
        time.sleep(0.1)


def read_uid(timeout_ms: int = 5000) -> tuple[bool, str]:
    """Return ``(True, uid_hex)`` for the card on the reader, else ``(False, reason)``."""
    try:
        library = _load_winscard()
        if library is None:
            return False, REASON_NO_DLL
        deadline = time.monotonic() + max(200, int(timeout_ms)) / 1000.0
        connection, reason = _connect_with_deadline(library, deadline)
        if connection is None:
            return False, reason or REASON_NO_CARD
        with connection:
            response = connection.transmit([0xFF, 0xCA, 0x00, 0x00, 0x00])
            if response is None:
                return False, REASON_READ_FAILED
            status_word, data = response
            if status_word != 0x9000 or not data:
                return False, REASON_READ_FAILED
            return True, data.hex().upper()
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("nfc read_uid failed: %s", exc)
        return False, REASON_READ_FAILED


def read_text(timeout_ms: int = 5000) -> tuple[bool, str]:
    """Return ``(True, text)`` parsed from an NDEF text record, else a reason."""
    try:
        library = _load_winscard()
        if library is None:
            return False, REASON_NO_DLL
        deadline = time.monotonic() + max(200, int(timeout_ms)) / 1000.0
        connection, reason = _connect_with_deadline(library, deadline)
        if connection is None:
            return False, reason or REASON_NO_CARD
        raw = b""
        with connection:
            for page in range(_NDEF_START_PAGE, _NDEF_START_PAGE + _MAX_NDEF_PAGES):
                response = connection.transmit([0xFF, 0xB0, 0x00, page, 0x04])
                if response is None:
                    return False, REASON_READ_FAILED
                status_word, data = response
                if status_word != 0x9000:
                    return False, REASON_READ_FAILED
                raw += data
        return _parse_ndef_text(raw)
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("nfc read_text failed: %s", exc)
        return False, REASON_READ_FAILED


def write_text(text: Any, timeout_ms: int = 5000) -> tuple[bool, Any]:
    """Write a short NDEF text record. Payload is capped at 48 bytes."""
    payload = str(text or "").encode("utf-8")
    if not payload:
        return False, REASON_NO_RECORD
    if len(payload) > MAX_PAYLOAD_BYTES:
        return False, REASON_TOO_LONG
    try:
        library = _load_winscard()
        if library is None:
            return False, REASON_NO_DLL
        deadline = time.monotonic() + max(200, int(timeout_ms)) / 1000.0
        connection, reason = _connect_with_deadline(library, deadline)
        if connection is None:
            return False, reason or REASON_NO_CARD

        language = b"en"
        record = bytes(
            [
                0xD1,  # MB, ME, SR, TNF=well-known
                0x01,  # type length
                len(language) + 1 + len(payload),  # payload length
                0x54,  # 'T' — text record
                0x02,  # status: UTF-8, language length 2
            ]
        ) + language + payload
        while len(record) % 4:
            record += b"\xFE"

        with connection:
            page = _NDEF_START_PAGE
            position = 0
            while position < len(record):
                chunk = record[position : position + 4]
                if len(chunk) < 4:
                    chunk = chunk + b"\x00" * (4 - len(chunk))
                response = connection.transmit([0xFF, 0xD6, 0x00, page, 0x04] + list(chunk))
                if response is None:
                    return False, REASON_WRITE_FAILED
                status_word, _ = response
                if status_word != 0x9000:
                    return False, REASON_WRITE_FAILED
                page += 1
                position += 4
        return True, len(record)
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("nfc write_text failed: %s", exc)
        return False, REASON_WRITE_FAILED


def _parse_ndef_text(raw: bytes) -> tuple[bool, str]:
    """Best-effort parse of a Type-2 NDEF Text record from raw page bytes."""
    try:
        index = 0
        length = len(raw)
        while index < length:
            header = raw[index]
            if header & 0x07 == 0x01 and index + 4 < length:
                type_length = raw[index + 1]
                payload_length = raw[index + 2]
                if type_length == 1 and raw[index + 3] == 0x54:
                    status_byte = raw[index + 4]
                    language_length = status_byte & 0x3F
                    start = index + 5 + language_length
                    text = raw[start : start + (payload_length - 1 - language_length)]
                    return True, text.decode("utf-8", "replace")
            index += 1
        return False, REASON_NO_RECORD
    except Exception:  # pragma: no cover - defensive
        return False, REASON_READ_FAILED


class NfcBridge:
    """Object façade so the API layer has one thing to call."""

    def status(self) -> dict[str, Any]:
        """Return the reader status; always a dict, never an error."""
        return status()

    def read_uid(self, timeout_ms: int = 5000) -> tuple[bool, str]:
        """Return ``(ok, uid_or_reason)``."""
        return read_uid(timeout_ms)

    def read_text(self, timeout_ms: int = 5000) -> tuple[bool, str]:
        """Return ``(ok, text_or_reason)``."""
        return read_text(timeout_ms)

    def write_text(self, text: Any, timeout_ms: int = 5000) -> tuple[bool, Any]:
        """Return ``(ok, bytes_or_reason)``."""
        return write_text(text, timeout_ms)

    def read(self, timeout_ms: int = 5000) -> tuple[bool, dict[str, Any]]:
        """Read a UID first, then fall back to a text record."""
        ok, value = self.read_uid(timeout_ms)
        if ok:
            return True, {"uid": value, "text": None, "kind": "uid"}
        ok, value = self.read_text(timeout_ms)
        if ok:
            return True, {"uid": None, "text": value, "kind": "text"}
        return False, {"reason": value}
