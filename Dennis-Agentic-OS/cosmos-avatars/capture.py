#!/usr/bin/env python3
"""Render + capture harness screenshots for the cosmic avatar pipeline.

Starts a local HTTP server for cosmos-avatars/harness, opens headless Chrome
(CDP), renders each requested (emblem, view, size) and writes PNGs.

Usage:
  python3 capture.py --out renders/andromeda --shots "blockout:front,blockout:tilted"
    [--emblem andromeda] [--size 512] [--port 8130] [--keep-server]
Shots spec: "<name>:<view>[,<name>:view...]" — files land as <out>/<name>-<view>.png
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tests"))
from ws_client import WSClient  # noqa: E402

DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROJ = os.path.dirname(HERE)


def serve(port):
    handler = partial(SimpleHTTPRequestHandler, directory=HERE)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def open_chrome(chrome, cdp_port):
    profile = "/tmp/opencode/avatar-harness-chrome"
    os.makedirs(profile, exist_ok=True)
    proc = subprocess.Popen([
        chrome, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={profile}", "--hide-scrollbars",
        "--enable-unsafe-swiftshader", "--use-gl=swiftshader",
        "--window-size=900,900", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    targets = None
    for _ in range(80):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{cdp_port}/json/list", timeout=5) as res:
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


def capture(cdp, base, emblem, name, view, size, out_dir):
    url = f"{base}/harness/index.html?emblem={emblem}&view={view}&size={size}"
    for attempt in range(2):
        try:
            cdp.cdp("Page.navigate", {"url": url}, timeout=40)
            break
        except Exception:
            time.sleep(1.0)
    ready = False
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            ready = cdp.eval("String(window.__avatarReady || '')") == "ready"
        except Exception:
            ready = False
        if ready:
            break
        time.sleep(0.25)
    meta = {}
    try:
        meta = cdp.eval("JSON.stringify(window.__avatarMeta || {})")
        meta = json.loads(meta) if isinstance(meta, str) else {}
    except Exception:
        pass
    if not ready:
        print(f"  [TIMEOUT] {emblem} {name}:{view} (meta={meta})")
        return False
    clip = {}
    try:
        rect = cdp.eval(
            "(function(){var c=document.querySelector('canvas');"
            "var r=c.getBoundingClientRect();"
            "return JSON.stringify({x:r.x,y:r.y,width:r.width,height:r.height,scale:window.devicePixelRatio||1});})()")
        rect = json.loads(rect)
        clip = {"clip": {"x": rect["x"], "y": rect["y"],
                         "width": rect["width"], "height": rect["height"],
                         "scale": 1}}
    except Exception:
        clip = {}
    res = cdp.cdp("Page.captureScreenshot", dict({"format": "png"}, **clip), timeout=30)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}-{view}.png")
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(res["data"]))
    print(f"  wrote {path} (children={meta.get('children')})")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--emblem", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--shots", required=True, help="name:view,name:view")
    parser.add_argument("--size", type=int, default=512)
    parser.add_argument("--base", default="http://127.0.0.1:8130")
    parser.add_argument("--port", type=int, default=8130)
    parser.add_argument("--cdp-port", type=int, default=9345)
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    args = parser.parse_args()

    httpd = serve(args.port)
    cdp = open_chrome(args.chrome, args.cdp_port)
    failures = []
    try:
        for pair in args.shots.split(","):
            pair = pair.strip()
            if not pair:
                continue
            name, view = pair.split(":", 1)
            ok = capture(cdp, args.base, args.emblem, name, view, args.size, args.out)
            if not ok:
                failures.append(f"{name}:{view}")
    finally:
        try:
            cdp.proc.terminate()
        except Exception:
            pass
        httpd.shutdown()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
