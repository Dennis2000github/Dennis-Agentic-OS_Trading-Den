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
                     docker_runtime, preflight, agent_registry, vault, hermes,
                     skill_builder)
from backend import session_manager as sm
from backend.model_sources import (antigravity, claude, codex, opencode,
                                   cursor, openclaw, registry, base)
from backend.model_sources import hermes as hermes_source


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


class AgentRegistryDiscoveryTests(unittest.TestCase):
    """Installed host CLIs must appear in every agent dropdown."""

    def setUp(self):
        agent_registry.clear_detection_cache()

    def tearDown(self):
        agent_registry.clear_detection_cache()

    def _detected(self, agent_id="claude-code", name="Claude Code", binary="claude"):
        return {
            "id": agent_id, "name": name, "provider": "Anthropic",
            "version": "Installed", "icon_char": "C", "color_theme": "amber",
            "binary_path": "/usr/local/bin/" + binary, "type": "Coding CLI",
            "detected": True, "registered": False,
        }

    def test_availability_does_not_require_model_listing(self):
        with mock.patch.object(agent_registry, "resolve_binary", lambda a: "/bin/echo"):
            ok, reason = agent_registry.availability({"id": "unknown-cli"})
        self.assertTrue(ok)
        self.assertEqual(reason, "Ready")

    def test_available_agents_merges_detected_cli(self):
        registered = [{"id": "opencode", "name": "OpenCode", "binary_path": "/bin/echo"}]
        with mock.patch.object(agent_registry, "load_agents", lambda: registered), \
                mock.patch.object(agent_registry, "resolve_binary", lambda a: a.get("binary_path")), \
                mock.patch.object(agent_registry, "detect_installed",
                                  lambda **kw: [self._detected()]):
            agents = agent_registry.available_agents()
        by_id = {a["id"]: a for a in agents}
        self.assertIn("claude-code", by_id)
        self.assertTrue(by_id["claude-code"]["available"])
        self.assertFalse(by_id["claude-code"]["registered"])
        self.assertTrue(by_id["opencode"]["registered"])

    def test_available_agents_dedupes_registered_binary_alias(self):
        registered = [{"id": "codex-cli", "name": "Codex CLI", "binary_path": "/bin/codex"}]
        detected = [self._detected("codex", "Codex CLI", "codex"),
                    self._detected("claude-code", "Claude Code", "claude")]
        with mock.patch.object(agent_registry, "load_agents", lambda: registered), \
                mock.patch.object(agent_registry, "resolve_binary", lambda a: a.get("binary_path")), \
                mock.patch.object(agent_registry, "detect_installed", lambda **kw: detected):
            ids = [a["id"] for a in agent_registry.available_agents()]
        self.assertEqual(ids, ["codex-cli", "deepseek-harness", "claude-code"])

    def test_get_agent_falls_back_to_detected(self):
        with mock.patch.object(agent_registry, "load_agents", lambda: []), \
                mock.patch.object(agent_registry, "detect_installed",
                                  lambda **kw: [self._detected()]):
            agent = agent_registry.get_agent("claude-code")
        self.assertIsNotNone(agent)
        self.assertTrue(agent["detected"])
        self.assertEqual(agent["binary_path"], "/usr/local/bin/claude")
        with mock.patch.object(agent_registry, "load_agents", lambda: []), \
                mock.patch.object(agent_registry, "detect_installed", lambda **kw: []):
            self.assertIsNone(agent_registry.get_agent("missing"))

    def test_unsupported_clis_are_not_probed(self):
        probes = {p["id"] for p in agent_registry.host_agent_probes()}
        for unsupported in ("junie", "qoder-cli", "devin-cli"):
            self.assertNotIn(unsupported, probes)
            self.assertEqual(agent_registry.profile(unsupported), {})

    def test_hermes_cli_is_a_distinct_probe(self):
        probes = {p["id"]: p for p in agent_registry.host_agent_probes()}
        self.assertIn("hermes-cli", probes)
        self.assertEqual(probes["hermes-cli"]["binary"], "hermes")
        # The OS orchestrator owns the bare "hermes" id; the CLI must not.
        self.assertNotIn("hermes", probes)

    def test_hermes_cli_profile_and_one_shot(self):
        prof = agent_registry.profile("hermes-cli")
        self.assertEqual(prof["model_flag"], ["-m"])
        self.assertEqual(prof["resume_flag"], ["--resume"])
        argv = agent_registry.one_shot_argv(
            {"id": "hermes-cli", "binary_path": "/bin/echo"}, "task")
        self.assertEqual(argv, ["/bin/echo", "-z", "task"])
        argv = agent_registry.one_shot_argv(
            {"id": "hermes-cli", "binary_path": "/bin/echo"}, "task",
            model_id="deepseek-v4-flash")
        self.assertEqual(
            argv, ["/bin/echo", "-m", "deepseek-v4-flash", "-z", "task"])

    def test_cursor_and_openclaw_are_registered(self):
        self.assertIn("model_flag", agent_registry.profile("cursor"))
        self.assertIn("models_cmd", agent_registry.profile("openclaw"))
        self.assertEqual(agent_registry.profile("openclaw")["base_args"],
                         ["tui", "--local"])
        probes = {p["id"] for p in agent_registry.host_agent_probes()}
        self.assertTrue({"cursor", "openclaw"}.issubset(probes))

    def test_cursor_and_openclaw_one_shot_commands(self):
        self.assertEqual(
            agent_registry.one_shot_argv(
                {"id": "cursor", "binary_path": "/bin/echo"},
                "task", model_id="gpt-5"),
            ["/bin/echo", "-p", "task", "--model", "gpt-5"])
        self.assertEqual(
            agent_registry.one_shot_argv(
                {"id": "openclaw", "binary_path": "/bin/echo"},
                "task", model_id="openai/gpt-5"),
            ["/bin/echo", "agent", "exec", "--model", "openai/gpt-5", "task"])

    def test_skill_builder_hermes_fallback_uses_oneshot(self):
        self.assertEqual(skill_builder.AGENT_BINARIES["hermes-cli"], "hermes")
        argv = skill_builder._agent_argv("hermes-cli", "/bin/echo", "", "task")
        self.assertEqual(argv, ["/bin/echo", "-z", "task"])
        argv = skill_builder._agent_argv(
            "hermes-cli", "/bin/echo", "deepseek-v4-flash", "task")
        self.assertEqual(
            argv, ["/bin/echo", "-m", "deepseek-v4-flash", "-z", "task"])


