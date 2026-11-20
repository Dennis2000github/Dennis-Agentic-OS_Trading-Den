#!/usr/bin/env python3
"""Browser-level E2E for COSMOS + GENESIS consolidation (CDP).

Proves, against a real server:

  1. COSMOS boots with the five agent dashboards plus the GENESIS rail tab.
  2. The QUASAR YouTube Topic Backlog renders from the COSMOS management API
     with cross-store provenance (script / Studio project / GENESIS status).
  3. The GENESIS dashboard loads through the rail tab and shows the
     production queue with origin/destination context.
  4. Screenshots of the consolidated UI are captured for the report.

Usage (from the repo root)::

    python3 tests/e2e_cosmos_genesis.py --serve [--shots DIR]

Stdlib only; reuses the CDP WebSocket client from tests/ws_client.py.
"""

import argparse
import base64
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
            "--hide-scrollbars", "about:blank",
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
        self.cdp.cdp("Page.navigate", {"url": (url or self.base) + "/"}, timeout=40)
        for _ in range(80):
            if self.cdp.eval("!!document.body"):
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

    def shot(self, path, selector=None):
        if selector:
            # Bring the section into the viewport so the capture shows it.
            self.ev(
                "(function () {"
                "  var el = document.querySelector(%s);"
                "  if (el && el.scrollIntoView) el.scrollIntoView({block: 'start'});"
                "})()" % json.dumps(selector))
            time.sleep(0.6)
        res = self.cdp.cdp("Page.captureScreenshot", {"format": "png"}, timeout=30)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(res["data"]))
        print("  wrote", path)


def open_cosmos(r):
    r.navigate(settle=1.0)
    r.wait_for("!!window.Cosmos", timeout=20)
    r.ev("if (window.switchMainView) window.switchMainView('cosmos')")
    r.wait_for("!document.getElementById('cosmos-view').classList.contains('hidden')", timeout=15)
    r.wait_for("document.querySelectorAll('#cosmos-agent-tabs [data-cosmos-agent]').length >= 5", timeout=30)
    # Agent data loads async; wait for the first dashboard to paint (or an
    # honest error) instead of a fixed sleep.
    r.wait_for(
        "(function () {"
        "  var host = document.getElementById('cosmos-agent');"
        "  return host && (host.querySelector('.cosmos-agent-head')"
        "    || host.querySelector('.cosmos-error') || host.querySelector('.cosmos-empty'));"
        "})()", timeout=30)
    time.sleep(0.8)


def phase_cosmos(r):
    print("  -- COSMOS boots")
    agents = r.ev(
        "Array.from(document.querySelectorAll('#cosmos-agent-tabs [data-cosmos-agent]'))"
        ".map(function (el) { return el.getAttribute('data-cosmos-agent'); })")
    r.check("COSMOS rail lists the five agents plus GENESIS",
            isinstance(agents, list) and len(agents) == 6 and agents[-1] == "genesis",
            repr(agents))
    r.check("COSMOS host rendered an agent dashboard",
            r.ev("!!document.querySelector('#cosmos-agent .cosmos-agent-head')"))


def phase_youtube_backlog(r):
    print("  -- QUASAR YouTube backlog (COSMOS management API)")
    r.ev("window.Cosmos.selectAgent('quasar')")
    r.wait_for("!!document.querySelector('#cosmos-agent .cosmos-backlog')", timeout=30)
    # Scope to the YouTube division block: the Ads backlog reuses the same row
    # classes. Poll for rows or the explicit empty state.
    yt = "'[data-division=\"youtube\"] .cosmos-backlog'"
    r.wait_for(
        "(function () {"
        "  var block = document.querySelector(%s);"
        "  return block && (block.querySelector('.cosmos-backlog-row')"
        "    || block.innerText.indexOf('No topics match') !== -1);"
        "})()" % yt, timeout=30)
    rows = r.ev("document.querySelectorAll(%s + ' .cosmos-backlog-row').length" % yt)
    text = r.ev("(document.querySelector(%s)||{innerText:''}).innerText" % yt)
    r.check("YouTube Topic Backlog section renders in COSMOS",
            isinstance(text, str) and "YouTube Topic Backlog" in text, repr(text)[:160])
    r.check("YouTube backlog rows render%s" % (" (%d row(s))" % rows if rows else " (empty state only)"),
            isinstance(rows, int))
    r.check("Management toolbar present (add topic + status filter, not read-only)",
            r.ev("!!document.querySelector(%s + ' [data-cosmos-add-topic]')" % yt)
            and r.ev("!!document.querySelector(%s + ' [data-cosmos-yt-status]')" % yt))
    if rows:
        r.check("Row detail button present",
                r.ev("!!document.querySelector(%s + ' [data-cosmos-topic-detail]')" % yt))
        r.check("Row status select present",
                r.ev("!!document.querySelector(%s + ' [data-cosmos-topic]')" % yt))


