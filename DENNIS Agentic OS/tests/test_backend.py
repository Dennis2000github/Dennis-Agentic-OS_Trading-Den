"""Backend unit tests (stdlib unittest only).

Run:  ./.venv/bin/python -m unittest discover -s tests -v
"""

import base64
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import (config, projects, session_store, sandbox, ws, filewatch,
                     docker_runtime, preflight, agent_registry)
from backend import session_manager as sm
from backend.model_sources import (antigravity, claude, codex, opencode,
                                   registry, base)


class FakeCtx:
    """Stand-in for sandbox.ExecContext used by the model sources."""

    def __init__(self, stdout="", stderr="", rc=0, interactive=None):
        self._stdout = stdout
        self._stderr = stderr
        self._rc = rc
        self._interactive = interactive

    def exec(self, argv, cwd=None, timeout=30):
        return self._rc, self._stdout, self._stderr

    def capture_interactive(self, argv, cwd=None, send=None, settle=2.0, timeout=20,
                            ready_for=None, ready_timeout=15.0):
        if self._interactive is None:
            raise RuntimeError("no interactive output")
        return self._interactive


class ProjectPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-proj-")
        self._old = os.environ.get("DENNIS_ALLOWED_ROOTS")
        os.environ["DENNIS_ALLOWED_ROOTS"] = self.tmp

    def tearDown(self):
        if self._old is None:
            os.environ.pop("DENNIS_ALLOWED_ROOTS", None)
        else:
            os.environ["DENNIS_ALLOWED_ROOTS"] = self._old

    def test_valid_within_root(self):
        sub = os.path.join(self.tmp, "app")
        os.makedirs(sub)
        self.assertEqual(projects.validate_path(sub), os.path.realpath(sub))

    def test_rejects_outside_root(self):
        self.assertIsNone(projects.validate_path("/etc"))

    def test_rejects_traversal(self):
        self.assertIsNone(projects.validate_path(os.path.join(self.tmp, "..", "..", "etc")))


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self._old = config.SESSION_STORE_FILE
        fd, self.path = tempfile.mkstemp(prefix="dennis-sessions-", suffix=".json")
        os.write(fd, b"{}")
        os.close(fd)
        config.SESSION_STORE_FILE = self.path

    def tearDown(self):
        config.SESSION_STORE_FILE = self._old
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_crud(self):
        s = session_store.create_session("local", "opencode", "proj", "/tmp/proj")
        sid = s["id"]
        self.assertEqual(s["panel"]["state"], "S1")
        self.assertEqual(len(session_store.list_sessions("local")), 1)
        self.assertIsNotNone(session_store.get_session("local", sid))

        session_store.set_model("local", sid, "opencode/big-pickle")
        self.assertEqual(session_store.get_session("local", sid)["model_id"], "opencode/big-pickle")

        session_store.set_panel("local", sid, {"state": "S4", "code_file": "main.py"})
        rec = session_store.get_session("local", sid)
        self.assertEqual(rec["panel"]["state"], "S4")
        self.assertEqual(rec["panel"]["code_file"], "main.py")

        self.assertTrue(session_store.delete_session("local", sid))
        self.assertEqual(session_store.list_sessions("local"), [])

    def test_set_panel_ignores_unknown_keys(self):
        s = session_store.create_session("local", "opencode", "proj", "/tmp/proj")
        session_store.set_panel("local", s["id"], {"state": "S2", "bogus": "x"})
        self.assertNotIn("bogus", session_store.get_session("local", s["id"])["panel"])

    def test_filter_by_agent(self):
        session_store.create_session("local", "opencode", "a", "/tmp/a")
        session_store.create_session("local", "codex-cli", "b", "/tmp/b")
        self.assertEqual(len(session_store.list_sessions("local", agent_id="opencode")), 1)


