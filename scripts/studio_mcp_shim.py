#!/usr/bin/env python3
"""Expose DENNIS Studio tools to MCP clients over stdio.

This is a thin, dependency-free bridge. It does not run generation itself and
never reads provider credentials: every call goes through the running OS API,
which enforces the owner token, validation and the approval queue.

Configuration (environment):

    DENNIS_API_URL   Base URL of the running OS (default http://127.0.0.1:8080)
    DENNIS_TOKEN     Owner token; when omitted, data/auth.json is read locally

Example MCP client entry:

    {
      "command": "python3",
      "args": ["/absolute/path/to/scripts/studio_mcp_shim.py"],
      "env": {"DENNIS_API_URL": "http://127.0.0.1:8080"}
    }

Mutating tools (submit/cancel) return an ``approval_required`` payload. The
owner approves or rejects it in Studio → Activity / Setup or via
``POST /api/studio/approvals/<id>``. This shim deliberately cannot approve its
own spending actions.

Run:  python3 scripts/studio_mcp_shim.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend import studio_tools  # noqa: E402  (definitions only; no stores)

DEFAULT_API = "http://127.0.0.1:8080"
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
MAX_BODY = 4 * 1024 * 1024


class ShimError(Exception):
    def __init__(self, code, message, status=502):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def api_base():
    base = (os.environ.get("DENNIS_API_URL") or DEFAULT_API).strip().rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ShimError("invalid_api_url", "DENNIS_API_URL must be an http(s) URL.")
    return base


def owner_token():
    token = (os.environ.get("DENNIS_TOKEN") or "").strip()
    if token:
        return token
    path = os.environ.get("DENNIS_TOKEN_FILE") or os.path.join(ROOT, "data", "auth.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return ""
    token = data.get("token") if isinstance(data, dict) else None
    return token.strip() if isinstance(token, str) else ""


def http_json(method, path, body=None):
    token = owner_token()
    if not token:
        raise ShimError(
            "missing_token",
            "No owner token. Set DENNIS_TOKEN or run the OS so data/auth.json exists.",
            401,
        )
    data = None
    headers = {"X-Dennis-Token": token, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(api_base() + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            raw = response.read(MAX_BODY)
    except urllib.error.HTTPError as exc:
        raw = b""
        try:
            raw = exc.read(MAX_BODY)
        except Exception:
            pass
        finally:
            try:
                exc.close()
            except Exception:
                pass
        payload = {}
        try:
            payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError:
            payload = {}
        message = payload.get("error") or ("HTTP %s" % exc.code)
        code = payload.get("code") or "api_error"
        if exc.code == 401:
            message = "The OS rejected the owner token."
        raise ShimError(code, str(message), exc.code)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ShimError("api_unreachable", "Could not reach the OS API: %s" % exc, 502)
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        raise ShimError("invalid_response", "The OS API returned invalid JSON.", 502)


def tool_definitions():
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
        }
        for name, spec in studio_tools.TOOLS.items()
    ]


def call_tool(name, arguments):
    if name not in studio_tools.TOOLS:
        raise ShimError("unknown_tool", "Unknown Studio tool: %s" % name, 404)
    return http_json("POST", "/api/studio/tools/call", {"name": name, "arguments": arguments or {}})


def result_payload(payload):
    """Return MCP content plus the raw Studio API payload."""
    status = payload.get("status")
    if status == "approval_required":
        approval = payload.get("approval") or {}
        text = (
            "Approval required. The owner must approve %s in Studio before it runs. "
            "Approval id: %s" % (approval.get("tool") or "this action", approval.get("id") or "unknown")
        )
        return {"content": [{"type": "text", "text": text}], "structuredContent": payload}
    if status == "ok":
        result = payload.get("result")
        return {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "structuredContent": {"status": "ok", "result": result},
        }
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
    }


def error_result(exc):
    return {
        "content": [{"type": "text", "text": exc.message}],
        "structuredContent": {"status": "error", "error": {"code": exc.code, "message": exc.message}},
        "isError": True,
    }


def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(request_id, result):
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def fail(request_id, code, message):
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def handle(message):
    method = message.get("method")
    request_id = message.get("id")
    is_notification = "id" not in message
    if method == "initialize":
        params = message.get("params") or {}
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL_VERSION
        reply(request_id, {
            "protocolVersion": version,
            "serverInfo": {"name": "dennis-studio", "version": "1.0.0"},
            "capabilities": {"tools": {"listChanged": False}},
        })
        return
    if method in ("notifications/initialized", "notifications/cancelled"):
        return
    if method == "ping":
        if not is_notification:
            reply(request_id, {})
        return
    if method == "tools/list":
        reply(request_id, {"tools": tool_definitions()})
        return
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            if not isinstance(arguments, dict):
                raise ShimError("invalid_arguments", "Tool arguments must be an object.", 400)
            payload = call_tool(name, arguments)
            reply(request_id, result_payload(payload))
        except ShimError as exc:
            reply(request_id, error_result(exc))
        except Exception as exc:  # never leak a traceback to the client
            reply(request_id, error_result(
                ShimError("internal_error", "Studio shim failed: %s" % exc, 500)
            ))
        return
    if is_notification:
        return
    fail(request_id, -32601, "Method not found: %s" % method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if isinstance(message, list):
            for item in message:
                if isinstance(item, dict):
                    handle(item)
            continue
        if isinstance(message, dict):
            handle(message)
        else:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}})


if __name__ == "__main__":
    main()
