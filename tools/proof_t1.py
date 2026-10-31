"""T1 proof: the process starts, answers health, and refuses a second copy.

Run with the project's interpreter:

    IMRI_DATA_DIR=<scratch> python tools/proof_t1.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def wait_for_health(base: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.15)
    raise SystemExit("health never answered: %r" % (last,))


def main() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="imri-t1-"))
    environment = dict(os.environ)
    environment["IMRI_DATA_DIR"] = str(scratch)

    print("data dir:", scratch)
    process = subprocess.Popen(
        [PY, str(ROOT / "main.py"), "--no-window", "--port", "0"],
        cwd=str(ROOT),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        line = process.stdout.readline().strip()
        print("startup line:", line)
        parts = urllib.parse.urlsplit(line)
        base = "http://%s:%d" % (parts.hostname, parts.port)
        if not parts.hostname:
            raise SystemExit("unexpected startup line: %r" % line)

        envelope = wait_for_health(base)
        print("health:", json.dumps(envelope))
        health = envelope["data"]
        assert health["status"] == "ok", health
        assert health["version"] == "1.0.0", health

        # -- second instance must refuse ---------------------------------- #
        second = subprocess.run(
            [PY, str(ROOT / "main.py"), "--no-window", "--port", "0"],
            cwd=str(ROOT),
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        print("second exit code:", second.returncode)
        print("second stderr:", (second.stderr or "").strip().splitlines()[:2])
        assert second.returncode == 2, "expected exit 2, got %d" % second.returncode

        # -- lock file contents ------------------------------------------- #
        lock = json.loads((scratch / ".lock").read_text(encoding="utf-8"))
        print("lock:", lock)
        assert lock["pid"] == process.pid, lock

        # -- unlicensed API is gated -------------------------------------- #
        try:
            with urllib.request.urlopen(base + "/api/books", timeout=5) as response:
                body = json.loads(response.read().decode("utf-8"))
                raise SystemExit("expected 403, got %s" % response.status)
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            print("gated /api/books ->", exc.code, body["error"]["code"])
            assert exc.code == 403 and body["error"]["code"] == "license_required"

        # -- bootstrap stays reachable before activation ------------------ #
        with urllib.request.urlopen(base + "/api/bootstrap", timeout=5) as response:
            bootstrap = json.loads(response.read().decode("utf-8"))
            print("bootstrap licensed:", bootstrap["data"]["licensed"])
            assert bootstrap["data"]["licensed"] is False

        # -- bye shuts the process down ----------------------------------- #
        request = urllib.request.Request(
            base + "/api/system/bye",
            data=b"{}",
            headers={"Content-Type": "application/json", "X-IMRI-CSRF": "1"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            print("bye ->", response.status)

        process.wait(timeout=20)
        print("exit after bye:", process.returncode)
        assert process.returncode == 0, process.returncode
        assert not (scratch / ".lock").exists(), "lock file was not released"
        print("\nT1 PASS")
        return 0
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.stdout.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
