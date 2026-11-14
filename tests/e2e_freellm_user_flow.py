#!/usr/bin/env python3
"""Reproduce the exact user flow: Claude Code via the FreeLLMAPI gateway.

Runs against a scratch server that shares the real data directory, then
cleans up the one temp session it creates. Steps mirror what the user did:

  1. Workspace -> Agent dropdown -> Claude Code (live CLI model discovery)
  2. Source menu -> Gateway / Proxy
  3. Connection dropdown -> "FreeLLMAPI · claude-code"
  4. Model menu -> click the connection-scoped model (z-ai/glm-5.3-flash)
  5. Verify the pick resolved to a proper label in the UI
  6. REST: create a project session, POST the model switch with the
     connection-scoped id (the exact call the workspace makes), verify
     success + persisted model, then switch back to the plain id and verify
  7. Delete the temp session (user data restored)

Usage: ./.venv/bin/python tests/e2e_freellm_user_flow.py --serve
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ws_client import WSClient, Rest  # noqa: E402

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_PORT = 9337


class Browser:
    def __init__(self, chrome=DEFAULT_CHROME):
        self.profile = tempfile.mkdtemp(prefix="dennis-freellm-e2e-")
        self.proc = subprocess.Popen([
            chrome, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={self.profile}", "--window-size=1600,1000",
            "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        targets = None
        for _ in range(80):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=5) as res:
                    targets = json.loads(res.read().decode())
                break
            except Exception:
                time.sleep(0.25)
        page = next(t for t in targets if t["type"] == "page")
        self.cdp = WSClient(page["webSocketDebuggerUrl"])
        self.cdp.cdp("Runtime.enable")
        self.cdp.cdp("Log.enable")
        self.cdp.cdp("Page.enable")

    def stop(self):
        self.proc.terminate()

    def ev(self, expr, timeout=30):
        return self.cdp.eval(expr, timeout=timeout)

    def navigate(self, base, settle=3.5):
        self.cdp.cdp("Page.navigate", {"url": base + "/"})
        for _ in range(80):
            if self.ev("!!document.getElementById('agent-workspace')"):
                break
            time.sleep(0.25)
        time.sleep(settle)

    def click_row_containing(self, host_id, needle):
        return self.ev(
            "(function(){var h=document.getElementById(%s);if(!h) return 'no-host';"
            "var rows=h.querySelectorAll('.ws-menu-item');"
            "for(var i=0;i<rows.length;i++){"
            "if(rows[i].innerText.indexOf(%s)!==-1){rows[i].click();return 'clicked:'+rows[i].innerText.trim();}}"
            "return 'not-found:'+h.innerText.slice(0,150);})()"
            % (json.dumps(host_id), json.dumps(needle)))

    def wait_and_click(self, open_expr, host_id, needle, timeout=30, poll=0.6):
        """Open a menu, then poll until the row appears and click it."""
        deadline = time.time() + timeout
        last = "never-tried"
        while time.time() < deadline:
            self.ev(open_expr)
            time.sleep(poll)
            last = self.click_row_containing(host_id, needle)
            if str(last).startswith("clicked"):
                return last
        return last


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", default=None,
                        help="fixed port; default picks a free port so two "
                             "runs can never race on the same server")
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    args = parser.parse_args()

    server = None
    if args.serve:
        if args.port:
            port = args.port
        else:
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = str(probe.getsockname()[1])
            probe.close()
        server = subprocess.Popen(
            [sys.executable, os.path.join(PROJ, "app.py"), port],
            cwd=PROJ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, DENNIS_NO_DENMA="1"))
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                urllib.request.urlopen("http://127.0.0.1:%s/api/auth/session" % port, timeout=2)
                break
            except Exception:
                time.sleep(0.5)
    base = "http://127.0.0.1:%s" % port
    failures = []

    def check(name, ok, detail=""):
        print(("  [ok] " if ok else "  [FAIL] ") + name + (" -- " + detail if detail and not ok else ""))
        if not ok:
            failures.append(name)

    rest = Rest(base)
    browser = Browser(args.chrome)
    temp_session = None
    try:
        # Create an isolated session FIRST so the workspace auto-opens this
        # one, never the user's newest real session. A model switch restarts
        # the CLI, so the test must never touch real work.
        session = rest.api("/api/project-sessions", method="POST", body={
            "agent_id": "claude-code",
            "project": "agent-skill-builder",
            "title": "freellm-e2e-temp",
        })
        temp_session = session["session"]["id"]
        print("        temp session:", temp_session)
        browser.navigate(base)

        # 1. Select the Claude Code agent (live CLI model discovery runs).
        browser.ev("window.switchMainView('workspace')")
        time.sleep(0.5)
        clicked = browser.wait_and_click(
            "document.getElementById('ws-agent-btn').click()",
            "ws-agent-options", "Claude Code", timeout=45)
        check("agent dropdown: Claude Code clickable", str(clicked).startswith("clicked"), str(clicked))
        # Model discovery for claude-code drives a short-lived PTY picker; wait.
        deadline = time.time() + 60
        label = ""
        while time.time() < deadline:
            label = browser.ev("document.getElementById('ws-agent-label').textContent") or ""
            if "Claude Code" in label:
                break
            time.sleep(0.5)
        check("agent selected", "Claude Code" in str(label), str(label))
        time.sleep(6)  # give the CLI model list time to load

        # 2. The workspace has exactly the two selectors: Agent + Model.
        check("no Source dropdown (flattened UI)",
              not browser.ev("!!document.getElementById('ws-source-btn')"))
        check("no Provider dropdown (flattened UI)",
              not browser.ev("!!document.getElementById('ws-provider-btn')"))

        # Make sure the workspace is on the isolated temp session before any
        # model is picked (accept the restart confirmation if one appears).
        browser.ev("window.confirm = function(){return true;};")
        opened = browser.ev(
            "(function(){var row=document.querySelector('#ws-sessions-list "
            "[data-session-id=%s]');if(!row) return 'no-row';row.click();"
            "return 'clicked';})()" % json.dumps(temp_session))
        check("temp session row opens", opened == "clicked", str(opened))
        time.sleep(1.0)
        active_session = browser.ev(
            "window.Workspace && window.Workspace._state ? "
            "window.Workspace._state.sessionId : null")
        check("workspace is on the temp session",
              active_session == temp_session, str(active_session))

        # 3. Model dropdown: one merged list, grouped by source category.
        # The configured FreeLLMAPI model is read dynamically so the flow
        # tracks whatever endpoint the user currently has saved.
        fl = rest.api("/api/freellmapi?agent_id=claude-code")
        configured_model = ((fl.get("settings") or {}).get("model") or "").strip()
        check("a FreeLLMAPI model is configured", bool(configured_model),
              json.dumps(fl)[:150])
        conn_model_id = "conn:freellmapi:claude-code:" + configured_model
        deadline = time.time() + 60
        menu_text = ""
        while time.time() < deadline:
            browser.ev("document.getElementById('ws-model-btn').click()")
            time.sleep(0.5)
            menu_text = browser.ev(
                "(function(){var h=document.getElementById('ws-model-options');"
                "return h ? h.innerText : '';})()") or ""
            if "VIA FREELLMAPI" in str(menu_text).upper():
                break
        check("model menu lists the Via FreeLLMAPI section",
              "VIA FREELLMAPI" in str(menu_text).upper(), str(menu_text)[:200])
        deadline = time.time() + 20
        clicked = "not-found:never-appeared"
        while time.time() < deadline:
            clicked = browser.ev(
                "(function(){var h=document.getElementById('ws-model-options');"
                "if(!h) return 'no-host';"
                "var rows=h.querySelectorAll('.ws-menu-item');"
                "for(var i=0;i<rows.length;i++){"
                "if(rows[i].innerText.indexOf(%s)!==-1){rows[i].click();"
                "return 'clicked:'+rows[i].innerText.trim();}}"
                "return 'not-found:'+h.innerText.slice(0,160);})()" % json.dumps(configured_model))
            if str(clicked).startswith("clicked"):
                break
            time.sleep(0.5)
        check("saved FreeLLMAPI model row clickable", str(clicked).startswith("clicked"), str(clicked))
        time.sleep(0.5)

        # 4. The pick must resolve to the model name (no raw conn: id shown).
        deadline = time.time() + 15
        model_label = ""
        while time.time() < deadline:
            model_label = browser.ev("document.getElementById('ws-model-label').textContent") or ""
            if configured_model in str(model_label):
                break
            time.sleep(0.4)
        check("model label resolves the FreeLLMAPI model",
              configured_model in str(model_label), str(model_label))

        # 5. The exact backend call the workspace makes on the temp session.
        # A running CLI returns 409 with a warning; the workspace asks the
        # user, then retries with confirm:true. Mirror that here.
        def switch_model(model_id):
            try:
                return rest.api("/api/project-sessions/%s/model" % temp_session,
                                method="POST", body={"model_id": model_id})
            except urllib.error.HTTPError as exc:
                payload = json.loads(exc.read().decode() or "{}")
                if exc.code == 409 and payload.get("warning"):
                    return rest.api("/api/project-sessions/%s/model" % temp_session,
                                    method="POST",
                                    body={"model_id": model_id, "confirm": True})
                raise

        switch = switch_model(conn_model_id)
        check("model switch (connection id) succeeds",
              switch.get("success") is True, json.dumps(switch)[:200])
        stored = rest.api("/api/project-sessions/" + temp_session)
        check("session store keeps the model",
              stored.get("session", {}).get("model_id") == conn_model_id,
              json.dumps(stored.get("session", {}))[:160])
        # The plain id keeps working too (legacy path).
        switch2 = switch_model(configured_model)
        check("model switch (plain id) still succeeds",
              switch2.get("success") is True, json.dumps(switch2)[:200])
    except Exception as exc:
        check("flow completed without exception", False, repr(exc))
    finally:
        if temp_session:
            try:
                rest.api("/api/project-sessions/" + temp_session, method="DELETE")
                print("        temp session deleted (user data restored)")
            except Exception:
                pass
        browser.stop()
        if server is not None:
            server.terminate()

    print()
    if failures:
        print("FAILED checks: %d -> %s" % (len(failures), failures))
        return 1
    print("FULL USER FLOW PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