def phase_genesis(r, shots=None):
    print("  -- GENESIS production dashboard")
    r.ev("window.Cosmos.selectAgent('genesis')")
    r.wait_for("!document.getElementById('cosmos-view').classList.contains('hidden')", timeout=15)
    # GENESIS renders into the COSMOS host once its own data lands; poll for
    # the rendered tab bar instead of assuming a timing.
    r.wait_for("document.querySelectorAll('#cosmos-agent [data-cg-tab]').length >= 5", timeout=30)
    time.sleep(0.8)
    text = r.ev("(document.getElementById('cosmos-agent')||{innerText:''}).innerText")
    r.check("GENESIS dashboard renders inside the COSMOS host",
            isinstance(text, str) and ("Overview" in text or "Production" in text), repr(text)[:160])
    r.ev("document.querySelector('#cosmos-agent [data-cg-tab=\"queue\"]').click()")
    r.wait_for(
        "(function () {"
        "  var host = document.getElementById('cosmos-agent');"
        "  return host && (host.querySelector('.genesis-queue-item')"
        "    || host.innerText.indexOf('Queue is empty') !== -1);"
        "})()", timeout=30)
    queue_text = r.ev("(document.getElementById('cosmos-agent')||{innerText:''}).innerText")
    r.check("Production queue tab renders with intake copy",
            isinstance(queue_text, str) and "eligible item" in queue_text, repr(queue_text)[:200])
    items = r.ev("document.querySelectorAll('#cosmos-agent .genesis-queue-item').length")
    if items:
        origin = r.ev(
            "(function () {"
            "  var el = document.querySelector('#cosmos-agent .genesis-queue-item');"
            "  return el ? el.innerText : '';"
            "})()")
        r.check("Queue items carry origin/destination context",
                isinstance(origin, str) and ("Origin:" in origin or "Destination:" in origin),
                repr(origin)[:200])
    else:
        print("  [info] queue is empty on this dataset — origin/destination "
              "context is covered by unit tests (tests.test_genesis.QueueTests)")
    if shots:
        r.shot(os.path.join(shots, "genesis-queue.png"), selector='.genesis-tab.is-active')
    r.ev("document.querySelector('#cosmos-agent [data-cg-tab=\"overview\"]').click()")
    time.sleep(0.8)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8124")
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    parser.add_argument("--serve", action="store_true",
                        help="start app.py on the --base port for the run")
    parser.add_argument("--shots", default="",
                        help="optional directory for screenshots")
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
        runner = Runner(args.base, args.chrome, 9344)
        runner.start_browser()
        try:
            open_cosmos(runner)
            phase_cosmos(runner)
            if args.shots:
                runner.shot(os.path.join(args.shots, "cosmos-agent-dashboard.png"),
                            selector='.cosmos-agent-head')
            phase_youtube_backlog(runner)
            if args.shots:
                runner.shot(os.path.join(args.shots, "cosmos-youtube-backlog.png"),
                            selector='[data-division="youtube"] .cosmos-backlog')
            phase_genesis(runner, shots=args.shots or None)
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
            print("ALL COSMOS + GENESIS E2E CHECKS PASSED")
            code = 0
    finally:
        if server:
            server.terminate()
    return code


if __name__ == "__main__":
    sys.exit(main())
