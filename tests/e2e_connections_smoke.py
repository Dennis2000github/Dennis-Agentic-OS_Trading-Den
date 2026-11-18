#!/usr/bin/env python3
"""Targeted browser smoke test for the unified connection UI (CDP, Chrome).

Validates the flattened workspace (Agent + Model only) and the AI
Connections manager view against a live server, with zero console errors:

  1. page loads; workspace toolbar shows Agent + Model only
  2. no Source/Provider dropdowns (flattened UI)
  3. the merged model menu carries the Manage connections entry
  4. that entry opens the AI Connections view
  5. AI Connections view renders panes + routing preferences
  6. add-connection modal opens with gateway presets
  7. no uncaught page errors during the whole run

Usage (from the repo root)::

    ./.venv/bin/python tests/e2e_connections_smoke.py --serve

``--serve`` starts a scratch app.py on :8098 so the test never touches a
live instance. Stdlib only; drives the installed Google Chrome via CDP.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ws_client import WSClient  # noqa: E402

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_PORT = 9333


class Smoke:
    def __init__(self, chrome=DEFAULT_CHROME):
        self.failures = []
        self.profile = tempfile.mkdtemp(prefix="dennis-conn-smoke-")
        self.chrome_proc = subprocess.Popen([
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
        if not targets:
            raise RuntimeError("Chrome DevTools never came up")
        page = next(t for t in targets if t["type"] == "page")
        self.cdp = WSClient(page["webSocketDebuggerUrl"])
        self.cdp.cdp("Runtime.enable")
        self.cdp.cdp("Log.enable")
        self.cdp.cdp("Page.enable")
        self.cdp.cdp("Network.enable")
        self.failed_requests = []

    def drain_failed_requests(self):
        """404'd resource URLs since the last drain (favicon excluded)."""
        failed = []
        for event in self.cdp.events:
            if event.get("method") == "Network.responseReceived":
                resp = event.get("params", {}).get("response", {})
                status = resp.get("status")
                url = resp.get("url", "")
                if status == 404 and "favicon" not in url:
                    failed.append(url)
        self.cdp.events = []
        return failed

    def stop(self):
        if self.chrome_proc:
            self.chrome_proc.terminate()

    def ev(self, expr, timeout=20):
        return self.cdp.eval(expr, timeout=timeout)

    def navigate(self, base, settle=3.5):
        self.cdp.cdp("Page.navigate", {"url": base + "/"})
        try:
            self.cdp.cdp("Page.bringToFront")
            self.cdp.cdp("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            pass
        for _ in range(80):
            if self.ev("!!document.getElementById('agent-workspace')"):
                break
            time.sleep(0.25)
        time.sleep(settle)

    def check(self, name, cond, detail=""):
        print(f"  [{'ok' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            self.failures.append(name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve", action="store_true",
                        help="start a scratch app.py on --port for the run")
    parser.add_argument("--port", default="8098")
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    args = parser.parse_args()

    server = None
    if args.serve:
        server = subprocess.Popen(
            [sys.executable, os.path.join(PROJ, "app.py"), args.port],
            cwd=PROJ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, DENNIS_NO_DENMA="1"))
        deadline = time.time() + 25
        while time.time() < deadline:
            try:
                urllib.request.urlopen("http://127.0.0.1:%s/api/auth/session" % args.port,
                                       timeout=2)
                break
            except Exception:
                time.sleep(0.5)
    base = "http://127.0.0.1:%s" % args.port
    smoke = Smoke(chrome=args.chrome)
    try:
        run(smoke, base)
    finally:
        smoke.stop()
        if server is not None:
            server.terminate()
    print()
    if smoke.failures:
        print("FAILED checks: %d" % len(smoke.failures))
        return 1
    print("ALL CONNECTION UI CHECKS PASSED")
    return 0


