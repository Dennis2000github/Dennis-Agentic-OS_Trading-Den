#!/usr/bin/env python3
"""Lightweight Playwright re-check of the 2026-11-12 surfaces (load-tolerant).

Re-verifies the browser-facing guarantees after the voice-layer JS fixes:
jarvis default view, memory galaxy preview bar, custom provider row,
new-window button geometry. Skips gracefully if the machine cannot start a
browser (documented as environment, not code).
"""
import os
import subprocess
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
from playwright.sync_api import sync_playwright  # noqa: E402

PORT = "8141"
BASE = f"http://localhost:{PORT}"

server = subprocess.Popen(
    [sys.executable, os.path.join(PROJ, "app.py"), PORT],
    cwd=PROJ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    env=dict(os.environ, DENNIS_NO_DENMA="1"))
time.sleep(4)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


try:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=os.path.expanduser(
                "~/Library/Caches/ms-playwright/chromium-1169/chrome-mac/Chromium.app/Contents/MacOS/Chromium"))
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_function(
            "typeof window.switchMainView === 'function' && !!window.JarvisDashboard"
            " && !!window.MemoryGalaxy", timeout=45000)
        page.wait_for_timeout(1500)

        check("jarvis is the landing view", page.evaluate(
            "!document.getElementById('jarvis-view').classList.contains('hidden')"
            " && document.getElementById('agent-workspace').classList.contains('hidden')"))

        page.evaluate("window.switchMainView('memory-galaxy')")
        page.wait_for_function("window.__mg_debug && window.__mg_debug.nodeCount > 0", timeout=25000)
        check("memory galaxy renders stars", page.evaluate("window.__mg_debug.nodeCount") > 0)
        check("preview bar sits above the workspace bar", page.evaluate(
            "(function(){"
            " var p = document.getElementById('mg-preview-bar');"
            " var w = document.getElementById('mg-workspace-bar');"
            " return !!(p.compareDocumentPosition(w) & Node.DOCUMENT_POSITION_FOLLOWING); })()"))
        page.evaluate("window.__mg_debug.select(window.__mg_debug.firstNodeId)")
        page.wait_for_timeout(800)
        detail = page.evaluate("""
            (function(){
              return {
                emptyHidden: document.getElementById('mg-preview-empty').classList.contains('hidden'),
                contentShown: !document.getElementById('mg-preview-content').classList.contains('hidden'),
                title: document.getElementById('mg-preview-title').textContent,
                nodes: window.__mg_debug.nodeCount,
                first: window.__mg_debug.firstNodeId,
              };
            })()
        """)
        check("preview bar fills with the selected note",
              detail["emptyHidden"] and detail["contentShown"] and len(detail["title"]) > 0,
              str(detail))

        page.evaluate("window.switchMainView('workspace')")
        page.wait_for_timeout(800)
        check("new window button beside the tabs", page.evaluate(
            "(function(){"
            " var tabs = document.getElementById('ws-windows');"
            " var btn = document.getElementById('ws-new-window');"
            " var a = tabs.getBoundingClientRect(), b = btn.getBoundingClientRect();"
            " var bar = document.getElementById('ws-windows-bar').getBoundingClientRect();"
            " return (b.left - a.right) <= 14 && b.left < bar.right - 140; })()"))

        # Pick Claude Code, wait for its (slow) CLI model capture, then check
        # the Custom provider row + connect form.
        page.evaluate("""
          (function(){
            var rows = Array.from(document.querySelectorAll('#ws-agent-options button'));
            document.getElementById('ws-agent-btn').click();
            rows = Array.from(document.querySelectorAll('#ws-agent-options button'));
            var pick = rows.find(function(b){ return b.textContent.indexOf('Claude Code') !== -1; });
            if (pick) pick.click();
          })()
        """)
        found = False
        for _ in range(40):
            page.evaluate("document.getElementById('ws-provider-btn').click()")
            page.wait_for_timeout(1500)
            found = page.evaluate(
                "(function(){ var opts = document.getElementById('ws-provider-options');"
                " return Array.from(opts.querySelectorAll('button'))"
                "  .some(function(b){ return b.textContent.indexOf('Custom (FreeLLMAPI)') !== -1; }); })()")
            if found:
                break
            page.evaluate("document.body.click()")
        check("Custom (FreeLLMAPI) row in provider menu", found)
        if found:
            page.evaluate("""
              (function(){
                var opts = document.getElementById('ws-provider-options');
                var row = Array.from(opts.querySelectorAll('button'))
                  .find(function(b){ return b.textContent.indexOf('Custom (FreeLLMAPI)') !== -1; });
                row.click();
              })()
            """)
            page.wait_for_timeout(800)
            check("Custom opens the FreeLLMAPI connect form", page.evaluate(
                "!!document.getElementById('ws-free-form')"))

        browser.close()
finally:
    server.terminate()

failed = [n for n, ok in results if not ok]
print()
if failed:
    print("FAILED:", "; ".join(failed))
    raise SystemExit(1)
print("ALL RE-CHECKS PASSED")
