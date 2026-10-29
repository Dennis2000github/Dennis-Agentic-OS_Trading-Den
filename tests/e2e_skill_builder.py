"""Browser end-to-end test for the Agent Skill Builder.

Runs the real app on an isolated workspace + Vault, drives Chrome through
Playwright, generates one skill, edits it in the review overlay, publishes it,
then generates a second draft and rejects it. Requires the Chrome application
and the ``playwright`` Python package (no browser download needed).

Run:
    ../.venv/bin/python tests/e2e_skill_builder.py
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
NAME = "browser-e2e-skill"
DESCRIPTION = ("Browser end-to-end test skill that verifies the shared review, "
               "autosave and verified Vault publishing workflow.")


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_for_server(port, proc, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("server exited with code %s" % proc.returncode)
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/api/auth/session" % port, timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.25)
    raise RuntimeError("server did not start")


def main():
    temp = tempfile.mkdtemp(prefix="sb-e2e-")
    workspace = os.path.join(temp, "workspace")
    vault = os.path.join(temp, "vault")
    cache = os.path.join(temp, "cache")
    for path in (workspace, vault, cache):
        os.makedirs(path, exist_ok=True)
    env_file = os.path.join(ROOT, "agent-skill-builder", ".env")
    if os.path.isfile(env_file):
        shutil.copy2(env_file, os.path.join(workspace, ".env"))

    env = dict(os.environ)
    env.update({
        "DENNIS_NO_DENMA": "1",
        "DENNIS_SKILL_BUILDER_DIR": workspace,
        "DENNIS_VAULT_DIR": vault,
        "DENNIS_SB_CACHE_DIR": cache,
    })
    port = free_port()
    log_path = os.path.join(temp, "server.log")
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "app.py"), str(port)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        wait_for_server(port, proc)
        failures = run_browser(port)
        if failures:
            raise AssertionError("; ".join(failures))
        print("E2E_OK")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if os.environ.get("SB_E2E_KEEP"):
            print("kept temp dir:", temp)
        else:
            shutil.rmtree(temp, ignore_errors=True)


def run_browser(port):
    failures = []
    base = "http://127.0.0.1:%d" % port
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome", headless=True,
            args=["--no-sandbox", "--disable-gpu"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page_errors = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        page.goto(base, wait_until="domcontentloaded")
        page.click("#sidebar-nav-skillbuilder")
        page.wait_for_selector("#skill-builder-view:not(.hidden)", timeout=15000)

        # Mode wiring: character image is only offered for reference videos.
        page.click('.sb-mode[data-sb-mode="video"]')
        page.wait_for_selector("#sb-video-options:not(.hidden)", timeout=5000)
        page.check('input[name="sb-objective"][value="tutorial"]')
        if page.is_visible("#sb-character-url"):
            failures.append("character field visible for tutorial objective")
        page.check('input[name="sb-objective"][value="reference"]')
        if not page.is_visible("#sb-character-url"):
            failures.append("character field hidden for reference objective")
        page.click('.sb-mode[data-sb-mode="source"]')

        def generate_and_wait():
            page.fill("#sb-urls", YOUTUBE)
            page.click("#sb-build")
            page.wait_for_selector("#sb-review:not(.hidden)", timeout=240000)

        # 1) Generate, edit, autosave, publish, verify.
        generate_and_wait()
        if not page.input_value("#sb-review-name"):
            failures.append("review name is empty")
        if len(page.input_value("#sb-review-description")) < 10:
            failures.append("review description is too short")
        if len(page.input_value("#sb-review-content")) < 100:
            failures.append("review content is too short")

        page.fill("#sb-review-name", NAME)
        page.fill("#sb-review-description", DESCRIPTION)
        page.fill("#sb-review-content",
                  page.input_value("#sb-review-content") + "\n\n<!-- browser-e2e -->\n")
        page.wait_for_function(
            "() => (document.getElementById('sb-review-save-state').textContent || '')"
            ".indexOf('Draft saved') !== -1", timeout=15000)
        draft_id = (page.text_content("#sb-review-draft") or "").strip()
        draft = api(port, "/api/skill-builder/drafts/" + draft_id)
        if draft.get("name") != NAME or "<!-- browser-e2e -->" not in draft.get("content", ""):
            failures.append("autosave did not persist the reviewed edits")
        page.click("#sb-review-publish")
        page.wait_for_function(
            "() => (document.getElementById('sb-review-status').textContent || '')"
            ".indexOf('Saved and verified') !== -1", timeout=60000)
        status = page.text_content("#sb-review-status") or ""
        saved = api(port, "/api/skill-builder/skill?name=" + NAME)
        if "<!-- browser-e2e -->" not in saved.get("content", ""):
            failures.append("published artifact lost the reviewed content edit")
        if ("name: " + NAME) not in saved.get("content", ""):
            failures.append("published artifact has the wrong name")
        page.click("#sb-review-close")
        page.wait_for_selector("#sb-review.hidden", state="attached", timeout=5000)

        # 2) Generate a second draft and reject it; the published file survives.
        generate_and_wait()
        page.once("dialog", lambda dialog: dialog.accept())
        page.click("#sb-review-reject")
        page.wait_for_selector("#sb-review.hidden", state="attached", timeout=15000)

        drafts = api(port, "/api/skill-builder/drafts")["drafts"]
        if drafts:
            failures.append("drafts remain after reject: %d" % len(drafts))
        skills = api(port, "/api/skill-builder/skills")["skills"]
        published = [s for s in skills if s.get("name") == NAME and s.get("source") == "vault"]
        if not published:
            failures.append("published skill missing from Vault listing")
        if "Saved and verified" not in status:
            failures.append("publish status did not confirm verification")

        if page_errors:
            failures.append("uncaught page errors: %s" % page_errors[:3])
        browser.close()
    return failures


def api(port, path):
    token = ""
    try:
        with open(os.path.join(ROOT, "data", "auth.json"), encoding="utf-8") as fh:
            token = json.load(fh).get("token", "")
    except OSError:
        pass
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        headers={"X-Dennis-Token": token})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


if __name__ == "__main__":
    main()
