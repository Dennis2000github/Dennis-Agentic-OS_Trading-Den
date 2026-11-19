"""Minimal, reusable MCP stdio client for Studio providers.

The MCP server is a separate local process speaking newline-delimited JSON-RPC
2.0. This client implements only what a tool consumer needs: ``initialize``,
``tools/list`` and ``tools/call``, with per-request timeouts and a bounded,
redacted stderr buffer.

No shell is involved (``Popen`` receives an argument list), the command is only
spawned after the owner saves an explicit MCP configuration, and environment
values are stored in the same private ``data/`` tree as other credentials.

The official Higgsfield API remains the default provider. MCP is an optional
connector for locally installed MCP servers, including the community
``higgsfield-mcp-unified`` project (MIT).
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
import time

from . import config
from . import studio_contracts as contracts

STUDIO_DIR = os.path.join(config.DATA_DIR, "studio")
CONFIG_FILE = os.path.join(STUDIO_DIR, "mcp.json")
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "dennis-studio", "version": "1.0"}
MAX_MESSAGE = 5 * 1024 * 1024
MAX_STDERR_LINES = 40


class McpError(Exception):
    def __init__(self, message, code="mcp_error", retryable=False):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


def _ensure_dir():
    os.makedirs(STUDIO_DIR, mode=0o700, exist_ok=True)


def read_config():
    if not os.path.isfile(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise McpError("MCP configuration cannot be read: %s" % exc)
    if not isinstance(data, dict):
        raise McpError("MCP configuration has an unsupported format.")
    return data


def public_config():
    try:
        data = read_config()
    except McpError as exc:
        return {"configured": False, "error": str(exc)}
    configured = bool(data and data.get("command"))
    return {
        "configured": configured,
        "command": (data or {}).get("command") or "",
        "args": list((data or {}).get("args") or []),
        "env_keys": sorted(((data or {}).get("env") or {}).keys()),
        "updated_at": (data or {}).get("updated_at"),
    }


def save_config(command, args, env):
    if not isinstance(command, str) or not command.strip():
        raise McpError("An MCP server command is required.")
    command = command.strip()
    if any(ch in command for ch in ("\n", "\r", "\x00")):
        raise McpError("The MCP command contains an invalid character.")
    if not isinstance(args, list) or len(args) > 40:
        raise McpError("MCP arguments must be a list of at most 40 strings.")
    clean_args = []
    for arg in args:
        if not isinstance(arg, str) or len(arg) > 500 or "\x00" in arg:
            raise McpError("Each MCP argument must be a short string.")
        clean_args.append(arg)
    clean_env = {}
    if env is not None:
        if not isinstance(env, dict):
            raise McpError("MCP environment variables must be an object.")
        for key, value in env.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise McpError("MCP environment keys and values must be strings.")
            if not key or len(key) > 128 or any(ch in key for ch in ("=", "\x00", "\n")):
                raise McpError("Invalid MCP environment variable name.")
            if "\x00" in value or len(value) > 8192:
                raise McpError("Invalid MCP environment value.")
            clean_env[key] = value

    _ensure_dir()
    payload = {
        "version": 1,
        "command": command,
        "args": clean_args,
        "env": clean_env,
        "updated_at": config.now_iso(),
    }
    fd, tmp = tempfile.mkstemp(dir=STUDIO_DIR, prefix=".mcp-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, CONFIG_FILE)
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return public_config()


def clear_config():
    try:
        os.remove(CONFIG_FILE)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise McpError("MCP configuration could not be removed: %s" % exc)
    return public_config()


class McpClient:
    """One live stdio session. Not thread-safe; serialize calls with a lock."""

    def __init__(self, command, args=None, env=None, cwd=None, timeout=60):
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.timeout = timeout
        self._proc = None
        self._next_id = 0
        self._pending = {}
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._stderr = []
        self._stderr_lock = threading.Lock()
        self._closed = False
        self._reader = None
        self._stderr_reader = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self._proc is not None:
            return self
        env = os.environ.copy()
        # A predictable base, then owner-supplied values. Never log them.
        env.update(self.env)
        env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            self._proc = subprocess.Popen(
                [self.command] + self.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=env,
                bufsize=0,
            )
        except (OSError, ValueError) as exc:
            raise McpError("Could not start the MCP server: %s" % exc, "mcp_start_failed")
        self._reader = threading.Thread(target=self._read_stdout, daemon=True, name="mcp-stdout")
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True, name="mcp-stderr")
        self._stderr_reader.start()
        return self

    def close(self):
        self._closed = True
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # -- streams -----------------------------------------------------------
    def _read_stdout(self):
        proc = self._proc
        if not proc or not proc.stdout:
            return
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            if len(line) > MAX_MESSAGE:
                continue
            try:
                message = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if "id" in message and ("result" in message or "error" in message):
                with self._lock:
                    waiter = self._pending.pop(message["id"], None)
                if waiter:
                    waiter.put(message)
            # Notifications (logs) are intentionally ignored; stderr is kept
            # for diagnostics instead of interpolating arbitrary log lines.
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for waiter in pending:
            waiter.put({"error": {"code": -32000, "message": "MCP server exited."}})

    def _read_stderr(self):
        proc = self._proc
        if not proc or not proc.stderr:
            return
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            text = contracts.redact_text(line.decode("utf-8", "replace").strip())
            if not text:
                continue
            with self._stderr_lock:
                self._stderr.append(text[:500])
                if len(self._stderr) > MAX_STDERR_LINES:
                    self._stderr.pop(0)

    def stderr_tail(self, limit=5):
        with self._stderr_lock:
            return list(self._stderr[-limit:])

    # -- JSON-RPC ----------------------------------------------------------
    def _send(self, message):
        proc = self._proc
        if not proc or not proc.stdin or proc.poll() is not None:
            raise McpError("The MCP server is not running.", "mcp_not_running", retryable=True)
        raw = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                proc.stdin.write(raw)
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise McpError("The MCP server closed its input: %s" % exc, "mcp_not_running", retryable=True)

    def request(self, method, params=None, timeout=None):
        if self._closed:
            raise McpError("The MCP session is closed.")
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            waiter = queue.Queue(maxsize=1)
            self._pending[request_id] = waiter
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        try:
            response = waiter.get(timeout=timeout or self.timeout)
        except queue.Empty:
            with self._lock:
                self._pending.pop(request_id, None)
            raise McpError("The MCP server did not respond in time.", "mcp_timeout", retryable=True)
        if "error" in response:
            error = response.get("error") or {}
            raise McpError(
                contracts.redact_text(str(error.get("message") or "MCP request failed.")),
                "mcp_request_failed",
            )
        return response.get("result") or {}

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    # -- MCP operations ----------------------------------------------------
    def initialize(self):
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            timeout=min(self.timeout, 30),
        )
        self.notify("notifications/initialized")
        return result

    def list_tools(self):
        tools = []
        cursor = None
        for _ in range(20):
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params)
            page = result.get("tools")
            if isinstance(page, list):
                tools.extend(x for x in page if isinstance(x, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name, arguments=None, timeout=None):
        if not isinstance(name, str) or not name.strip():
            raise McpError("A tool name is required.")
        result = self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout or self.timeout,
        )
        if result.get("isError"):
            raise McpError(self._content_text(result) or "The MCP tool reported an error.")
        return result

    @staticmethod
    def _content_text(result):
        content = result.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            if parts:
                return "\n".join(parts)
        if isinstance(result.get("structuredContent"), dict):
            return json.dumps(result["structuredContent"])
        return ""

    @classmethod
    def payload(cls, result):
        """Best-effort JSON payload from a tools/call result."""
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        text = cls._content_text(result)
        if text:
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    return data
                if isinstance(data, list):
                    return {"items": data}
            except ValueError:
                return {"text": text[:4000]}
        return {}


def client_from_config(timeout=60):
    data = read_config()
    if not data or not data.get("command"):
        raise McpError("MCP is not configured.", "mcp_not_configured")
    return McpClient(
        data["command"],
        args=data.get("args") or [],
        env=data.get("env") or {},
        timeout=timeout,
    )


def probe(timeout=30):
    """Connect, initialize and list tools. Used by the Setup panel."""
    started = time.time()
    client = client_from_config(timeout=timeout)
    try:
        with client:
            info = client.initialize()
            tools = client.list_tools()
    except McpError:
        raise
    return {
        "ok": True,
        "server": info.get("serverInfo") or {},
        "protocol_version": info.get("protocolVersion"),
        "tools": [
            {
                "name": tool.get("name"),
                "description": (tool.get("description") or "")[:200],
            }
            for tool in tools
        ],
        "elapsed_ms": int((time.time() - started) * 1000),
        "stderr_tail": client.stderr_tail(),
    }
