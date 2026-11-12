#!/usr/bin/env python3
"""Browser-level E2E for the Ads Studio five-step workflow (headless Chrome + CDP).

Proves, against a real server:

  1. The stepper renders 5 steps: Model, Product/Props, Dressing,
     Reference Video, Production.
  2. Step 1 (Model) is the entry step; its tabs and controls render.
  3. Step 2 (Product/Props) has the upload-left / prompt-right split panel,
     Preset Images grid, and the Prompt 2 seed button.
  4. Step 3 (Dressing) has the Prompt 3 seed button, generate + skip controls.
  5. Step 4 (Reference Video) has the storyboard prompt box and a skip that
     jumps to Production with the skip flag set.
  6. Step 5 (Production) has both template dropdowns populated (10 environment
     groups, 5 video-direction groups), the final prompt, and the Generate
     dock button.
  7. The result action bar (play/edit/library/download/delete) exists.
  8. Preset Video remains the default preview page and renders.
  9. No console errors during the run.

Usage (from the repo root)::

    python3 tests/e2e_ads_studio.py --serve

Requires Apple Google Chrome at the default path (override with --chrome).
Stdlib only; reuses the CDP WebSocket client from tests/ws_client.py.

Exit code 0 means every check passed. Each check prints [ok]/[FAIL].
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

PROMPT_2 = "use the attached images to create product sheet"
PROMPT_3 = "step 1 model dress/wear like step 2."


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
        """DevTools occasionally wedges on navigate; open a fresh session."""
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


def open_studio(r):
    """Navigate home, wait for boot, switch to the Studio view."""
    r.navigate()
    ok = r.wait_for("!!document.getElementById('studio-view')", timeout=30)
    r.check("App booted and Studio view exists in the DOM", ok)
    visible = False
    for _ in range(4):  # hash routing binds during boot; retry a few times
        r.ev("window.location.hash = '#studio'")
        time.sleep(0.8)
        if r.ev("!document.getElementById('studio-view').classList.contains('hidden')"):
            visible = True
            break
    r.check("Studio view becomes visible", visible)
    r.wait_for("!!window.StudioFashion", timeout=20)
    r.ev("if (window.StudioFashion) StudioFashion.show()")
    r.wait_for("!!document.getElementById('collart-stepper')", timeout=20)
    time.sleep(1.0)


def phase_steps(r):
    print("  -- stepper and step sections")
    labels = r.ev(
        "Array.from(document.querySelectorAll('#collart-stepper .collart-stepchip span:last-child'))"
        ".map(function (el) { return el.textContent; })")
    r.check("Stepper has 5 steps", isinstance(labels, list) and len(labels) == 5, repr(labels))
    expected = ["Model", "Product/Props", "Dressing", "Reference Video", "Production"]
    r.check("Step labels are Model / Product-Props / Dressing / Reference Video / Production",
            labels == expected, repr(labels))

    visible = r.ev(
        "Array.from({length: 5}, function (_, i) {"
        "  var el = document.getElementById('step-' + (i + 1) + '-content');"
        "  return el ? !el.classList.contains('hidden') : null; })")
    r.check("Step 1 (Model) is the entry step", visible == [True, False, False, False, False], repr(visible))

    r.check("Step 1 preset model grid present",
            r.ev("!!document.getElementById('model-grid')"))
    r.check("Step 1 Create Model tab present",
            r.ev("!!document.getElementById('tab-model-create')"))


def phase_step2(r):
    print("  -- step 2: Product/Props")
    r.ev("document.getElementById('step-btn-2').click()")
    time.sleep(0.4)
    r.check("Step 2 section visible",
            r.ev("!document.getElementById('step-2-content').classList.contains('hidden')"))
    r.check("Upload panel (left) present",
            r.ev("!!document.getElementById('fashion-product-drop') && !!document.getElementById('fashion-product-file')"))
    r.check("Prompt chatbox (right) present",
            r.ev("!!document.getElementById('fashion-product-prompt') && !!document.getElementById('fashion-product-generate')"))
    r.check("Preset Images grid present",
            r.ev("!!document.getElementById('product-preset-grid')"))
    r.ev("document.getElementById('fashion-prompt2-seed').click()")
    time.sleep(0.2)
    value = r.ev("document.getElementById('fashion-product-prompt').value")
    r.check("Prompt 2 seed fills the product sheet prompt", value == PROMPT_2, repr(value))
    r.check("Carried bar exists below the stepper and is hidden while nothing is selected",
            r.ev("!!document.getElementById('fashion-carried')")
            and r.ev("document.getElementById('fashion-carried').classList.contains('hidden')"))


def phase_step3(r):
    print("  -- step 3: Dressing")
    r.ev("document.getElementById('step-btn-3').click()")
    time.sleep(0.4)
    r.check("Step 3 section visible",
            r.ev("!document.getElementById('step-3-content').classList.contains('hidden')"))
    r.check("Dressing prompt + generate present",
            r.ev("!!document.getElementById('fashion-dressing-prompt') && !!document.getElementById('fashion-dressing-generate')"))
    r.ev("document.getElementById('fashion-prompt3-seed').click()")
    time.sleep(0.2)
    value = r.ev("document.getElementById('fashion-dressing-prompt').value")
    r.check("Prompt 3 seed fills the dressing prompt", value == PROMPT_3, repr(value))
    r.check("Skip control present",
            r.ev("!!document.querySelector('[data-dressing-skip]')"))


def phase_step4(r):
    print("  -- step 4: Reference Video")
    r.ev("document.getElementById('step-btn-4').click()")
    time.sleep(0.4)
    r.check("Step 4 section visible",
            r.ev("!document.getElementById('step-4-content').classList.contains('hidden')"))
    r.check("Preset Video tab shown by default",
            r.ev("!document.getElementById('step1-preset-view').classList.contains('hidden')"
                 " && document.getElementById('tab-preset-video').classList.contains('is-active')"))
    r.check("Storyboard prompt box present",
            r.ev("!!document.getElementById('fashion-storyboard')"))
    r.check("Skip control present",
            r.ev("!!document.querySelector('[data-ref-skip]')"))
    r.ev("document.getElementById('fashion-storyboard').value = "
         "'create a storyboard, 9:16, each scene is 0.3sec, total duration is 15sec'")
    r.ev("document.querySelector('[data-ref-skip]').click()")
    time.sleep(0.4)
    r.check("Skip jumps to Production (step 5)",
            r.ev("!document.getElementById('step-5-content').classList.contains('hidden')"))
    r.check("Step 4 chip marked done after skip",
            r.ev("document.getElementById('step-btn-4').classList.contains('is-done')"))


def phase_step5(r):
    print("  -- step 5: Production")
    r.check("Both template dropdowns present",
            r.ev("!!document.getElementById('fashion-env-template')"
                 " && !!document.getElementById('fashion-video-template')"))
    env_groups = r.ev(
        "document.getElementById('fashion-env-template')"
        ".querySelectorAll('optgroup').length")
    r.check("Model Placement & Environment dropdown has 10 groups",
            env_groups == 10, repr(env_groups))
    env_options = r.ev(
        "document.getElementById('fashion-env-template')"
        ".querySelectorAll('option').length")
    r.check("Environment dropdown has 300 template options + placeholder",
            env_options == 301, repr(env_options))
    video_groups = r.ev(
        "document.getElementById('fashion-video-template')"
        ".querySelectorAll('optgroup').length")
    r.check("Video Generation dropdown has 5 groups", video_groups == 5, repr(video_groups))
    video_options = r.ev(
        "document.getElementById('fashion-video-template')"
        ".querySelectorAll('option').length")
    r.check("Video dropdown has 50 template options + placeholder",
            video_options == 51, repr(video_options))

    r.ev("document.getElementById('fashion-final-prompt').value = "
         "'at 0 to 5sec the model showing the product to the camera, at 5sec to 15sec is the motion transfer'")
    r.ev("document.getElementById('fashion-env-template').value = "
         "'Minimalist Studio & Clean Commercial Sets::0';"
         "document.getElementById('fashion-env-template').dispatchEvent(new Event('change'));"
         "document.getElementById('fashion-video-template').value = "
         "'Runway, Walk & Movement::0';"
         "document.getElementById('fashion-video-template').dispatchEvent(new Event('change'))")
    time.sleep(0.2)
    r.check("Generate dock button visible on step 5",
            r.ev("!document.getElementById('generate-btn').classList.contains('hidden')"))
    r.check("Result actions bar (play/edit/library/download/delete) exists",
            r.ev("!!document.getElementById('fashion-result-actions')") and
            r.ev("document.querySelectorAll('#fashion-result-actions [data-result-action]').length") == 5)
    r.check("Quick configuration bar present",
            r.ev("!!document.getElementById('fashion-resolution')"
                 " && !!document.getElementById('fashion-duration')"
                 " && !!document.getElementById('fashion-aspect')"))


def phase_preset_page(r):
    print("  -- preset video preview page")
    r.ev("if (window.StudioFashion) StudioFashion.showPresetPage()")
    time.sleep(0.6)
    ok = r.ev("!!document.getElementById('preset-page-grid')")
    r.check("Preset Video page grid renders", ok)


def phase_free_engines(r):
    """Step 5 renders the free video engines (Veo via Google AI)."""
    print("  -- free engines (Pollinations + Google AI)")
    ok = r.wait_for("!!(window.StudioFashion && StudioFashion.state.freeModels)", timeout=20)
    r.check("Free engine capabilities load", ok)
    count = r.ev("document.querySelectorAll('#fashion-engines .collart-engine').length")
    r.check("Step 5 shows 3 free Veo engines", count == 3, repr(count))
    note = r.ev("document.getElementById('fashion-engine-mode').textContent")
    r.check("Engine note names the Google Flow engine", "Google Flow" in (note or ""), repr(note))
    pairs = r.ev("['1','2','3','5'].map(function (s) {"
                 "  var p = document.getElementById('fashion-gen-provider-' + s);"
                 "  var m = document.getElementById('fashion-gen-model-' + s);"
                 "  return [p ? p.options.length : 0, m ? m.options.length : 0]; })")
    r.check("Provider + Model dropdown pairs present in all four generation steps",
            str(pairs).replace(" ", "") == "[[4,1],[4,1],[4,1],[3,3]]", repr(pairs))
    providerLabels = r.ev("Array.from(document.getElementById('fashion-gen-provider-1').options)"
                          ".map(function (o) { return o.textContent; })")
    r.check("Provider dropdown lists connected providers",
            providerLabels and any("Google" in t for t in providerLabels)
            and any("Higgsfield" in t for t in providerLabels)
            and any("Pollinations" in t or "Auto" in t for t in providerLabels),
            repr(providerLabels))
    groups = r.ev("Array.from(document.getElementById('fashion-gen-model-5').querySelectorAll('optgroup'))"
                  ".map(function (g) { return g.label; })")
    r.check("Step 5 Model dropdown is classified by mode (optgroups)",
            groups and len(groups) >= 2, repr(groups))
    google = r.ev("StudioFashion.state.freeModels.google")
    banner = r.ev("document.getElementById('fashion-setup-hint').classList.contains('hidden')")
    if not google:
        r.check("Without a Google key the setup banner offers the free key",
                banner == False and "free" in (r.ev("document.getElementById('fashion-setup-hint-text').textContent") or ""))
    else:
        r.check("With a Google key the setup banner stays hidden", banner)


def phase_registry(r):
    """Provider Setup renders the registry with connected providers."""
    print("  -- provider registry (Setup)")
    r.ev("window.location.hash = '#studio'")
    r.ev("if (window.Studio && Studio.setTab) Studio.setTab('setup')")
    ok = r.wait_for("!!document.querySelector('.studio-registry-row')", timeout=20)
    r.check("Registry renders provider rows", ok)
    rows = r.ev("document.querySelectorAll('.studio-registry-row').length")
    r.check("Registry lists all 9 providers", rows == 9, repr(rows))
    connected = r.ev("Array.from(document.querySelectorAll('.studio-registry-row')).filter("
                     "function (row) { return row.classList.contains('connected'); }).length")
    r.check("At least Auto + Pollinations always connected", connected >= 2, repr(connected))
    r.ev("if (window.Studio && Studio.setTab) Studio.setTab('fashion')")
    time.sleep(0.5)


def phase_credential_error(r):
    """Auth failures from the free engines surface as actionable guidance."""
    print("  -- credential error UX")
    # Seed a full draft, then pretend a Google key exists. The dock validity
    # is recomputed by dispatching a change on the engine select.
    r.ev("StudioFashion.refresh()")
    time.sleep(1.8)
    r.ev("StudioFashion.state.model = {url: 'https://example.com/m.jpg', name: 'M'};"
         "StudioFashion.state.reference = {url: 'https://example.com/r.mp4', name: 'R', duration: 5};"
         "StudioFashion.state.freeModels = Object.assign({}, StudioFashion.state.freeModels, {google: true});"
         "var sel = document.getElementById('fashion-model-select');"
         "sel.dispatchEvent(new Event('change', {bubbles: true}));")
    time.sleep(0.4)
    r.check("Generate button enabled with a full draft",
            r.ev("!document.getElementById('generate-btn').disabled"))
    # Stub the free job submit with the engine's raw key rejection.
    r.ev("var _f = window.fetch; window.__realFetch = _f; window.fetch = function(path, opts){"
         "  if (path.indexOf('/api/studio/free/jobs') !== -1) {"
         "    return Promise.resolve(new Response("
         "      JSON.stringify({error: 'Google AI rejected the key (Invalid key).', code: 'invalid_key'}),"
         "      {status: 502}));"
         "  }"
         "  return _f.apply(window, arguments); }")
    r.ev("document.getElementById('generate-btn').click()")
    time.sleep(0.8)
    toast = r.ev("var el = document.getElementById('studio-toast'); el ? el.textContent : ''")
    r.check("Key rejection toast is actionable (names Google AI + fix)",
            "Google AI rejected the key" in (toast or "") and "Provider Setup" in (toast or ""),
            repr(toast))
    r.check("Setup banner appears with re-save instructions",
            r.ev("!document.getElementById('fashion-setup-hint').classList.contains('hidden')")
            and "rejected your key" in (r.ev("document.getElementById('fashion-setup-hint-text').textContent") or ""))

    # A valid submit posts the free payload shape.
    r.ev("window.__posted = null; window.fetch = function(path, opts){"
         "  if (path.indexOf('/api/studio/free/jobs') !== -1 && opts && opts.method === 'POST') {"
         "    window.__posted = JSON.parse(opts.body);"
         "    return Promise.resolve(new Response(JSON.stringify({job: {id: 'free_x', state: 'queued',"
         "      terminal: false, outputs: [], engine: 'veo-3.0-fast-generate-001'}}), {status: 200}));"
         "  }"
         "  return window.__realFetch.apply(window, arguments); }")
    r.ev("document.getElementById('generate-btn').click()")
    time.sleep(0.8)
    posted = r.ev("window.__posted")
    r.check("Submit posts a free video job", (posted or {}).get("kind") == "video", repr(posted))
    r.check("Submit carries the model image and aspect",
            (posted or {}).get("image_urls") == ["https://example.com/m.jpg"]
            and (posted or {}).get("aspect_ratio") == "9:16", repr(posted))
    r.check("Submit carries duration and composed prompt",
            (posted or {}).get("duration") is not None
            and len((posted or {}).get("prompt") or "") > 10, repr(posted))
    r.check("Result panel visible after submit",
            r.ev("!document.getElementById('fashion-result').classList.contains('hidden')"))


def phase_carried_bar(r):
    """One carried bar below the stepper, filled from every step's selection."""
    print("  -- carried bar (below the steps)")
    r.ev("window.fetch = window.__realFetch || window.fetch;")
    r.ev("StudioFashion.state.credentialProblem = false;"
         "StudioFashion.state.model = {url: 'https://example.com/m.jpg', name: 'Test model'};"
         "StudioFashion.state.productSelected = ['https://example.com/product.jpg'];"
         "StudioFashion.state.dressed = {url: 'https://example.com/dressed.jpg', name: 'Dressed'};"
         "StudioFashion.state.reference = {url: 'https://example.com/ref.mp4', name: 'Ref', duration: 5};"
         "StudioFashion.refresh()")
    time.sleep(1.5)
    r.check("Carried bar visible with selections",
            r.ev("!document.getElementById('fashion-carried').classList.contains('hidden')"))
    items = r.ev("document.querySelectorAll('#fashion-carried .collart-carrieditem').length")
    r.check("Carried bar shows 4 entries (model, product, dressed, reference)",
            items == 4, repr(items))
    # It must be above the step sections (below the stepper) in DOM order.
    order = r.ev("(function(){var bar=document.getElementById('fashion-carried');"
                 "var s1=document.getElementById('step-1-content');"
                 "return !!(bar.compareDocumentPosition(s1) & Node.DOCUMENT_POSITION_FOLLOWING);})()")
    r.check("Carried bar sits above every step section (below the stepper)", order)


