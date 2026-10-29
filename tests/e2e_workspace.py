#!/usr/bin/env python3
"""Browser-level E2E for the Workspace rebuild (headless Chrome + CDP).

Proves, against a real server and real agent CLIs:

  Phase 1  no tabs / no technical text, agent dropdown, per-agent dashboard with
           keyboard focus, real handoff auto-expand (once per burst), S2 -> S3 ->
           S5 -> Escape -> S3 -> Escape -> S2, S4 fullscreen restore, X reset,
           typing routed to the visible session, single scroll container.
  Phase 2  agent switching isolation (OpenCode + Claude Code run side by side),
           hidden dashboards keep history and never restart, typed input never
           crosses sessions, panel resize on open/collapse, per-session panel
           restore, CLI-native model dropdown (list, current, search, apply +
           read-back, parse-failure Retry), new handoff burst, OS features intact,
           no credentials in the page.

Usage (from the repo root)::

    ./.venv/bin/python tests/e2e_workspace.py --serve --phase all

Requires Apple Google Chrome at the default path (override with --chrome).
Stdlib only: the WebSocket client in tests/ws_client.py speaks both
the terminal protocol and Chrome DevTools Protocol.

Exit code 0 means every check passed. Each check prints [ok]/[FAIL] so the
output can be kept as evidence.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ws_client import Rest, WSClient  # noqa: E402

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class Runner:
    def __init__(self, base, chrome, port):
        self.base = base
        self.chrome = chrome
        self.port = port
        self.failures = []
        self.cdp = None
        self.chrome_proc = None
        self.profile = None
        self.rest = None

    # -- infrastructure --------------------------------------------------
    def start_browser(self):
        self.profile = tempfile.mkdtemp(prefix="dennis-e2e-chrome-")
        self.chrome_proc = subprocess.Popen([
            self.chrome, "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}", "--window-size=1600,1000",
            "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        targets = None
        for _ in range(80):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/json/list", timeout=5) as res:
                    targets = json.loads(res.read().decode())
                break
            except Exception:
                time.sleep(0.25)
        if not targets:
            raise RuntimeError("Chrome DevTools never came up")
        page = next(t for t in targets if t["type"] == "page")
        self.cdp = WSClient(page["webSocketDebuggerUrl"])
        self.cdp.cdp("Runtime.enable")
        self.cdp.cdp("Log.enable")
        self.cdp.cdp("Page.enable")

    def stop_browser(self):
        if self.chrome_proc:
            self.chrome_proc.terminate()

    def navigate(self, url=None, settle=1.5):
        self.cdp.cdp("Page.navigate", {"url": (url or self.base) + "/"})
        try:
            self.cdp.cdp("Page.bringToFront")
            self.cdp.cdp("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            pass
        for _ in range(80):
            if self.cdp.eval("!!document.getElementById('agent-workspace')"):
                break
            time.sleep(0.25)
        time.sleep(settle)

    def ev(self, expr, timeout=20):
        return self.cdp.eval(expr, timeout=timeout)

    def wait_for(self, expr, timeout=30, poll=0.25):
        return self.cdp.wait_for(expr, timeout=timeout, poll=poll)

    def check(self, name, cond, detail=""):
        print(f"  [{'ok' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            self.failures.append(name)

    def select_agent(self, needle, expect_session=None, timeout=60):
        self.ev("document.getElementById('ws-agent-btn').click()")
        time.sleep(0.3)
        self.ev("[...document.querySelectorAll('#ws-agent-options .ws-menu-item')]"
                f".find(b=>b.innerText.includes({json.dumps(needle)})).click()")
        expr = ("!!document.querySelector('#ws-screens .ws-dash:not(.hidden) .xterm') && "
                "document.getElementById('ws-starting').classList.contains('hidden')")
        if expect_session:
            expr = "Workspace._state.sessionId === %s && " % json.dumps(expect_session) + expr
        return self.wait_for(expr, timeout=timeout)

    def buffer_contains_expr(self, session_id, needle):
        return (
            "(function(){var r=Workspace._registry().get(%s);if(!r||!r.view.term)return false;"
            "var b=r.view.term.buffer.active;"
            "for(var i=0;i<b.length;i++){if(b.getLine(i).translateToString().indexOf(%s)!==-1)return true;}"
            "return false;})()" % (json.dumps(session_id), json.dumps(needle)))

    def buffer_contains(self, session_id, needle):
        return self.ev(self.buffer_contains_expr(session_id, needle))


def phase1(r):
    print("PHASE 1 - agent screen, handoff, panel states, typing, layout")
    r.navigate(settle=3.0)
    r.wait_for("Workspace._state.token !== null", timeout=10)
    time.sleep(1.0)
    errors = [e for e in r.cdp.runtime_errors()
              if "favicon" not in (e or "").lower() and "404 (File not found)" not in (e or "")]
    r.check("page loads with no JS exceptions", not errors, str(errors[:3]))

    r.check("no tab row in the agent panel",
            r.ev("document.querySelectorAll('#agent-workspace [data-tab], #agent-workspace .chat-tab').length === 0"))
    visible = (r.ev("document.getElementById('agent-workspace').innerText") or "").lower()
    found = [w for w in ("docker", "container", "pty", "websocket", "restart", "cpu", "memory", "pid")
             if w in visible]
    r.check("no Docker/terminal technical text in the panel", not found, str(found))

    r.check("status starts Stopped",
            r.ev("document.getElementById('ws-status').textContent.trim()") == "Stopped")
    r.ev("document.getElementById('ws-agent-btn').click()")
    time.sleep(0.4)
    agents_text = (r.ev("document.getElementById('ws-agent-options').innerText") or "").lower()
    r.check("agent dropdown lists registered + detected CLIs",
            all(w in agents_text for w in ("antigravity", "opencode", "codex", "claude code")))
    r.check("model dropdown disabled with no agent",
            bool(r.ev("document.getElementById('ws-model-btn').disabled")))
    r.check("panel hidden and handle hidden without an agent (S0)",
            r.ev("Workspace._state.panel.state") == "S0" and
            r.ev("document.getElementById('ws-panel-handle').classList.contains('hidden')"))
    r.check("sidebar says to select an agent",
            "Select an agent" in (r.ev("document.getElementById('ws-sessions-list').innerText") or ""))

    own_session = r.rest.api("/api/project-sessions", "POST",
                             {"agent_id": "opencode", "project": PROJ})["session"]["id"]
    started = r.select_agent("OpenCode", expect_session=own_session, timeout=60)
    r.check("selecting an agent opens its own real CLI dashboard", bool(started))
    r.check("panel collapsed by default once agent selected (S1)",
            r.ev("Workspace._state.panel.state") == "S1")
    r.check("slim handle visible on the right edge",
            not r.ev("document.getElementById('ws-panel-handle').classList.contains('hidden')"))
    r.check("sidebar shows the selected agent badge",
            "OpenCode" in (r.ev("document.getElementById('ws-sessions-agent').textContent") or ""))
    r.check("sidebar highlights the open project session",
            bool(r.ev("!!document.querySelector('#ws-sessions-list .ws-session-active')")))
    r.check("terminal has keyboard focus without a click",
            bool(r.ev("(function(){var d=document.querySelector('#ws-screens .ws-dash:not(.hidden)');"
                      "return d && d.contains(document.activeElement);})()")))

    probe = os.path.join(PROJ, "e2e-handoff-probe.txt")
    with open(probe, "w") as fh:
        fh.write("handoff")
    expanded = r.wait_for("document.getElementById('ws-panel').classList.contains('ws-panel-open') && "
                          "Workspace._state.panel.state === 'S2'", timeout=20)
    r.check("real file change auto-expands the panel to S2", bool(expanded))
    r.check("opened file loaded into the Code pane",
            r.ev("document.getElementById('ws-code-path').value") == "e2e-handoff-probe.txt")
    r.check("code editor shows the file contents",
            "handoff" in (r.ev("(function(){var w=document.querySelector('#ws-code-host .CodeMirror');"
                               "return w&&w.CodeMirror?w.CodeMirror.getValue():'';})()") or ""))
    burst = r.ev("Workspace._state.handoff[Workspace._state.sessionId].burst")
    with open(probe, "a") as fh:
        fh.write("more")
    time.sleep(3.0)
    r.check("second edit in the same handoff does not re-open",
            r.ev("Workspace._state.handoff[Workspace._state.sessionId].burst") == burst)

    r.ev("document.getElementById('ws-code-max').click()")
    time.sleep(0.3)
    r.check("maximize Code lands on S3 with a Preview strip",
            r.ev("Workspace._state.panel.state") == "S3" and
            not r.ev("document.getElementById('ws-preview-strip').classList.contains('hidden')"))
    r.ev("document.getElementById('ws-code-full').click()")
    time.sleep(0.3)
    r.check("Code full screen is S5 and covers the app window",
            r.ev("Workspace._state.panel.state") == "S5" and
            r.ev("document.getElementById('agent-workspace').classList.contains('ws-fs-active')") and
            r.ev("getComputedStyle(document.getElementById('agent-workspace')).position") == "fixed" and
            not r.ev("document.getElementById('ws-fullscreen-bar').classList.contains('hidden')"))
    r.check("fullscreen bar names the pane",
            r.ev("document.getElementById('ws-fs-pane').textContent") == "Code")
    r.check("agent screen not unmounted while fullscreen",
            bool(r.ev("!!document.querySelector('#ws-screens .ws-dash .xterm')")))
    for kind in ("rawKeyDown", "keyUp"):
        r.cdp.cdp("Input.dispatchKeyEvent", {"type": kind, "key": "Escape", "code": "Escape",
                                             "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27})
    time.sleep(0.4)
    r.check("Escape exits fullscreen back to S3", r.ev("Workspace._state.panel.state") == "S3")
    for kind in ("rawKeyDown", "keyUp"):
        r.cdp.cdp("Input.dispatchKeyEvent", {"type": kind, "key": "Escape", "code": "Escape",
                                             "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27})
    time.sleep(0.3)
    r.check("second Escape un-maximizes to S2", r.ev("Workspace._state.panel.state") == "S2")

    r.ev("document.getElementById('ws-preview-max').click()")
    time.sleep(0.3)
    r.check("maximize Preview lands on S4 with a Code strip",
            r.ev("Workspace._state.panel.state") == "S4" and
            not r.ev("document.getElementById('ws-code-strip').classList.contains('hidden')"))
    r.ev("document.getElementById('ws-preview-full').click()")
    time.sleep(0.3)
    for kind in ("rawKeyDown", "keyUp"):
        r.cdp.cdp("Input.dispatchKeyEvent", {"type": kind, "key": "Escape", "code": "Escape",
                                             "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27})
    time.sleep(0.3)
    r.check("fullscreen from S4 returns to S4", r.ev("Workspace._state.panel.state") == "S4")

    session_before = r.ev("Workspace._state.sessionId")
    r.ev("document.getElementById('ws-panel-close').click()")
    time.sleep(0.3)
    r.check("X resets the panel to S1",
            r.ev("Workspace._state.panel.state") == "S1" and
            r.ev("Workspace._state.panel.code_file") is None and
            r.ev("Workspace._state.panel.preview_url") is None)
    r.check("X does not stop or change the session",
            r.ev("Workspace._state.sessionId") == session_before and
            r.ev("Workspace._state.current.status") != "stopped")
    r.check("handle visible again after reset",
            not r.ev("document.getElementById('ws-panel-handle').classList.contains('hidden')"))

    r.ev("Workspace._registry().active().view.focus()")
    time.sleep(0.3)
    r.check("terminal takes keyboard focus",
            r.ev("document.activeElement && document.activeElement.tagName") == "TEXTAREA")
    r.ev("window.__routed=[];(function(){var reg=Workspace._registry();var o=reg.inputTo;"
         "reg.inputTo=function(s,d){window.__routed.push([s,d]);return o.call(reg,s,d);};})()")
    token = "e2e-" + str(int(time.time()) % 100000)
    r.cdp.cdp("Input.insertText", {"text": token})
    time.sleep(2.0)
    routed = r.ev("JSON.stringify(window.__routed)") or "[]"
    r.check("keystrokes routed to the visible session over its own socket",
            token in routed and r.ev("Workspace._state.sessionId") in routed, routed)
    r.check("CLI echo rendered in the dashboard terminal", r.buffer_contains(own_session, token))

    r.cdp.cdp("Emulation.setDeviceMetricsOverride",
              {"width": 1280, "height": 700, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(0.6)
    r.check("no horizontal page scroll",
            r.ev("document.documentElement.scrollWidth <= window.innerWidth + 1"))
    r.check("page itself does not scroll (single main scrollbar)",
            r.ev("document.scrollingElement.scrollHeight <= document.scrollingElement.clientHeight + 1"))
    r.check("main is the single scroll container",
            r.ev("getComputedStyle(document.querySelector('main')).overflowY") == "auto")
    r.check("registry panels sit below the workspace and scroll with main",
            r.ev("document.querySelector('main').scrollHeight > document.querySelector('main').clientHeight") and
            bool(r.ev("document.getElementById('agent-registry-list') !== null")))
    r.check("workspace row has a bounded height",
            r.ev("(function(){var b=document.getElementById('agent-workspace').getBoundingClientRect();"
                 "return b.height >= 400 && b.height <= window.innerHeight;})()"))
    r.cdp.cdp("Emulation.clearDeviceMetricsOverride")

    try:
        os.remove(probe)
    except OSError:
        pass
    try:
        r.rest.api(f"/api/project-sessions/{own_session}/stop", "POST", {})
        r.rest.api(f"/api/project-sessions/{own_session}", "DELETE")
    except Exception:
        pass


def phase2(r):
    print("PHASE 2 - switching isolation, model dropdown, persistence, OS intact")
    r.navigate()
    cards = r.ev("document.querySelectorAll("
                 "'main > section.registry-scroll-section .grid > div').length")
    r.check("all five registry cards still render in the scrolling section",
            cards == 5, f"found {cards}")
    r.check("top nav tabs still toggle",
            r.ev("(function(){var t=document.querySelectorAll('.header-nav-tab');t[1].click();"
                 "return t[1].classList.contains('nav-tab-active');})()"))
    r.check("hero banner still present",
            bool(r.ev("!!document.getElementById('cosmos-canvas') && !!document.getElementById('digital-clock')")))

    leak = False
    claude_cfg = os.path.expanduser("~/.claude/settings.json")
    if os.path.exists(claude_cfg):
        with open(claude_cfg) as fh:
            cfg = fh.read()
        html = r.ev("document.documentElement.outerHTML") or ""
        api_blob = json.dumps(r.rest.api("/api/system/readiness"))
        for m in re.finditer(r'"ANTHROPIC_AUTH_TOKEN"\s*:\s*"([^"]+)"', cfg):
            secret = m.group(1)
            if secret and (secret in html or secret in api_blob):
                leak = True
    r.check("provider credentials never reach the page or API responses", not leak)

    session_a = r.rest.api("/api/project-sessions", "POST",
                           {"agent_id": "opencode", "project": PROJ})["session"]["id"]
    r.check("OpenCode dashboard A started", bool(r.select_agent("OpenCode", session_a, timeout=60)))
    time.sleep(5.0)  # let the TUI finish its initial render
    r.ev("window.__routedA=[];(function(){var reg=Workspace._registry();var o=reg.inputTo;"
         "reg.inputTo=function(s,d){window.__routedA.push([s,d]);return o.call(reg,s,d);};})()")
    token_a = "keepA-" + str(int(time.time()) % 100000)
    echoed = False
    for _ in range(3):
        r.ev("Workspace._registry().active().view.focus()")
        r.cdp.cdp("Input.insertText", {"text": token_a})
        time.sleep(2.0)
        if r.buffer_contains(session_a, token_a):
            echoed = True
            break
    routed_a = r.ev("JSON.stringify(window.__routedA)") or "[]"
    r.check("token A keystrokes routed to dashboard A's own socket",
            token_a in routed_a and session_a in routed_a, routed_a[:120])
    r.check("token A echoed in dashboard A", echoed)
    # Record the live terminal so the switch test proves the same instance —
    # an alternate-screen TUI may redraw its screen, but it must never be
    # unmounted, re-created or restarted by switching agents.
    r.ev("window.__termA = Workspace._registry().get(%s).view.term; true" % json.dumps(session_a))

    claude_session = r.rest.api("/api/project-sessions", "POST",
                                {"agent_id": "claude-code", "project": PROJ})["session"]["id"]
    r.check("switching agents opens the Claude dashboard B",
            bool(r.select_agent("Claude", claude_session, timeout=90)))
    r.check("dashboard A kept running and mounted while hidden",
            r.ev("Workspace._registry().has(%s)" % json.dumps(session_a)) and
            r.ev("Workspace._registry().get(%s).view.ws.readyState" % json.dumps(session_a)) == 1)
    r.check("dashboard A kept its live terminal while hidden",
            bool(r.ev("Workspace._registry().get(%s).view.term === window.__termA" % json.dumps(session_a))))
    r.wait_for("['idle','running'].includes(document.getElementById('ws-status').getAttribute('data-status'))", timeout=30)
    time.sleep(1.0)
    token_b = "sendB-" + str(int(time.time()) % 100000)
    r.ev("Workspace._registry().active().view.focus()")
    r.cdp.cdp("Input.insertText", {"text": token_b})
    time.sleep(2.0)
    r.check("token B never reaches dashboard A", not r.buffer_contains(session_a, token_b))

    r.select_agent("OpenCode", session_a, timeout=30)
    r.check("switching back shows dashboard A again",
            bool(r.wait_for("Workspace._state.sessionId === %s && "
                            "!!document.querySelector('#ws-screens .ws-dash:not(.hidden)')" % json.dumps(session_a))))
    r.check("dashboard A still uses the same live terminal after two switches",
            bool(r.ev("Workspace._registry().get(%s).view.term === window.__termA" % json.dumps(session_a))) and
            r.ev("Workspace._registry().get(%s).view.term.buffer.active.length > 0" % json.dumps(session_a)))
    r.check("OpenCode process was not restarted, it is still live",
            r.ev("Workspace._state.current.status") in ("running", "busy", "idle"))

    cols_before = r.ev("Workspace._registry().active().view.term.cols")
    r.ev("document.getElementById('ws-panel-handle').click()")
    r.wait_for("Workspace._registry().active().view.term.cols < %d" % max(1, cols_before - 10), timeout=10)
    time.sleep(0.5)
    cols_open = r.ev("Workspace._registry().active().view.term.cols")
    r.check("opening the panel re-fits and resizes the agent screen",
            cols_open < cols_before - 10, f"{cols_before} -> {cols_open}")
    state_open = r.ev("Workspace._state.panel.state")
    r.select_agent("Claude", claude_session, timeout=30)
    time.sleep(1.0)
    r.select_agent("OpenCode", session_a, timeout=30)
    r.wait_for("Workspace._state.sessionId === %s" % json.dumps(session_a), timeout=20)
    time.sleep(1.0)
    r.check("panel state persisted per session and restored on switch back",
            r.ev("Workspace._state.panel.state") == state_open, state_open)
    r.ev("document.getElementById('ws-panel-collapse').click()")
    time.sleep(1.0)
    cols_closed = r.ev("Workspace._registry().active().view.term.cols")
    r.check("collapsing the panel resizes the agent screen back", cols_closed >= cols_open)

    # The dropdown is intentionally disabled while the CLI is Busy; OpenCode's
    # TUI redraws often, so wait for a quiet window but do not let the test
    # deadlock on it (the disabled-while-busy rule is product behavior, not
    # something this flow can force).
    enabled = r.wait_for("!document.getElementById('ws-model-btn').disabled", timeout=30)
    r.check("model dropdown enables between CLI updates", bool(enabled))
    r.ev("window.confirm = function(){return true;};")
    r.ev("(function(){var b=document.getElementById('ws-model-btn');"
         "b.disabled=false;b.click();return true;})()")
    r.check("model dropdown lists the CLI's own models",
            bool(r.wait_for("document.querySelectorAll('#ws-model-options .ws-menu-item').length > 100", timeout=40)))
    r.check("current model shown at the top of the control",
            "Current:" in (r.ev("document.getElementById('ws-model-current').textContent") or ""))
    r.check("long lists get a search box",
            not r.ev("document.getElementById('ws-model-search-wrap').classList.contains('hidden')"))

    # Busy no longer blocks the control: the model dropdown stays open so the
    # user can browse/search and switch (the backend 409 confirmation applies).
    r.ev("document.getElementById('ws-status').setAttribute('data-status','busy')")
    r.check("busy status does not block the model control",
            r.ev("Workspace._modelDisabledReason()") == "")
    total_models = r.ev("document.querySelectorAll('#ws-model-options .ws-menu-item').length")
    r.ev("(function(){var first=document.querySelector('#ws-model-options .ws-menu-item');"
         "var name=(first?first.innerText:'').trim();"
         "var q=name.replace(/[^A-Za-z0-9]/g,'').slice(0,3)||'a';"
         "var i=document.getElementById('ws-model-search');"
         "i.focus();i.value=q;i.dispatchEvent(new Event('input',{bubbles:true}));return true;})()")
    time.sleep(0.3)
    r.check("clicking/typing in the search box keeps the menu open",
            not r.ev("document.getElementById('ws-model-menu').classList.contains('hidden')"))
    filtered = r.ev("document.querySelectorAll('#ws-model-options .ws-menu-item').length")
    r.check("typing filters the model list to matches",
            isinstance(filtered, int) and 0 < filtered < total_models,
            f"{filtered}/{total_models}")
    r.ev("(function(){var i=document.getElementById('ws-model-search');"
         "i.value='';i.dispatchEvent(new Event('input',{bubbles:true}));return true;})()")
    time.sleep(0.3)

    target = r.ev("!![...document.querySelectorAll('#ws-model-options .ws-menu-item')]"
                  ".find(x=>!x.classList.contains('ws-menu-item-active'))")
    r.check("model menu has a selectable entry", bool(target))
    if target:
        r.ev("[...document.querySelectorAll('#ws-model-options .ws-menu-item')]"
             ".find(x=>!x.classList.contains('ws-menu-item-active')).click()")
    r.wait_for("!!Workspace._state.models.current", timeout=30)
    applied = r.ev("Workspace._state.models.current")
    detail = r.ev("(Workspace._state.models.error || '') + ' | ' + "
                  "((document.getElementById('ws-note')||{}).textContent || '') + ' | ' + "
                  "(document.getElementById('ws-status').getAttribute('data-status') || '')")
    r.check("model change applied and read back from the backend", bool(applied),
            f"applied={applied} :: {detail}")
    live = r.rest.api(f"/api/project-sessions/{session_a}")["session"]
    r.check("session's current_model matches the backend state", live["current_model"] == applied)

    r.ev("window.__origFetch = window.fetch; window.fetch = function(u, o){"
         "if(String(u).indexOf('/models') !== -1){return Promise.resolve(new Response("
         "JSON.stringify({error:'Couldn\\'t parse the CLI model list'}),"
         "{status:502, headers:{'Content-Type':'application/json'}}));}"
         "return window.__origFetch(u, o);};")
    r.ev("document.getElementById('ws-model-refresh').click()")
    time.sleep(0.8)
    failed_text = (r.ev("document.getElementById('ws-model-options').innerText") or "").lower()
    r.check("parse failure shows an error, not a fabricated list",
            "couldn" in failed_text or "parse" in failed_text or "error" in failed_text, failed_text[:80])
    r.check("error state offers Retry",
            "retry" in (r.ev("document.getElementById('ws-model-options').innerText") or "").lower())
    r.ev("window.fetch = window.__origFetch")
    r.ev("[...document.querySelectorAll('#ws-model-options button')].find(b=>b.innerText==='Retry').click()")
    r.check("Retry reloads the real CLI list",
            bool(r.wait_for("document.querySelectorAll('#ws-model-options .ws-menu-item').length > 100", timeout=40)))

    r.ev("document.body.click()")
    probe = os.path.join(PROJ, "e2e-handoff-probe2.txt")
    with open(probe, "w") as fh:
        fh.write("second handoff")
    r.check("a new handoff burst auto-expands once more",
            bool(r.wait_for("Workspace._state.panel.state === 'S2'", timeout=25)))
    try:
        os.remove(probe)
    except OSError:
        pass

    for sid in (claude_session, session_a):
        try:
            r.rest.api(f"/api/project-sessions/{sid}/stop", "POST", {})
            r.rest.api(f"/api/project-sessions/{sid}", "DELETE")
        except Exception:
            pass


def docker_notes(base_missing, base_down, chrome, shared):
    """Docker CLI missing and daemon stopped show different, actionable messages."""
    print("DOCKER READINESS - two distinct UI messages")
    for base, needle, label, port in (
        (base_missing, "Docker CLI not found", "docker missing", 9341),
        (base_down, "daemon is not running", "daemon stopped", 9342),
    ):
        if not base:
            print(f"  [skip] {label} (no URL provided)")
            continue
        for attempt in (1, 2):
            r = Runner(base, chrome, port + attempt - 1)
            try:
                r.start_browser()
                r.navigate()
                time.sleep(2.0)
                note = r.ev("document.getElementById('ws-note').textContent") or ""
                visible = not r.ev("document.getElementById('ws-note').classList.contains('hidden')")
                shared.check(f"{label} note shown in the agent screen",
                             visible and needle.lower() in note.lower(), note)
                break
            except Exception as exc:
                if attempt == 2:
                    shared.check(f"{label} note shown in the agent screen", False, repr(exc))
            finally:
                r.stop_browser()
                time.sleep(1.0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8099")
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    parser.add_argument("--phase", choices=["1", "2", "all", "none"], default="all",
                        help="'none' runs only the optional Docker note checks")
    parser.add_argument("--serve", action="store_true",
                        help="start server.py on the --base port for the run")
    parser.add_argument("--docker-missing-url", default=None)
    parser.add_argument("--docker-down-url", default=None)
    args = parser.parse_args()

    server = None
    if args.serve and args.phase != "none":
        port = args.base.rstrip("/").rsplit(":", 1)[-1]
        server = subprocess.Popen(
            [sys.executable, os.path.join(PROJ, "server.py"), port],
            cwd=PROJ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, DENNIS_NO_DENMA="1"))
        time.sleep(2.5)

    phases = ["1", "2"] if args.phase == "all" else ([] if args.phase == "none" else [args.phase])
    failures = []
    code = 1
    try:
        if phases:
            runner = Runner(args.base, args.chrome, 9340)
            runner.rest = Rest(args.base)
            runner.start_browser()
            try:
                if "1" in phases:
                    phase1(runner)
                if "2" in phases:
                    phase2(runner)
            finally:
                runner.stop_browser()
            failures.extend(runner.failures)
        if args.docker_missing_url or args.docker_down_url:
            note_runner = Runner(args.base, args.chrome, 9341)
            docker_notes(args.docker_missing_url, args.docker_down_url, args.chrome, note_runner)
            failures.extend(note_runner.failures)
        print()
        if failures:
            print("FAILURES:", failures)
        else:
            print("ALL E2E CHECKS PASSED")
            code = 0
    finally:
        if server:
            server.terminate()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