class ModelSourceTests(unittest.TestCase):
    def test_opencode_parses_provider_slash_model(self):
        out = "Fetching...\nopencode/big-pickle\nopenai/gpt-6\n\n# comment\nnot-a-model\n"
        models = opencode.OpenCodeModelSource().list_models(FakeCtx(stdout=out))
        ids = [m["id"] for m in models]
        self.assertEqual(ids, ["opencode/big-pickle", "openai/gpt-6"])
        self.assertEqual([m["provider"] for m in models], ["opencode", "openai"])

    def test_providers_group_reported_providers_only(self):
        models = [
            {"id": "openai/gpt-6", "provider": "openai", "available": True},
            {"id": "openai/gpt-6-mini", "provider": "openai", "available": False},
            {"id": "anthropic/claude", "provider": "anthropic", "available": True},
            {"id": "no-provider", "available": True},
        ]
        providers = registry.providers(models)
        self.assertEqual([p["id"] for p in providers], ["openai", "anthropic"])
        self.assertEqual(providers[0]["label"], "OpenAI")
        self.assertEqual(providers[0]["model_count"], 2)
        self.assertTrue(providers[0]["available"])
        self.assertEqual(providers[1]["label"], "Anthropic")

    def test_provider_label_keeps_unknown_ids_verbatim(self):
        self.assertEqual(registry.provider_label("ollama-cloud"), "Ollama Cloud")
        self.assertEqual(registry.provider_label("some-local-gateway"),
                         "some-local-gateway")

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

    def test_openclaw_json_models(self):
        payload = json.dumps({"models": [
            {"key": "openai/gpt-5", "name": "GPT-5", "provider": "openai",
             "default": True},
            {"key": "anthropic/claude", "provider": "anthropic"},
        ]})
        models = openclaw.OpenClawModelSource().list_models(FakeCtx(stdout=payload))
        self.assertEqual([m["id"] for m in models],
                         ["openai/gpt-5", "anthropic/claude"])
        self.assertEqual(models[0]["provider"], "openai")
        self.assertTrue(models[0]["is_default"])

    def test_cursor_interactive_models(self):
        raw = "Select model\n> 1. auto\n  2. anthropic/claude-sonnet\n"
        models = cursor.CursorModelSource().list_models(FakeCtx(interactive=raw))
        self.assertEqual([m["id"] for m in models],
                         ["auto", "anthropic/claude-sonnet"])

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

    def _with_hermes_home(self, config, cache):
        home = tempfile.mkdtemp(prefix="hermes-home-")
        old = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = home
        self.addCleanup(lambda: (os.environ.__setitem__("HERMES_HOME", old)
                                 if old is not None
                                 else os.environ.pop("HERMES_HOME", None)))
        if config is not None:
            with open(os.path.join(home, "config.yaml"), "w", encoding="utf-8") as fh:
                fh.write(config)
        if cache is not None:
            with open(os.path.join(home, "provider_models_cache.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(cache, fh)
        return home

    def test_hermes_reads_config_and_provider_cache(self):
        self._with_hermes_home(
            "model:\n"
            "  default: deepseek-v4-flash\n"
            "  provider: ollama-cloud\n"
            "  base_url: https://ollama.com/v1\n"
            "fallback_providers: []\n",
            {"ollama-cloud": {"models": ["gpt-oss:120b", "deepseek-v4-flash",
                                         "glm-5.3"]},
             "copilot-acp": {"models": ["gpt-5.4"]}},
        )
        models = hermes_source.HermesModelSource().list_models(FakeCtx())
        self.assertEqual([m["id"] for m in models],
                         ["deepseek-v4-flash", "gpt-oss:120b", "glm-5.3"])
        self.assertTrue(models[0]["is_default"])
        self.assertEqual({m["provider"] for m in models}, {"ollama-cloud"})
        # Only the active provider's models are exposed.
        self.assertNotIn("gpt-5.4", [m["id"] for m in models])

    def test_hermes_falls_back_to_single_cached_provider(self):
        self._with_hermes_home(None, {"ollama-cloud": {"models": ["glm-5.3"]}})
        models = hermes_source.HermesModelSource().list_models(FakeCtx())
        self.assertEqual([m["id"] for m in models], ["glm-5.3"])

    def test_hermes_missing_cache_raises_actionable(self):
        self._with_hermes_home("model:\n  provider: ollama-cloud\n", None)
        with self.assertRaises(base.ModelListError) as cm:
            hermes_source.HermesModelSource().list_models(FakeCtx())
        self.assertIn("hermes model --refresh", str(cm.exception))

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

    def test_screen_replays_cursor_positioned_output(self):
        screen = sm._Screen(6, 24)
        screen.feed("\x1b[1;1Hhello\x1b[2;3Hworld")
        text = screen.snapshot()
        self.assertIn("hello", text)
        self.assertIn("  world", text)
        self.assertNotIn("\x1b", text)

    def test_screen_handles_split_sequences_erase_and_alt_screen(self):
        screen = sm._Screen(4, 16)
        screen.feed("\x1b[2")
        screen.feed(";3HOK")
        self.assertIn("OK", screen.snapshot())
        screen.feed("\x1b[2J")
        self.assertEqual(screen.snapshot(), "")
        screen.feed("ready\x1b[?1049h")
        self.assertEqual(screen.snapshot(), "")

    def test_history_archives_settled_screens_without_escape_codes(self):
        a = sm.AgentSession("A", "opencode", self.tmp)
        a._on_data("\x1b[1;1Hfirst screen\x1b[2;1Hwith a line")
        a._capture_screen()
        a._on_data("\x1b[2J\x1b[1;1Hsecond screen")
        a._capture_screen()
        text = a.history()
        self.assertIn("first screen", text)
        self.assertIn("with a line", text)
        self.assertIn("second screen", text)
        self.assertNotIn("\x1b", text)
        self.assertIn("\u2500\u2500", text)  # timestamped snapshot separator

    def test_history_deduplicates_unchanged_screens(self):
        a = sm.AgentSession("A", "opencode", self.tmp)
        a._on_data("steady\x1b[1;1Hsteady")
        a._capture_screen()
        a._capture_screen()
        self.assertEqual(a.history().count("steady"), 1)

    def test_history_keeps_only_the_tail(self):
        a = sm.AgentSession("A", "opencode", self.tmp)
        a._on_data("\x1b[1;1Hfirst screen")
        a._capture_screen()
        a._on_data("\x1b[2J\x1b[1;1Hlast screen")
        a._capture_screen()
        text = a.history(limit=20)
        self.assertIn("last screen", text)
        self.assertNotIn("first screen", text)

    def test_current_model_is_what_the_backend_launched_with(self):
        a = sm.AgentSession("A", "claude-code", self.tmp)
        a._runner = self.manager.runner
        a.start(self._agent("claude-code"), "claude-code/big-pickle")
        self.assertEqual(a.snapshot()["current_model"], "claude-code/big-pickle")
        self.assertIn("--model", a.proc.argv)
        self.assertIn("claude-code/big-pickle", a.proc.argv)

    def test_opencode_launches_without_an_unsupported_model_flag(self):
        # OpenCode v2's interactive TUI rejects -m/--model ("Unrecognized flag"),
        # which used to abort the CLI on launch. It must start bare and let the
        # TUI's /model picker choose the model.
        a = sm.AgentSession("A", "opencode", self.tmp)
        a._runner = self.manager.runner
        a.start(self._agent(), "opencode/big-pickle")
        self.assertNotIn("-m", a.proc.argv)
        self.assertNotIn("--model", a.proc.argv)
        self.assertNotIn("opencode/big-pickle", a.proc.argv)

    def test_same_model_restart_continues_the_conversation(self):
        a = sm.AgentSession("A", "claude-code", self.tmp)
        a._runner = self.manager.runner
        a.start(self._agent("claude-code"), "claude-code/beam-free")
        self.assertNotIn("--continue", a.proc.argv, "a brand-new session must not resume")
        a.stop()
        a.start(self._agent("claude-code"), "claude-code/beam-free")  # same model: safe to resume
        self.assertIn("--continue", a.proc.argv, "a same-model restart continues the conversation")
        self.assertIn("claude-code/beam-free", a.proc.argv)

    def test_model_change_restarts_fresh_so_the_new_model_applies(self):
        # OpenCode (and other CLIs) ignore the model flag when resuming, so a
        # model change must NOT carry the continue/resume flags or the CLI would
        # keep its old model.
        a = sm.AgentSession("A", "claude-code", self.tmp)
        a._runner = self.manager.runner
        a.start(self._agent("claude-code"), "claude-code/beam-free")
        a.stop()
        a.start(self._agent("claude-code"), "claude-code/big-pickle")  # model change
        self.assertNotIn("--continue", a.proc.argv, "a model change must start fresh")
        self.assertNotIn("--resume", a.proc.argv, "a model change must not resume a session id")
        self.assertIn("--model", a.proc.argv)
        self.assertIn("claude-code/big-pickle", a.proc.argv)
        # And from "no model" to a specific model is also a change.
        b = sm.AgentSession("B", "claude-code", self.tmp)
        b._runner = self.manager.runner
        b.start(self._agent("claude-code"), None)
        b.stop()
        b.start(self._agent("claude-code"), "claude-code/beam-free")
        self.assertNotIn("--continue", b.proc.argv)

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


class VaultTests(unittest.TestCase):
    def setUp(self):
        import backend.vault as vault
        self.vault = vault
        self._old = vault.VAULT_DIR
        self.tmp = tempfile.mkdtemp(prefix="dennis-vault-")
        vault.VAULT_DIR = self.tmp

    def tearDown(self):
        self.vault.VAULT_DIR = self._old

    def test_seed_and_tree(self):
        self.vault.ensure_vault()
        tree = self.vault.list_tree()
        names = {n["name"] for n in tree}
        self.assertIn("Welcome.md", names)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "Welcome.md")))

    def test_write_read_roundtrip(self):
        self.vault.ensure_vault()
        self.vault.write_note("Notes/Deep.md", "# Deep\n\nBody.\n")
        note = self.vault.read_note("Notes/Deep.md")
        self.assertEqual(note["title"], "Deep")
        self.assertIn("Body.", note["content"])

    def test_path_escape_rejected(self):
        self.vault.ensure_vault()
        with self.assertRaises(self.vault.VaultError):
            self.vault.write_note("../escape.md", "nope")
        with self.assertRaises(self.vault.VaultError):
            self.vault.read_note("../../etc/passwd")

    def test_rename_and_soft_delete(self):
        self.vault.ensure_vault()
        self.vault.write_note("A.md", "# A\n")
        self.vault.rename_note("A.md", "B.md")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "B.md")))
        res = self.vault.delete_note("B.md")
        self.assertFalse(os.path.isfile(os.path.join(self.tmp, "B.md")))
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, res["trashed"])))

    def test_wikilink_graph_and_backlinks(self):
        self.vault.ensure_vault()
        self.vault.write_note("Alpha.md", "# Alpha\n\nLinks to [[Beta]].\n")
        self.vault.write_note("Beta.md", "# Beta\n")
        g = self.vault.graph()
        edges = {(l["source"], l["target"]) for l in g["links"]}
        self.assertIn(("Alpha.md", "Beta.md"), edges)
        backs = self.vault.backlinks("Beta.md")
        self.assertEqual([b["path"] for b in backs], ["Alpha.md"])

    def test_search_scores_and_snippets(self):
        self.vault.ensure_vault()
        self.vault.write_note("Gamma.md", "# Gamma\n\nThe needle is here.\n")
        results = self.vault.search("needle")
        self.assertEqual(results[0]["path"], "Gamma.md")
        self.assertIn("needle", results[0]["snippet"])


