#!/usr/bin/env python3
"""Capture Ads Studio screenshots at desktop + narrow widths (headless Chrome + CDP).

Usage (server already running on --base)::

    python3 tests/screenshot_ads_studio.py --out /tmp/shots [--steps 1,2,3,4,5] [--setup]

Writes full-page PNGs: step-<n>.png, step-<n>-narrow.png, setup.png.
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

DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def connect(chrome, port):
    proc = subprocess.Popen([
        chrome, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", f"--remote-debugging-port={port}",
        f"--user-data-dir={tempfile.mkdtemp(prefix='dennis-shot-')}",
        "--window-size=1600,1000", "--hide-scrollbars", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    targets = None
    for _ in range(80):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=5) as res:
                targets = json.loads(res.read().decode())
            break
        except Exception:
            time.sleep(0.25)
    if not targets:
        raise RuntimeError("Chrome DevTools never came up")
    page = next(t for t in targets if t["type"] == "page")
    cdp = WSClient(page["webSocketDebuggerUrl"])
    cdp.cdp("Runtime.enable")
    cdp.cdp("Page.enable")
    cdp.proc = proc
    return cdp


def shot(cdp, path):
    res = cdp.cdp("Page.captureScreenshot", {"format": "png"}, timeout=30)
    with open(path, "wb") as fh:
        fh.write(base64_bytes(res["data"]))
    print("wrote", path)


def base64_bytes(data):
    import base64
    return base64.b64decode(data)


def set_viewport(cdp, w, h):
    cdp.cdp("Emulation.setDeviceMetricsOverride",
            {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": w < 900})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8099")
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    parser.add_argument("--out", default="/tmp/ads-shots")
    parser.add_argument("--port", type=int, default=9343)
    parser.add_argument("--steps", default="1,2,3,4,5")
    parser.add_argument("--setup", action="store_true")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    cdp = connect(args.chrome, args.port)
    try:
        cdp.cdp("Page.navigate", {"url": args.base + "/"}, timeout=40)
        for _ in range(80):
            if cdp.eval("!!document.body"):
                break
            time.sleep(0.25)
        for _ in range(40):
            if cdp.eval("!!window.StudioFashion"):
                break
            time.sleep(0.25)
        time.sleep(1.5)
        cdp.eval("window.location.hash = '#studio'")
        time.sleep(0.8)
        for _ in range(40):
            if cdp.eval("!!(window.Studio && Studio.setTab && window.StudioFashion)"):
                break
            time.sleep(0.25)
        cdp.eval("if (window.Studio && Studio.setTab) Studio.setTab('fashion')")
        time.sleep(0.8)
        cdp.eval("if (window.StudioFashion) StudioFashion.show()")
        time.sleep(1.5)

        set_viewport(cdp, 1600, 1000)
        for step in [s.strip() for s in args.steps.split(",") if s.strip()]:
            cdp.eval(f"document.getElementById('step-btn-{step}').click()")
            time.sleep(1.0)
            shot(cdp, os.path.join(args.out, f"step-{step}.png"))
            set_viewport(cdp, 780, 1100)
            time.sleep(0.6)
            shot(cdp, os.path.join(args.out, f"step-{step}-narrow.png"))
            set_viewport(cdp, 1600, 1000)
            time.sleep(0.6)

        if args.setup:
            cdp.eval("if (window.Studio) Studio.setTab('setup')")
            time.sleep(1.2)
            shot(cdp, os.path.join(args.out, "setup.png"))
    finally:
        try:
            cdp.proc.terminate()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