def phase_free_upload(r):
    """Uploading must store locally — no provider, no credential errors."""
    print("  -- free upload route (all steps use it)")
    # A 1x1 transparent PNG, uploaded exactly the way the UI does.
    r.ev("window.__up = null;"
         "var png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';"
         "window.fetch('/api/studio/free/uploads', {method: 'POST',"
         "  headers: {'Content-Type': 'application/json'},"
         "  body: JSON.stringify({filename: 'e2e.png', content_type: 'image/png', data_base64: png})"
         "}).then(function (res) { return res.json().then(function (j) {"
         "  window.__up = {status: res.status, media_id: j.media_id, url: j.public_url}; }); })")
    time.sleep(1.5)
    upload = r.ev("window.__up")
    r.check("Free upload stores the file locally", upload and upload.get("status") == 200
            and str(upload.get("media_id", "")).startswith("med_"), repr(upload))
    r.check("Uploaded media is served back",
            r.ev("window.__up && window.__up.url"
                 " ? fetch(window.__up.url).then(function (res) {"
                 "     return res.ok && (res.headers.get('Content-Type') || '').indexOf('image/') === 0;"
                 "   }) : Promise.resolve(false)", timeout=20))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8099")
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    parser.add_argument("--serve", action="store_true",
                        help="start server.py on the --base port for the run")
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
        runner = Runner(args.base, args.chrome, 9342)
        runner.start_browser()
        try:
            open_studio(runner)
            phase_steps(runner)
            phase_step2(runner)
            phase_step3(runner)
            phase_step4(runner)
            phase_step5(runner)
            phase_preset_page(runner)
            phase_free_engines(runner)
            phase_free_upload(runner)
            phase_registry(runner)
            phase_credential_error(runner)
            phase_carried_bar(runner)
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
            print("ALL ADS STUDIO E2E CHECKS PASSED")
            code = 0
    finally:
        if server:
            server.terminate()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