class ModelSourceTests(unittest.TestCase):
    def test_opencode_parses_provider_slash_model(self):
        out = "Fetching...\nopencode/big-pickle\nopenai/gpt-6\n\n# comment\nnot-a-model\n"
        models = opencode.OpenCodeModelSource().list_models(FakeCtx(stdout=out))
        ids = [m["id"] for m in models]
        self.assertEqual(ids, ["opencode/big-pickle", "openai/gpt-6"])

    def test_opencode_empty_raises(self):
        with self.assertRaises(base.ModelListError):
            opencode.OpenCodeModelSource().list_models(FakeCtx(stdout="no models here"))

    def test_antigravity_tab_separated(self):
        out = "Fetching available models...\ngemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
        models = antigravity.AntigravityModelSource().list_models(FakeCtx(stdout=out))
        self.assertEqual(models[0]["id"], "gemini-3.8-flash-high")
        self.assertEqual(models[0]["name"], "Gemini 3.8 Flash (High)")

    def test_codex_json(self):
        payload = json.dumps({"models": [
            {"slug": "gpt-6-astra", "display_name": "GPT-6-Astra", "visibility": "list"},
            {"slug": "secret", "display_name": "Secret", "visibility": "hidden"},
        ]})
        models = codex.CodexModelSource().list_models(FakeCtx(stdout=payload))
        self.assertEqual(models[0]["id"], "gpt-6-astra")
        self.assertFalse(models[1]["available"])

    def test_codex_bad_json_raises(self):
        with self.assertRaises(base.ModelListError):
            codex.CodexModelSource().list_models(FakeCtx(stdout="{not json"))

    def test_claude_numbered_menu(self):
        # Mirrors real /model output: a "Select model" section, cursor-collapsed
        # tokens ("OpusOpus5.5"), and a glued checkmark on the custom row.
        raw = ("\x1b[2Jsome banner text\n"
               "Select model\n"
               "1. Default (recommended)   Use thedefaul model\n"
               "\x1b[32m2.OpusOpus5.5 ·Best\x1b[0m\n"
               "3.FableFable5.1·Mostcapable\n"
               "\u276f4.9routerfallbackcombo\u2714Custommodel\n")
        models = claude.ClaudeModelSource().list_models(FakeCtx(interactive=raw))
        self.assertEqual([m["id"] for m in models],
                         ["default", "opus", "fable", "9routerfallbackcombo"])
        self.assertEqual([m["name"] for m in models],
                         ["Default", "Opus 5.5", "Fable 5.1", "9routerfallbackcombo"])

    def test_claude_ignores_rows_before_section(self):
        # Numbered rows that appear before "Select model" must not be parsed.
        raw = "1. Opus\n2. Sonnet\nSelect model\n1. Default (recommended)\n"
        models = claude.ClaudeModelSource().list_models(FakeCtx(interactive=raw))
        self.assertEqual([m["id"] for m in models], ["default"])

    def test_claude_trust_prompt_raises_actionable(self):
        raw = "Do you trust this folder?  1. Yes, I trust this folder  2. No, exit"
        with self.assertRaises(base.ModelListError) as cm:
            claude.ClaudeModelSource().list_models(FakeCtx(interactive=raw))
        self.assertIn("trust", str(cm.exception).lower())

    def test_claude_garbage_raises_never_fabricates(self):
        raw = "some banner\nexport TERM\nyes itrustworkspace"
        with self.assertRaises(base.ModelListError):
            claude.ClaudeModelSource().list_models(FakeCtx(interactive=raw))

    def test_registry_unknown_agent_raises(self):
        with self.assertRaises(base.ModelListError):
            registry.list_models("nope", FakeCtx())

    def test_registry_caches_and_refresh(self):
        registry.clear_cache()
        calls = {"n": 0}

        class CountingCtx(FakeCtx):
            def exec(self, argv, cwd=None, timeout=30):
                calls["n"] += 1
                return 0, "opencode/big-pickle", ""

        registry.list_models("opencode", CountingCtx())
        registry.list_models("opencode", CountingCtx())
        self.assertEqual(calls["n"], 1)
        registry.list_models("opencode", CountingCtx(), refresh=True)
        self.assertEqual(calls["n"], 2)


