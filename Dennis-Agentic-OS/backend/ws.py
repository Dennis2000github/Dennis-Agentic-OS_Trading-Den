"""Minimal RFC6455 WebSocket server (stdlib only).

Python's standard library ships no WebSocket server, so this implements just
enough: the handshake and text/ping/pong/close frames needed to stream a PTY.
Binary frames are accepted but decoded as UTF-8 text; the terminal stream is
inherently text.

The HTTP handler performs the handshake, then hands the socket to
:func:`run_websocket`, which blocks in that (threaded) request until the client
disconnects.
"""

import base64
import hashlib
import json
import struct
import threading

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def is_websocket_request(handler):
    upgrade = handler.headers.get("Upgrade", "").lower()
    connection = handler.headers.get("Connection", "").lower()
    return upgrade == "websocket" and "upgrade" in connection


def accept_key(client_key):
    digest = hashlib.sha1((client_key + _GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


class WebSocket:
    def __init__(self, sock):
        self.sock = sock
        self._send_lock = threading.Lock()
        self.closed = False

    # -- low level -------------------------------------------------------
    def _recv_exact(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("socket closed")
            buf += chunk
        return bytes(buf)

    def _send_frame(self, opcode, payload=b""):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        header = bytearray()
        header.append(0x80 | opcode)
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header += struct.pack("!H", length)
        else:
            header.append(127)
            header += struct.pack("!Q", length)
        with self._send_lock:
            if self.closed:
                return
            try:
                self.sock.sendall(bytes(header) + payload)
            except OSError:
                self.closed = True

    # -- public ----------------------------------------------------------
    def send_text(self, message):
        if not isinstance(message, str):
            message = json.dumps(message)
        self._send_frame(0x1, message)

    def recv_message(self):
        """Return the next text message, or ``None`` once the peer closes."""
        while True:
            try:
                header = self._recv_exact(2)
            except (ConnectionError, OSError):
                return None
            b1, b2 = header[0], header[1]
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            try:
                if length == 126:
                    length = struct.unpack("!H", self._recv_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self._recv_exact(8))[0]
                mask = self._recv_exact(4) if masked else None
                payload = self._recv_exact(length) if length else b""
            except (ConnectionError, OSError):
                return None
            if mask:
                payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))

            if opcode == 0x8:  # close
                self._send_frame(0x8, b"")
                self.closed = True
                return None
            if opcode == 0x9:  # ping
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:  # pong
                continue
            if opcode in (0x1, 0x2):  # text / binary
                return payload.decode("utf-8", "replace")
            # continuation / unknown: ignore
            continue

    def close(self):
        if not self.closed:
            self._send_frame(0x8, b"")
        self.closed = True


def run_websocket(handler, on_message, on_open=None, on_close=None):
    """Perform the handshake and pump the message loop in the caller's thread."""
    key = handler.headers.get("Sec-WebSocket-Key")
    if not key:
        handler.send_error(400, "Missing Sec-WebSocket-Key")
        return

    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n"
    )
    handler.connection.sendall(response.encode("ascii"))
    handler.close_connection = True

    ws = WebSocket(handler.connection)
    if on_open:
        try:
            on_open(ws)
        except Exception:
            pass
    try:
        while True:
            message = ws.recv_message()
            if message is None:
                break
            try:
                on_message(ws, message)
            except Exception as exc:  # keep the socket alive on handler errors
                ws.send_text({"type": "error", "message": str(exc)})
    finally:
        ws.closed = True
        if on_close:
            try:
                on_close(ws)
            except Exception:
                pass