class _FakeRegistry:
    """Minimal stand-in for backend.agent_registry's read API."""

    def __init__(self, ids):
        self.ids = list(ids)

    def available_agents(self):
        return [{"id": i, "name": i.title(), "provider": "test",
                 "icon_char": "x", "available": True, "reason": "Ready"}
                for i in self.ids]

    def get_agent(self, agent_id):
        if agent_id in self.ids:
            return {"id": agent_id, "name": agent_id.title()}
        return None


class _FakeHermesRunner:
    """Runs instantly in a thread; records emissions."""

    def __init__(self, task, emit, done):
        self.task = task
        self.emit = emit
        self.done = done
        self._buf = ""
        self.stopped = False

    def buffer(self):
        return self._buf

    def start(self):
        import threading

        def work():
            time.sleep(0.02)
            self._buf = "result:" + self.task["title"]
            self.emit(self._buf)
            self.done(self._buf, "")

        threading.Thread(target=work, daemon=True).start()
        return True

    def stop(self):
        self.stopped = True


class HermesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-hermes-")
        self._old_vault = vault.VAULT_DIR
        vault.VAULT_DIR = os.path.join(self.tmp, "vault")
        self.registry = _FakeRegistry(["antigravity", "opencode", "codex-cli"])
        self.path = os.path.join(self.tmp, "hermes.json")

    def tearDown(self):
        vault.VAULT_DIR = self._old_vault

    def _make(self, planner=None, runner_factory=None, concurrency=2):
        return hermes.HermesOrchestrator(
            path=self.path, vault_module=vault, registry=self.registry,
            planner=planner, runner_factory=runner_factory, concurrency=concurrency)

    def test_decompose_is_five_role_pipeline(self):
        spec = hermes.decompose("Ship the billing feature")
        self.assertEqual([t["role"] for t in spec],
                         ["research", "plan", "implement", "verify", "document"])
        self.assertTrue(all(t["title"] and t["detail"] for t in spec))

    def test_decompose_requires_a_goal(self):
        with self.assertRaises(hermes.HermesError):
            hermes.decompose("  ")

    def test_normalize_plan_filters_and_falls_back(self):
        self.assertEqual(hermes._normalize_plan(None, "g"),
                         hermes.decompose("g"))
        cleaned = hermes._normalize_plan(
            [{"title": "Good", "role": "bogus"}, "junk", {}], "g")
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["role"], "implement")  # unknown -> implement

    def test_route_verify_avoids_implementer(self):
        h = self._make()
        task = {"role": "verify", "implemented_by": "antigravity"}
        chosen = h.route(task, available=["antigravity", "opencode"])
        self.assertNotEqual(chosen, "antigravity")

    def test_route_is_least_loaded_and_deterministic(self):
        h = self._make()
        chosen = h.route({"role": "implement"},
                         available=["antigravity", "opencode"],
                         in_use=["antigravity", "antigravity"])
        self.assertEqual(chosen, "opencode")

    def test_route_returns_none_without_agents(self):
        h = self._make()
        self.assertIsNone(h.route({"role": "implement"}, available=[]))

    def test_plan_assigns_autonomously_and_writes_vault(self):
        h = self._make(planner=lambda g, c: None)
        result = h.plan("Build a login page")
        self.assertEqual(result["run"]["planner"], "vault")
        self.assertEqual(len(result["tasks"]), 5)
        for t in result["tasks"]:
            self.assertEqual(t["assignment"], "autonomous")
            self.assertIn(t["agent_id"], self.registry.ids)
            self.assertEqual(t["status"], "assigned")
        # second brain note created under Hermes/Plans
        plans = os.path.join(vault.VAULT_DIR, "Hermes", "Plans")
        self.assertTrue(os.listdir(plans))

    def test_plan_spreads_load_across_subagents(self):
        h = self._make(planner=lambda g, c: None)
        h.plan("Distribute the work")
        used = {t["agent_id"] for t in h.list_tasks()}
        self.assertGreaterEqual(len(used), 2)  # not all on one agent

    def test_plan_uses_cli_brain_when_requested(self):
        calls = {}

        def planner(goal, context):
            calls["goal"] = goal
            return [{"title": "Only task", "detail": "d", "role": "implement"}]

        h = self._make(planner=planner)
        result = h.plan("Do the thing", brain="cli")
        self.assertEqual(result["run"]["planner"], "cli")
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(calls["goal"], "Do the thing")

    def test_run_fans_out_and_aggregates(self):
        h = self._make(planner=lambda g, c: None,
                       runner_factory=lambda task, agent, emit, done: _FakeHermesRunner(task, emit, done))
        h.run("Refactor auth", wait=True)
        run = h.list_runs()[0]
        self.assertEqual(run["status"], "complete")
        statuses = {t["role"]: t["status"] for t in h.list_tasks()}
        self.assertEqual(statuses["verify"], "done")
        self.assertEqual(statuses["document"], "done")
        self.assertEqual(statuses["implement"], "review")  # awaits human review
        runs_dir = os.path.join(vault.VAULT_DIR, "Hermes", "Runs")
        self.assertTrue(os.listdir(runs_dir))

    def test_manual_assign_and_status_move(self):
        h = self._make()
        task = h.create_task("Wire the API", role="implement")
        self.assertEqual(task["status"], "backlog")
        h.assign_task(task["id"], "opencode")
        self.assertEqual(h.list_tasks()[0]["agent_id"], "opencode")
        self.assertEqual(h.list_tasks()[0]["assignment"], "manual")
        h.set_status(task["id"], "done")
        self.assertEqual(h.list_tasks()[0]["status"], "done")
        with self.assertRaises(hermes.HermesError):
            h.set_status(task["id"], "nonsense")

    def test_board_groups_by_column(self):
        h = self._make()
        h.create_task("A", role="implement")
        t2 = h.create_task("B", role="verify", agent_id="opencode")
        h.set_status(t2["id"], "running")
        cols = {c["id"]: c["count"] for c in h.board()["columns"]}
        self.assertEqual(cols["backlog"], 1)
        self.assertEqual(cols["running"], 1)

    def test_memory_roundtrip(self):
        h = self._make()
        h.remember("Architecture Decision", "We chose the Vault as the second brain.")
        hits = h.recall("second brain")
        self.assertTrue(any("architecture-decision" in x["path"] for x in hits))

    def test_subagents_excludes_orchestrator(self):
        h = hermes.HermesOrchestrator(
            path=self.path, vault_module=vault,
            registry=_FakeRegistry(["hermes", "opencode"]))
        ids = [a["id"] for a in h.subagents()]
        self.assertNotIn("hermes", ids)
        self.assertIn("opencode", ids)
        self.assertEqual(h.identity()["id"], "hermes")
        self.assertEqual(h.identity()["role"], "orchestrator")


if __name__ == "__main__":
    unittest.main(verbosity=2)