class SandboxTests(unittest.TestCase):
    def test_docker_argv_hardening(self):
        argv = sandbox.build_docker_argv("dennis-x", "img:latest", "/proj",
                                         ["opencode", "-m", "p/m"], rows=24, cols=80)
        joined = " ".join(argv)
        self.assertIn("--user 1000:1000", joined)
        self.assertIn("--cap-drop ALL", joined)
        self.assertIn("--security-opt no-new-privileges", joined)
        self.assertIn("--pids-limit 512", joined)
        self.assertIn("-v /proj:/workspace", joined)
        self.assertIn("LINES=24", joined)
        self.assertIn("COLUMNS=80", joined)
        self.assertEqual(argv[-3:], ["opencode", "-m", "p/m"])
        self.assertNotIn("docker.sock", joined)

    def test_docker_available_returns_bool(self):
        self.assertIsInstance(sandbox.docker_available(), bool)


class WebSocketTests(unittest.TestCase):
    def test_accept_key_rfc6455_example(self):
        # RFC 6455 section 1.3 example.
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        self.assertEqual(ws.accept_key(key), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")


def _write_script(path, exit_code):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit %d\n" % exit_code)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


class DockerStatusTests(unittest.TestCase):
    """Docker CLI missing and daemon stopped are different, actionable errors."""

    def test_cli_missing(self):
        with mock.patch.dict(os.environ, {"DOCKER_BIN": "/nonexistent/dennis/docker"}):
            st = docker_runtime.status()
        self.assertEqual(st["code"], docker_runtime.CLI_MISSING)
        self.assertFalse(st["cli_found"])
        self.assertIn("DOCKER_BIN", st["message"])

    def test_daemon_down(self):
        tmp = tempfile.mkdtemp(prefix="dennis-docker-")
        fake = os.path.join(tmp, "docker")
        _write_script(fake, 1)  # `docker info` fails
        with mock.patch.dict(os.environ, {"DOCKER_BIN": fake}):
            st = docker_runtime.status()
        self.assertEqual(st["code"], docker_runtime.DAEMON_DOWN)
        self.assertTrue(st["cli_found"])
        self.assertFalse(st["daemon_ok"])
        self.assertIn("daemon", st["message"].lower())

    def test_ready(self):
        tmp = tempfile.mkdtemp(prefix="dennis-docker-")
        fake = os.path.join(tmp, "docker")
        _write_script(fake, 0)  # `docker info` succeeds
        with mock.patch.dict(os.environ, {"DOCKER_BIN": fake}):
            st = docker_runtime.status()
            self.assertTrue(sandbox.docker_available())
        self.assertEqual(st["code"], docker_runtime.READY)

    def test_messages_differ(self):
        missing = {
            "code": docker_runtime.CLI_MISSING,
            "message": docker_runtime.status.__doc__ and "",
        }
        with mock.patch.dict(os.environ, {"DOCKER_BIN": "/nonexistent/dennis/docker"}):
            a = docker_runtime.status()
        tmp = tempfile.mkdtemp(prefix="dennis-docker-")
        fake = os.path.join(tmp, "docker")
        _write_script(fake, 1)
        with mock.patch.dict(os.environ, {"DOCKER_BIN": fake}):
            b = docker_runtime.status()
        self.assertNotEqual(a["message"], b["message"])
        self.assertNotEqual(a["code"], b["code"])


class FilewatchTests(unittest.TestCase):
    def test_find_preview_url_local_only(self):
        self.assertEqual(filewatch.find_preview_url("see http://localhost:5173/app now"),
                         "http://localhost:5173/app")
        self.assertEqual(filewatch.find_preview_url("at http://127.0.0.1:8000"), "http://127.0.0.1:8000")
        self.assertIsNone(filewatch.find_preview_url("https://example.com/docs"))

    def test_scanner_dedupes(self):
        scanner = filewatch.PreviewScanner()
        self.assertEqual(scanner.scan("http://localhost:3000"), "http://localhost:3000")
        self.assertIsNone(scanner.scan("again http://localhost:3000"))
        self.assertEqual(scanner.scan("http://localhost:4000"), "http://localhost:4000")

    def test_watcher_reports_one_handoff_per_burst(self):
        tmp = tempfile.mkdtemp(prefix="dennis-watch-")
        events = []
        old_poll, old_gap = filewatch._POLL_SECONDS, filewatch._BURST_GAP_SECONDS
        filewatch._POLL_SECONDS = 0.05
        filewatch._BURST_GAP_SECONDS = 0.5
        try:
            watcher = filewatch.ProjectWatcher(tmp, on_change=events.append)
            watcher.start()
            with open(os.path.join(tmp, "a.txt"), "w") as fh:
                fh.write("one")
            deadline = time.time() + 4
            while not events and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(events, "watcher detected the first change")
            first_burst = events[0]["burst"]
            with open(os.path.join(tmp, "b.txt"), "w") as fh:
                fh.write("two")
            time.sleep(0.4)  # inside the burst gap
            while events[-1].get("count") is None and time.time() < deadline:
                time.sleep(0.05)
            self.assertTrue(all(e["burst"] == first_burst for e in events),
                            "rapid edits stay in the same burst")
            time.sleep(0.8)  # past the gap
            with open(os.path.join(tmp, "c.txt"), "w") as fh:
                fh.write("three")
            deadline2 = time.time() + 4
            while time.time() < deadline2:
                if any(e["burst"] > first_burst for e in events):
                    break
                time.sleep(0.05)
            self.assertTrue(any(e["burst"] > first_burst for e in events),
                            "a new handoff burst starts after the quiet gap")
            watcher.stop()
        finally:
            filewatch._POLL_SECONDS, filewatch._BURST_GAP_SECONDS = old_poll, old_gap


class _FakeProc:
    def __init__(self, on_data=None, on_exit=None):
        self.on_data = on_data or (lambda chunk: None)
        self.on_exit = on_exit or (lambda code: None)
        self.writes = []
        self.alive = False
        self.last_output_at = time.time()

    def start(self, argv, cwd, env, rows=30, cols=100):
        self.argv = argv
        self.rows, self.cols = rows, cols
        self.alive = True
        return True, 4242

    def write(self, data):
        self.writes.append(data)
        return True

    def resize(self, rows, cols):
        self.rows, self.cols = rows, cols

    def is_alive(self):
        return self.alive

    def stop(self):
        self.alive = False


class _FakeRunner:
    uses_docker = False

    def build_argv(self, binary, argv, project, session_id, rows, cols, agent_id=None):
        return [binary] + list(argv)

    def env_for(self, env):
        return dict(env)

    def cleanup(self, session_id):
        return None

    def read_text(self, path, agent_id=None, session_id=None, timeout=10):
        return None


class _Sink:
    def __init__(self):
        self.messages = []

    def send_text(self, message):
        self.messages.append(message)

    def types(self):
        return [m.get("type") for m in self.messages]


class SessionManagerTests(unittest.TestCase):
    """Per-session isolation, handoff signals, timings and model read-back."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-session-")
        self.manager = sm.SessionManager(runner=_FakeRunner(), idle_timeout=99999,
                                         backend_note=None)
        self._patches = [
            mock.patch.object(sm.sandbox, "PtyProcess", _FakeProc),
            mock.patch.object(agent_registry, "resolve_binary", lambda agent: "/bin/echo"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _record(self, sid, agent="opencode"):
        return {"id": sid, "agent_id": agent, "project_path": self.tmp,
                "model_id": None, "cli_session_id": None}

    def _agent(self, agent="opencode"):
        return {"id": agent, "binary_path": "/bin/echo"}

    def test_two_sessions_have_separate_processes_and_buffers(self):
        a = sm.AgentSession("A", "opencode", self.tmp)
        b = sm.AgentSession("B", "claude-code", self.tmp)
        a._runner = self.manager.runner
        b._runner = self.manager.runner
        ok_a, _ = a.start(self._agent(), None, rows=24, cols=80)
        ok_b, _ = b.start(self._agent("claude-code"), None, rows=30, cols=100)
        self.assertTrue(ok_a and ok_b)
        self.assertIsNot(a.proc, b.proc)

        sink_a, sink_b = _Sink(), _Sink()
        a.add_client(sink_a)
        b.add_client(sink_b)
        a._on_data("output-for-A")
        self.assertNotIn("output-for-A", [m.get("data") for m in sink_b.messages])

        a.write("typed-in-A")
        self.assertEqual(a.proc.writes, ["typed-in-A"])
        self.assertEqual(b.proc.writes, [])

    def test_start_dimensions_and_timings_recorded(self):
        a = sm.AgentSession("A", "opencode", self.tmp)
        a._runner = self.manager.runner
        a.start(self._agent(), None, rows=40, cols=120)
        self.assertEqual((a.proc.rows, a.proc.cols), (40, 120))
        self.assertIsInstance(a.timings["start_ms"], int)
        self.assertIsNone(a.timings["first_output_ms"])
        a._on_data("hello")
        self.assertIsInstance(a.timings["first_output_ms"], int)

    def test_current_model_is_what_the_backend_launched_with(self):
        a = sm.AgentSession("A", "opencode", self.tmp)
        a._runner = self.manager.runner
        a.start(self._agent(), "opencode/big-pickle")
        self.assertEqual(a.snapshot()["current_model"], "opencode/big-pickle")
        self.assertIn("-m", a.proc.argv)
        self.assertIn("opencode/big-pickle", a.proc.argv)

    def test_same_model_restart_continues_the_conversation(self):
        a = sm.AgentSession("A", "opencode", self.tmp)
        a._runner = self.manager.runner
        a.start(self._agent(), "opencode/beam-free")
        self.assertNotIn("-c", a.proc.argv, "a brand-new session must not resume")
        a.stop()
        a.start(self._agent(), "opencode/beam-free")  # same model: safe to resume
        self.assertIn("-c", a.proc.argv, "a same-model restart continues the conversation")
        self.assertIn("opencode/beam-free", a.proc.argv)

    def test_model_change_restarts_fresh_so_the_new_model_applies(self):
        # OpenCode (and other CLIs) ignore the model flag when resuming, so a
        # model change must NOT carry the continue/resume flags or the CLI would
        # keep its old model.
        a = sm.AgentSession("A", "opencode", self.tmp)
        a._runner = self.manager.runner
        a.start(self._agent(), "opencode/beam-free")
        a.stop()
        a.start(self._agent(), "opencode/big-pickle")  # model change
        self.assertNotIn("-c", a.proc.argv, "a model change must start fresh")
        self.assertNotIn("-s", a.proc.argv, "a model change must not resume a session id")
        self.assertIn("-m", a.proc.argv)
        self.assertIn("opencode/big-pickle", a.proc.argv)
        # And from "no model" to a specific model is also a change.
        b = sm.AgentSession("B", "opencode", self.tmp)
        b._runner = self.manager.runner
        b.start(self._agent(), None)
        b.stop()
        b.start(self._agent(), "opencode/beam-free")
        self.assertNotIn("-c", b.proc.argv)

    def test_preview_url_emits_one_handoff_per_burst(self):
        a = sm.AgentSession("A", "opencode", self.tmp)
        a._runner = self.manager.runner
        a.start(self._agent(), None)
        sink = _Sink()
        a.add_client(sink)
        a._on_data("Local: http://localhost:5173/")
        handoffs = [m for m in sink.messages if m.get("type") == "handoff"]
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(handoffs[0]["reason"], "preview")
        self.assertEqual(handoffs[0]["preview_url"], "http://localhost:5173/")
        self.assertEqual(handoffs[0]["burst"], 1)
        # Same URL again: deduplicated, no new handoff.
        a._on_data("Local: http://localhost:5173/")
        handoffs = [m for m in sink.messages if m.get("type") == "handoff"]
        self.assertEqual(len(handoffs), 1)
        # A different URL inside the same burst is still burst 1.
        a._on_data("also http://localhost:6006")
        handoffs = [m for m in sink.messages if m.get("type") == "handoff"]
        self.assertEqual(len(handoffs), 2)
        self.assertEqual(handoffs[1]["burst"], 1)

    def test_stop_only_stops_its_own_session(self):
        a = sm.AgentSession("A", "opencode", self.tmp)
        b = sm.AgentSession("B", "opencode", self.tmp)
        a._runner = self.manager.runner
        b._runner = self.manager.runner
        a.start(self._agent(), None)
        b.start(self._agent(), None)
        a.stop()
        self.assertFalse(a.is_alive())
        self.assertTrue(b.is_alive())

    def test_manager_summary_is_secret_free(self):
        rec = self._record("A")
        self.manager._sessions["A"] = sm.AgentSession("A", "opencode", self.tmp)
        self.manager._sessions["A"]._runner = self.manager.runner
        summary = self.manager.summary("A")
        self.assertIn("status", summary)
        self.assertIn("current_model", summary)
        self.assertIn("timings", summary)
        self.assertNotIn("binary", json.dumps(summary))
        self.assertIsNone(self.manager.summary("missing"))


class PtyInputTests(unittest.TestCase):
    """A real PTY: typing a line reaches the process and comes back."""

    def test_typing_a_line_reaches_the_process(self):
        chunks = []
        exited = []
        proc = sandbox.PtyProcess(on_data=chunks.append, on_exit=exited.append)
        ok, info = proc.start(["/bin/cat"], os.getcwd(), {"TERM": "xterm-256color"},
                              rows=24, cols=80)
        self.assertTrue(ok, info)
        try:
            deadline = time.time() + 5
            while time.time() < deadline:
                if proc.write("hello-from-the-browser\n"):
                    break
                time.sleep(0.05)
            deadline = time.time() + 5
            while time.time() < deadline:
                if "hello-from-the-browser" in "".join(chunks):
                    break
                time.sleep(0.05)
            self.assertIn("hello-from-the-browser", "".join(chunks))
            proc.resize(40, 120)
        finally:
            proc.stop()


class ReadinessTests(unittest.TestCase):
    def test_report_shape_and_actionable_messages(self):
        report = preflight.run(session_backend="local")
        for key in ("ready", "backend", "message", "docker", "node", "build_tools",
                    "agents", "images"):
            self.assertIn(key, report)
        self.assertIn(report["docker"]["code"],
                      (docker_runtime.CLI_MISSING, docker_runtime.DAEMON_DOWN,
                       docker_runtime.READY))
        self.assertTrue(report["docker"]["message"])
        self.assertNotIn("docker.io", report["docker"]["message"])

    def test_cli_and_daemon_are_reported_separately(self):
        with mock.patch.dict(os.environ, {"DOCKER_BIN": "/nonexistent/dennis/docker"}):
            report = preflight.run(session_backend="local")
        self.assertEqual(report["docker"]["code"], docker_runtime.CLI_MISSING)
        self.assertFalse(report["docker"]["cli_found"])


class McpConnectorTests(unittest.TestCase):
    def setUp(self):
        import backend.mcp as mcp
        self.mcp = mcp
        fd, self.path = tempfile.mkstemp(prefix="dennis-mcp-", suffix=".json")
        os.close(fd)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write('{"servers": []}')
        self._old = mcp.MCP_FILE
        mcp.MCP_FILE = self.path

    def tearDown(self):
        self.mcp.MCP_FILE = self._old
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_policy_presets(self):
        ro = self.mcp.default_policy("read-only")
        self.assertFalse(ro["allow_write"])
        self.assertEqual(ro["risk_level"], "low")
        trusted = self.mcp.default_policy("trusted")
        self.assertTrue(trusted["allow_write"])
        self.assertFalse(trusted["require_approval"])

    def test_create_validation_and_persistence(self):
        with self.assertRaises(ValueError):
            self.mcp.create_server({"name": ""})
        with self.assertRaises(ValueError):
            self.mcp.create_server({"name": "No URL", "transport": "streamable-http"})
        with self.assertRaises(ValueError):
            self.mcp.create_server({"name": "Bad transport", "transport": "carrier-pigeon"})
        server = self.mcp.create_server({
            "name": "Acme", "transport": "stdio", "command": "npx",
            "args": "-y pkg", "auth_type": "none",
        })
        self.assertEqual(server["status"], "policy_review")
        self.assertEqual(server["args"], ["-y", "pkg"])
        stored = self.mcp.load_servers()["servers"]
        self.assertEqual(len(stored), 1)

    def test_public_view_hides_tokens(self):
        server = self.mcp.create_server({
            "name": "Token test", "transport": "streamable-http",
            "url": "https://mcp.example.com/mcp", "auth_type": "oauth",
        })
        server["oauth"] = {"access_token": "secret-1234", "refresh_token": "r"}
        self.mcp._upsert(server)
        public = self.mcp.list_public()[0]
        self.assertNotIn("access_token", public)
        self.assertTrue(public["has_token"])
        self.assertEqual(public["access_token_preview"], "••••1234")

    def test_registry_discovery_parsing_and_fallback(self):
        payload = {"servers": [{"server": {
            "name": "io.test/tool", "title": "Tool", "description": "d",
            "version": "1.0.0", "remotes": [{"url": "https://x/mcp", "type": "streamable-http"}],
        }}, {"server": {
            "name": "io.test/local", "version": "1.0.0",
            "packages": [{"identifier": "npx", "transport": {"type": "stdio", "args": ["-y", "pkg"]}}],
        }}]}
        with mock.patch.object(self.mcp, "_http_json", return_value=payload):
            result = self.mcp.discover("tool")
        self.assertEqual(result["source"], "registry")
        self.assertEqual(result["results"][0]["id"], "io.test/tool")
        self.assertEqual(result["results"][0]["url"], "https://x/mcp")
        self.assertEqual(result["results"][1]["transport"], "stdio")
        with mock.patch.object(self.mcp, "_http_json", side_effect=OSError("offline")):
            offline = self.mcp.discover("github")
        self.assertEqual(offline["source"], "builtin")
        self.assertTrue(offline["error"])
        self.assertTrue(offline["results"])

    def test_oauth_discovery_and_authorization_url(self):
        server = self.mcp.create_server({
            "name": "Remote", "transport": "streamable-http",
            "url": "https://mcp.example.com/mcp", "auth_type": "oauth",
        })
        prm = {"authorization_servers": ["https://auth.example.com"],
               "scopes_supported": ["mcp:tools", "mcp:write"]}
        asm = {"authorization_endpoint": "https://auth.example.com/authorize",
               "token_endpoint": "https://auth.example.com/token",
               "registration_endpoint": "https://auth.example.com/register"}
        reg = {"client_id": "client-abc"}

        def fake(url, method="GET", data=None, headers=None, timeout=None):
            if "oauth-protected-resource" in url:
                return prm
            if "oauth-authorization-server" in url:
                return asm
            if url.endswith("/register"):
                return reg
            raise AssertionError(url)

        with mock.patch.object(self.mcp, "_http_json", side_effect=fake):
            result = self.mcp.begin_oauth(server["id"])
        self.assertEqual(result["client_id"], "client-abc")
        self.assertIn("client_id=client-abc", result["authorization_url"])
        self.assertIn("code_challenge_method=S256", result["authorization_url"])
        self.assertIn("resource=https%3A%2F%2Fmcp.example.com%2Fmcp",
                      result["authorization_url"])
        stored = self.mcp.get_server(server["id"])
        self.assertEqual(stored["status"], "authorizing")

    def test_complete_connection_requires_accepted_policy(self):
        server = self.mcp.create_server({
            "name": "Stdio", "transport": "stdio", "command": "npx",
            "args": ["-y", "pkg"], "auth_type": "none",
        })
        with self.assertRaises(ValueError):
            self.mcp.complete_connection(server["id"])
        self.mcp.update_policy(server["id"], {"accepted": True})
        connected = self.mcp.complete_connection(server["id"])
        self.assertEqual(connected["status"], "connected")


if __name__ == "__main__":
    unittest.main(verbosity=2)

