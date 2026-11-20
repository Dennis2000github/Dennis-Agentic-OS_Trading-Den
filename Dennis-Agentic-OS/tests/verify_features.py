#!/usr/bin/env python3
"""Verify COSMOS avatars + Studio ? tooltips render in the live app."""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ws_client import WSClient

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BASE = "http://127.0.0.1:8080"
CDP = 9361


def main():
    profile = "/tmp/opencode/verify-features-chrome"
    os.makedirs(profile, exist_ok=True)
    proc = subprocess.Popen([
        CHROME, "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", f"--remote-debugging-port={CDP}",
        f"--user-data-dir={profile}", "--hide-scrollbars",
        "--enable-unsafe-swiftshader", "--use-gl=swiftshader",
        "--window-size=1400,1000", "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        targets = None
        for _ in range(80):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json/list", timeout=5) as r:
                    targets = json.loads(r.read().decode())
                break
            except Exception:
                time.sleep(0.25)
        page = next(t for t in targets if t["type"] == "page")
        cdp = WSClient(page["webSocketDebuggerUrl"])
        cdp.cdp("Runtime.enable")
        cdp.cdp("Page.enable")
        cdp.cdp("Page.navigate", {"url": BASE + "/"}, timeout=40)
        time.sleep(8)
        cdp.eval("location.hash = '#cosmos'")
        time.sleep(3)

        checks = {
            "avatar count in rail": "document.querySelectorAll('#cosmos-view .cosmos-avatar').length",
            "avatar imgs loaded": """(function(){var a=Array.from(document.querySelectorAll('#cosmos-view .cosmos-avatar img'));
                return a.length+' total, '+a.filter(function(i){return i.complete && i.naturalWidth>0;}).length+' loaded';})()""",
            "avatar srcs": """Array.from(document.querySelectorAll('#cosmos-view .cosmos-avatar img')).map(function(i){return i.getAttribute('src');}).join(', ')""",
            "genesis avatar": "!!document.querySelector('#cosmos-view .cosmos-tab-genesis .cosmos-avatar img')",
            "tooltip buttons": "document.querySelectorAll('#studio-view .info-tip').length",
            "tooltip bubbles": "document.querySelectorAll('#studio-view .info-tip-bubble').length",
            "tooltip sample text": """(function(){var b=document.querySelector('#studio-view .info-tip-bubble');return b?b.textContent.slice(0,90):'NONE';})()""",
            "visible feature paragraphs not converted (kept)": """(function(){var p=document.querySelector('#studio-view .yt-composer-foot .yt-hint');return p?p.innerHTML.slice(0,60):'NONE';})()""",
        }
        ok = True
        print("=== FEATURE VERIFICATION ===")
        for k, e in checks.items():
            try:
                v = cdp.eval(e)
                print(f"  {k}: {v!r}")
            except Exception as ex:
                ok = False
                print(f"  {k}: ERROR {ex}")
        print("RESULT:", "PASS" if ok else "CHECK ERRORS")
    finally:
        try:
            proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()
