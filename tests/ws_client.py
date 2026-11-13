"""Minimal RFC6455 WebSocket *client* + REST helper for live tests.

Used by ``tests/e2e_workspace.py`` to drive the real server and Chrome
DevTools Protocol over stdlib sockets only (no third-party packages).

Run from the repo root with the project venv, e.g.::

    ./.venv/bin/python tests/e2e_workspace.py --serve
"""

import base64
import json
import os
import socket
import struct
import time
import urllib.request
from urllib.parse import urlparse


class WSClient:
    """Tiny WebSocket client: masked text frames out, unmasked frames in."""

    def __init__(self, url, timeout=10):
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        req = (
            f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b"101" not in buf.split(b"\r\n")[0]:
            raise ConnectionError(buf[:200])
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self._id = 0
        self.events = []

    def _recv_exact(self, n):
        out = self.buf
        self.buf = b""
        while len(out) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed")
            out += chunk
        self.buf = out[n:]
        return out[:n]

    def send(self, text):
        payload = text.encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        elif len(payload) < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", len(payload))
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", len(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def recv(self, timeout=1.0):
        self.sock.settimeout(timeout)
        try:
            b1, b2 = self._recv_exact(2)
        except (socket.timeout, TimeoutError):
            return None
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        payload = self._recv_exact(length) if length else b""
        if (b1 & 0x0F) == 0x8:
            raise ConnectionError("server closed")
        return payload.decode("utf-8", "replace")

    # -- CDP convenience -------------------------------------------------
    def cdp(self, method, params=None, timeout=20):
        self._id += 1
        mid = self._id
        self.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            remaining = max(0.05, end - time.time())
            msg = self.recv(timeout=remaining)
            if msg is None:
                continue
            parsed = json.loads(msg)
            if parsed.get("id") == mid:
                if "error" in parsed:
                    raise RuntimeError(f"{method}: {parsed['error']}")
                return parsed.get("result", {})
            self.events.append(parsed)
        raise TimeoutError(method)

    def runtime_errors(self):
        """Console errors / uncaught exceptions seen since the last drain."""
        found = []
        for event in self.events:
            if event.get("method") == "Runtime.exceptionThrown":
                found.append(event["params"]["exceptionDetails"].get("text"))
            if event.get("method") == "Log.entryAdded":
                entry = event["params"]["entry"]
                if entry.get("level") == "error":
                    found.append(entry.get("text"))
        self.events = []
        return found

    def eval(self, expression, timeout=20):
        res = self.cdp("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        }, timeout=timeout)
        if res.get("exceptionDetails"):
            raise RuntimeError(json.dumps(res["exceptionDetails"])[:300])
        return res.get("result", {}).get("value")

    def wait_for(self, expression, timeout=30, poll=0.25):
        import time
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.eval(expression):
                    return True
            except Exception:
                pass
            time.sleep(poll)
        return False


class Rest:
    """Authenticated JSON calls against the local DENNIS server."""

    def __init__(self, base):
        self.base = base.rstrip("/")
        with urllib.request.urlopen(self.base + "/api/auth/session", timeout=10) as res:
            self.token = json.loads(res.read().decode())["token"]

    def api(self, path, method="GET", body=None, timeout=60):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("X-Dennis-Token", self.token)
        if data:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode())

    def ws_url(self, session_id):
        proto = "wss" if self.base.startswith("https") else "ws"
        host = urlparse(self.base).netloc
        return f"{proto}://{host}/api/ws/terminal?session_id={session_id}&token={self.token}"