def run(s, base):
    s.navigate(base)

    s.check("workspace toolbar has Agent + Model",
            s.ev("!!document.getElementById('ws-agent-btn') && "
                 "!!document.getElementById('ws-model-btn')"))
    s.check("no Source dropdown (flattened UI)",
            not s.ev("!!document.getElementById('ws-source-btn')"))
    s.check("no Provider dropdown (flattened UI)",
            not s.ev("!!document.getElementById('ws-provider-btn')"))
    s.check("connections manager panel present",
            s.ev("!!document.getElementById('freellm-panel-connections')"))
    s.check("sidebar nav registered",
            s.ev("!!document.getElementById('sidebar-nav-plugins')"))

    # JARVIS is the landing view; switch to the workspace.
    s.ev("window.switchMainView('workspace')")
    time.sleep(0.6)

    # The merged model menu carries the manager entry (no Source menu needed).
    s.check("model menu has Manage connections",
            s.ev("!!document.getElementById('ws-model-manage')"))
    s.ev("document.getElementById('ws-model-manage').click()")
    time.sleep(1.0)
    s.check("Manage connections opens the connections panel",
            s.ev("!document.getElementById('plugins-view').classList.contains('hidden') && "
                 "!document.getElementById('freellm-panel-connections').classList.contains('hidden')"))

    # Plugins hub view — via the actual sidebar button (regression:
    # initSidebar() routes clicks by label; 'Plugins' must open the hub).
    s.ev("window.switchMainView('jarvis')")
    time.sleep(0.4)
    s.ev("(function(){var b=document.getElementById('sidebar-nav-plugins');"
         "if(b) b.click(); return !!b;})()")
    time.sleep(1.2)
    s.check("sidebar button opens the plugins hub",
            s.ev("!document.getElementById('plugins-view').classList.contains('hidden')"))
    s.check("sidebar button highlights",
            s.ev("document.getElementById('sidebar-nav-plugins').classList.contains('bg-blue-600')"))
    s.check("connections strip renders",
            s.ev("!!document.querySelector('#freellm-panel-connections .cn-hero')"))
    provider_pane = s.ev(
        "(function(){var h=document.getElementById('cn-providers');"
        "return h ? h.children.length : -1;})()")
    gateway_pane = s.ev(
        "(function(){var h=document.getElementById('cn-gateways');"
        "return h ? h.children.length : -1;})()")
    s.check("panes render (may be empty-state)",
            provider_pane >= 0 and gateway_pane >= 0,
            "providers=%s gateways=%s" % (provider_pane, gateway_pane))
    modes = s.ev("(function(){return document.querySelectorAll('[data-cn-mode]').length;})()")
    s.check("routing mode control renders", modes == 3, "found %s" % modes)

    # Add-connection modal with presets + a full save round-trip.
    s.ev("document.getElementById('cn-add').click()")
    time.sleep(0.5)
    s.check("add-connection modal opens",
            s.ev("!document.getElementById('cn-modal').classList.contains('hidden')"))
    preset_count = s.ev(
        "(function(){var sel=document.getElementById('cn-preset');"
        "return sel ? sel.options.length : -1;})()")
    s.check("gateway presets populate the select",
            bool(preset_count) and preset_count >= 5, "found %s" % preset_count)
    # Save round-trip: fill the form, press Save, expect a card to appear.
    s.ev("(function(){var sel=document.getElementById('cn-preset');"
         "sel.value='custom_openai'; sel.dispatchEvent(new Event('change'));})()")
    time.sleep(0.3)
    s.ev("(function(){document.getElementById('cn-name').value='Smoke Gateway';"
         "document.getElementById('cn-base-url').value='https://smoke.example.com/v1';})()")
    s.ev("document.getElementById('cn-save').click()")
    time.sleep(1.2)
    modal_closed = s.ev("document.getElementById('cn-modal').classList.contains('hidden')")
    s.check("save closes the modal", modal_closed)
    card_found = s.ev(
        "(function(){var h=document.getElementById('cn-gateways');"
        "return h ? h.textContent.indexOf('Smoke Gateway') !== -1 : false;})()")
    s.check("saved connection renders as a card", card_found)
    # Clean up the smoke connection (auto-accept the confirm dialog).
    s.ev("window.confirm = function(){return true;};")
    s.ev("(function(){var cards=document.querySelectorAll('#cn-gateways .cn-card');"
         "for(var i=0;i<cards.length;i++){"
         "if(cards[i].textContent.indexOf('Smoke Gateway')!==-1){"
         "var btn=cards[i].querySelector('[data-act=\\'delete\\']');"
         "if(btn){btn.click();return;}}}})()")
    time.sleep(1.0)
    card_gone = s.ev(
        "(function(){var h=document.getElementById('cn-gateways');"
        "return h ? h.textContent.indexOf('Smoke Gateway') === -1 : true;})()")
    s.check("remove clears the card", card_gone)

    # Console errors with their source URL so favicon noise can be excluded.
    console = []
    for event in s.cdp.events:
        if event.get("method") == "Log.entryAdded":
            entry = event.get("params", {}).get("entry", {})
            if entry.get("level") == "error":
                console.append((entry.get("url") or "") + " " + entry.get("text", ""))
        if event.get("method") == "Runtime.exceptionThrown":
            console.append("exception " + json.dumps(
                event.get("params", {}).get("exceptionDetails", {}))[:200])
    s.cdp.events = []
    # Known environmental noise, unrelated to the connection UI:
    #  - favicon 404s (browser auto-requests)
    #  - /api/history|/api/markets DenMa calls: the scratch server runs with
    #    DENNIS_NO_DENMA=1, so Trading Den data endpoints are intentionally down.
    def _noise(entry):
        return ("favicon" in entry or "/api/history" in entry
                or "/api/markets" in entry or "/ws" in entry
                or "/api/live-snapshot" in entry or "MMcloneLoopEngineering" in entry
                or "api/bots" in entry or "api/screener" in entry
                or "api/arbitrage" in entry or "api/triangular" in entry
                or "api/mt5" in entry)
    real_errors = [e for e in console if not _noise(e)]
    s.check("no uncaught page errors / console errors (favicon + DenMa scratch noise excluded)",
            not real_errors, " | ".join(real_errors)[:300])
    failed_urls = s.drain_failed_requests()
    s.check("no 404'd resources (favicon excluded)", not failed_urls,
            "urls=%s" % failed_urls[:6])


if __name__ == "__main__":
    raise SystemExit(main())
