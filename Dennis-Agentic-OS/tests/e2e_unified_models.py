#!/usr/bin/env python3
"""Prove the merged Model dropdown lists an explicit gateway's models.

Starts a mock Anthropic-compatible upstream, registers it as a Gateway
connection, discovers its models, then drives the real workspace in Chrome:

  1. Agent + Model only (no Source/Provider dropdowns)
  2. select Claude Code
  3. the Model menu shows "VIA FREELLMAPI" and "VIA GATEWAY · Mock Anthro GW"
  4. the gateway's discovered models are listed with context/capability chips
  5. picking one stores its connection-scoped id and the label shows the route
  6. the backend switch succeeds on an isolated temp session

Usage: ./.venv/bin/python tests/e2e_unified_models.py --serve

The temp session and temp gateway connection are always removed.
"""

import argparse
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ws_client import Rest  # noqa: E402
from e2e_freellm_user_flow import Browser, DEFAULT_CHROME  # noqa: E402

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MockAnthropic(http.server.BaseHTTPRequestHandler):
    """Minimal Anthropic-compatible model listing + health probe."""

    def log_message(self, *args):
        pass

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/v1/models":
            return self._json(200, {"data": [
                {
                    "id": "mock-anthro-1",
                    "display_name": "Mock Anthropic One",
                    "context_length": 200000,
                    "tool_call": True,
                    "architecture": {"modality": "text+image->text",
                                     "input_modalities": ["text", "image"]},
                },
                {
                    "id": "mock-anthro-2",
                    "display_name": "Mock Anthropic Two",
                    "context_length": 128000,
                    "tool_call": False,
                },
            ]})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0].endswith("/v1/messages"):
            return self._json(200, {
                "id": "msg_mock", "type": "message", "role": "assistant",
                "model": "mock-anthro-1",
                "content": [{"type": "text", "text": "OK"}],
                "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1},
            })
        return self._json(404, {"error": "not found"})


