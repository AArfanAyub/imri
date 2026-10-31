"""Minimal Chrome DevTools Protocol client.

Only what the UI proof needs: open one WebSocket to a page target, send
`Runtime.evaluate` / `Page.navigate` / `Page.captureScreenshot`, and collect the
console and exception events that arrive in between.

Standard library only — no `websocket-client`, no `requests`. The handshake and
frame codec are the twenty lines everyone writes once.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.request
from urllib.parse import urlparse


class WebSocketError(RuntimeError):
    """Raised when the socket cannot be opened or a frame is malformed."""


class WebSocket:
    """A blocking RFC 6455 client, text frames only."""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")

        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buffer = b""

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode("ascii"))

        header = self._read_until(b"\r\n\r\n")
        if b"101" not in header.split(b"\r\n", 1)[0]:
            raise WebSocketError(f"handshake rejected: {header[:120]!r}")

    # -- socket helpers ---------------------------------------------------- #

    def _recv(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise WebSocketError("connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buffer:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise WebSocketError("connection closed during handshake")
            self._buffer += chunk
        head, _, rest = self._buffer.partition(marker)
        self._buffer = rest
        return head + marker

    # -- frames ------------------------------------------------------------ #

    def send(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def recv(self, timeout: float = 30.0) -> str:
        deadline = time.time() + timeout
        chunks: list[bytes] = []
        while True:
            if time.time() > deadline:
                raise TimeoutError("no websocket frame before the deadline")
            first, second = self._recv(2)
            fin = first & 0x80
            opcode = first & 0x0F
            masked = second & 0x80
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv(8))[0]
            mask = self._recv(4) if masked else b""
            payload = self._recv(length)
            if masked:
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))

            if opcode == 0x9:  # ping
                self._sock.sendall(b"\x8a" + bytes([len(payload)]) + payload)
                continue
            if opcode == 0x8:  # close
                raise WebSocketError("server closed the websocket")
            chunks.append(payload)
            if fin:
                return b"".join(chunks).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class CDP:
    """A CDP session over one page target."""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self._ws = WebSocket(url, timeout=timeout)
        self._next_id = 0
        self.events: list[dict] = []

    @staticmethod
    def page_target(port: int, url_fragment: str = "") -> str:
        """Find the websocket URL of a page target."""
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as response:
            targets = json.load(response)
        pages = [t for t in targets if t.get("type") == "page"]
        if url_fragment:
            pages = [t for t in pages if url_fragment in (t.get("url") or "")] or pages
        if not pages:
            raise RuntimeError("no page target; is the browser running?")
        return pages[0]["webSocketDebuggerUrl"]

    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        """Send a command and return its result, buffering any events."""
        self._next_id += 1
        message_id = self._next_id
        payload = {"id": message_id, "method": method}
        if params:
            payload["params"] = params
        self._ws.send(json.dumps(payload))

        deadline = time.time() + timeout
        while True:
            remaining = max(0.1, deadline - time.time())
            raw = self._ws.recv(timeout=remaining)
            message = json.loads(raw)
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            if "method" in message:
                self.events.append(message)

    def evaluate(self, expression: str, timeout: float = 30.0):
        """Run an expression in the page and return its value.

        Awaits promises, returns plain JSON, and raises with the page's own
        message when the expression throws.
        """
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            text = details.get("text") or ""
            description = (details.get("exception") or {}).get("description") or ""
            raise RuntimeError(f"{text} {description}".strip())
        return result.get("result", {}).get("value")

    # -- waiting ----------------------------------------------------------- #

    def wait_for(
        self,
        expression: str,
        timeout: float = 5.0,
        description: str = "",
        interval: float = 0.05,
    ) -> bool:
        """Poll ``expression`` until it is truthy.

        Raises `TimeoutError` naming what was awaited, so a failing proof says
        which boundary it never crossed instead of "element not found".
        """
        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            try:
                if self.evaluate(f"Boolean({expression})", timeout=5):
                    return True
            except RuntimeError as exc:  # the page is mid-navigation
                last_error = str(exc)
            time.sleep(interval)
        label = description or expression
        raise TimeoutError(f"timed out after {timeout:.1f}s waiting for {label}{last_error}")

    def wait_for_selector(self, selector: str, timeout: float = 5.0) -> bool:
        """Wait until ``selector`` matches an element in the page."""
        return self.wait_for(
            f"document.querySelector({selector!r})",
            timeout=timeout,
            description=f"selector {selector!r}",
        )

    def wait_for_route(self, name: str, timeout: float = 5.0) -> bool:
        """Wait until the router reports ``name`` as the active route.

        The hash changes before the lazily imported module has mounted, so this
        is the *first* half of a navigation boundary: follow it with
        `wait_for_selector` for something only that page renders.
        """
        expression = (
            "window.__IMRI__ && window.__IMRI__.store.currentRoute()"
            f" && window.__IMRI__.store.currentRoute().name === {name!r}"
        )
        try:
            return self.wait_for(expression, timeout=timeout, description=f"route {name!r}")
        except TimeoutError:
            active = "unknown"
            try:
                active = self.evaluate(
                    "(window.__IMRI__ && window.__IMRI__.store.currentRoute().name) || location.hash"
                )
            except RuntimeError:
                pass
            raise TimeoutError(f"timed out waiting for route {name!r}; the active route is {active!r}")

    def screenshot(self, path: str, full_page: bool = False) -> None:
        """Capture a PNG of the viewport (or the whole page)."""
        params = {"format": "png"}
        if full_page:
            metrics = self.call("Page.getLayoutMetrics")
            size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
            params["clip"] = {
                "x": 0,
                "y": 0,
                "width": size.get("width", 1440),
                "height": size.get("height", 900),
                "scale": 1,
            }
            params["captureBeyondViewport"] = True
        result = self.call("Page.captureScreenshot", params)
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(result["data"]))

    def drain(self, timeout: float = 0.35) -> None:
        """Read whatever events are already queued on the socket.

        `call()` only reads while it is waiting for its own reply, so events
        that arrive afterwards stay unread until the next command. Call this
        before inspecting `events`.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self._ws.recv(timeout=max(0.02, deadline - time.time()))
            except (TimeoutError, OSError):
                return
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if "method" in message:
                self.events.append(message)

    def console_errors(self) -> list[str]:
        """Every console error and uncaught exception seen so far."""
        self.drain()
        problems: list[str] = []
        for event in self.events:
            method = event.get("method")
            params = event.get("params") or {}
            if method == "Runtime.exceptionThrown":
                details = params.get("exceptionDetails") or {}
                text = details.get("text") or ""
                description = (details.get("exception") or {}).get("description") or ""
                problems.append(f"exception: {text} {description}".strip())
            elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "assert"):
                args = params.get("args") or []
                text = " ".join(str(arg.get("value", arg.get("description", ""))) for arg in args)
                problems.append(f"console.{params.get('type')}: {text}".strip())
            elif method == "Log.entryAdded":
                entry = params.get("entry") or {}
                if entry.get("level") == "error":
                    problems.append(f"log: {entry.get('text', '')}".strip())
        return problems

    def close(self) -> None:
        self._ws.close()
