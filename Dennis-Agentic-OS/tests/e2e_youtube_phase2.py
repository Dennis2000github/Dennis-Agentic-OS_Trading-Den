#!/usr/bin/env python3
"""Browser-level E2E for the YouTube Studio consolidation (CDP).

Proves, against a real server:

  1. A deep link lands on Create (the default landing section) with a working
     prompt-first composer.
  2. The sidebar carries exactly the five retained sections: Create,
     Templates, Projects, Analytics, Settings. Topic Backlog, Publishing and
     YouTube Intelligence are gone from Studio (they live in COSMOS/GENESIS).
  3. Section switching shows exactly one panel at a time (double-panel fixed).
  4. Projects exposes Projects/Scripts subtabs.
  5. Legacy deep links (#studio/youtube/backlog|publishing|intelligence)
     redirect into COSMOS/GENESIS instead of opening a removed panel.
  6. Settings renders channel profiles, the LLM status (GLM default) and the
     honest OAuth connection state.
  7. Analytics shows the honest not-configured state.
  8. No JS exceptions during the run.

Usage (from the repo root)::

    python3 tests/e2e_youtube_phase2.py --serve

Stdlib only; reuses the CDP WebSocket client from tests/ws_client.py.
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


class Runner:
    def __init__(self, base, chrome, port):
        self.base = base
        self.chrome = chrome
        self.port = port
        self.failures = []
        self.cdp = None
        self.chrome_proc = None
        self.profile = None

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
        target_url = (url or self.base) + "/"
        for attempt in range(3):
            try:
                self.cdp.cdp("Page.navigate", {"url": target_url}, timeout=40)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(1.0)
                self._reconnect()
        for _ in range(80):
            if self.cdp.eval("!!document.body"):
                break
            time.sleep(0.25)
        time.sleep(settle)

    def _reconnect(self):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/json/list", timeout=5) as res:
            targets = json.loads(res.read().decode())
        page = next(t for t in targets if t["type"] == "page")
        try:
            self.cdp.sock.close()
        except Exception:
            pass
        self.cdp = WSClient(page["webSocketDebuggerUrl"])
        self.cdp.cdp("Runtime.enable")
        self.cdp.cdp("Log.enable")
        self.cdp.cdp("Page.enable")

    def ev(self, expr, timeout=20):
        return self.cdp.eval(expr, timeout=timeout)

    def wait_for(self, expr, timeout=30, poll=0.25):
        return self.cdp.wait_for(expr, timeout=timeout, poll=poll)

    def check(self, name, cond, detail=""):
        print(f"  [{'ok' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))
        if not cond:
            self.failures.append(name)


def open_youtube_studio(r):
    """Navigate home, boot, then switch into the YouTube workspace."""
    r.navigate()
    ok = r.wait_for("!!document.getElementById('studio-view')", timeout=30)
    r.check("App booted and Studio view exists in the DOM", ok)
    # index.html is large: #studio-youtube-main parses well after #studio-view,
    # so wait for the element itself before probing it.
    r.wait_for("!!document.getElementById('studio-youtube-main')", timeout=30)
    entered = False
    for _ in range(4):
        r.ev("window.location.hash = '#studio/youtube/create'")
        time.sleep(0.9)
        if r.ev("(function () { var m = document.getElementById('studio-youtube-main');"
                " return !!m && !m.classList.contains('is-hidden'); })()"):
            entered = True
            break
    r.check("YouTube Studio becomes visible", entered)
    r.wait_for("!!window.YouTubeStudio", timeout=20)
    time.sleep(1.2)


def phase_landing(r):
    print("  -- Create landing page")
    r.check("Create panel is the visible section",
            r.ev("!document.querySelector('[data-yt-panel=\"create\"]').classList.contains('is-hidden')"
                 " && window.YouTubeStudio.section() === 'create'"))
    r.check("Composer prompt box present",
            r.ev("!!document.getElementById('yt-create-prompt') && !!document.getElementById('yt-create-submit')"))
    r.check("Template selector present and disabled until Phase 3",
            r.ev("var el = document.getElementById('yt-create-template'); !!el && el.disabled"))
    meta = r.ev("document.querySelector('.yt-composer-meta') ? document.querySelector('.yt-composer-meta').innerText : ''")
    r.check("Meta chips carry OpenCode + Kokoro defaults",
            isinstance(meta, str) and "OpenCode" in meta and "Kokoro" in meta, repr(meta)[:120])
    r.check("Model chip resolves from the LLM status",
            r.ev("(document.getElementById('yt-create-model')||{}).textContent"))
    sections = r.ev(
        "Array.from(document.querySelectorAll('#yt-side-nav [data-yt-section]'))"
        ".map(function (el) { return el.getAttribute('data-yt-section'); })")
    expected = ["create", "templates", "projects", "analytics", "settings"]
    r.check("Sidebar has exactly the 5 retained sections", sections == expected, repr(sections))
    r.check("Removed sections are gone from the DOM (no backlog/publishing/intelligence panels)",
            r.ev("!document.querySelector('[data-yt-panel=\"backlog\"]')"
                 " && !document.querySelector('[data-yt-panel=\"publishing\"]')"
                 " && !document.querySelector('[data-yt-panel=\"intelligence\"]')"))
    r.check("Only Templates carries a phase badge",
            r.ev("(function () {"
                 "  var badges = Array.from(document.querySelectorAll('#yt-side-nav .yt-soon'));"
                 "  var texts = badges.map(function (el) { return el.textContent.trim(); });"
                 "  return badges.length === 1 && texts.indexOf('Phase 3') !== -1"
                 "    && texts.every(function (t) { return t.indexOf('Stage') === -1; });"
                 "})()"))


def phase_one_panel_rule(r):
    print("  -- exactly one panel visible at a time")
    r.ev("document.querySelector('[data-yt-section=\"projects\"]').click()")
    time.sleep(0.8)
    visible = r.ev(
        "Array.from(document.querySelectorAll('[data-yt-panel]'))"
        ".filter(function (el) { return !el.classList.contains('is-hidden'); })"
        ".map(function (el) { return el.getAttribute('data-yt-panel'); })")
    r.check("Projects switch shows only the projects panel", visible == ["projects"], repr(visible))
    r.check("Projects list and toolbar present",
            r.ev("!!document.getElementById('yt-projects-list') && !!document.getElementById('yt-projects-search')"))


def phase_projects_scripts(r):
    print("  -- projects + scripts subtab")
    r.ev("document.querySelector('[data-yt-ptab=\"scripts\"]').click()")
    time.sleep(0.5)
    r.check("Scripts subtab reveals the script studio",
            r.ev("!document.querySelector('[data-yt-ptab-panel=\"scripts\"]').classList.contains('is-hidden')"
                 " && !!document.getElementById('yt-script-generate')"))
    r.ev("document.querySelector('[data-yt-ptab=\"projects\"]').click()")
    time.sleep(0.3)
    r.check("Projects subtab comes back",
            r.ev("!document.querySelector('[data-yt-ptab-panel=\"projects\"]').classList.contains('is-hidden')"))


def phase_legacy_redirects(r):
    print("  -- legacy deep links redirect into COSMOS/GENESIS")
    for section, target in (("backlog", "andromeda"), ("publishing", "genesis"),
                            ("intelligence", "quasar")):
        r.ev("window.location.hash = '#studio/youtube/%s'" % section)
        r.wait_for("window.location.hash.indexOf('#cosmos/%s') === 0" % target, timeout=15)
        r.check("Legacy #%s deep link lands on #cosmos/%s" % (section, target),
                r.ev("window.location.hash.indexOf('#cosmos/%s') === 0" % target),
                r.ev("window.location.hash"))
    # Return to the Studio so later phases run against the real workspace.
    r.ev("window.location.hash = '#studio/youtube/create'")
    r.wait_for("window.YouTubeStudio.section() === 'create'", timeout=15)
    r.check("Deep link back into Studio lands on Create",
            r.ev("window.YouTubeStudio.section() === 'create'"))


def phase_settings(r):
    print("  -- settings")
    # Settings renders after /settings + /channels + /llm/status resolve; poll
    # for the rendered content instead of sleeping a fixed time.
    r.ev("document.querySelector('[data-yt-section=\"settings\"]').click()")
    r.wait_for("(document.getElementById('yt-channel-list')||{innerText:''}).innerText.length > 0", timeout=15)
    r.wait_for("(document.getElementById('yt-llm-status')||{innerText:''}).innerText.length > 0", timeout=15)
    r.check("Channel profile card present with seeded channel",
            r.ev("!!document.getElementById('yt-channel-create')"
                 " && (document.getElementById('yt-channel-list')||{innerText:''}).innerText.length > 0"))
    llm = r.ev("(document.getElementById('yt-llm-status')||{innerText:''}).innerText")
    r.check("LLM status shows the opencode profile and GLM model",
            isinstance(llm, str) and "OpenCode" in llm and "glm" in llm.lower(), repr(llm)[:140])
    conn = r.ev("(document.getElementById('yt-connection-status')||{innerText:''}).innerText")
    r.check("Connection card is honest about OAuth",
            isinstance(conn, str) and "OAuth" in conn, repr(conn)[:140])


def phase_flags(r):
    print("  -- analytics flag")
    # The flag content renders after the /features fetch; poll for the text
    # instead of sleeping a fixed time so the check is not load-sensitive.
    r.ev("document.querySelector('[data-yt-section=\"analytics\"]').click()")
    r.wait_for("(document.getElementById('yt-analytics-flag')||{innerText:''})"
               ".innerText.indexOf('Not available yet') !== -1", timeout=15)
    flag = r.ev("(document.getElementById('yt-analytics-flag')||{innerText:''}).innerText")
    r.check("Analytics shows the honest not-configured state",
            isinstance(flag, str) and "Not available yet" in flag, repr(flag)[:140])
    r.ev("document.querySelector('[data-yt-section=\"templates\"]').click()")
    time.sleep(0.4)
    r.check("Templates shows the Phase 3 placeholder",
            r.ev("document.querySelector('[data-yt-panel=\"templates\"]').innerText.indexOf('Phase 3') !== -1"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8099")
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    parser.add_argument("--serve", action="store_true",
                        help="start app.py on the --base port for the run")
    args = parser.parse_args()

    server = None
    if args.serve:
        port = args.base.rstrip("/").rsplit(":", 1)[-1]
        server = subprocess.Popen(
            [sys.executable, os.path.join(PROJ, "app.py"), port],
            cwd=PROJ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, DENNIS_NO_DENMA="1"))
        for _ in range(60):
            try:
                with urllib.request.urlopen(args.base + "/", timeout=5) as res:
                    if res.status == 200:
                        break
            except Exception:
                time.sleep(0.5)

    failures = []
    code = 1
    try:
        runner = Runner(args.base, args.chrome, 9343)
        runner.start_browser()
        try:
            open_youtube_studio(runner)
            phase_landing(runner)
            phase_one_panel_rule(runner)
            phase_projects_scripts(runner)
            phase_legacy_redirects(runner)
            phase_settings(runner)
            phase_flags(runner)
            time.sleep(0.5)
            errors = [e for e in runner.cdp.runtime_errors()
                      if e and "favicon" not in e.lower()
                      and "Failed to load resource" not in e]
            runner.check("No JS exceptions during the run", not errors, "; ".join(errors[:4]))
        finally:
            runner.stop_browser()
        failures.extend(runner.failures)
        print()
        if failures:
            print("FAILURES:", failures)
        else:
            print("ALL YOUTUBE STUDIO PHASE 2 E2E CHECKS PASSED")
            code = 0
    finally:
        if server:
            server.terminate()
    return code


if __name__ == "__main__":
    sys.exit(main())