def free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", default=None)
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    args = parser.parse_args()

    mock = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockAnthropic)
    mock_port = mock.server_address[1]
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    mock_base = "http://127.0.0.1:%d" % mock_port
    print("        mock upstream:", mock_base)

    server = None
    if args.serve:
        port = args.port or str(free_port())
        server = subprocess.Popen(
            [sys.executable, os.path.join(PROJ, "app.py"), port],
            cwd=PROJ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, DENNIS_NO_DENMA="1"))
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                urllib.request.urlopen(
                    "http://127.0.0.1:%s/api/auth/session" % port, timeout=2)
                break
            except Exception:
                time.sleep(0.5)
    port = args.port or str(port)
    base = "http://127.0.0.1:%s" % port
    failures = []

    def check(name, ok, detail=""):
        print(("  [ok] " if ok else "  [FAIL] ") + name +
              (" -- " + detail if detail and not ok else ""))
        if not ok:
            failures.append(name)

    rest = Rest(base)
    browser = Browser(args.chrome)
    temp_session = None
    connection_id = None
    try:
        # Isolated session first: never touch the user's real newest session.
        session = rest.api("/api/project-sessions", method="POST", body={
            "agent_id": "claude-code",
            "project": "agent-skill-builder",
            "title": "unified-models-e2e-temp",
        })
        temp_session = session["session"]["id"]
        print("        temp session:", temp_session)

        # Register + discover the mock gateway.
        created = rest.api("/api/connections", method="POST", body={
            "type": "gateway",
            "name": "Mock Anthro GW",
            "provider_id": "mock_anthropic",
            "protocol": "anthropic",
            "base_url": mock_base,
            "api_key": "mock-key",
            "pricing": "free",
        })
        connection_id = created["connection"]["id"]
        print("        temp gateway:", connection_id)
        discovery = rest.api("/api/connections/%s/discover" % connection_id,
                             method="POST", body={})
        discovered = [m["native_id"] for m in discovery.get("models") or []]
        check("gateway discovery returns its models",
              discovered == ["mock-anthro-1", "mock-anthro-2"], str(discovered))

        models_payload = rest.api("/api/agents/claude-code/models")
        sources = models_payload.get("connection_sources") or {}
        gateway_ids = [c["id"] for c in sources.get("gateway") or []]
        check("gateway is offered for Claude Code", connection_id in gateway_ids,
              str(gateway_ids))
        conn_models = [m["id"] for m in models_payload.get("connection_models") or []
                       if m.get("connection_id") == connection_id]
        check("gateway models reach the workspace payload",
              len(conn_models) == 2, str(conn_models))

        browser.navigate(base)
        browser.ev("window.switchMainView('workspace')")
        time.sleep(0.5)
        clicked = browser.wait_and_click(
            "document.getElementById('ws-agent-btn').click()",
            "ws-agent-options", "Claude Code", timeout=45)
        check("agent dropdown: Claude Code clickable",
              str(clicked).startswith("clicked"), str(clicked))
        deadline = time.time() + 60
        label = ""
        while time.time() < deadline:
            label = browser.ev("document.getElementById('ws-agent-label').textContent") or ""
            if "Claude Code" in label:
                break
            time.sleep(0.5)
        check("agent selected", "Claude Code" in str(label), str(label))

        browser.ev("window.confirm = function(){return true;};")
        # The session list loads asynchronously after the agent is selected.
        deadline = time.time() + 25
        opened = "no-row"
        while time.time() < deadline:
            opened = browser.ev(
                "(function(){var row=document.querySelector('#ws-sessions-list "
                "[data-session-id=%s]');if(!row) return 'no-row';row.click();"
                "return 'clicked';})()" % json.dumps(temp_session))
            if opened == "clicked":
                break
            time.sleep(0.5)
        check("temp session row opens", opened == "clicked", str(opened))
        time.sleep(0.8)

        check("no Source/Provider dropdowns",
              not browser.ev("!!document.getElementById('ws-source-btn')") and
              not browser.ev("!!document.getElementById('ws-provider-btn')"))

        deadline = time.time() + 60
        menu_text = ""
        while time.time() < deadline:
            browser.ev("document.getElementById('ws-model-btn').click()")
            time.sleep(0.5)
            menu_text = browser.ev(
                "(function(){var h=document.getElementById('ws-model-options');"
                "return h ? h.innerText : '';})()") or ""
            if "VIA GATEWAY" in str(menu_text).upper():
                break
        text = str(menu_text)
        check("FreeLLMAPI section still present",
              "VIA FREELLMAPI" in text.upper(), text[:200])
        check("gateway section labelled with its name",
              "VIA GATEWAY" in text.upper() and "MOCK ANTHRO GW" in text.upper(),
              text[:220])
        check("gateway models listed",
              "Mock Anthropic One" in text and "Mock Anthropic Two" in text,
              text[:220])
        # Rows show the human display name; the native id stays available as
        # the row tooltip (never silently rewritten).
        native_tooltips = browser.ev(
            "(function(){var h=document.getElementById('ws-model-options');"
            "if(!h) return [];var out=[];var rows=h.querySelectorAll('.ws-menu-item');"
            "for(var i=0;i<rows.length;i++) out.push(rows[i].title||'');"
            "return out;})()") or []
        check("gateway native ids kept in row tooltips",
              any("mock-anthro-1" in str(t) for t in native_tooltips) and
              any("mock-anthro-2" in str(t) for t in native_tooltips),
              str(native_tooltips)[:220])
        check("gateway model chips (ctx/tools/vision)",
              "ctx 200K" in text and "tools" in text and "vision" in text,
              text[:260])

        clicked = browser.ev(
            "(function(){var h=document.getElementById('ws-model-options');"
            "if(!h) return 'no-host';var rows=h.querySelectorAll('.ws-menu-item');"
            "for(var i=0;i<rows.length;i++){"
            "if(rows[i].innerText.indexOf('Mock Anthropic One')!==-1){rows[i].click();"
            "return 'clicked:'+rows[i].innerText.trim();}}"
            "return 'not-found:'+h.innerText.slice(0,160);})()")
        check("gateway model row clickable",
              str(clicked).startswith("clicked"), str(clicked))
        deadline = time.time() + 15
        model_label = ""
        while time.time() < deadline:
            model_label = browser.ev(
                "document.getElementById('ws-model-label').textContent") or ""
            if "Mock Anthropic One" in str(model_label):
                break
            time.sleep(0.4)
        check("label shows the gateway route",
              "Mock Anthro GW" in str(model_label) and
              "Mock Anthropic One" in str(model_label), str(model_label))

        conn_model_id = "conn:%s:mock-anthro-1" % connection_id
        try:
            switch = rest.api("/api/project-sessions/%s/model" % temp_session,
                              method="POST", body={"model_id": conn_model_id})
        except urllib.error.HTTPError as exc:
            payload = json.loads(exc.read().decode() or "{}")
            if exc.code == 409 and payload.get("warning"):
                switch = rest.api("/api/project-sessions/%s/model" % temp_session,
                                  method="POST",
                                  body={"model_id": conn_model_id, "confirm": True})
            else:
                raise
        check("backend switch to the gateway model succeeds",
              switch.get("success") is True, json.dumps(switch)[:200])
        saved = rest.api("/api/project-sessions/" + temp_session)
        check("session stores the gateway model id",
              saved.get("session", {}).get("model_id") == conn_model_id,
              json.dumps(saved.get("session", {}))[:160])
    except Exception as exc:
        check("flow completed without exception", False, repr(exc))
    finally:
        if temp_session:
            try:
                rest.api("/api/project-sessions/" + temp_session, method="DELETE")
                print("        temp session deleted")
            except Exception:
                pass
        if connection_id:
            try:
                rest.api("/api/connections/" + connection_id, method="DELETE")
                print("        temp gateway deleted")
            except Exception:
                pass
        browser.stop()
        if server is not None:
            server.terminate()
        mock.shutdown()
        mock.server_close()

    print()
    if failures:
        print("FAILED checks: %d -> %s" % (len(failures), failures))
        return 1
    print("UNIFIED MODEL DROPDOWN PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
