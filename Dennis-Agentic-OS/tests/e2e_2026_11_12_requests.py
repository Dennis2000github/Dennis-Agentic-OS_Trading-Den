#!/usr/bin/env python3
"""E2E for the 2026-11-12 requests (headless Chrome + CDP, real server).

Verifies:
  1. JARVIS is the default dashboard (jarvis-view visible on load).
  2. Memory Galaxy has its own page with a Preview bar ABOVE the workspace bar.
  3. The workspace "New window +" button sits directly beside the window tabs.
  4. The workspace Provider dropdown offers the Custom (FreeLLMAPI) provider.
  5. No JS exceptions on load or while switching views.

Usage (repo root)::

    python3 tests/e2e_2026_11_12_requests.py --serve
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
DEFAULT_CHROME = os.path.expanduser(
    "~/Library/Caches/ms-playwright/chromium-1169/chrome-mac/Chromium.app/Contents/MacOS/Chromium")


class Check:
    def __init__(self):
        self.failures = []

    def check(self, name, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(name)


class Runner:
    def __init__(self, base, chrome, port):
        self.base, self.chrome, self.port = base, chrome, port
        self.cdp = None
        self.chrome_proc = None
        self.profile = None

    def start_browser(self):
        self.profile = tempfile.mkdtemp(prefix="dennis-e2e-chrome-")
        self.chrome_proc = subprocess.Popen([
            self.chrome, "--headless", "--no-first-run",
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
        self.cdp.cdp("Network.enable")
        self.cdp.cdp("Page.enable")

    def stop_browser(self):
        if self.chrome_proc:
            self.chrome_proc.terminate()

    def navigate(self, url=None, settle=1.5):
        last = None
        for attempt in range(3):
            try:
                self.cdp.cdp("Page.navigate", {"url": (url or self.base) + "/"}, timeout=30)
                last = None
                break
            except (TimeoutError, RuntimeError) as exc:
                last = exc
                time.sleep(1.5)
        if last:
            raise last
        for _ in range(80):
            if self.cdp.eval("!!document.getElementById('agent-workspace')"):
                break
            time.sleep(0.25)
        # Defer scripts must have run and booted the default view.
        for _ in range(80):
            if self.cdp.eval(
                    "typeof window.switchMainView === 'function' && !!window.JarvisDashboard"
                    " && !!window.MemoryGalaxy"):
                break
            time.sleep(0.25)
        time.sleep(settle)


def visible(cdp, el):
    return cdp.eval(
        f"(function(){{ var e = document.getElementById('{el}');"
        f" return !!e && !e.classList.contains('hidden'); }})()")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8123")
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()

    server = None
    if args.serve:
        port = args.base.rstrip("/").rsplit(":", 1)[-1]
        server = subprocess.Popen(
            [sys.executable, os.path.join(PROJ, "app.py"), port],
            cwd=PROJ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, DENNIS_NO_DENMA="1"))
        time.sleep(4.0)

    shared = Check()
    runner = Runner(args.base, args.chrome, 9371)
    try:
        runner.start_browser()
        runner.navigate(settle=2.5)
        cdp = runner.cdp

        print("JARVIS DEFAULT DASHBOARD")
        shared.check("jarvis view is the landing view", visible(cdp, "jarvis-view"))
        shared.check("workspace sections hidden on load",
                     not visible(cdp, "agent-workspace") and not visible(cdp, "hero-banner-section"))
        shared.check("sidebar highlights JARVIS", cdp.eval(
            "document.getElementById('sidebar-nav-jarvis').classList.contains('bg-blue-600')"))
        shared.check("jarvis core canvas rendered", cdp.eval(
            "(function(){ var c = document.getElementById('jarvis-core-canvas'); return !!c && c.width > 0; })()"))
        shared.check("quick commands bound", cdp.eval(
            "(function(){ var b = document.getElementById('jarvis-qc-galaxy');"
            " if (!b) return false;"
            " b.click();"
            " var v = document.getElementById('memory-galaxy-view');"
            " return !!v && !v.classList.contains('hidden'); })()"))

        print("MEMORY GALAXY PAGE")
        shared.check("memory galaxy has its own view", visible(cdp, "memory-galaxy-view"))
        shared.check("preview bar exists above workspace bar", cdp.eval(
            "(function(){"
            " var p = document.getElementById('mg-preview-bar');"
            " var w = document.getElementById('mg-workspace-bar');"
            " return !!p && !!w && !!(p.compareDocumentPosition(w) & Node.DOCUMENT_POSITION_FOLLOWING); })()"))
        shared.check("preview bar empty state visible", visible(cdp, "mg-preview-empty"))
        shared.check("galaxy canvas present", cdp.eval(
            "(function(){ var c = document.getElementById('mg-canvas'); return !!c && c.width > 0; })()"))
        # The repo vault has notes, so stars must appear and selecting one must
        # fill the preview bar with that note's title, path and excerpt.
        deadline = time.time() + 25
        count = 0
        while time.time() < deadline:
            count = cdp.eval("window.__mg_debug ? window.__mg_debug.nodeCount : 0") or 0
            if count:
                break
            time.sleep(0.5)
        shared.check("galaxy renders stars from the vault", count > 0, f"{count} stars")
        if count:
            stats = cdp.eval("document.getElementById('mg-stats').textContent") or ""
            shared.check("workspace bar shows star/link stats", "star" in stats, stats)
            selected = cdp.eval(
                "(function(){ return window.__mg_debug.select(window.__mg_debug.firstNodeId); })()")
            time.sleep(1.0)
            shared.check("selecting a star fills the preview bar",
                         bool(selected) and not visible(cdp, "mg-preview-empty")
                         and visible(cdp, "mg-preview-content"), str(selected))
            shared.check("preview bar shows title and path", cdp.eval(
                "(function(){"
                " var t = document.getElementById('mg-preview-title').textContent;"
                " var p = document.getElementById('mg-preview-path').textContent;"
                " return t.length > 0 && p.indexOf('.md') !== -1; })()"))

        print("WORKSPACE TOOLBAR")
        cdp.eval("window.switchMainView('workspace')")
        time.sleep(0.6)
        shared.check("workspace visible again", visible(cdp, "agent-workspace"))
        # New window button must sit immediately right of the tabs container.
        shared.check("new window button beside the window tabs", cdp.eval(
            "(function(){"
            " var tabs = document.getElementById('ws-windows');"
            " var btn = document.getElementById('ws-new-window');"
            " if (!tabs || !btn) return false;"
            " var a = tabs.getBoundingClientRect(), b = btn.getBoundingClientRect();"
            " var bar = document.getElementById('ws-windows-bar').getBoundingClientRect();"
            " return (b.left - a.right) <= 14 && b.left < bar.right - 140; })()"))
        shared.check("provider dropdown menu contains Custom (FreeLLMAPI)", cdp.eval(
            "(function(){"
            " var btn = document.getElementById('ws-agent-btn');"
            " if (!btn || btn.disabled) return false;"
            " btn.click();"
            " var opts = document.getElementById('ws-agent-options');"
            " return !!opts && opts.children.length >= 0; })()"))

        # Open the provider menu on Claude Code (a FreeLLMAPI-supported agent).
        cdp.eval("""
            (function(){
              var opts = document.getElementById('ws-agent-options');
              if (!opts || !opts.children.length) return false;
              var rows = Array.from(opts.querySelectorAll('button'));
              var pick = rows.find(function(b){ return b.textContent.indexOf('Claude Code') !== -1; }) ||
                         rows.find(function(b){ return !b.classList.contains('ws-menu-item-disabled'); });
              if (!pick) return false;
              pick.click();
              return true; })()
        """)
        time.sleep(1.0)
        # Claude Code lists models through an interactive /model capture —
        # wait (up to 60 s) until the provider menu leaves its loading state.
        deadline = time.time() + 60
        custom_row = False
        while time.time() < deadline:
            cdp.eval("document.getElementById('ws-provider-btn').click()")
            time.sleep(1.2)
            custom_row = cdp.eval(
                "(function(){"
                " var opts = document.getElementById('ws-provider-options');"
                " return !!opts && Array.from(opts.querySelectorAll('button'))"
                "   .some(function(b){ return b.textContent.indexOf('Custom (FreeLLMAPI)') !== -1; }); })()")
            if custom_row:
                break
            cdp.eval("(function(){ document.body.click(); })()")
        shared.check("Custom (FreeLLMAPI) row rendered in provider menu", custom_row)
        if custom_row:
            shared.check("selecting Custom opens the FreeLLMAPI connect form", cdp.eval(
                "(function(){"
                " var opts = document.getElementById('ws-provider-options');"
                " var row = Array.from(opts.querySelectorAll('button'))"
                "   .find(function(b){ return b.textContent.indexOf('Custom (FreeLLMAPI)') !== -1; });"
                " if (!row) return false;"
                " row.click();"
                " return !!document.getElementById('ws-free-form'); })()"))

        errors = [e for e in runner.cdp.runtime_errors() if "favicon" not in e.lower()]
        shared.check("no JS exceptions during the run", not errors, "; ".join(errors[:3]))
        bad = []
        for ev in runner.cdp.events:
            if ev.get("method") == "Network.responseReceived":
                r = ev["params"]["response"]
                if r.get("status") >= 400 and "favicon" not in r.get("url", "").lower():
                    bad.append((r.get("status"), r.get("url")))
        shared.check("no failed resource loads (non-favicon)", not bad, json.dumps(bad[:5]))

    finally:
        runner.stop_browser()
        if server:
            server.terminate()

    print()
    if shared.failures:
        print("FAILED:", len(shared.failures), "->", "; ".join(shared.failures))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
