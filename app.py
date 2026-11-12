"""
DENNIS Agenti
c OS - Local Development Server
Provides static file serving and REST API endpoints for agent orchestration,
model registry, agent discovery, live in-OS installation, and framework routing.
"""

import http.server
import http.client
import html
import gzip
import io
import mimetypes
import re
import socketserver
import socket
import base64
import json
import os
import sys
import time
import atexit
import shutil
import subprocess
import pty
import fcntl
import termios
import struct
import signal
import select
import threading
import queue
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, parse_qs, quote

# ---------------------------------------------------------------------------
# Agent-screen backend (project sessions, CLI-native models, sandboxed PTY/Docker)
# ---------------------------------------------------------------------------
from backend import auth as dennis_auth
from backend import config as dennis_config
from backend import projects as dennis_projects
from backend import session_store as dennis_sessions
from backend import agent_registry as dennis_agents
from backend import model_sources as dennis_models
from backend import freellmapi as dennis_freellmapi
from backend import freellm_catalog as dennis_freellm_catalog
from backend import session_manager as dennis_manager
from backend import ws as dennis_ws
from backend import preflight as dennis_preflight
from backend import denma as dennis_denma
from backend import runner as dennis_runner
from backend import vault as dennis_vault
from backend import hermes as dennis_hermes
from backend import skill_builder as dennis_skills
from backend import skill_compiler as dennis_skill_compiler
from backend import skill_jobs as dennis_skill_jobs
from backend import skill_registry as dennis_skill_registry
from backend import skill_sources as dennis_skill_sources
from backend import deepseek_harness as dennis_deepseek
from backend import deepseek_native as dennis_deepseek_native
from backend import plugins as dennis_plugins
from backend import composio as dennis_composio
from backend import megalist as dennis_megalist
from backend import megalist_catalog as dennis_megalist_catalog
from backend import megalist_store as dennis_megalist_store
from backend import megalist_verify as dennis_megalist_verify
from backend import studio_catalog as dennis_studio_catalog
from backend import studio_collections as dennis_studio_collections
from backend import studio_contracts as dennis_studio_contracts
from backend import studio_fashion as dennis_studio_fashion
from backend import studio_free as dennis_studio_free
from backend import studio_registry as dennis_studio_registry
from backend import studio_higgsfield as dennis_studio_higgsfield
from backend import studio_jobs as dennis_studio_jobs
from backend import studio_mcp as dennis_studio_mcp
from backend import studio_media as dennis_studio_media
from backend import studio_model_library as dennis_studio_model_library
from backend import studio_providers as dennis_studio_providers
from backend import studio_secrets as dennis_studio_secrets
from backend import studio_tools as dennis_studio_tools
from backend import studio_videokit as dennis_studio_videokit
from backend import studio_workspaces as dennis_studio_workspaces
from backend import tts as dennis_tts
from backend import youtube_api as dennis_youtube_api
from backend.sandbox import ExecContext
from backend import tts as dennis_tts

# Hermes: the orchestrator agent. Owns the Kanban board and delegates work to
# the registered CLI sub-agents (persistent state in data/hermes.json).
HERMES = dennis_hermes.HermesOrchestrator()

# Studio: provider registry + async generation jobs + agent tool approvals.
# The monitor is started by run(); tests may start it explicitly.
STUDIO_RUNNER = dennis_studio_jobs.StudioRunner(dennis_studio_providers.REGISTRY)
STUDIO_TOOLS = dennis_studio_tools.StudioTools(
    dennis_studio_providers.REGISTRY, STUDIO_RUNNER
)
STUDIO_MAX_BODY = 40 * 1024 * 1024

PORT = 8080
# DenMa (Trading Den) endpoints proxied through this server so the embedded
# dashboard is same-origin. These paths do not collide with the OS's own /api/*.
DENMA_PROXY_EXACT = {
    '/api/markets', '/api/live-snapshot', '/api/screener',
    '/api/triangular', '/api/mt5-pricing',
}
DENMA_PROXY_PREFIX = ('/api/history/', '/api/arbitrage/', '/api/bots/')
DENMA_PAGE_MARKER = '/trading_den/MMcloneLoopEngineering/'
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(DIRECTORY, "data")
AGENTS_FILE = os.path.join(DATA_DIR, "agents.json")
CONFIG_FILE = os.path.join(DATA_DIR, "os_config.json")
MODELS_FILE = os.path.join(DATA_DIR, "models.json")
CONVERSATION_FILE = os.path.join(DATA_DIR, "conversation.json")
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")

# Diagnostic flags the pre-flight tester may pass to an agent binary. Kept
# deliberately small: the tester exists to check a binary responds, not to run
# it with arbitrary arguments.
SAFE_DIAG_ARGS = {"--version", "-V", "-v", "--help", "-h", "version", "help"}

# Max buffered terminal frames when no SSE consumer is draining the queue.
TERMINAL_QUEUE_MAX = 2048

# Static text assets that are worth gzip-compressing on the fly.
GZIP_STATIC_EXTS = {
    ".html", ".htm", ".css", ".js", ".mjs", ".json",
    ".svg", ".txt", ".xml", ".map", ".webmanifest",
}
GZIP_STATIC_MIN_BYTES = 1024

os.makedirs(DATA_DIR, exist_ok=True)


def _hermes_notes(tree, prefix="Hermes/"):
    """Flatten the vault tree to the notes inside Hermes' second-brain folder."""
    found = []
    for node in tree or []:
        if node.get("type") == "dir":
            found += _hermes_notes(node.get("children"), prefix)
        elif str(node.get("path", "")).startswith(prefix):
            found.append({"path": node.get("path"), "title": node.get("title")})
    return found

def load_models():
    if not os.path.exists(MODELS_FILE):
        return []
    try:
        with open(MODELS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def load_conversation():
    if not os.path.exists(CONVERSATION_FILE):
        return []
    try:
        with open(CONVERSATION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_conversation(messages):
    try:
        dennis_config.write_json(CONVERSATION_FILE, messages)
    except Exception as e:
        print(f"Error saving conversation: {e}")

# ---------------------------------------------------------------------------
# CHAT SESSIONS
# ---------------------------------------------------------------------------

def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")

def _derive_title(messages):
    for m in messages or []:
        if m.get("role") == "user" and m.get("text"):
            t = m["text"].strip()
            return (t[:40] + "…") if len(t) > 40 else t
    return "New Session"

def load_sessions():
    """Returns the session store. Migrates legacy conversation.json on first run."""
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("sessions"), list):
                return data
        except Exception as e:
            print(f"Error loading sessions: {e}")

    sessions = []
    legacy = load_conversation()
    if legacy:
        ts = _now_iso()
        sessions.append({
            "id": f"sess-{int(time.time() * 1000)}",
            "title": _derive_title(legacy),
            "created_at": ts,
            "updated_at": ts,
            "agent_id": "antigravity",
            "model_id": "gemini-2.5-flash",
            "messages": legacy
        })
    store = {
        "active_session_id": sessions[0]["id"] if sessions else None,
        "sessions": sessions
    }
    save_sessions(store)
    return store

def save_sessions(store):
    try:
        dennis_config.write_json(SESSIONS_FILE, store)
    except Exception as e:
        print(f"Error saving sessions: {e}")

def create_session(store, title=None):
    ts = _now_iso()
    session = {
        "id": f"sess-{int(time.time() * 1000)}",
        "title": title or "New Session",
        "created_at": ts,
        "updated_at": ts,
        "agent_id": None,
        "model_id": None,
        "messages": []
    }
    store["sessions"].insert(0, session)
    store["active_session_id"] = session["id"]
    return session

def get_session(store, session_id):
    if not session_id:
        return None
    return next((s for s in store["sessions"] if s["id"] == session_id), None)

def session_meta(s):
    preview = ""
    for m in s.get("messages", []):
        if m.get("text"):
            preview = m["text"]
    return {
        "id": s["id"],
        "title": s.get("title") or "New Session",
        "created_at": s.get("created_at"),
        "updated_at": s.get("updated_at"),
        "message_count": len(s.get("messages", [])),
        "preview": preview[:60]
    }

DEFAULT_AGENTS = [
    {
        "id": "antigravity",
        "name": "Antigravity",
        "version": "v1.3.5",
        "binary_path": "/Users/macbookpro/.local/bin/agy",
        "config_dir": "/Users/macbookpro/.gemini/antigravity-cli",
        "type": "AI Coding Assistant & OS Core",
        "provider": "Google DeepMind",
        "status": "Connected",
        "capabilities": [
            "Subagents",
            "Artifacts",
            "Terminal Sandbox",
            "Skills & Rules",
            "Sidecars",
            "MCP Integration"
        ],
        "icon_char": "A",
        "color_theme": "cyan",
        "is_system": True,
        "last_sync": "Active now"
    }
]

def get_augmented_env():
    """Returns an environment with complete CLI binary paths"""
    env = os.environ.copy()
    user_home = os.path.expanduser("~")
    extra_paths = [
        os.path.join(DIRECTORY, ".venv", "bin"),
        os.path.join(user_home, ".local", "bin"),
        os.path.join(user_home, ".opencode", "bin"),
        os.path.join(user_home, ".nvm", "versions", "node", "v24.21.0", "bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/usr/bin",
        "/bin"
    ]
    env["PATH"] = ":".join(extra_paths) + ":" + env.get("PATH", "")
    return env

def build_agent_tui_argv(agent, model):
    """Builds the argv to launch an agent's interactive TUI, applying the
    selected model when the CLI accepts a CLI-specific model identifier."""
    binary = (agent.get("binary_path") or agent.get("binary") or "").strip()
    argv = [binary]
    aid = agent.get("id", "")

    cli_model = ""
    if isinstance(model, dict):
        mapping = model.get("cli_models")
        if isinstance(mapping, dict):
            cli_model = mapping.get(aid, "")

    if cli_model and aid != "opencode":
        if aid == "claude-code":
            argv += ["--model", cli_model]
        elif aid in ("codex", "codex-cli"):
            argv += ["-m", cli_model]
        elif aid == "gemini-cli":
            argv += ["--model", cli_model]
        elif aid == "hermes-cli":
            argv += ["-m", cli_model]
    return argv


def load_agents():
    if not os.path.exists(AGENTS_FILE):
        save_agents(DEFAULT_AGENTS)
        return DEFAULT_AGENTS
    try:
        with open(AGENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure Antigravity is always present
            if not any(a.get("id") == "antigravity" for a in data):
                data.insert(0, DEFAULT_AGENTS[0])
            return data
    except Exception as e:
        print(f"Error loading agents: {e}")
        return DEFAULT_AGENTS

def save_agents(agents):
    try:
        dennis_config.write_json(AGENTS_FILE, agents)
    except Exception as e:
        print(f"Error saving agents: {e}")


def integrate_detected_agents(agent_ids=None):
    """Add host-detected CLIs to the Agent Registry (idempotent).

    ``agent_ids=None`` integrates every detected CLI. Existing registry entries
    are never overwritten, and dedupe by id *and* binary keeps aliases such as
    ``codex-cli``/``codex`` from appearing twice. Returns ``added``/``missing``
    plus the resulting registry.
    """
    wanted = set(agent_ids) if agent_ids else None
    agents = [dict(a) for a in load_agents()]
    seen_ids = {a.get("id") for a in agents}

    def _bin(agent):
        return os.path.basename((agent.get("binary_path") or "").strip()).lower()

    seen_bins = {_bin(a) for a in agents if _bin(a)}
    detected = dennis_agents.detect_installed(with_version=True)
    detected_ids = {d.get("id") for d in detected}
    added = []

    for det in detected:
        agent_id = det.get("id")
        if not agent_id or (wanted is not None and agent_id not in wanted):
            continue
        binary = os.path.basename(det.get("binary_path") or "").lower()
        if agent_id in seen_ids or (binary and binary in seen_bins):
            continue
        agents.append({
            "id": agent_id,
            "name": det.get("name") or agent_id,
            "version": det.get("version") or "Installed",
            "binary_path": det.get("binary_path") or "",
            "type": det.get("type") or "Coding CLI",
            "provider": det.get("provider"),
            "status": "Connected",
            "icon_char": det.get("icon_char"),
            "color_theme": det.get("color_theme"),
            "last_sync": "Just now",
        })
        seen_ids.add(agent_id)
        if binary:
            seen_bins.add(binary)
        added.append(agent_id)

    if added:
        save_agents(agents)
    missing = sorted(wanted - detected_ids) if wanted else []
    return {"added": added, "missing": missing, "agents": agents}


def load_config():
    if not os.path.exists(CONFIG_FILE):
        cfg = {"active_agent_id": "antigravity"}
        save_config(cfg)
        return cfg
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"active_agent_id": "antigravity"}

def save_config(cfg):
    try:
        dennis_config.write_json(CONFIG_FILE, cfg)
    except Exception as e:
        print(f"Error saving config: {e}")

class TerminalSession:
    """A single interactive PTY-backed terminal session.

    Streams raw terminal output to connected SSE clients and forwards client
    keystrokes back into the PTY. This lets the web UI drive the agent's real
    interactive TUI (e.g. `opencode`, where `/model` works) exactly as if it
    were running in a native terminal window.
    """

    def __init__(self):
        self.master_fd = None
        self.pid = None
        self.queue = queue.Queue(maxsize=TERMINAL_QUEUE_MAX)
        self.lock = threading.Lock()
        self.running = False
        self._exit_code = 0

    def _offer(self, item):
        """Enqueue a frame, dropping the oldest one when a client is behind.

        Keeps memory bounded when no SSE consumer is draining the stream.
        """
        try:
            self.queue.put_nowait(item)
            return
        except queue.Full:
            pass
        try:
            self.queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self.queue.put_nowait(item)
        except queue.Full:
            pass

    def start(self, argv, cwd, env, rows=30, cols=100):
        with self.lock:
            self._stop_locked()
            # Release any clients still attached to the previous stream.
            self._offer(("exit", {"code": 0}))
            self.queue = queue.Queue(maxsize=TERMINAL_QUEUE_MAX)

            env = dict(env)
            env.setdefault("TERM", "xterm-256color")
            env.setdefault("COLORTERM", "truecolor")

            try:
                pid, fd = pty.fork()
            except Exception as e:
                return False, str(e)

            if pid == 0:
                # Child process: attach to the PTY and exec the agent CLI.
                try:
                    os.chdir(cwd or DIRECTORY)
                    os.environ.clear()
                    os.environ.update(env)
                    os.execvpe(argv[0], argv, env)
                except Exception:
                    os._exit(127)
                os._exit(127)

            self.pid = pid
            self.master_fd = fd
            self.running = True
            self._set_winsize(fd, rows, cols)
            threading.Thread(target=self._read_loop, args=(fd,), daemon=True).start()
            threading.Thread(target=self._wait_loop, args=(pid,), daemon=True).start()
            return True, pid

    @staticmethod
    def _set_winsize(fd, rows, cols):
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass

    def _read_loop(self, fd):
        while self.running:
            try:
                ready, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                break
            if fd in ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                self._offer(("data", data.decode("utf-8", "replace")))
        # Ensure a client blocked on the stream wakes up if the PTY closes.
        self._offer(("exit", {"code": self._exit_code}))

    def _wait_loop(self, pid):
        code = 0
        try:
            _, status = os.waitpid(pid, 0)
            if hasattr(os, "waitstatus_to_exitcode"):
                code = os.waitstatus_to_exitcode(status)
            else:
                code = status
        except ChildProcessError:
            code = 0
        except Exception:
            code = -1
        self._exit_code = code
        self.running = False

    def write(self, data):
        with self.lock:
            fd = self.master_fd
        if fd is None:
            return False
        try:
            os.write(fd, data.encode("utf-8", "replace"))
            return True
        except OSError:
            return False

    def resize(self, rows, cols):
        with self.lock:
            if self.master_fd is not None:
                self._set_winsize(self.master_fd, rows, cols)

    def stop(self):
        with self.lock:
            self._stop_locked()

    def _stop_locked(self):
        pid = self.pid
        self.running = False
        self.pid = None
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
        fd = self.master_fd
        self.master_fd = None
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass


TERMINAL = TerminalSession()

class DennisOSHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # Single-origin local application: do not advertise any cross-origin
        # sharing. Omitting Access-Control-Allow-* makes browsers block
        # cross-origin reads of API responses (which can include the session
        # token) and forces a preflight that we never approve.
        #
        # API responses must never be stored. First-party static assets may be
        # cached but are always revalidated (the handler sends Last-Modified),
        # so repeat loads avoid re-downloading the ~600 KB of JS/CodeMirror
        # while a code change is still picked up immediately.
        path = urlparse(getattr(self, 'path', '') or '').path
        if path.startswith('/api/') or path == '/ws':
            self.send_header('Cache-Control', 'no-store')
        else:
            self.send_header('Cache-Control', 'no-cache, must-revalidate')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204, "no content")
        self.end_headers()

    def log_message(self, format, *args):
        # The session token may appear in a ``?token=`` query string; never
        # write it to the access log.
        message = re.sub(r'(token=)[^&\s"]+', r'\1REDACTED', format % args)
        sys.stderr.write("%s - - [%s] %s\n" % (
            self.address_string(), self.log_date_time_string(), message))

    def send_json(self, data, status_code=200):
        body = json.dumps(data, indent=2).encode('utf-8')
        encoding = None
        # Compress only payloads large enough to benefit, and only when the
        # client advertised gzip support.
        if len(body) >= 1024 and 'gzip' in (self.headers.get('Accept-Encoding') or '').lower():
            body = gzip.compress(body, compresslevel=5)
            encoding = 'gzip'
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Vary', 'Accept-Encoding')
        if encoding:
            self.send_header('Content-Encoding', encoding)
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------
    # Centralized TTS Router: speech generation + saved-audio serving
    # ------------------------------------------------------------------
    def _tts_speech(self, body):
        """POST /api/tts/speech — generate speech via the resolved provider.

        Body: {text, studio?, preset?, voice_id?, speed?, lang?,
               response_format? ('wav'|'pcm'), stream? (bool), save?, name?}
        Returns raw audio bytes, or (with save:true) a JSON descriptor whose
        `url` serves the saved file for download / studio libraries.

        With ``stream: true`` the Kokoro PCM stream is relayed chunk-by-chunk
        (sentence-level synthesis) so long replies start playing early.
        """
        body = body if isinstance(body, dict) else {}
        text = (body.get("text") or body.get("input") or "").strip()
        if not text:
            return self.send_json({"error": "text is required"}, 400)
        try:
            provider, preset, _cfg = dennis_tts.effective_provider(
                body.get("studio") or "apollo", body.get("preset"))
        except dennis_tts.TTSError as exc:
            return self.send_json({"error": str(exc)}, 400)

        if body.get("stream") and provider == "kokoro":
            voice_id = (body.get("voice_id") or body.get("voice")
                        or (preset or {}).get("voice_id") or "bm_george")
            speed = body.get("speed")
            if speed is None:
                speed = (preset or {}).get("speed", 1.03)
            lang = body.get("lang") or (preset or {}).get("lang")
            return self._tts_speech_stream(text, voice_id, speed, lang)

        try:
            audio, ctype, rate, meta = dennis_tts.generate(
                text,
                studio=body.get("studio") or "apollo",
                preset_slug=body.get("preset"),
                # js/tts.js sends `voice` / `format`; the long forms are the
                # canonical names — accept both aliases.
                voice_id=body.get("voice_id") or body.get("voice"),
                speed=body.get("speed"),
                lang=body.get("lang"),
                response_format=body.get("response_format") or body.get("format"),
            )
        except dennis_tts.TTSError as exc:
            return self.send_json({"error": str(exc)}, 503)
        except Exception as exc:  # unexpected — keep the OS alive, report honestly
            return self.send_json({"error": f"TTS generation failed: {exc}"}, 500)

        if body.get("save"):
            try:
                record = dennis_tts.save_audio(audio, name=body.get("name"), meta=meta)
            except dennis_tts.TTSError as exc:
                return self.send_json({"error": str(exc)}, 500)
            return self.send_json({"success": True, "audio": record})

        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(audio)))
        if rate:
            self.send_header('X-Audio-Sample-Rate', str(rate))
        self.end_headers()
        self.wfile.write(audio)

    def _tts_audio_file(self, name):
        """GET /api/tts/audio/<file> — serve a previously saved voiceover."""
        filename = os.path.basename(name)
        if filename != name or not filename or filename.startswith('.'):
            return self.send_json({"error": "Invalid audio file name"}, 400)
        path = os.path.join(dennis_tts.AUDIO_DIR, filename)
        if not os.path.isfile(path):
            return self.send_json({"error": "Audio file not found"}, 404)
        ext = os.path.splitext(filename)[1].lower()
        ctype = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg',
                 '.pcm': 'audio/pcm; rate=24000'}.get(ext, 'application/octet-stream')
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except OSError:
            return self.send_json({"error": "Audio file unreadable"}, 500)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Disposition', f'inline; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)


    # ------------------------------------------------------------------
    # Static text assets: gzip on the fly (the API paths already do this)
    # ------------------------------------------------------------------
    def _wants_static_gzip(self, path):
        if 'gzip' not in (self.headers.get('Accept-Encoding') or '').lower():
            return False
        if os.path.splitext(path)[1].lower() not in GZIP_STATIC_EXTS:
            return False
        try:
            return os.path.isfile(path) and os.path.getsize(path) >= GZIP_STATIC_MIN_BYTES
        except OSError:
            return False

    def send_head(self):
        # Delegate the normal path for anything we won't compress. For eligible
        # text assets we let the base class do all the work (directory handling,
        # conditional GET, 404s) while capturing its headers, then re-emit them
        # with the body gzipped. The captured headers are dropped in favour of a
        # recomputed Content-Length plus Content-Encoding/Vary.
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            for index in getattr(self, 'index_pages', ('index.html', 'index.htm')):
                candidate = os.path.join(path, index)
                if os.path.isfile(candidate):
                    path = candidate
                    break
        if not self._wants_static_gzip(path):
            return super().send_head()

        real_send_header = self.send_header
        real_end_headers = self.end_headers
        captured = []

        self.send_header = lambda keyword, value: captured.append((keyword, value))
        self.end_headers = lambda: None
        try:
            f = super().send_head()
        finally:
            self.send_header = real_send_header
            self.end_headers = real_end_headers

        status_line = self._headers_buffer[0] if getattr(self, '_headers_buffer', None) else b''
        self._headers_buffer = [status_line] if status_line else []

        for keyword, value in captured:
            if keyword.lower() != 'content-length':
                real_send_header(keyword, value)

        if f is None:  # 304 Not Modified or an error response
            real_end_headers()
            return None

        try:
            raw = f.read()
        finally:
            f.close()
        body = gzip.compress(raw, compresslevel=5)
        real_send_header('Content-Encoding', 'gzip')
        real_send_header('Vary', 'Accept-Encoding')
        real_send_header('Content-Length', str(len(body)))
        real_end_headers()
        return io.BytesIO(body)

    # ------------------------------------------------------------------
    # Agent-screen helpers (auth token, exec context, terminal WebSocket)
    # ------------------------------------------------------------------
    def _request_token(self, parsed):
        token = self.headers.get('X-Dennis-Token')
        if not token:
            token = parse_qs(parsed.query).get('token', [None])[0]
        return token

    def _require_owner(self, parsed):
        owner = dennis_auth.AUTH.owner_for(self._request_token(parsed))
        if not owner:
            self.send_json({"error": "Unauthorized"}, 401)
        return owner

    def _is_cross_site(self):
        """True when a browser request was clearly initiated by another site.

        Browsers attach ``Sec-Fetch-Site`` themselves and page JavaScript
        cannot forge it; an ``Origin``/``Host`` mismatch is a fallback for
        older clients. Non-browser clients (curl, the test harness) send
        neither header and are treated as same-origin.
        """
        site = (self.headers.get('Sec-Fetch-Site') or '').strip().lower()
        if site and site not in ('same-origin', 'none'):
            return True
        origin = self.headers.get('Origin')
        if origin and origin != 'null':
            try:
                ohost = urlparse(origin).netloc
            except Exception:
                return True
            if ohost != (self.headers.get('Host') or ''):
                return True
        return False

    def _reject_cross_site(self, path):
        """Send 403 and return True for cross-site browser requests."""
        if self._is_cross_site():
            self.send_json({"error": "Cross-site request blocked"}, 403)
            return True
        return False

    def _tts_speech_stream(self, text, voice_id, speed, lang):
        """Relay the Kokoro service's chunked PCM stream to the browser.

        The browser never talks to the inference service directly: it posts to
        the owner-gated /api/tts/speech route with ``stream: true`` and
        receives ``audio/pcm`` chunks as the local service synthesizes each
        sentence, so long agent replies start playing after roughly one
        sentence instead of the full synthesis time. Non-kokoro providers
        (and any upstream that answers without a stream) fall back to the
        buffered response built by _tts_speech.
        """
        payload = {
            "model": "kokoro",
            "input": text,
            "voice": voice_id,
            "speed": float(speed),
            "response_format": "pcm",
        }
        if lang:
            payload["lang"] = lang
        try:
            parsed = urlparse(dennis_tts.KOKORO_BASE_URL)
            conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=600)
            conn.request(
                "POST", "/v1/audio/speech", body=json.dumps(payload),
                headers={"Content-Type": "application/json",
                         "Content-Length": str(len(json.dumps(payload)))})
            resp = conn.getresponse()
        except OSError:
            return self.send_json(
                {"error": "Kokoro service unreachable — start it from the Apollo voice panel"}, 503)

        if resp.status != 200:
            detail = resp.read().decode("utf-8", "replace")[:300]
            try:
                conn.close()
            except OSError:
                pass
            return self.send_json({"error": f"Kokoro service error {resp.status}: {detail}"}, 502)

        rate = resp.getheader("X-Audio-Sample-Rate") or "24000"
        ctype = f"audio/pcm; rate={rate}"
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("X-TTS-Provider", "kokoro")
            self.send_header("X-TTS-Voice", str(voice_id))
            self.send_header("X-Audio-Sample-Rate", str(rate))
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii"))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser cancelled playback; stop pulling from upstream
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _require_api_owner(self, parsed):
        """Owner-gate every OS API route except the two public bootstrap paths.

        The Trading Den reverse proxy is handled before this and stays
        reachable for the same-origin embedded dashboard (guarded by the
        cross-site check instead of the token, which the iframe cannot send).
        """
        public = ('/api/auth/session',)
        if parsed.path.startswith('/api/') and parsed.path not in public:
            if not self._require_owner(parsed):
                return False
        return True

    def _exec_ctx(self, project_path, agent_id=None):
        return ExecContext(dennis_manager.MANAGER.runner,
                           dennis_agents.get_augmented_env(agent_id), project_path,
                           agent_id=agent_id)

    def _serve_terminal_ws(self, sess):
        def on_open(ws):
            sess.add_client(ws)

        def on_message(ws, message):
            try:
                msg = json.loads(message)
            except Exception:
                return
            mtype = msg.get('type')
            if mtype == 'input':
                sess.write(msg.get('data', ''))
            elif mtype == 'resize':
                sess.resize(int(msg.get('rows') or 30), int(msg.get('cols') or 100))
            elif mtype == 'ping':
                ws.send_text({"type": "pong"})

        def on_close(ws):
            sess.remove_client(ws)

        dennis_ws.run_websocket(self, on_message, on_open, on_close)

    # ------------------------------------------------------------------
    # DeepSeek Harness: owner-gated SSE stream for one chat turn.
    # ------------------------------------------------------------------
    def _serve_deepseek_stream(self, exchange):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        events = exchange.events()
        try:
            for event in events:
                chunk = "data: " + json.dumps(event) + "\n\n"
                self.wfile.write(chunk.encode('utf-8'))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            # The browser closed the tab/Stop: close the upstream socket and
            # persist whatever the assistant had produced so far.
            pass
        finally:
            try:
                events.close()
            except Exception:
                pass
            exchange.close()

    # ------------------------------------------------------------------
    # Trading Den (DenMa) reverse proxy: makes the embedded dashboard
    # same-origin with the OS. REST (/api/*) and WS (/ws) are forwarded to
    # the supervised DenMa backend on its private loopback port.
    # ------------------------------------------------------------------
    def _is_denma_request(self, path):
        if path == '/ws':
            return True
        if path == '/api/status':
            return DENMA_PAGE_MARKER in (self.headers.get('Referer') or '')
        if path in DENMA_PROXY_EXACT:
            return True
        return any(path.startswith(prefix) for prefix in DENMA_PROXY_PREFIX)

    def _proxy_denma_http(self, body=None):
        backend = dennis_denma.BACKEND
        conn = http.client.HTTPConnection(backend.host, backend.port, timeout=60)
        headers = {}
        ctype = self.headers.get('Content-Type')
        if ctype:
            headers['Content-Type'] = ctype
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            return self.send_json(
                {"error": f"DenMa backend unreachable ({exc}). "
                          f"Check /api/trading-den."}, 502)
        finally:
            conn.close()
        self.send_response(resp.status)
        for key, value in resp.getheaders():
            if key.lower() in ('transfer-encoding', 'connection', 'content-length'):
                continue
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy_denma_ws(self):
        backend = dennis_denma.BACKEND
        key = self.headers.get('Sec-WebSocket-Key')
        if not key:
            return self.send_error(400, "Missing Sec-WebSocket-Key")
        try:
            upstream = socket.create_connection((backend.host, backend.port), timeout=15)
        except OSError:
            return self.send_error(502, "DenMa backend unreachable")

        up_key = base64.b64encode(os.urandom(16)).decode('ascii')
        handshake = (
            "GET /ws HTTP/1.1\r\n"
            f"Host: {backend.host}:{backend.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {up_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        try:
            upstream.sendall(handshake.encode('ascii'))
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = upstream.recv(1024)
                if not chunk:
                    break
                buf += chunk
        except OSError:
            upstream.close()
            return self.send_error(502, "DenMa WS handshake failed")
        if b" 101 " not in buf.split(b"\r\n", 1)[0]:
            upstream.close()
            return self.send_error(502, "DenMa WS upgrade rejected")

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {dennis_ws.accept_key(key)}\r\n\r\n"
        )
        self.connection.sendall(response.encode('ascii'))
        self.close_connection = True

        left = buf.split(b"\r\n\r\n", 1)[1]
        if left:
            self.connection.sendall(left)

        client = self.connection
        try:
            while True:
                readable, _, errored = select.select([client, upstream], [], [client, upstream], 30)
                if errored:
                    break
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is client else client).sendall(data)
        except OSError:
            pass
        finally:
            for sock in (client, upstream):
                try:
                    sock.close()
                except OSError:
                    pass

    # ------------------------------------------------------------------
    # Code & Preview IDE: save files and run the open project's server.
    # ------------------------------------------------------------------
    def _denma_project(self, project_path):
        if not project_path:
            return False
        return os.path.realpath(project_path) == os.path.realpath(dennis_denma.PROJECT_DIR)

    def _run_status_for(self, rec):
        project_path = rec.get("project_path")
        if not dennis_projects.validate_path(project_path):
            return {"running": False, "command": "", "url": "", "pid": 0,
                    "uptime": 0, "output": [], "error": "Project folder is no longer available"}
        if self._denma_project(project_path):
            st = dennis_denma.BACKEND.status()
            return {
                "running": bool(st["running"]),
                "command": "app.py (DenMa backend)",
                "url": "/trading_den/MMcloneLoopEngineering/templates/index.html" if st["running"] else "",
                "pid": 0,
                "uptime": st.get("uptime", 0),
                "output": [],
                "error": st.get("error", ""),
                "denma": True,
            }
        st = dennis_runner.status_for(project_path)
        url = st.get("url") or ""
        # A static site has no server of its own; serve it from the project
        # files through the raw endpoint so the Preview iframe can load it.
        if url and not url.startswith(("http://", "https://", "/api/")):
            st["url"] = ("/api/project-sessions/" + rec.get("id", "") +
                         "/raw?path=" + quote(url, safe=""))
        return st

    def _run_start_for(self, rec):
        project_path = rec.get("project_path")
        if not dennis_projects.validate_path(project_path):
            return {"error": "Project folder is no longer available"}
        if self._denma_project(project_path):
            dennis_denma.BACKEND.start()
            dennis_denma.BACKEND.wait_ready(timeout=30.0)
        else:
            dennis_runner.start_for(project_path)
        return self._run_status_for(rec)

    def _run_stop_for(self, rec):
        project_path = rec.get("project_path")
        if not dennis_projects.validate_path(project_path):
            return {"error": "Project folder is no longer available"}
        if self._denma_project(project_path):
            dennis_denma.BACKEND.stop()
        else:
            dennis_runner.stop_for(project_path)
        return self._run_status_for(rec)

    # ------------------------------------------------------------------
    # Studio: error mapping and media streaming
    # ------------------------------------------------------------------
    def _studio_send_error(self, exc):
        status = 400
        code = "studio_error"
        if isinstance(exc, dennis_studio_contracts.ContractError):
            status, code = exc.status, exc.code
        elif isinstance(exc, dennis_studio_providers.ProviderError):
            status, code = exc.status, exc.code
        elif isinstance(exc, dennis_studio_jobs.JobError):
            status, code = exc.status, exc.code
        elif isinstance(exc, dennis_studio_tools.ToolError):
            status, code = exc.status, exc.code
        elif isinstance(exc, dennis_studio_collections.CollectionError):
            status, code = exc.status, exc.code
        elif isinstance(exc, dennis_studio_mcp.McpError):
            status, code = 502, exc.code
        elif isinstance(exc, dennis_studio_secrets.SecretError):
            status, code = 400, "credentials_error"
        elif isinstance(exc, dennis_studio_media.MediaError):
            text = str(exc)
            status = 404 if ("not found" in text.lower() or "missing" in text.lower()) else 400
            code = "media_error"
        elif isinstance(exc, dennis_studio_catalog.CatalogError):
            text = str(exc)
            status = 404 if "unknown model" in text.lower() else 500
            code = "catalog_error"
        elif isinstance(exc, dennis_studio_fashion.FashionError):
            status, code = exc.status, exc.code
        elif isinstance(exc, dennis_studio_model_library.LibraryError):
            status, code = exc.status, exc.code
        elif isinstance(exc, dennis_studio_free.FreeError):
            status, code = exc.status, exc.code
        elif isinstance(exc, dennis_studio_higgsfield.ProviderRequestError):
            status, code = exc.status or 502, (exc.error or {}).get("code", "provider_error")
        elif isinstance(exc, dennis_studio_workspaces.WorkspaceError):
            status, code = exc.status, exc.code
        self.send_json({
            "error": dennis_studio_contracts.redact_text(
                getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
            ),
            "code": code,
        }, status if 400 <= status <= 599 else 500)
        return True

    def _send_studio_media(self, media_id, parsed):
        try:
            path, record = dennis_studio_media.open_path(media_id)
        except dennis_studio_media.MediaError as exc:
            return self._studio_send_error(exc)
        try:
            size = os.path.getsize(path)
        except OSError:
            return self._studio_send_error(dennis_studio_media.MediaError("Media file is missing."))

        start, end = 0, max(0, size - 1)
        partial = False
        range_header = self.headers.get("Range") or ""
        if range_header.startswith("bytes=") and size > 0:
            spec = range_header[6:].split(",", 1)[0].strip()
            first, _, last = spec.partition("-")
            try:
                if first:
                    start = int(first)
                    end = int(last) if last else size - 1
                elif last:
                    start = max(0, size - int(last))
                    end = size - 1
                else:
                    raise ValueError
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
                partial = True
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        length = max(0, end - start + 1) if size else 0
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", record.get("content_type") or "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        disposition = "attachment" if parse_qs(parsed.query).get("download") else "inline"
        self.send_header(
            "Content-Disposition",
            '%s; filename="%s"' % (disposition, record.get("filename") or (record["id"] + ".bin")),
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if self._reject_cross_site(path):
            return

        # Browsers request this unprompted; answer quietly instead of a 404
        # that shows up as console noise in every session.
        if path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return

        # Trading Den (DenMa) proxy: WS + its REST endpoints, same-origin.
        if path == '/ws':
            return self._proxy_denma_ws()
        if self._is_denma_request(path):
            return self._proxy_denma_http()

        # Every OS API route from here on is owner-authenticated (the token
        # bootstrap and OAuth callback are the only public exceptions).
        if not self._require_api_owner(parsed):
            return

        # Official DeepSeek Harness native Web UI supervisor.
        if path == '/api/deepseek-native/status':
            return self.send_json(dennis_deepseek_native.status())

        # ==================================================================
        # Centralized TTS Router (Kokoro default; ElevenLabs optional)
        # ==================================================================
        if path == '/api/tts/status':
            return self.send_json(dennis_tts.status())
        if path == '/api/tts/config':
            return self.send_json({"success": True, "config": dennis_tts.load_config()})
        if path == '/api/tts/voices':
            return self.send_json({"voices": dennis_tts.kokoro_voices()})
        if path.startswith('/api/tts/audio/'):
            return self._tts_audio_file(path[len('/api/tts/audio/'):])
        # Trading Den / DenMa backend supervisor status
        if path == '/api/trading-den':
            return self.send_json(dennis_denma.BACKEND.status())

        # ==================================================================
        # STUDIO (read): providers, pinned catalog, jobs, library, tools.
        # ==================================================================
        if path == '/api/studio/providers':
            return self.send_json({
                "providers": dennis_studio_providers.REGISTRY.list(),
                "mcp": dennis_studio_mcp.public_config(),
                "catalog": dennis_studio_catalog.info(),
            })

        # Studio workspaces: registry, per-workspace settings, projects and
        # activity. These stores are isolated from the existing Ads generation
        # data (jobs/media/playlists/templates) and never enumerate it.
        if path == '/api/studio/workspaces':
            try:
                return self.send_json(dennis_studio_workspaces.overview())
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/workspaces/active':
            try:
                return self.send_json({"active": dennis_studio_workspaces.get_active()})
            except Exception as exc:
                return self._studio_send_error(exc)

        # YouTube Studio editorial APIs (channels, research, topics, scripts).
        if path.startswith('/api/studio/youtube/'):
            status, payload = dennis_youtube_api.handle('GET', path, parse_qs(parsed.query), None)
            return self.send_json(payload, status)

        if path.startswith('/api/studio/workspaces/'):
            rest = path[len('/api/studio/workspaces/'):]
            parts = [part for part in rest.split('/') if part]
            try:
                if len(parts) == 1:
                    return self.send_json(dennis_studio_workspaces.descriptor(parts[0]))
                if len(parts) == 2 and parts[1] == 'settings':
                    return self.send_json({
                        "settings": dennis_studio_workspaces.get_settings(parts[0])
                    })
                if len(parts) == 2 and parts[1] == 'activity':
                    qs = parse_qs(parsed.query)
                    return self.send_json(dennis_studio_workspaces.activity(
                        parts[0], qs.get('limit', ['50'])[0]
                    ))
                if len(parts) == 2 and parts[1] == 'projects':
                    qs = parse_qs(parsed.query)
                    return self.send_json(dennis_studio_workspaces.list_projects(
                        parts[0],
                        status=(qs.get('status', [None])[0] or None),
                        query=(qs.get('q', [None])[0] or None),
                        include_archived=(qs.get('include_archived', ['0'])[0] in ('1', 'true')),
                    ))
                if len(parts) == 3 and parts[1] == 'projects':
                    return self.send_json({
                        "project": dennis_studio_workspaces.get_project(parts[0], parts[2])
                    })
                return self.send_json({"error": "Workspace endpoint not found"}, 404)
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/models':
            qs = parse_qs(parsed.query)
            provider_id = (qs.get('provider', [dennis_studio_providers.HIGGSFIELD_ID])[0]
                           or dennis_studio_providers.HIGGSFIELD_ID)
            try:
                provider = dennis_studio_providers.REGISTRY.get(provider_id)
                if qs.get('id'):
                    return self.send_json(provider.model_detail(qs['id'][0]))
                models = provider.models(
                    kind=(qs.get('kind', [None])[0] or None),
                    mode=(qs.get('mode', [None])[0] or None),
                    query=(qs.get('q', [None])[0] or None),
                )
                payload = {
                    "provider": provider_id,
                    "models": models,
                    "count": len(models),
                }
                if provider_id == dennis_studio_providers.HIGGSFIELD_ID:
                    payload["modes"] = dennis_studio_catalog.modes(provider_id)
                    payload["catalog"] = dennis_studio_catalog.info(provider_id)
                return self.send_json(payload)
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/fashion/config':
            qs = parse_qs(parsed.query)
            provider_id = (qs.get('provider', [dennis_studio_providers.HIGGSFIELD_ID])[0]
                           or dennis_studio_providers.HIGGSFIELD_ID)
            try:
                provider = dennis_studio_providers.REGISTRY.get(provider_id)
                payload = dennis_studio_fashion.build_config(
                    provider.models(kind="video"), provider_id
                )
                payload["configured"] = bool(provider.credentials().get("configured"))
                return self.send_json(payload)
            except Exception as exc:
                return self._studio_send_error(exc)

        # Ads Studio free engines: capabilities and job polling (GET).
        if path == '/api/studio/free/models':
            return self.send_json(dennis_studio_free.capabilities())

        if path.startswith('/api/studio/free/jobs/'):
            job_id = path[len('/api/studio/free/jobs/'):]
            try:
                return self.send_json(dennis_studio_free.job_status(job_id))
            except Exception as exc:
                return self._studio_send_error(exc)

        # Provider registry: Setup-page view + classified generation catalog.
        if path == '/api/studio/registry':
            return self.send_json(dennis_studio_registry.registry())

        if path == '/api/studio/generation/catalog':
            return self.send_json(dennis_studio_registry.generation_catalog())

        if path == '/api/studio/fashion/model-library':
            try:
                return self.send_json(dennis_studio_model_library.list_library())
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/jobs':
            qs = parse_qs(parsed.query)
            limit = qs.get('limit', ['50'])[0]
            state = qs.get('state', [None])[0]
            jobs = [
                dennis_studio_jobs.public_job(job)
                for job in STUDIO_RUNNER.store.list(limit=limit, state=state)
            ]
            return self.send_json({"jobs": jobs})

        if path.startswith('/api/studio/jobs/'):
            job_id = path[len('/api/studio/jobs/'):]
            job = STUDIO_RUNNER.store.get(job_id)
            if not job:
                return self.send_json({"error": "Job not found", "code": "job_not_found"}, 404)
            return self.send_json(dennis_studio_jobs.public_job(job))

        if path == '/api/studio/library':
            qs = parse_qs(parsed.query)
            try:
                items = dennis_studio_media.list_items(
                    kind=(qs.get('kind', [None])[0] or None),
                    role=(qs.get('role', ['output'])[0] or 'output'),
                    query=(qs.get('q', [None])[0] or None),
                    limit=qs.get('limit', ['60'])[0],
                )
                return self.send_json({
                    "items": items,
                    "stats": dennis_studio_media.stats(),
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/media/'):
            return self._send_studio_media(path[len('/api/studio/media/'):], parsed)

        if path == '/api/studio/playlists':
            try:
                return self.send_json({
                    "playlists": dennis_studio_collections.list_playlists()
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/templates':
            try:
                return self.send_json({
                    "templates": dennis_studio_collections.list_templates()
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/video-kit/templates':
            try:
                return self.send_json(dennis_studio_videokit.list_video_templates())
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/video-kit/avatars':
            try:
                return self.send_json(dennis_studio_videokit.list_vault_avatars())
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/tools':
            return self.send_json({
                "tools": STUDIO_TOOLS.list_tools(),
                "approvals": STUDIO_TOOLS.list_approvals(),
            })

        if path == '/api/studio/approvals':
            qs = parse_qs(parsed.query)
            return self.send_json({
                "approvals": STUDIO_TOOLS.list_approvals(status=qs.get('status', [None])[0])
            })

        if path == '/api/studio/mcp':
            return self.send_json({
                "config": dennis_studio_mcp.public_config(),
                "provider": dennis_studio_providers.REGISTRY.get(
                    dennis_studio_providers.MCP_ID).descriptor(),
            })

        # 1. System Health / Status
        if path == '/api/status':
            agents = load_agents()
            config = load_config()
            return self.send_json({
                "system": "DENNIS Agentic OS",
                "core": "HERMES Apollo Core",
                "status": "Online",
                "version": "v0.1.0",
                "active_tasks": 4,
                "registered_agents_count": len(agents),
                "primary_agent": "Antigravity",
                "active_agent_id": config.get("active_agent_id", "antigravity"),
                "orchestrator": HERMES.identity(),
            })

        # 2. Get Registered Agents & Active Agent
        if path == '/api/agents':
            agents = load_agents()
            config = load_config()
            return self.send_json({
                "agents": agents,
                "active_agent_id": config.get("active_agent_id", "antigravity")
            })

        # 3. Auto-Detect Installed Agent CLIs on Host System
        if path == '/api/agents/detect':
            detected = self.detect_host_agents()
            return self.send_json({"detected": detected})

        # 4. Agent Catalog for Discovery
        if path == '/api/agents/catalog':
            catalog = self.get_agent_catalog()
            return self.send_json({"catalog": catalog})

        # 5. Model Catalog
        if path == '/api/models':
            models = load_models()
            return self.send_json({"models": models})

        # 6. Chat History (active session)
        if path == '/api/chat/history':
            store = load_sessions()
            session = get_session(store, store.get("active_session_id"))
            history = session.get("messages", []) if session else []
            return self.send_json({
                "history": history,
                "session_id": session["id"] if session else None
            })

        # 7. List Sessions
        if path == '/api/sessions':
            store = load_sessions()
            if not store["sessions"]:
                create_session(store)
                save_sessions(store)
            return self.send_json({
                "sessions": [session_meta(s) for s in store["sessions"]],
                "active_session_id": store.get("active_session_id")
            })

        # 8. View a Single Session (with messages)
        if path.startswith('/api/sessions/'):
            session_id = path[len('/api/sessions/'):]
            store = load_sessions()
            session = get_session(store, session_id)
            if not session:
                return self.send_json({"error": "Session not found"}, 404)
            return self.send_json({"session": session})

        # 9. Interactive Terminal Output Stream (Server-Sent Events)
        if path == '/api/terminal/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()

            q = TERMINAL.queue
            try:
                while True:
                    try:
                        typ, payload = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b': keepalive\n\n')
                        self.wfile.flush()
                        continue
                    if typ == 'exit':
                        self.wfile.write(("event: exit\ndata: " + json.dumps(payload) + "\n\n").encode('utf-8'))
                        self.wfile.flush()
                        break
                    self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode('utf-8'))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            return

        # ==================================================================
        # AGENT SCREEN: auth, projects, agent availability, project sessions,
        # CLI-native model lists and the terminal WebSocket.
        # ==================================================================

        # Session token for the same-origin browser (not a provider secret).
        if path == '/api/auth/session':
            return self.send_json(dennis_auth.AUTH.issue())

        # Preflight readiness: Docker CLI/daemon, Node, build tools, agent images.
        # Missing CLI and a stopped daemon are different, actionable states.
        if path == '/api/system/readiness':
            owner = self._require_owner(parsed)
            if not owner:
                return
            report = dennis_preflight.run(session_backend=dennis_manager.MANAGER.backend_name)
            if dennis_manager.MANAGER.backend_note:
                report['backend_note'] = dennis_manager.MANAGER.backend_note
            return self.send_json(report)

        # Allowed project folders (validated server-side).
        if path == '/api/projects':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json({"projects": dennis_projects.list_projects()})

        # ==================================================================
        # VAULT (Obsidian-compatible Markdown knowledge base).
        # ==================================================================
        if path == '/api/vault/notes':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json({
                    "vault": dennis_vault.ensure_vault(),
                    "tree": dennis_vault.list_tree(),
                })
            except dennis_vault.VaultError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/vault/note':
            owner = self._require_owner(parsed)
            if not owner:
                return
            rel = parse_qs(parsed.query).get('path', [None])[0]
            try:
                return self.send_json(dennis_vault.read_note(rel))
            except dennis_vault.VaultError as e:
                return self.send_json({"error": str(e)}, 404 if 'not found' in str(e).lower() else 400)

        if path == '/api/vault/search':
            owner = self._require_owner(parsed)
            if not owner:
                return
            qs = parse_qs(parsed.query)
            q = qs.get('q', [''])[0]
            try:
                limit = int(qs.get('limit', ['50'])[0])
            except ValueError:
                limit = 50
            try:
                return self.send_json({"query": q, "results": dennis_vault.search(q, limit=limit)})
            except dennis_vault.VaultError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/vault/graph':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_vault.graph())
            except dennis_vault.VaultError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/vault/backlinks':
            owner = self._require_owner(parsed)
            if not owner:
                return
            rel = parse_qs(parsed.query).get('path', [None])[0]
            try:
                return self.send_json({"path": rel, "backlinks": dennis_vault.backlinks(rel)})
            except dennis_vault.VaultError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/vault/asset':
            owner = self._require_owner(parsed)
            if not owner:
                return
            rel = parse_qs(parsed.query).get('path', [None])[0]
            try:
                import backend.studio_videokit as dennis_videokit
                target = dennis_videokit.vault_asset_path(rel)
            except (dennis_vault.VaultError, ValueError) as e:
                return self.send_json({"error": str(e)}, 404)
            try:
                with open(target, "rb") as fh:
                    raw = fh.read()
            except OSError:
                return self.send_json({"error": "Vault asset is missing."}, 404)
            content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "private, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)
            return

        # ==================================================================
        # AGENT SKILL BUILDER: YouTube -> transcript (ears) -> Gemini visual
        # analysis (eyes) -> reusable Claude Code skill.
        # ==================================================================
        if path == '/api/skill-builder/status':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(dennis_skills.status())

        if path == '/api/skill-builder/skills':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(dennis_skills.list_skills())

        if path == '/api/skill-builder/skill':
            owner = self._require_owner(parsed)
            if not owner:
                return
            name = parse_qs(parsed.query).get('name', [None])[0]
            try:
                return self.send_json(dennis_skills.read_skill(name))
            except dennis_skills.SkillBuilderError as e:
                return self.send_json({"error": str(e)}, 404)

        if path == '/api/skill-builder/jobs':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json({"jobs": dennis_skill_jobs.RUNNER.list()})

        if path.startswith('/api/skill-builder/jobs/'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            job_id = path.rsplit('/', 1)[-1]
            job = dennis_skill_jobs.RUNNER.get(job_id)
            if not job:
                return self.send_json({"error": "Job not found"}, 404)
            return self.send_json(job)

        if path == '/api/skill-builder/drafts':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json({"drafts": dennis_skills.list_drafts()})

        if path == '/api/skill-builder/registry':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(dennis_skill_registry.list_skills())

        if path == '/api/skill-builder/package':
            owner = self._require_owner(parsed)
            if not owner:
                return
            draft_id = parse_qs(parsed.query).get('draft', [None])[0]
            try:
                draft = dennis_skills.get_draft(draft_id)
            except dennis_skills.SkillBuilderError as e:
                return self.send_json({"error": str(e)}, 404)
            root = ((draft.get("package") or {}).get("root") or "")
            if not root or not os.path.isdir(root):
                return self.send_json({"error": "This draft has no compiled package."}, 404)
            return self.send_json(dennis_skill_compiler.preview(root))

        if path.startswith('/api/skill-builder/drafts/'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            draft_id = path.rsplit('/', 1)[-1]
            try:
                return self.send_json(dennis_skills.get_draft(draft_id))
            except dennis_skills.SkillBuilderError as e:
                return self.send_json({"error": str(e)}, 404)

        if path == '/api/skill-builder/cache':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(dennis_skill_sources.cache_stats())

        if path == '/api/skill-builder/templates':
            owner = self._require_owner(parsed)
            if not owner:
                return
            qs = parse_qs(parsed.query)
            category = qs.get('category', [None])[0]
            query = qs.get('query', [None])[0]
            import backend.claude_templates as dennis_claude_templates
            return self.send_json({"templates": dennis_claude_templates.list_templates(category=category, query=query)})

        if path.startswith('/api/skill-builder/templates/'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            tmpl_id = path[len('/api/skill-builder/templates/'):]
            import backend.claude_templates as dennis_claude_templates
            item = dennis_claude_templates.get_template(tmpl_id)
            if not item:
                return self.send_json({"error": "Template not found"}, 404)
            return self.send_json(item)

        # ==================================================================
        # PLUGINS: agent-agnostic skill packs (open Agent Skills standard).
        # ==================================================================
        if path == '/api/plugins':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(dennis_plugins.list_plugins())

        if path == '/api/plugins/status':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(dennis_plugins.status())

        if path == '/api/plugins/plugin':
            owner = self._require_owner(parsed)
            if not owner:
                return
            name = parse_qs(parsed.query).get('name', [None])[0]
            try:
                return self.send_json(dennis_plugins.read_plugin(name))
            except dennis_plugins.PluginError as e:
                return self.send_json({"error": str(e)}, 404)

        if path == '/api/plugins/catalog':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(dennis_plugins.catalog())

        if path == '/api/plugins/installed':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(dennis_plugins.installed_apps())

        if path == '/api/plugins/skills':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(dennis_plugins.list_skills())

        # ==================================================================
        # COMPOSIO: plugin pack, MCP wiring, catalog, keys (Plugins tab).
        # ==================================================================
        if path == '/api/composio/overview':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_composio.overview())
            except dennis_composio.ComposioError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/composio/toolkit':
            owner = self._require_owner(parsed)
            if not owner:
                return
            slug = parse_qs(parsed.query).get('slug', [None])[0]
            try:
                return self.send_json(dennis_composio.toolkit(slug))
            except dennis_composio.ComposioError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/composio/mcp':
            owner = self._require_owner(parsed)
            if not owner:
                return
            client = parse_qs(parsed.query).get('client', ['generic'])[0]
            return self.send_json(dennis_composio.mcp_snippet(client))

        # ==================================================================
        # API MEGA LIST: the real upstream repository workspace.
        # ==================================================================
        # ==================================================================
        # API MEGA LIST: normalized catalog, saved entries, readiness.
        # The legacy /status|/tree|/file|/asset routes below still power the
        # repository browser in the Catalog Sources section.
        # ==================================================================
        if path == '/api/megalist/overview':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.ensure_index()
                history = dennis_megalist_catalog.read_history()
                saved = dennis_megalist_store.saved_map(owner)
                verify = dennis_megalist_store.verification_map()
                readiness = dennis_megalist_store.readiness_map(owner)
                return self.send_json(dennis_megalist_catalog.overview(
                    index, saved, verify, history, readiness))
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/catalog':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.ensure_index()
                saved = dennis_megalist_store.saved_map(owner)
                verify = dennis_megalist_store.verification_map()
                params = parse_qs(parsed.query)
                return self.send_json(dennis_megalist_catalog.query(
                    index, params, saved, verify))
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/categories':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.ensure_index()
                return self.send_json({
                    "categories": index.get("categories") or [],
                    "featured": index.get("featured") or [],
                    "count": len(index.get("categories") or []),
                })
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/entry':
            owner = self._require_owner(parsed)
            if not owner:
                return
            entry_id = parse_qs(parsed.query).get('id', [''])[0]
            try:
                index = dennis_megalist_catalog.ensure_index()
                entry = dennis_megalist_catalog.get_entry(index, entry_id)
                if not entry:
                    return self.send_json(
                        {"error": "Catalog entry not found.", "code": "entry_not_found"}, 404)
                saved = dennis_megalist_store.saved_map(owner)
                verify = dennis_megalist_store.verification_map()
                payload = dennis_megalist_catalog.detail(entry, saved, verify)
                payload["readiness"] = dennis_megalist_store.get_readiness(owner, entry_id)
                return self.send_json(payload)
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/saved':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.ensure_index()
                saved = dennis_megalist_store.saved_map(owner)
                verify = dennis_megalist_store.verification_map()
                params = parse_qs(parsed.query)
                params['saved'] = ['saved']
                result = dennis_megalist_catalog.query(index, params, saved, verify)
                known = {entry.get("id") for entry in index.get("entries") or []}
                orphans = [
                    {"id": key, "name": value.get("name") or key,
                     "saved_at": value.get("saved_at") or ""}
                    for key, value in saved.items() if key not in known
                ]
                result["collections"] = dennis_megalist_store.collections(owner)
                result["orphans"] = orphans
                result["orphan_count"] = len(orphans)
                return self.send_json(result)
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/integrations':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.ensure_index()
                verify = dennis_megalist_store.verification_map()
                saved = dennis_megalist_store.saved_map(owner)
                plans = dennis_megalist_store.readiness_map(owner)
                items = []
                for entry_id, plan in sorted(
                        plans.items(), key=lambda item: item[1].get("updated_at") or "",
                        reverse=True):
                    entry = dennis_megalist_catalog.get_entry(index, entry_id)
                    if not entry:
                        continue
                    summary = dennis_megalist_catalog.summary(entry, saved, verify)
                    items.append({
                        "id": entry_id,
                        "name": entry.get("name"),
                        "plan": plan,
                        "entry": summary,
                    })
                return self.send_json({
                    "items": items,
                    "count": len(items),
                    "candidates": dennis_megalist_catalog.query(
                        index, {"integration": ["mcp_server"], "page_size": ["1"]},
                        saved, verify).get("total", 0),
                })
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/history':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.read_index()
                return self.send_json({
                    "records": dennis_megalist_catalog.read_history(),
                    "index": {
                        "parser_version": (index or {}).get("parser_version"),
                        "generated_at": (index or {}).get("generated_at"),
                        "source": (index or {}).get("source") or {},
                        "counts": (index or {}).get("counts") or {},
                        "import": (index or {}).get("import") or {},
                    },
                })
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/settings':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json({
                    "settings": dennis_megalist_store.settings(owner),
                    "defaults": dennis_megalist_store.DEFAULT_SETTINGS,
                    "source": {
                        "repository": dennis_megalist_catalog.REPOSITORY_URL,
                        "branch": dennis_megalist.BRANCH,
                        "editable": False,
                        "note": "The first version supports the configured API Mega List "
                                "source only. Arbitrary repository ingestion is not enabled.",
                    },
                })
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/status':

            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_megalist.status())
            except dennis_megalist.MegalistError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/tree':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_megalist.tree())
            except dennis_megalist.MegalistError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/file':
            owner = self._require_owner(parsed)
            if not owner:
                return
            rel = parse_qs(parsed.query).get('path', ['README.md'])[0]
            try:
                return self.send_json(dennis_megalist.read(rel))
            except dennis_megalist.MegalistError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/asset':
            owner = self._require_owner(parsed)
            if not owner:
                return
            rel = parse_qs(parsed.query).get('path', [''])[0]
            try:
                body, ctype, _normalized = dennis_megalist.asset(rel)
            except dennis_megalist.MegalistError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)
            return

        # ==================================================================
        # HERMES ORCHESTRATOR: state, Kanban board, runs, sub-agents, memory.
        # ==================================================================
        if path == '/api/hermes/state':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(HERMES.snapshot())

        if path == '/api/hermes/tasks':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(HERMES.board())

        if path == '/api/hermes/runs':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json({"runs": HERMES.list_runs()})

        if path == '/api/hermes/agents':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json({"orchestrator": HERMES.identity(),
                                   "subagents": HERMES.subagents()})

        if path == '/api/hermes/memory':
            owner = self._require_owner(parsed)
            if not owner:
                return
            q = parse_qs(parsed.query).get('q', [''])[0]
            if q:
                return self.send_json({"query": q, "results": HERMES.recall(q)})
            try:
                tree = dennis_vault.list_tree()
                notes = _hermes_notes(tree)
            except dennis_vault.VaultError:
                notes = []
            return self.send_json({"notes": notes})

        if path == '/api/hermes/ws':
            owner = dennis_auth.AUTH.owner_for(self._request_token(parsed))
            if not owner:
                return self.send_error(401, "Unauthorized")
            if not dennis_ws.is_websocket_request(self):
                return self.send_error(400, "Expected a WebSocket upgrade")
            task_id = parse_qs(parsed.query).get('task', [None])[0]
            if not task_id:
                return self.send_error(400, "task required")

            def on_open(ws):
                HERMES.subscribe(task_id, ws)

            def on_message(ws, message):
                try:
                    msg = json.loads(message)
                except Exception:
                    return
                if msg.get('type') == 'ping':
                    ws.send_text({"type": "pong"})

            def on_close(ws):
                HERMES.unsubscribe(task_id, ws)

            return dennis_ws.run_websocket(self, on_message, on_open, on_close)

        # Agents with availability + session counts, for the agent dropdown.
        if path == '/api/agents/availability':
            owner = self._require_owner(parsed)
            if not owner:
                return
            counts = dennis_sessions.counts_by_agent(owner)
            agents = dennis_agents.available_agents()
            for a in agents:
                a["session_count"] = counts.get(a["id"], 0)
            return self.send_json({
                "agents": agents,
                "backend": dennis_manager.MANAGER.backend_name,
                "backend_note": dennis_manager.MANAGER.backend_note,
            })

        # DeepSeek Harness (native chat agent): status + saved conversations.
        if path == '/api/deepseek/status':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json(dennis_deepseek.status())

        if path == '/api/deepseek/conversations':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json({
                "conversations": dennis_deepseek.list_conversations(owner),
            })

        if path.startswith('/api/deepseek/conversations/'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            conversation_id = path[len('/api/deepseek/conversations/'):]
            conv = dennis_deepseek.get_conversation(owner, conversation_id)
            if not conv:
                return self.send_json({"error": "Conversation not found"}, 404)
            return self.send_json({"conversation": conv})

        # Authenticated terminal stream (WebSocket, RFC6455).
        if path == '/api/ws/terminal':
            owner = dennis_auth.AUTH.owner_for(self._request_token(parsed))
            if not owner:
                self.send_error(401, "Unauthorized")
                return
            if not dennis_ws.is_websocket_request(self):
                self.send_error(400, "Expected a WebSocket upgrade")
                return
            session_id = parse_qs(parsed.query).get('session_id', [None])[0]
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                self.send_error(404, "Session not found")
                return
            sess = dennis_manager.MANAGER.get_or_create(rec)
            return self._serve_terminal_ws(sess)

        # Project sessions for the sidebar (filtered by agent).
        if path == '/api/project-sessions':
            owner = self._require_owner(parsed)
            if not owner:
                return
            agent_id = parse_qs(parsed.query).get('agent_id', [None])[0]
            sessions = []
            for s in dennis_sessions.list_sessions(owner, agent_id=agent_id):
                item = dict(s)
                item["status"] = dennis_manager.MANAGER.status(s["id"])
                sessions.append(item)
            return self.send_json({"sessions": sessions})

        # Read one text file inside a session's project (Code pane).
        if path.startswith('/api/project-sessions/') and path.endswith('/file'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/file')]
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            project_path = rec.get('project_path')
            if not dennis_projects.validate_path(project_path):
                return self.send_json({"error": "Project folder is no longer available"}, 400)
            rel = parse_qs(parsed.query).get('path', [None])[0]
            if not rel:
                return self.send_json({"error": "path required"}, 400)
            root = os.path.realpath(project_path)
            target = os.path.realpath(os.path.join(root, rel))
            if target != root and not target.startswith(root + os.sep):
                return self.send_json({"error": "Path escapes the project folder"}, 400)
            if not os.path.isfile(target):
                return self.send_json({"error": "File not found"}, 404)
            try:
                size = os.path.getsize(target)
                if size > 512 * 1024:
                    return self.send_json({"error": "File is larger than 512 KB"}, 413)
                with open(target, 'rb') as fh:
                    raw = fh.read()
            except OSError as e:
                return self.send_json({"error": f"Cannot read file: {e}"}, 400)
            if b'\x00' in raw:
                return self.send_json({"error": "Binary file cannot be shown as text"}, 415)
            return self.send_json({
                "path": os.path.relpath(target, root),
                "size": size,
                "content": raw.decode('utf-8', errors='replace'),
            })

        # Raw bytes of a project file (Preview pane: static sites, assets).
        if path.startswith('/api/project-sessions/') and path.endswith('/raw'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/raw')]
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            project_path = rec.get('project_path')
            if not dennis_projects.validate_path(project_path):
                return self.send_json({"error": "Project folder is no longer available"}, 400)
            rel = parse_qs(parsed.query).get('path', [''])[0]
            root = os.path.realpath(project_path)
            target = os.path.realpath(os.path.join(root, rel or ''))
            if target != root and not target.startswith(root + os.sep):
                return self.send_json({"error": "Path escapes the project folder"}, 400)
            if os.path.isdir(target):
                target = os.path.join(target, 'index.html')
            if not os.path.isfile(target):
                return self.send_json({"error": "File not found"}, 404)
            try:
                with open(target, 'rb') as fh:
                    raw = fh.read()
            except OSError as e:
                return self.send_json({"error": f"Cannot read file: {e}"}, 400)
            ctype = mimetypes.guess_type(target)[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(raw)
            return

        # List one folder inside a session's project (Code pane file tree).
        if path.startswith('/api/project-sessions/') and path.endswith('/tree'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/tree')]
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            project_path = rec.get('project_path')
            if not dennis_projects.validate_path(project_path):
                return self.send_json({"error": "Project folder is no longer available"}, 400)
            rel = parse_qs(parsed.query).get('path', [''])[0] or ''
            root = os.path.realpath(project_path)
            target = os.path.realpath(os.path.join(root, rel))
            if target != root and not target.startswith(root + os.sep):
                return self.send_json({"error": "Path escapes the project folder"}, 400)
            if not os.path.isdir(target):
                return self.send_json({"error": "Folder not found"}, 404)
            skip = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'dist',
                    'build', '.idea', '.next', '.cache'}
            entries = []
            try:
                for name in sorted(os.listdir(target), key=str.lower):
                    full = os.path.join(target, name)
                    is_dir = os.path.isdir(full)
                    if is_dir and (name in skip or name.startswith('.')):
                        continue
                    try:
                        stat = os.stat(full)
                    except OSError:
                        continue
                    entries.append({
                        "name": name,
                        "type": "dir" if is_dir else "file",
                        "size": 0 if is_dir else stat.st_size,
                        "modified": int(stat.st_mtime),
                    })
                    if len(entries) >= 500:
                        break
            except OSError as e:
                return self.send_json({"error": f"Cannot list folder: {e}"}, 400)
            return self.send_json({
                "path": os.path.relpath(target, root) if target != root else "",
                "entries": entries,
            })

        # Run status of the open project's dev server (Code & Preview IDE).
        if path.startswith('/api/project-sessions/') and path.endswith('/run'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/run')]
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            return self.send_json(self._run_status_for(rec))

        # Plain-text terminal history for one session (the History drawer).
        # Full-screen TUIs own the alternate screen, so xterm.js has no
        # scrollback for them; this returns the OS-side copy instead.
        if path.startswith('/api/project-sessions/') and path.endswith('/history'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/history')]
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            live = dennis_manager.MANAGER.get(session_id)
            text = live.history() if live else ""
            return self.send_json({
                "success": True,
                "session_id": session_id,
                "agent_id": rec.get("agent_id"),
                "text": text,
                "lines": text.count("\n") + (1 if text else 0),
            })

        if path.startswith('/api/project-sessions/'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):]
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            rec = dict(rec)
            live = dennis_manager.MANAGER.summary(session_id)
            rec["status"] = live["status"] if live else "stopped"
            rec["current_model"] = live["current_model"] if live else rec.get("model_id")
            rec["timings"] = live["timings"] if live else None
            rec["backend"] = dennis_manager.MANAGER.backend_name
            return self.send_json({"session": rec})

        # CLI-native model list for one agent.
        if path.startswith('/api/agents/') and path.endswith('/models'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            agent_id = path[len('/api/agents/'):-len('/models')]
            qs = parse_qs(parsed.query)
            refresh = qs.get('refresh', ['0'])[0] in ('1', 'true', 'yes')
            project_path = qs.get('project', [None])[0]
            project_path = dennis_projects.validate_path(project_path) if project_path else None
            if not project_path:
                available = dennis_projects.list_projects()
                project_path = available[0]["path"] if available else DIRECTORY
            ctx = self._exec_ctx(project_path, agent_id=agent_id)
            try:
                models = dennis_models.list_models(agent_id, ctx, cwd=project_path, refresh=refresh)
            except dennis_models.ModelListError as e:
                free_model = dennis_freellmapi.model_entry(agent_id)
                if free_model:
                    models = [free_model]
                else:
                    return self.send_json({
                        "error": str(e),
                        "agent_id": agent_id,
                        "retry": True,
                        "provider_setup": dennis_agents.provider_setup(agent_id),
                    }, 502)
            free_model = dennis_freellmapi.model_entry(agent_id)
            if free_model:
                existing = next((i for i, m in enumerate(models)
                                 if m.get("id") == free_model["id"]), None)
                if existing is None:
                    models.append(free_model)
                else:
                    models[existing] = free_model
            current = None
            live = None
            session_id = qs.get('session_id', [None])[0]
            if session_id:
                live = dennis_manager.MANAGER.summary(session_id)
                current = live["current_model"] if live else None
            prof = dennis_agents.profile(agent_id) or {}
            custom = dennis_freellmapi.status(agent_id)
            custom["supported"] = agent_id in dennis_freellmapi.SUPPORTED_AGENTS
            return self.send_json({
                "agent_id": agent_id,
                "models": models,
                "providers": dennis_models.providers(models),
                "provider_setup": dennis_agents.provider_setup(agent_id),
                "custom_provider": custom,
                "current_model": current,
                "session_status": live["status"] if live else None,
                "supports_live_model_switch": bool(prof.get("supports_live_model_switch")),
                "backend": dennis_manager.MANAGER.backend_name,
            })

        if path == '/api/freellmapi/catalog':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                catalog = dennis_freellm_catalog.catalog()
            except dennis_freellm_catalog.FreeLLMCatalogError as exc:
                return self.send_json({"error": str(exc)}, 503)
            agents = {
                agent_id: dennis_freellmapi.status(agent_id)
                for agent_id in dennis_freellmapi.SUPPORTED_AGENTS
            }
            return self.send_json({"catalog": catalog, "agents": agents})

        if path == '/api/freellmapi':
            owner = self._require_owner(parsed)
            if not owner:
                return
            agent_id = parse_qs(parsed.query).get('agent_id', [''])[0]
            return self.send_json({"settings": dennis_freellmapi.status(agent_id)})

        # Fallback to static files
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get('Content-Length', 0))
        if path.startswith('/api/studio/') and content_length > STUDIO_MAX_BODY:
            return self.send_json(
                {"error": "Uploads are limited to 25 MB of file data.", "code": "request_too_large"},
                413,
            )
        post_data = self.rfile.read(content_length)

        try:
            body = json.loads(post_data.decode('utf-8')) if post_data else {}
        except Exception:
            body = {}

        if self._reject_cross_site(path):
            return

        # Trading Den proxy: forward DenMa REST writes (e.g. /api/bots/run).
        if path != '/ws' and self._is_denma_request(path):
            return self._proxy_denma_http(body=post_data)

        # Every OS API write is owner-authenticated.
        if not self._require_api_owner(parsed):
            return

        # Official DeepSeek Harness native Web UI supervisor.
        if path == '/api/deepseek-native/start':
            try:
                return self.send_json(dennis_deepseek_native.start())
            except dennis_deepseek_native.HarnessError as exc:
                return self.send_json({"error": str(exc)}, 503)

        # ==================================================================
        # Centralized TTS Router (Kokoro default; ElevenLabs optional)
        # ==================================================================
        if path == '/api/tts/config':
            try:
                return self.send_json(
                    {"success": True, "config": dennis_tts.save_config(body)})
            except dennis_tts.TTSError as exc:
                return self.send_json({"error": str(exc)}, 400)
        if path == '/api/tts/secret':
            name = (body or {}).get("name")
            value = (body or {}).get("value")
            if not name or not value:
                return self.send_json({"error": "name and value are required"}, 400)
            try:
                dennis_tts.set_provider_secret(str(name), str(value))
                return self.send_json({"success": True, "configured": str(name)})
            except dennis_tts.TTSError as exc:
                return self.send_json({"error": str(exc)}, 400)
        if path == '/api/tts/service/start':
            try:
                return self.send_json(dennis_tts.service_control("start"))
            except dennis_tts.TTSError as exc:
                return self.send_json({"error": str(exc)}, 503)
        if path == '/api/tts/service/stop':
            try:
                return self.send_json(dennis_tts.service_control("stop"))
            except dennis_tts.TTSError as exc:
                return self.send_json({"error": str(exc)}, 503)
        if path == '/api/tts/service':
            action = (body or {}).get("action") or "status"
            try:
                return self.send_json(dennis_tts.service_control(action))
            except dennis_tts.TTSError as exc:
                return self.send_json({"error": str(exc)}, 400)
        if path == '/api/tts/speech':
            return self._tts_speech(body)

        # Trading Den / DenMa backend supervisor controls
        if path == '/api/trading-den/restart':
            dennis_denma.BACKEND.restart()
            return self.send_json(dennis_denma.BACKEND.status())
        if path == '/api/trading-den/stop':
            dennis_denma.BACKEND.stop()
            return self.send_json(dennis_denma.BACKEND.status())

        # ==================================================================
        # DEEPSEEK HARNESS (native chat agent): key setup, conversations,
        # and the streaming reply endpoint.
        # ==================================================================
        if path == '/api/freellmapi':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                settings = dennis_freellmapi.set_config(body)
            except dennis_freellmapi.FreeLLMAPIError as exc:
                return self.send_json({"error": str(exc)}, 400)
            dennis_models.clear_cache()
            return self.send_json({"settings": settings})

        if path == '/api/deepseek/credentials':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json({"credentials": dennis_deepseek.set_api_key(body.get("api_key"))})
            except dennis_deepseek.ChatError as exc:
                return self.send_json({"error": str(exc)}, 400)

        if path == '/api/deepseek/conversations':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                conv = dennis_deepseek.create_conversation(
                    owner, model_id=body.get("model_id"), title=body.get("title"))
            except dennis_deepseek.ChatError as exc:
                return self.send_json({"error": str(exc)}, 400)
            return self.send_json({"success": True, "conversation": conv})

        if path.startswith('/api/deepseek/conversations/') and path.endswith('/messages'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            conversation_id = path[len('/api/deepseek/conversations/'):-len('/messages')]
            try:
                exchange = dennis_deepseek.prepare_exchange(
                    owner, conversation_id, body.get("content"),
                    model_id=body.get("model_id"))
            except dennis_deepseek.NotFound as exc:
                return self.send_json({"error": str(exc)}, 404)
            except dennis_deepseek.ChatBusy as exc:
                return self.send_json({"error": str(exc)}, 409)
            except dennis_deepseek.NotConfigured as exc:
                return self.send_json({"error": str(exc), "needs_key": True}, 409)
            except dennis_deepseek.ChatError as exc:
                return self.send_json({"error": str(exc)}, 400)
            return self._serve_deepseek_stream(exchange)

        # ==================================================================
        # STUDIO (writes): credentials, uploads, jobs, tools, MCP config.
        # ==================================================================
        if path.startswith('/api/studio/youtube/'):
            status, payload = dennis_youtube_api.handle('POST', path, parse_qs(parsed.query), body)
            return self.send_json(payload, status)

        if path == '/api/studio/workspaces/active':
            try:
                workspace = dennis_studio_workspaces.set_active(body.get('workspace'))
                return self.send_json({"active": workspace})
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/workspaces/'):
            rest = path[len('/api/studio/workspaces/'):]
            parts = [part for part in rest.split('/') if part]
            try:
                if len(parts) == 2 and parts[1] == 'projects':
                    return self.send_json({
                        "project": dennis_studio_workspaces.create_project(parts[0], body)
                    })
                return self.send_json({"error": "Workspace endpoint not found"}, 404)
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/providers/'):
            rest = path[len('/api/studio/providers/'):]
            provider_id, _, action = rest.partition('/')
            if provider_id != dennis_studio_providers.HIGGSFIELD_ID:
                return self.send_json(
                    {"error": "This provider is configured through its own settings.",
                     "code": "unsupported_provider"},
                    400,
                )
            if action == 'credentials':
                try:
                    status = dennis_studio_secrets.set_values(
                        provider_id,
                        {"api_key": body.get("api_key"), "secret": body.get("secret")},
                    )
                    dennis_studio_providers.REGISTRY.get(provider_id)
                    return self.send_json({"credentials": status})
                except Exception as exc:
                    return self._studio_send_error(exc)
            if action == 'test':
                try:
                    return self.send_json(
                        dennis_studio_providers.REGISTRY.get(provider_id).test()
                    )
                except Exception as exc:
                    return self._studio_send_error(exc)
            return self.send_json({"error": "Unknown Studio provider action"}, 404)

        if path == '/api/studio/uploads':
            provider_id = (body.get("provider") or dennis_studio_providers.HIGGSFIELD_ID)
            raw = body.get("data_base64")
            if not isinstance(raw, str) or not raw:
                return self.send_json(
                    {"error": "data_base64 is required", "code": "missing_file"}, 400
                )
            if len(raw) > 36 * 1024 * 1024:
                return self.send_json(
                    {"error": "Files are limited to 25 MB.", "code": "file_too_large"}, 413
                )
            try:
                data = base64.b64decode(raw, validate=True)
            except Exception:
                return self.send_json(
                    {"error": "The uploaded file is not valid base64.", "code": "invalid_file"}, 400
                )
            content_type = (body.get("content_type") or "").split(";", 1)[0].strip().lower()
            filename = dennis_studio_contracts.sanitize_filename(
                body.get("filename") or "upload"
            )
            if not content_type:
                guessed, _ = mimetypes.guess_type(filename)
                content_type = (guessed or "").lower()
            if content_type not in dennis_studio_media.ALLOWED_CONTENT_TYPES:
                return self.send_json(
                    {"error": "Unsupported file type: %s" % (content_type or "unknown"),
                     "code": "unsupported_media_type"},
                    400,
                )
            try:
                result = dennis_studio_providers.REGISTRY.get(provider_id).upload(
                    data, content_type, filename
                )
                return self.send_json(result)
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/video-kit/analyze':
            ref = body.get("reference")
            avatar = body.get("avatar")
            product = body.get("product")
            prompt = body.get("prompt") or ""
            if not ref:
                return self.send_json(
                    {"error": "reference is required for Video Kit visual analysis", "code": "missing_reference"}, 400
                )
            try:
                import backend.studio_videokit as dennis_videokit
                result = dennis_videokit.analyze_video_kit(
                    reference=ref,
                    avatar=avatar,
                    product=product,
                    prompt=prompt
                )
                return self.send_json(result)
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/video-kit/save-skill':
            name = body.get("name") or "Studio Reference Video"
            prompt = body.get("prompt") or ""
            try:
                import backend.studio_videokit as dennis_videokit
                result = dennis_videokit.save_video_skill(
                    name=name,
                    prompt=prompt,
                    analysis=body.get("analysis") or "",
                    source=body.get("source") or "",
                    platform=body.get("platform") or "",
                    model=body.get("model") or "",
                )
                return self.send_json({"success": True, "skill": result})
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/fashion/model-library':
            return self.send_json(dennis_studio_model_library.list_library())

        if path == '/api/studio/fashion/model-library':
            try:
                model = dennis_studio_model_library.add_model(
                    body.get("url"),
                    body.get("name"),
                    body.get("source") or "upload",
                    body.get("prompt") or "",
                    body.get("kind") or "model",
                )
                return self.send_json({"model": model})
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/fashion/model-library/hide':
            try:
                result = dennis_studio_model_library.set_preset_hidden(
                    body.get("preset_id"), bool(body.get("hidden", True))
                )
                return self.send_json(result)
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/fashion/jobs':
            provider_id = body.get("provider") or dennis_studio_providers.HIGGSFIELD_ID
            try:
                provider = dennis_studio_providers.REGISTRY.get(provider_id)
                plan = dennis_studio_fashion.prepare_job(provider, body)
                job, duplicate = STUDIO_RUNNER.submit(
                    provider_id,
                    plan["model_id"],
                    plan["params"],
                    auto_download=bool(body.get("auto_download", True)),
                )
                return self.send_json({
                    "job": job,
                    "duplicate": duplicate,
                    "plan": {
                        "model_id": plan["model_id"],
                        "image_count": plan["image_count"],
                        "prompt": plan["params"].get("prompt", ""),
                    },
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        # ------------------------------------------------------------------
        # Ads Studio free engines (Pollinations + Google AI). No paid keys.
        # POST-only: uploads, job submission and Google key management. The
        # GET routes (capabilities + job polling) live in do_GET.
        # ------------------------------------------------------------------
        if path == '/api/studio/free/uploads':
            raw = body.get("data_base64")
            if not isinstance(raw, str) or not raw:
                return self.send_json(
                    {"error": "data_base64 is required", "code": "missing_file"}, 400
                )
            if len(raw) > 36 * 1024 * 1024:
                return self.send_json(
                    {"error": "Files are limited to 25 MB.", "code": "file_too_large"}, 413
                )
            try:
                data = base64.b64decode(raw, validate=True)
            except Exception:
                return self.send_json(
                    {"error": "The uploaded file is not valid base64.", "code": "invalid_file"}, 400
                )
            try:
                filename = dennis_studio_contracts.sanitize_filename(
                    body.get("filename") or "upload", fallback="upload"
                )
                record = dennis_studio_free.upload_once(
                    data,
                    body.get("content_type") or "image/png",
                    filename,
                )
            except Exception as exc:
                return self._studio_send_error(exc)
            media_id = record.get("id")
            return self.send_json({
                "media_id": media_id,
                "public_url": "/api/studio/media/" + str(media_id),
                "item": record,
            })

        if path == '/api/studio/free/jobs':
            try:
                job = dennis_studio_free.submit_job(body)
                return self.send_json({"job": job, "duplicate": False})
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/free/credentials':
            try:
                status = dennis_studio_free.save_google_key(body.get("api_key"))
                return self.send_json({"credentials": status, "capabilities": dennis_studio_free.capabilities()})
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/free/test':
            try:
                return self.send_json(dennis_studio_free.test_google_key())
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/free/credentials/delete':
            try:
                status = dennis_studio_free.delete_google_key()
                return self.send_json({"credentials": status, "capabilities": dennis_studio_free.capabilities()})
            except Exception as exc:
                return self._studio_send_error(exc)

        # ------------------------------------------------------------------
        # Provider registry writes: save / test / remove credentials for any
        # provider from the Setup registry.
        # ------------------------------------------------------------------
        if path == '/api/studio/registry/credentials':
            try:
                result = dennis_studio_registry.save_credentials(
                    body.get("provider"), body.get("values"))
                return self.send_json({
                    "result": result,
                    "registry": dennis_studio_registry.registry(),
                    "catalog": dennis_studio_registry.generation_catalog(),
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/registry/credentials/delete':
            try:
                result = dennis_studio_registry.delete_credentials(body.get("provider"))
                return self.send_json({
                    "result": result,
                    "registry": dennis_studio_registry.registry(),
                    "catalog": dennis_studio_registry.generation_catalog(),
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/registry/test':
            try:
                return self.send_json(dennis_studio_registry.test_provider(body.get("provider")))
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/jobs':
            provider_id = body.get("provider") or dennis_studio_providers.HIGGSFIELD_ID
            model_id = body.get("model_id")
            params = body.get("params") or {}
            if not isinstance(model_id, str) or not model_id.strip():
                return self.send_json(
                    {"error": "model_id is required", "code": "missing_model"}, 400
                )
            if not isinstance(params, dict):
                return self.send_json(
                    {"error": "params must be an object", "code": "invalid_params"}, 400
                )
            try:
                job, duplicate = STUDIO_RUNNER.submit(
                    provider_id,
                    model_id.strip(),
                    params,
                    auto_download=bool(body.get("auto_download", True)),
                )
                return self.send_json({"job": job, "duplicate": duplicate})
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/jobs/') and path.endswith('/cancel'):
            job_id = path[len('/api/studio/jobs/'):-len('/cancel')]
            try:
                return self.send_json(STUDIO_RUNNER.cancel(job_id))
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/playlists':
            try:
                return self.send_json({
                    "playlist": dennis_studio_collections.create_playlist(
                        body.get("name")
                    )
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/playlists/') and path.endswith('/items'):
            playlist_id = path[len('/api/studio/playlists/'):-len('/items')]
            try:
                return self.send_json({
                    "playlist": dennis_studio_collections.add_to_playlist(
                        playlist_id, body.get("media_id")
                    )
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/playlists/'):
            playlist_id = path[len('/api/studio/playlists/'):]
            try:
                return self.send_json({
                    "playlist": dennis_studio_collections.rename_playlist(
                        playlist_id, body.get("name")
                    )
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/templates':
            try:
                provider_id = body.get("provider") or dennis_studio_providers.HIGGSFIELD_ID
                model_id = body.get("model_id")
                params = body.get("params") or {}
                if not isinstance(model_id, str) or not model_id.strip():
                    return self.send_json(
                        {"error": "model_id is required", "code": "missing_model"}, 400
                    )
                if not isinstance(params, dict):
                    return self.send_json(
                        {"error": "params must be an object", "code": "invalid_params"}, 400
                    )
                provider = dennis_studio_providers.REGISTRY.get(provider_id)
                model = provider.model(model_id.strip())
                cleaned = dennis_studio_contracts.validate_params(model, params)
                detail = provider.model_detail(model_id.strip())
                return self.send_json({
                    "template": dennis_studio_collections.save_template(
                        body.get("name"),
                        provider_id,
                        model_id.strip(),
                        detail.get("label"),
                        cleaned,
                        detail.get("summary"),
                    )
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/library/import':
            try:
                url = body.get("url")
                if not isinstance(url, str) or not url.strip():
                    return self.send_json(
                        {"error": "url is required", "code": "missing_url"}, 400
                    )
                kind = body.get("kind")
                if kind not in (None, "image", "video", "audio", "file"):
                    return self.send_json(
                        {"error": "Unsupported kind", "code": "invalid_kind"}, 400
                    )
                item = dennis_studio_media.add_from_url(
                    url.strip(),
                    kind=kind,
                    meta={
                        "title": dennis_studio_contracts.sanitize_filename(
                            body.get("title") or "imported", fallback="imported"
                        ),
                        "provider": body.get("provider"),
                        "model_id": body.get("model_id"),
                    },
                )
                return self.send_json({"item": item, "stats": dennis_studio_media.stats()})
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/tools/call':
            try:
                return self.send_json(
                    STUDIO_TOOLS.call(body.get("name"), body.get("arguments") or {})
                )
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/approvals/'):
            approval_id = path[len('/api/studio/approvals/'):]
            try:
                return self.send_json(
                    STUDIO_TOOLS.decide(approval_id, body.get("decision"))
                )
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/mcp/config':
            try:
                config = dennis_studio_mcp.save_config(
                    body.get("command"), body.get("args") or [], body.get("env") or {}
                )
                dennis_studio_providers.REGISTRY.reset_mcp()
                return self.send_json({"config": config})
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/mcp/connect':
            try:
                provider = dennis_studio_providers.REGISTRY.get(
                    dennis_studio_providers.MCP_ID)
                return self.send_json(provider.connect())
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/mcp/disconnect':
            return self.send_json(
                dennis_studio_providers.REGISTRY.get(
                    dennis_studio_providers.MCP_ID).disconnect()
            )

        # Vault (Obsidian-compatible notes): create/update, rename, delete.
        if path == '/api/vault/note':
            owner = self._require_owner(parsed)
            if not owner:
                return
            rel = body.get("path")
            content = body.get("content")
            create = bool(body.get("create"))
            try:
                if create:
                    result = dennis_vault.create_note(rel, content)
                else:
                    result = dennis_vault.write_note(rel, content)
            except dennis_vault.VaultError as e:
                return self.send_json({"error": str(e)}, 400)
            return self.send_json(result)

        if path == '/api/vault/rename':
            owner = self._require_owner(parsed)
            if not owner:
                return
            src = body.get("from")
            dst = body.get("to")
            if not src or not dst:
                return self.send_json({"error": "from and to are required"}, 400)
            try:
                return self.send_json(dennis_vault.rename_note(src, dst))
            except dennis_vault.VaultError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/vault/delete':
            owner = self._require_owner(parsed)
            if not owner:
                return
            rel = body.get("path")
            try:
                return self.send_json(dennis_vault.delete_note(rel))
            except dennis_vault.VaultError as e:
                return self.send_json({"error": str(e)}, 400)

        # ==================================================================
        # AGENT SKILL BUILDER (writes).
        # ==================================================================
        if path == '/api/skill-builder/analyze':
            owner = self._require_owner(parsed)
            if not owner:
                return
            url = (body.get("url") or "").strip()
            if not url:
                return self.send_json({"error": "url is required"}, 400)
            try:
                return self.send_json(dennis_skills.analyze(url, eyes=bool(body.get("eyes"))))
            except dennis_skills.SkillBuilderError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/skill-builder/jobs':
            owner = self._require_owner(parsed)
            if not owner:
                return
            urls = body.get("urls") or ([body.get("url")] if body.get("url") else [])
            urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
            if not urls:
                return self.send_json({"error": "at least one source URL is required"}, 400)
            if len(urls) > 8:
                return self.send_json({"error": "Use at most 8 sources per job."}, 400)
            action = (body.get("action") or "generate").strip().lower()
            mode = (body.get("mode") or "source").strip().lower()
            objective = (body.get("objective") or "tutorial").strip().lower()
            eyes = bool(body.get("eyes", False))
            refresh = bool(body.get("refresh", False))
            agent_id = (body.get("agent_id") or "").strip() or None
            model_id = (body.get("model_id") or "").strip() or None
            character_url = (body.get("character_image_url") or "").strip() or None
            requested_name = (body.get("name") or "").strip() or None
            requested_desc = (body.get("description") or "").strip() or None
            extract_frames = bool(body.get("extract_frames", False))
            replicate = bool(body.get("replicate", False))
            replication_topic = (body.get("replication_topic") or "").strip()
            key = "|".join([action, mode, objective, "1" if eyes else "0",
                            "1" if refresh else "0", "1" if extract_frames else "0",
                            "1" if replicate else "0", replication_topic,
                            agent_id or "", character_url or "",
                            requested_name or ""] + sorted(urls))
            request_body = dict(body)
            request_body["urls"] = urls
            if action == "analyze":
                fn = lambda job: dennis_skills.run_extraction(
                    urls, refresh=refresh, eyes=eyes, job=job)
                kind = "analyze"
            elif action == "generate":
                try:
                    dennis_skills.validate_request(
                        urls, mode=mode, objective=objective,
                        character_image_url=character_url)
                except dennis_skills.SkillBuilderError as e:
                    return self.send_json({"error": str(e)}, 400)
                fn = lambda job: dennis_skills.run_generation(
                    urls, mode=mode, objective=objective, eyes=eyes,
                    refresh=refresh, agent_id=agent_id, model_id=model_id,
                    character_image_url=character_url,
                    requested_name=requested_name,
                    requested_description=requested_desc, job=job,
                    extract_frames=extract_frames, replicate=replicate,
                    replication_topic=replication_topic)
                kind = "generate"
            else:
                return self.send_json({"error": "Unknown action"}, 400)
            job, reused = dennis_skill_jobs.RUNNER.submit(
                kind=kind, key=key, fn=fn, stages=dennis_skills.STAGES,
                request=request_body)
            return self.send_json({"job": job, "deduplicated": reused}, 202)

        if path.startswith('/api/skill-builder/jobs/') and path.endswith('/cancel'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            job_id = path.split('/')[-2]
            try:
                cancelled = dennis_skill_jobs.RUNNER.cancel(job_id)
                return self.send_json({"id": job_id, "cancelled": bool(cancelled)})
            except dennis_skill_jobs.JobError as e:
                return self.send_json({"error": str(e)}, 404)

        if path.startswith('/api/skill-builder/drafts/') and path.endswith('/reject'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            draft_id = path.split('/')[-2]
            try:
                return self.send_json(dennis_skills.reject_draft(draft_id))
            except dennis_skills.SkillBuilderError as e:
                return self.send_json({"error": str(e)}, 404)

        if path.startswith('/api/skill-builder/drafts/') and path.endswith('/publish'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            draft_id = path.split('/')[-2]
            try:
                return self.send_json(dennis_skills.publish_draft(
                    draft_id,
                    fields={
                        "name": body.get("name"),
                        "description": body.get("description"),
                        "content": body.get("content"),
                    },
                    overwrite=bool(body.get("overwrite")),
                ))
            except dennis_skills.SkillConflictError as e:
                return self.send_json({"error": str(e), "conflict": True}, 409)
            except dennis_skills.SkillBuilderError as e:
                return self.send_json({"error": str(e)}, 400)

        if path.startswith('/api/skill-builder/drafts/'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            draft_id = path.rsplit('/', 1)[-1]
            try:
                draft = dennis_skills.update_draft(draft_id, body)
                return self.send_json(draft)
            except dennis_skills.SkillBuilderError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/skill-builder/templates/adopt':
            owner = self._require_owner(parsed)
            if not owner:
                return
            template_id = body.get("template_id") or body.get("id")
            if not template_id:
                return self.send_json({"error": "template_id is required"}, 400)
            try:
                import backend.claude_templates as dennis_claude_templates
                draft = dennis_claude_templates.adopt_template(template_id)
                return self.send_json({"draft": draft})
            except Exception as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/skill-builder/cache':
            owner = self._require_owner(parsed)
            if not owner:
                return
            if body.get("clear"):
                return self.send_json(dennis_skill_sources.clear_cache())
            return self.send_json(dennis_skill_sources.cache_stats())

        if path == '/api/skill-builder/build':
            # Legacy synchronous endpoint: still never publishes without review.
            owner = self._require_owner(parsed)
            if not owner:
                return
            urls = body.get("urls") or ([body.get("url")] if body.get("url") else [])
            urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
            if not urls:
                return self.send_json({"error": "at least one url is required"}, 400)
            try:
                result = dennis_skills.run_generation(
                    urls, mode="source", objective="tutorial",
                    eyes=bool(body.get("eyes", False)),
                    agent_id=(body.get("agent_id") or "").strip() or None,
                    model_id=(body.get("model_id") or "").strip() or None,
                    requested_name=(body.get("name") or "").strip() or None,
                    requested_description=(body.get("description") or "").strip() or None,
                )
                draft = result["draft"]
                return self.send_json({
                    "draft_id": draft["id"], "name": draft["name"],
                    "description": draft["description"], "content": draft["content"],
                    "author": draft.get("author"), "agent_error": draft.get("agent_error"),
                    "path": None, "installed": False, "saved": False,
                    "review_required": True, "conflict": draft.get("conflict"),
                    "timings": result.get("timings"), "warnings": result.get("warnings"),
                })
            except dennis_skills.SkillBuilderError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/skill-builder/setup':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_skills.setup())
            except dennis_skills.SkillBuilderError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/skill-builder/install':
            owner = self._require_owner(parsed)
            if not owner:
                return
            name = (body.get("name") or "").strip()
            if not name:
                return self.send_json({"error": "name is required"}, 400)
            try:
                return self.send_json(dennis_skills.install_skill(name))
            except dennis_skills.SkillBuilderError as e:
                return self.send_json({"error": str(e)}, 400)

        # ==================================================================
        # PLUGINS (writes).
        # ==================================================================
        if path == '/api/plugins/install':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                catalog_id = body.get("catalog_id")
                if catalog_id:
                    return self.send_json(dennis_plugins.install_catalog(
                        str(catalog_id), force=bool(body.get("force"))))
                return self.send_json(dennis_plugins.install_plugin(
                    body.get("source") or body.get("path") or "",
                    name=body.get("name"),
                    force=bool(body.get("force")),
                ))
            except dennis_plugins.PluginError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/plugins/toggle':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_plugins.set_enabled(
                    body.get("name"), bool(body.get("enabled")),
                    selected_agents=body.get("agents"),
                ))
            except dennis_plugins.PluginError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/plugins/install-skills':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_plugins.install_skills(
                    body.get("name"), selected_agents=body.get("agents"),
                    force=bool(body.get("force")),
                ))
            except dennis_plugins.PluginError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/plugins/uninstall':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_plugins.uninstall_plugin(
                    body.get("name"), keep_skills=bool(body.get("keep_skills")),
                ))
            except dennis_plugins.PluginError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/plugins/convert':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_plugins.convert_to_claude(body.get("name")))
            except dennis_plugins.PluginError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/plugins/scaffold':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_plugins.scaffold(
                    body.get("name"), description=body.get("description") or "",
                ))
            except dennis_plugins.PluginError as e:
                return self.send_json({"error": str(e)}, 400)

        # ==================================================================
        # COMPOSIO (writes): keys, catalog refresh, plugin, MCP wiring.
        # ==================================================================
        if path == '/api/composio/keys':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_composio.save_keys(
                    consumer_key=body.get("consumer_key"),
                    api_key=body.get("api_key"),
                ))
            except dennis_composio.ComposioError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/composio/clear-keys':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_composio.clear_keys())
            except dennis_composio.ComposioError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/composio/refresh':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_composio.catalog(force=True))
            except dennis_composio.ComposioError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/composio/install':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_composio.install_plugin(
                    force=bool(body.get("force")),
                    link_agents=body.get("agents"),
                ))
            except (dennis_composio.ComposioError, dennis_plugins.PluginError) as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/composio/wire':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_composio.wire(
                    clients_list=body.get("clients"),
                    remove=bool(body.get("remove")),
                ))
            except dennis_composio.ComposioError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/composio/connect':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_composio.connect(body.get("slug")))
            except dennis_composio.ComposioError as e:
                payload = {"error": str(e), "code": e.code}
                payload.update(getattr(e, "details", None) or {})
                return self.send_json(payload, e.status)

        # ==================================================================
        # API MEGA LIST: owner-scoped saved entries, notes, readiness, checks.
        # ==================================================================
        if path == '/api/megalist/saved':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.ensure_index()
                entry = dennis_megalist_catalog.get_entry(index, body.get("id"))
                if not entry:
                    return self.send_json(
                        {"error": "Catalog entry not found.", "code": "entry_not_found"}, 404)
                saved = bool(body.get("saved", True))
                record = dennis_megalist_store.set_saved(
                    owner, entry, saved, collection=body.get("collection"))
                return self.send_json({
                    "saved": saved,
                    "id": entry["id"],
                    "record": record,
                    "collections": dennis_megalist_store.collections(owner),
                })
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/notes':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.ensure_index()
                entry = dennis_megalist_catalog.get_entry(index, body.get("id"))
                if not entry:
                    return self.send_json(
                        {"error": "Catalog entry not found.", "code": "entry_not_found"}, 404)
                record = dennis_megalist_store.set_notes(owner, entry, body.get("notes"))
                return self.send_json({"id": entry["id"], "saved": True, "record": record})
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/collection':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.ensure_index()
                entry = dennis_megalist_catalog.get_entry(index, body.get("id"))
                if not entry:
                    return self.send_json(
                        {"error": "Catalog entry not found.", "code": "entry_not_found"}, 404)
                record = dennis_megalist_store.set_collection(
                    owner, entry, body.get("collection") or "")
                return self.send_json({
                    "id": entry["id"], "record": record,
                    "collections": dennis_megalist_store.collections(owner),
                })
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/readiness':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                action = str(body.get("action") or "create").lower()
                entry_id = str(body.get("id") or "")
                if action == "delete":
                    return self.send_json(
                        dennis_megalist_store.delete_readiness(owner, entry_id))
                if action == "update":
                    return self.send_json(
                        dennis_megalist_store.update_readiness(owner, entry_id, body))
                index = dennis_megalist_catalog.ensure_index()
                entry = dennis_megalist_catalog.get_entry(index, entry_id)
                if not entry:
                    return self.send_json(
                        {"error": "Catalog entry not found.", "code": "entry_not_found"}, 404)
                plan = dennis_megalist_store.prepare_readiness(owner, entry)
                return self.send_json({"id": entry_id, "plan": plan})
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/verify':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.ensure_index()
                raw_ids = body.get("ids")
                if not isinstance(raw_ids, list):
                    raw_ids = [body.get("id")]
                ids = [str(item) for item in raw_ids if item]
                if not ids:
                    return self.send_json(
                        {"error": "Provide an entry id or a list of ids.", "code": "ids_required"}, 400)
                if len(ids) > 25:
                    return self.send_json(
                        {"error": "At most 25 links can be checked per request.",
                         "code": "too_many_ids"}, 400)
                settings = dennis_megalist_store.settings(owner)
                timeout = int(settings.get("verify_timeout") or 8)
                workers = int(settings.get("verify_concurrency") or 4)
                allowed = settings.get("verify_hosts") or None
                entries = []
                missing = []
                for entry_id in dict.fromkeys(ids):
                    entry = dennis_megalist_catalog.get_entry(index, entry_id)
                    if entry:
                        entries.append(entry)
                    else:
                        missing.append(entry_id)
                results = []

                def check(entry):
                    outcome = dennis_megalist_verify.verify_entry(
                        entry, timeout=timeout, allowed=allowed)
                    record = dennis_megalist_store.record_verification(entry, outcome)
                    return {
                        "id": entry["id"],
                        "name": entry.get("name"),
                        "status": record.get("status"),
                        "http_status": record.get("http_status"),
                        "checked_at": record.get("checked_at"),
                        "error": record.get("error") or "",
                    }

                if entries:
                    with ThreadPoolExecutor(max_workers=max(1, min(6, workers))) as pool:
                        results = list(pool.map(check, entries))
                return self.send_json({
                    "results": results, "missing": missing, "checked": len(results),
                })
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/report':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.ensure_index()
                entry = dennis_megalist_catalog.get_entry(index, body.get("id"))
                if not entry:
                    return self.send_json(
                        {"error": "Catalog entry not found.", "code": "entry_not_found"}, 404)
                record = dennis_megalist_store.report_broken(
                    owner, entry, body.get("reason") or "")
                return self.send_json({"id": entry["id"], "verification": record})
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/settings':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json({
                    "settings": dennis_megalist_store.update_settings(owner, body),
                })
            except dennis_megalist_store.StoreError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        if path == '/api/megalist/index':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                index = dennis_megalist_catalog.rebuild_index(trigger="manual")
                return self.send_json({
                    "rebuilt": True,
                    "counts": index.get("counts") or {},
                    "import": index.get("import") or {},
                })
            except dennis_megalist_catalog.CatalogError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)

        # ==================================================================
        # API MEGA LIST: pull the latest upstream repository snapshot.
        # ==================================================================
        if path == '/api/megalist/sync':

            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(dennis_megalist.sync())
            except dennis_megalist.MegalistError as e:
                return self.send_json({"error": str(e), "code": e.code}, e.status)
            except OSError as e:
                return self.send_json({"error": str(e), "code": "sync_failed"}, 500)

        # ==================================================================
        # HERMES ORCHESTRATOR: plan, delegate, task/board, memory.
        # ==================================================================
        if path == '/api/hermes/plan':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(HERMES.plan(body.get("goal", ""),
                                                  context=body.get("context", "") or "",
                                                  brain=body.get("brain", "vault")))
            except dennis_hermes.HermesError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/hermes/run':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(HERMES.run(body.get("goal", ""),
                                                 context=body.get("context", "") or "",
                                                 brain=body.get("brain", "vault")))
            except dennis_hermes.HermesError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/hermes/delegate':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(HERMES.delegate(task_ids=body.get("task_ids"),
                                                      run_id=body.get("run_id")))
            except dennis_hermes.HermesError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/hermes/memory':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json(HERMES.remember(body.get("title", ""),
                                                      body.get("body", "")))
            except dennis_hermes.HermesError as e:
                return self.send_json({"error": str(e)}, 400)

        if path == '/api/hermes/tasks':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                task = HERMES.create_task(body.get("title", ""),
                                          detail=body.get("detail", ""),
                                          role=body.get("role", "implement"),
                                          agent_id=body.get("agent_id"),
                                          assignment=body.get("assignment"))
                return self.send_json(task)
            except dennis_hermes.HermesError as e:
                return self.send_json({"error": str(e)}, 400)

        if path.startswith('/api/hermes/tasks/') and path.endswith('/assign'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            task_id = path[len('/api/hermes/tasks/'):-len('/assign')]
            try:
                return self.send_json(HERMES.assign_task(task_id, body.get("agent_id")))
            except dennis_hermes.HermesError as e:
                return self.send_json({"error": str(e)}, 404)

        if path.startswith('/api/hermes/tasks/') and path.endswith('/status'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            task_id = path[len('/api/hermes/tasks/'):-len('/status')]
            try:
                return self.send_json(HERMES.set_status(task_id, body.get("status")))
            except dennis_hermes.HermesError as e:
                return self.send_json({"error": str(e)}, 400)

        if path.startswith('/api/hermes/tasks/') and path.endswith('/cancel'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            task_id = path[len('/api/hermes/tasks/'):-len('/cancel')]
            try:
                return self.send_json(HERMES.cancel(task_id))
            except dennis_hermes.HermesError as e:
                return self.send_json({"error": str(e)}, 400)

        # Code & Preview IDE: run/stop the open project's dev server.
        if path.startswith('/api/project-sessions/') and (
                path.endswith('/run/start') or path.endswith('/run/stop')):
            owner = self._require_owner(parsed)
            if not owner:
                return
            suffix = '/run/start' if path.endswith('/run/start') else '/run/stop'
            session_id = path[len('/api/project-sessions/'):-len(suffix)]
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            if suffix == '/run/start':
                return self.send_json(self._run_start_for(rec))
            return self.send_json(self._run_stop_for(rec))

        # Code & Preview IDE: save an edited file back into the project.
        if path.startswith('/api/project-sessions/') and path.endswith('/file'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/file')]
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            project_path = rec.get('project_path')
            if not dennis_projects.validate_path(project_path):
                return self.send_json({"error": "Project folder is no longer available"}, 400)
            rel = body.get("path")
            content = body.get("content")
            if not rel or content is None:
                return self.send_json({"error": "path and content are required"}, 400)
            if not isinstance(content, str):
                return self.send_json({"error": "content must be a string"}, 400)
            if len(content.encode('utf-8')) > 2 * 1024 * 1024:
                return self.send_json({"error": "File is larger than 2 MB"}, 413)
            root = os.path.realpath(project_path)
            target = os.path.realpath(os.path.join(root, rel))
            if target == root or (target != root and not target.startswith(root + os.sep)):
                return self.send_json({"error": "Path escapes the project folder"}, 400)
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                tmp = target + ".dennis-save.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(content)
                os.replace(tmp, target)
            except OSError as e:
                return self.send_json({"error": f"Cannot save file: {e}"}, 400)
            return self.send_json({
                "path": os.path.relpath(target, root),
                "size": len(content.encode('utf-8')),
            })

        # 1. Add / Register a New Agent
        if path == '/api/agents':
            agent_data = body.get("agent")
            if not agent_data or not agent_data.get("name"):
                return self.send_json({"error": "Agent name and details required"}, 400)

            # Auto-verify binary path exists on system to prevent misleading status
            binary_path = agent_data.get("binary_path", "").strip()
            env = dennis_agents.get_augmented_env()
            resolved_path = None
            if binary_path:
                if os.path.exists(binary_path) and os.access(binary_path, os.X_OK):
                    resolved_path = binary_path
                else:
                    resolved_path = shutil.which(binary_path, path=env["PATH"])

            if not resolved_path:
                return self.send_json({
                    "error": f"Cannot register agent: executable '{binary_path}' does not exist on your computer. Please install the agent first.",
                    "code": "BINARY_NOT_FOUND"
                }, 400)

            agent_data["binary_path"] = resolved_path
            agents = load_agents()
            agent_id = agent_data.get("id") or agent_data["name"].lower().replace(" ", "-")
            agent_data["id"] = agent_id
            agent_data["status"] = "Connected"
            agent_data["last_sync"] = "Just now"

            # Check if agent already exists; update or append
            existing_idx = next((i for i, a in enumerate(agents) if a["id"] == agent_id), -1)
            if existing_idx >= 0:
                agents[existing_idx] = agent_data
            else:
                agents.append(agent_data)

            save_agents(agents)

            # If user wanted to set it active, update config
            if body.get("set_active"):
                save_config({"active_agent_id": agent_id})

            return self.send_json({"success": True, "agent": agent_data, "agents": agents})

        # 1b. Integrate host-detected CLIs into the Agent Registry.
        if path == '/api/agents/integrate':
            owner = self._require_owner(parsed)
            if not owner:
                return
            agent_id = (body.get("agent_id") or "").strip()
            integrate_all = bool(body.get("all"))
            if not agent_id and not integrate_all:
                return self.send_json({"error": "agent_id or all is required"}, 400)
            result = integrate_detected_agents(None if integrate_all else [agent_id])
            if agent_id and not integrate_all and result.get("missing"):
                return self.send_json({
                    "success": False,
                    "error": "Agent CLI is not installed on this host",
                    **result,
                }, 404)
            return self.send_json({"success": True, **result})

        # 2. Switch Active Agent in OS
        if path == '/api/agents/active':
            agent_id = body.get("agent_id")
            if not agent_id:
                return self.send_json({"error": "agent_id required"}, 400)

            agents = load_agents()
            matched = next((a for a in agents if a["id"] == agent_id), None)
            if not matched:
                return self.send_json({"error": f"Agent '{agent_id}' not found in registry"}, 404)

            save_config({"active_agent_id": agent_id})
            return self.send_json({
                "success": True,
                "active_agent_id": agent_id,
                "agent": matched
            })

        # 3. Live In-OS Agent Installation Runner
        if path == '/api/agents/install':
            command = body.get("command", "").strip()
            binary_name = body.get("binary_name", "").strip()

            if not command:
                return self.send_json({"success": False, "error": "install command required"}, 400)

            # Only the catalog's own installer commands may run. The command
            # still needs a shell (some installers pipe to bash), but because it
            # must exactly match a known constant, a request can no longer
            # execute an arbitrary shell string.
            allowed = {c.get("install_command"): c
                       for c in self.get_agent_catalog() if c.get("install_command")}
            entry = allowed.get(command)
            if entry is None:
                return self.send_json({
                    "success": False,
                    "error": "Command is not an approved agent installer"
                }, 400)
            if not binary_name:
                binary_name = (entry.get("binary") or "").strip()

            env = dennis_agents.get_augmented_env(active_id)
            install_timeout = int(entry.get("install_timeout") or 120)
            try:
                # Run the real installation command
                res = subprocess.run(command, shell=True, capture_output=True, text=True,
                                     timeout=install_timeout, env=env)
                stdout = res.stdout.strip()
                stderr = res.stderr.strip()
                combined_output = (stdout + "\n" + stderr).strip()

                if res.returncode == 0:
                    # Resolve binary path after install
                    resolved_bin = None
                    if binary_name:
                        resolved_bin = shutil.which(binary_name, path=env["PATH"])
                    
                    version = "Installed"
                    if resolved_bin:
                        try:
                            v_res = subprocess.run([resolved_bin, "--version"], capture_output=True, text=True, timeout=3, env=env)
                            raw_v = (v_res.stdout.strip() or v_res.stderr.strip()).splitlines()
                            if raw_v:
                                version = raw_v[0]
                        except Exception:
                            pass

                    return self.send_json({
                        "success": True,
                        "output": combined_output or "Installation completed successfully.",
                        "binary_path": resolved_bin or binary_name,
                        "version": version
                    })
                else:
                    return self.send_json({
                        "success": False,
                        "error": f"Command exited with code {res.returncode}",
                        "output": combined_output or "Installation failed"
                    }, 400)
            except subprocess.TimeoutExpired:
                return self.send_json({
                    "success": False,
                    "error": f"Installation timed out after {install_timeout} seconds",
                }, 408)
            except Exception as e:
                return self.send_json({"success": False, "error": str(e)}, 500)

        # 4. Pre-flight Test / Health Check for an Agent Binary
        if path == '/api/agents/test':
            binary_path = body.get("binary_path", "").strip()
            args = body.get("args", ["--version"])
            if not binary_path:
                return self.send_json({"success": False, "error": "binary_path required"}, 400)
            if not isinstance(args, list) or not all(
                    isinstance(a, str) and a in SAFE_DIAG_ARGS for a in args):
                return self.send_json(
                    {"success": False, "error": "Unsupported diagnostic arguments"}, 400)

            result = self.run_agent_diagnostic(binary_path, args)
            return self.send_json(result)

        # 5. Session Management
        if path == '/api/sessions':
            store = load_sessions()
            session = create_session(store, body.get("title"))
            save_sessions(store)
            return self.send_json({
                "success": True,
                "session": session_meta(session),
                "active_session_id": session["id"]
            })

        if path.startswith('/api/sessions/') and path.endswith('/activate'):
            session_id = path[len('/api/sessions/'):-len('/activate')]
            store = load_sessions()
            if not get_session(store, session_id):
                return self.send_json({"error": "Session not found"}, 404)
            store["active_session_id"] = session_id
            save_sessions(store)
            return self.send_json({"success": True, "active_session_id": session_id})

        if path.startswith('/api/sessions/') and path.endswith('/rename'):
            session_id = path[len('/api/sessions/'):-len('/rename')]
            store = load_sessions()
            session = get_session(store, session_id)
            if not session:
                return self.send_json({"error": "Session not found"}, 404)
            new_title = (body.get("title") or "").strip()
            if new_title:
                session["title"] = new_title
                session["updated_at"] = _now_iso()
                save_sessions(store)
            return self.send_json({"success": True, "session": session_meta(session)})

        # 6. Universal Chat Execution with Selected Agent & Model
        if path == '/api/chat':
            query = body.get("message", "").strip()
            if not query:
                return self.send_json({"error": "message is required"}, 400)

            req_agent_id = body.get("agent_id")
            req_model_id = body.get("model_id")

            config = load_config()
            active_id = req_agent_id or config.get("active_agent_id", "antigravity")
            agents = load_agents()
            # Resolve registered agents *and* installed-but-unregistered CLIs
            # (get_agent falls back to host detection). Falling back to
            # agents[0] here silently ran Antigravity for every detected CLI.
            active_agent = (dennis_agents.get_agent(active_id)
                            or next((a for a in agents if a["id"] == active_id),
                                    agents[0] if agents else DEFAULT_AGENTS[0]))

            models = load_models()

            def agent_aliases(agent_id):
                aliases = {agent_id}
                if agent_id.endswith("-cli"):
                    aliases.add(agent_id[:-4])
                if agent_id == "codex":
                    aliases.add("codex-cli")
                if agent_id.startswith("custom-"):
                    aliases.add("custom-agent")
                return aliases

            aliases = agent_aliases(active_agent["id"])

            def model_compatible(model):
                recommended = model.get("recommended_for", [])
                return isinstance(recommended, list) and any(a in recommended for a in aliases)

            model = None
            if req_model_id:
                candidate = next((m for m in models if m["id"] == req_model_id), None)
                # Only honor the requested model if it is compatible with the active agent.
                if candidate and model_compatible(candidate):
                    model = candidate
            if not model:
                compatible = [m for m in models if model_compatible(m)]
                if not compatible:
                    compatible = models
                # No registry entry is not an error: leave the model unset so
                # the CLI runs with its own configured default. Substituting a
                # fabricated Gemini id here broke every non-Gemini CLI.
                model = compatible[0] if compatible else None

            import time
            timestamp = time.strftime("%H:%M:%S")

            reply = ""
            steps = []
            env = dennis_agents.get_augmented_env(
                active_agent["id"], (model or {}).get("id"))

            # -------------------------------------------------------
            # Determine which binary to invoke for this agent
            # -------------------------------------------------------
            agent_binary = active_agent.get("binary_path", "")
            if not agent_binary:
                # Try to find by known binary name on PATH
                binary_name = active_agent.get("binary", active_agent["id"].replace("-", ""))
                agent_binary = shutil.which(binary_name, path=env["PATH"]) or ""

            # Antigravity uses the 'agy' CLI
            if active_agent["id"] == "antigravity":
                agent_binary = shutil.which("agy", path=env["PATH"]) or agent_binary

            # -------------------------------------------------------
            # Try to invoke the real agent binary in --print mode
            # -------------------------------------------------------
            agent_invoked = False
            if agent_binary and os.path.isfile(agent_binary) and os.access(agent_binary, os.X_OK):
                try:
                    # Build the correct non-interactive invocation for each agent CLI,
                    # matching how it is used in a terminal.
                    aid = active_agent["id"]
                    # Resolve the display model id to a CLI-specific identifier.
                    # Some agents (e.g. opencode) require a provider/model id and
                    # reject bare names, so honor an optional per-agent mapping.
                    cli_models = (model or {}).get("cli_models")
                    if isinstance(cli_models, dict):
                        model_id = cli_models.get(aid, "")
                    else:
                        model_id = (model or {}).get("id", "")

                    if aid == "antigravity":
                        cmd = [agent_binary, "--print", query]
                        if model_id:
                            cmd += ["--model", model_id]
                    elif aid == "claude-code":
                        cmd = [agent_binary, "-p", query]
                        if model_id:
                            cmd += ["--model", model_id]
                    elif aid == "opencode":
                        cmd = [agent_binary, "run"]
                        if model_id:
                            cmd += ["-m", model_id]
                        cmd.append(query)
                    elif aid in ("codex", "codex-cli"):
                        cmd = [agent_binary, "exec"]
                        if model_id:
                            cmd += ["-m", model_id]
                        cmd.append(query)
                    elif aid == "gemini-cli":
                        cmd = [agent_binary, "-p", query]
                        if model_id:
                            cmd += ["--model", model_id]
                    elif aid == "aider":
                        cmd = [agent_binary, "--yes", "--message", query]
                        if model_id:
                            cmd += ["--model", model_id]
                    elif aid == "goose":
                        cmd = [agent_binary, "run", "-t", query]
                    elif aid == "hermes-cli":
                        # Top-level one-shot: `hermes [-m MODEL] -z PROMPT`.
                        cmd = [agent_binary]
                        if model_id:
                            cmd += ["-m", model_id]
                        cmd += ["-z", query]
                    else:
                        cmd = [agent_binary, query]

                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        cwd=DIRECTORY,
                        env=env
                    )

                    raw_out = result.stdout.strip() or result.stderr.strip()

                    if result.returncode == 0 and raw_out:
                        reply = raw_out
                        steps = [
                            f"Prompt dispatched to {active_agent['name']} CLI ({agent_binary})",
                            f"Model: {(model or {}).get('name', 'CLI default')} ({(model or {}).get('provider', 'CLI default')})",
                            f"Received response ({len(raw_out)} chars)",
                        ]
                        agent_invoked = True
                    else:
                        # Binary ran but returned error or empty output
                        err_detail = result.stderr.strip() or f"Exit code {result.returncode}"
                        reply = (
                            f"⚠️ **{active_agent['name']}** ran but returned an error:\n\n"
                            f"```\n{err_detail[:600]}\n```\n\n"
                            f"Make sure the agent is correctly configured and authenticated."
                        )
                        steps = [
                            f"Invoked {agent_binary}",
                            f"Exit code: {result.returncode}",
                            "Agent returned non-zero or empty output"
                        ]
                        agent_invoked = True  # Still mark as invoked — honest error

                except subprocess.TimeoutExpired:
                    reply = (
                        f"⏱️ **{active_agent['name']}** timed out after 120 seconds.\n\n"
                        f"The agent may be waiting for user input or is performing a long-running task. "
                        f"Try a simpler query or check the agent's interactive session."
                    )
                    steps = ["Agent invoked", "Timeout after 120s"]
                    agent_invoked = True

                except Exception as e:
                    reply = (
                        f"❌ Failed to invoke **{active_agent['name']}**: `{str(e)}`\n\n"
                        f"Binary path: `{agent_binary}`"
                    )
                    steps = [f"Invoke failed: {str(e)}"]
                    agent_invoked = True

            # -------------------------------------------------------
            # Fallback: agent binary not found or not invokable
            # -------------------------------------------------------
            if not agent_invoked:
                binary_display = agent_binary or "not found on PATH"
                reply = (
                    f"⚠️ **{active_agent['name']}** could not be invoked.\n\n"
                    f"Binary path: `{binary_display}`\n\n"
                    f"**To fix this:**\n"
                    f"1. Make sure the agent is installed on your system\n"
                    f"2. Verify the binary path in the Agent Registry settings\n"
                    f"3. For Antigravity, ensure `agy` is installed and on your PATH\n\n"
                    f"You can test the agent using the ⚡ ping button in the Agent Registry panel."
                )
                steps = [
                    f"Binary lookup failed: {binary_display}",
                    "No invokable agent found",
                    "Returning diagnostic guidance"
                ]

            user_msg = {
                "id": f"msg-user-{int(time.time()*1000)}",
                "role": "user",
                "text": query,
                "timestamp": timestamp
            }
            assistant_msg = {
                "id": f"msg-agent-{int(time.time()*1000)+1}",
                "role": "assistant",
                "agent": active_agent["name"],
                "agent_id": active_agent["id"],
                "model": (model or {}).get("name") or "CLI default",
                "model_id": (model or {}).get("id"),
                "text": reply,
                "steps": steps,
                "timestamp": timestamp
            }

            # Persist the exchange into the active chat session (creating one
            # on demand). Sessions are the source of truth for chat history.
            store = load_sessions()
            session = get_session(store, store.get("active_session_id"))
            if not session:
                session = create_session(store)
            session.setdefault("messages", []).append(user_msg)
            session["messages"].append(assistant_msg)
            session["agent_id"] = active_agent["id"]
            session["model_id"] = (model or {}).get("id")
            session["updated_at"] = _now_iso()
            if not session.get("title") or session.get("title") == "New Session":
                session["title"] = _derive_title(session["messages"])
            save_sessions(store)

            return self.send_json({
                "success": True,
                "session_id": session["id"],
                "session_title": session["title"],
                "user_message": user_msg,
                "assistant_message": assistant_msg
            })

        # 7. Interactive Terminal Control (PTY-backed agent session)
        if path == '/api/terminal/start':
            config = load_config()
            agents = load_agents()
            active_id = body.get("agent_id") or config.get("active_agent_id", "antigravity")
            agent = next((a for a in agents if a["id"] == active_id), None)
            if not agent:
                # Detected-but-not-registered CLIs are still launchable.
                agent = dennis_agents.get_agent(active_id)
            if not agent:
                return self.send_json({"success": False, "error": f"Agent '{active_id}' not found"}, 404)

            env = dennis_agents.get_augmented_env(active_id)
            binary = (agent.get("binary_path") or "").strip()
            if not binary or not (os.path.exists(binary) and os.access(binary, os.X_OK)):
                resolved = shutil.which(binary or agent.get("binary", ""), path=env["PATH"])
                if not resolved:
                    return self.send_json({
                        "success": False,
                        "error": f"Executable not found: {binary or agent.get('binary') or agent['id']}"
                    }, 404)
                binary = resolved

            # Resolve the selected model so the TUI launches with it directly.
            model = None
            req_model_id = body.get("model_id")
            if req_model_id:
                model = next((m for m in load_models() if m.get("id") == req_model_id), None)

            env = dennis_agents.get_augmented_env(active_id, (model or {}).get("id"))

            agent = dict(agent)
            agent["binary_path"] = binary
            argv = build_agent_tui_argv(agent, model)

            rows = int(body.get("rows") or 30)
            cols = int(body.get("cols") or 100)
            ok, info = TERMINAL.start(argv, DIRECTORY, env, rows=rows, cols=cols)
            if not ok:
                return self.send_json({"success": False, "error": info}, 500)
            return self.send_json({
                "success": True,
                "pid": info,
                "agent": agent["name"],
                "agent_id": agent["id"],
                "binary": binary,
                "model": (model or {}).get("name", ""),
                "model_id": (model or {}).get("id", ""),
                "argv": argv
            })

        if path == '/api/terminal/input':
            data = body.get("data", "")
            if data:
                TERMINAL.write(data)
            return self.send_json({"success": True})

        if path == '/api/terminal/resize':
            TERMINAL.resize(int(body.get("rows") or 30), int(body.get("cols") or 100))
            return self.send_json({"success": True})

        if path == '/api/terminal/stop':
            TERMINAL.stop()
            return self.send_json({"success": True})

        # Launch an agent in its own macOS Terminal window.
        if path == '/api/agents/launch':
            agent_id = body.get("agent_id")
            agents = load_agents()
            agent = next((a for a in agents if a["id"] == agent_id), None)
            if not agent:
                agent = dennis_agents.get_agent(agent_id)
            if not agent:
                return self.send_json({"error": f"Agent '{agent_id}' not found"}, 404)

            env = dennis_agents.get_augmented_env(agent_id)
            binary_path = agent.get("binary_path", "").strip()
            if not binary_path or not os.path.exists(binary_path):
                resolved = shutil.which(binary_path, path=env["PATH"])
                if not resolved:
                    return self.send_json({"success": False, "error": f"Executable not found: {binary_path}"}, 404)
                binary_path = resolved

            script_path = os.path.join("/tmp", f"dennis-launch-{agent_id}.command")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write("#!/bin/zsh\\n")
                f.write("export PATH=\"" + env["PATH"] + "\"\\n")
                f.write('cd "' + DIRECTORY.replace('"', '\\"') + '"\\n')
                f.write('exec "' + binary_path.replace('"', '\\"') + '"\\n')
            os.chmod(script_path, 0o755)

            try:
                subprocess.Popen(["open", "-a", "Terminal", script_path], env=env)
                return self.send_json({"success": True, "agent_id": agent_id, "script": script_path})
            except Exception as e:
                return self.send_json({"success": False, "error": str(e)}, 500)

        # ==================================================================
        # AGENT SCREEN: project session lifecycle + CLI-native model apply
        # ==================================================================

        # Create a project session for an agent + project.
        if path == '/api/project-sessions':
            owner = self._require_owner(parsed)
            if not owner:
                return
            agent_id = body.get('agent_id')
            agent = dennis_agents.get_agent(agent_id)
            if not agent:
                return self.send_json({"error": "Unknown agent"}, 400)
            if agent.get('transport') == 'chat':
                return self.send_json({
                    "error": "This agent has its own chat dashboard and does not use project sessions.",
                    "code": "chat_agent",
                }, 400)
            project_path = dennis_projects.resolve_project(body.get('project'))
            if not project_path:
                return self.send_json({"error": "Unknown or disallowed project folder"}, 400)
            project_name = next(
                (p['name'] for p in dennis_projects.list_projects() if p['path'] == project_path),
                os.path.basename(project_path),
            )
            # The operator may name the session; when they skip it, the agent
            # supplies the name (agent display name + project).
            title = (body.get('title') or '').strip()
            if not title:
                title = f"{agent.get('name') or agent_id} · {project_name}"
            rec = dennis_sessions.create_session(owner, agent_id, project_name,
                                                 project_path, model_id=body.get('model_id'),
                                                 title=title)
            rec = dict(rec)
            rec['status'] = 'stopped'
            return self.send_json({"success": True, "session": rec})

        if path.startswith('/api/project-sessions/') and path.endswith('/start'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/start')]
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            if not dennis_projects.validate_path(rec.get('project_path')):
                return self.send_json({"error": "Project folder is no longer available"}, 400)
            model_id = body.get('model_id') or rec.get('model_id')
            rows = int(body.get('rows') or 30)
            cols = int(body.get('cols') or 100)
            ok, info = dennis_manager.MANAGER.ensure_started(rec, model_id=model_id,
                                                             rows=rows, cols=cols)
            if not ok:
                return self.send_json({"success": False, "error": info}, 502)
            dennis_sessions.touch(owner, session_id)
            live = dennis_manager.MANAGER.summary(session_id)
            response = {
                "success": True,
                "status": live["status"],
                "current_model": live["current_model"],
                "timings": live["timings"],
                "backend": dennis_manager.MANAGER.backend_name,
            }
            if dennis_manager.MANAGER.backend_note:
                response["message"] = dennis_manager.MANAGER.backend_note
            return self.send_json(response)

        if path.startswith('/api/project-sessions/') and path.endswith('/stop'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/stop')]
            if not dennis_sessions.get_session(owner, session_id):
                return self.send_json({"error": "Session not found"}, 404)
            dennis_manager.MANAGER.stop(session_id)
            return self.send_json({"success": True, "status": "stopped"})

        if path.startswith('/api/project-sessions/') and path.endswith('/cli-session'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/cli-session')]
            rec = dennis_sessions.set_cli_session(owner, session_id, body.get('cli_session_id'))
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            return self.send_json({"success": True, "cli_session_id": rec.get('cli_session_id')})

        if path.startswith('/api/project-sessions/') and path.endswith('/panel'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/panel')]
            rec = dennis_sessions.set_panel(owner, session_id, body.get('panel') or {})
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            return self.send_json({"success": True, "panel": rec.get('panel')})

        if path.startswith('/api/project-sessions/') and path.endswith('/reset-panel'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/reset-panel')]
            rec = dennis_sessions.reset_panel(owner, session_id)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            return self.send_json({"success": True, "panel": rec.get('panel')})

        if path == '/api/project-sessions/active/agent':
            owner = self._require_owner(parsed)
            if not owner:
                return
            store = load_sessions()
            session_id = store.get('active_session_id')
            if not session_id:
                return self.send_json({'error': 'No active session found'}, 404)
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({'error': 'Active session not found in store'}, 404)
            agent_id = body.get('agent_id')
            if not agent_id:
                return self.send_json({'error': 'agent_id required'}, 400)
            
            # Update the session metadata
            rec['agent_id'] = agent_id
            dennis_sessions.set_model(owner, session_id, rec.get('model_id')) # Trigger a save via set_model or similar
            
            # If the session is running, restart it with the new agent
            sess = dennis_manager.MANAGER.get(session_id)
            if sess and sess.is_alive():
                # We need the latest session record for the restart
                rec = dennis_sessions.get_session(owner, session_id)
                ok, info = dennis_manager.MANAGER.restart(sess, rec, rec.get('model_id'))
                if not ok:
                    return self.send_json({'success': False, 'error': info}, 502)

            return self.send_json({'success': True, 'agent_id': agent_id})

        if path == '/api/project-sessions/active/model':
            owner = self._require_owner(parsed)
            if not owner:
                return
            store = load_sessions()
            session_id = store.get('active_session_id')
            if not session_id:
                return self.send_json({'error': 'No active session found'}, 404)
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({'error': 'Active session not found in store'}, 404)
            model_id = body.get('model_id')
            if not model_id:
                return self.send_json({'error': 'model_id required'}, 400)
            dennis_sessions.set_model(owner, session_id, model_id)
            
            # IMPORTANT: If the session is currently running, we MUST restart it
            # for the model change to actually take effect in the CLI process.
            sess = dennis_manager.MANAGER.get(session_id)
            if sess and sess.is_alive():
                ok, info = dennis_manager.MANAGER.restart(sess, rec, model_id)
                if not ok:
                    return self.send_json({'success': False, 'error': info}, 502)

            return self.send_json({'success': True, 'model_id': model_id})

        if path.startswith('/api/project-sessions/') and path.endswith('/model'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):-len('/model')]
            rec = dennis_sessions.get_session(owner, session_id)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            model_id = body.get('model_id')
            if not model_id:
                return self.send_json({"error": "model_id required"}, 400)

            project_path = rec.get('project_path')
            if not dennis_projects.validate_path(project_path):
                return self.send_json({"error": "Project folder is no longer available"}, 400)

            # Validate against a freshly loaded CLI list; reject anything else.
            ctx = self._exec_ctx(project_path, agent_id=rec.get('agent_id'))
            try:
                models = dennis_models.list_models(rec['agent_id'], ctx,
                                                   cwd=project_path, refresh=True)
            except dennis_models.ModelListError as e:
                free_model = dennis_freellmapi.model_entry(rec.get('agent_id'))
                if free_model:
                    models = [free_model]
                else:
                    return self.send_json({"error": str(e), "retry": True}, 502)
            free_model = dennis_freellmapi.model_entry(rec.get('agent_id'))
            if free_model:
                existing = next((i for i, m in enumerate(models)
                                 if m.get("id") == free_model["id"]), None)
                if existing is None:
                    models.append(free_model)
                else:
                    models[existing] = free_model
            match = next((m for m in models if m['id'] == model_id), None)
            if not match:
                return self.send_json({
                    "error": f"'{model_id}' is not offered by this agent's CLI",
                }, 400)
            if not match.get('available', True):
                return self.send_json({
                    "error": match.get('reason') or "Model unavailable in this CLI",
                }, 400)

            sess = dennis_manager.MANAGER.get(session_id)
            running = bool(sess and sess.is_alive())
            if running and sess.compute_status() == 'busy' and not body.get('confirm'):
                return self.send_json({
                    "success": False,
                    "warning": True,
                    "message": "Agent is mid-task. Switching model restarts the session.",
                }, 409)

            if running:
                ok, info = dennis_manager.MANAGER.restart(sess, rec, model_id)
                if not ok:
                    return self.send_json({"success": False, "error": info}, 502)

            # Persist only after the CLI was actually (re)launched with it, and
            # read the applied value back from the live session (never the ask).
            dennis_sessions.set_model(owner, session_id, model_id)
            live = dennis_manager.MANAGER.summary(session_id)
            applied = live["current_model"] if live and live["current_model"] else model_id
            if live and live["current_model"] and live["current_model"] != model_id and running:
                return self.send_json({
                    "success": False,
                    "error": "The CLI did not accept that model. Keeping the previous one.",
                }, 502)
            return self.send_json({
                "success": True,
                "model_id": applied,
                "current_model": applied,
                "status": live["status"] if live else "stopped",
            })

        self.send_error(404, "Endpoint not found")

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get('Content-Length', 0))
        if path.startswith('/api/studio/') and content_length > STUDIO_MAX_BODY:
            return self.send_json(
                {"error": "Request body is too large.", "code": "request_too_large"}, 413
            )
        patch_data = self.rfile.read(content_length)
        try:
            body = json.loads(patch_data.decode('utf-8')) if patch_data else {}
        except Exception:
            body = {}

        if self._reject_cross_site(path):
            return
        if not self._require_api_owner(parsed):
            return

        # Rename a project session (its display project name). The folder on
        # disk is never touched.
        if path.startswith('/api/project-sessions/'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):]
            if not session_id or '/' in session_id:
                self.send_error(404, "Endpoint not found")
                return
            title = (body.get('title') or '').strip()
            if not title:
                return self.send_json({"error": "Project name is required."}, 400)
            if len(title) > 120:
                return self.send_json({"error": "Project name is too long (120 characters max)."}, 400)
            rec = dennis_sessions.set_title(owner, session_id, title)
            if not rec:
                return self.send_json({"error": "Session not found"}, 404)
            return self.send_json({"success": True, "session": rec})

        if path.startswith('/api/studio/workspaces/'):
            rest = path[len('/api/studio/workspaces/'):]
            parts = [part for part in rest.split('/') if part]
            try:
                if len(parts) == 2 and parts[1] == 'settings':
                    return self.send_json({
                        "settings": dennis_studio_workspaces.update_settings(parts[0], body)
                    })
                if len(parts) == 3 and parts[1] == 'projects':
                    return self.send_json({
                        "project": dennis_studio_workspaces.update_project(parts[0], parts[2], body)
                    })
            except Exception as exc:
                return self._studio_send_error(exc)

        # YouTube Studio editorial APIs.
        if path.startswith('/api/studio/youtube/'):
            status, payload = dennis_youtube_api.handle('PATCH', path, parse_qs(parsed.query), body)
            return self.send_json(payload, status)

        self.send_error(404, "Endpoint not found")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query_params = parse_qs(parsed.query)

        if self._reject_cross_site(path):
            return
        if not self._require_api_owner(parsed):
            return

        if path == '/api/freellmapi':
            owner = self._require_owner(parsed)
            if not owner:
                return
            agent_id = query_params.get('agent_id', [''])[0]
            settings = dennis_freellmapi.clear(agent_id)
            dennis_models.clear_cache()
            return self.send_json({"settings": settings})

        # DeepSeek Harness: remove a saved key or one conversation.
        if path == '/api/deepseek/credentials':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                return self.send_json({"credentials": dennis_deepseek.clear_api_key()})
            except dennis_deepseek.ChatError as exc:
                return self.send_json({"error": str(exc)}, 400)

        if path.startswith('/api/deepseek/conversations/'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            conversation_id = path[len('/api/deepseek/conversations/'):]
            if not dennis_deepseek.delete_conversation(owner, conversation_id):
                return self.send_json({"error": "Conversation not found"}, 404)
            return self.send_json({"success": True})

        # Studio: finished job history, library items, provider credentials,
        # and the optional MCP configuration.
        if path.startswith('/api/studio/workspaces/'):
            rest = path[len('/api/studio/workspaces/'):]
            parts = [part for part in rest.split('/') if part]
            try:
                if len(parts) == 3 and parts[1] == 'projects':
                    return self.send_json(
                        dennis_studio_workspaces.delete_project(parts[0], parts[2])
                    )
            except Exception as exc:
                return self._studio_send_error(exc)
            return self.send_json({"error": "Workspace endpoint not found"}, 404)

        # YouTube Studio editorial APIs.
        if path.startswith('/api/studio/youtube/'):
            status, payload = dennis_youtube_api.handle('DELETE', path, parse_qs(parsed.query), None)
            return self.send_json(payload, status)

        if path.startswith('/api/studio/fashion/model-library/'):
            model_id = path[len('/api/studio/fashion/model-library/'):]
            try:
                return self.send_json(dennis_studio_model_library.delete_model(model_id))
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/jobs/'):
            try:
                return self.send_json(
                    STUDIO_RUNNER.delete(path[len('/api/studio/jobs/'):])
                )
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/library/'):
            media_id = path[len('/api/studio/library/'):]
            try:
                result = dennis_studio_media.delete(media_id)
                dennis_studio_collections.forget_media(media_id)
                return self.send_json(result)
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/playlists/') and '/items/' in path:
            rest = path[len('/api/studio/playlists/'):]
            playlist_id, media_id = rest.split('/items/', 1)
            try:
                return self.send_json({
                    "playlist": dennis_studio_collections.remove_from_playlist(
                        playlist_id, media_id
                    )
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/playlists/'):
            try:
                return self.send_json(
                    dennis_studio_collections.delete_playlist(
                        path[len('/api/studio/playlists/'):]
                    )
                )
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/templates/'):
            try:
                return self.send_json(
                    dennis_studio_collections.delete_template(
                        path[len('/api/studio/templates/'):]
                    )
                )
            except Exception as exc:
                return self._studio_send_error(exc)

        if path.startswith('/api/studio/providers/') and path.endswith('/credentials'):
            provider_id = path[len('/api/studio/providers/'):-len('/credentials')]
            try:
                return self.send_json({
                    "credentials": dennis_studio_secrets.delete(provider_id)
                })
            except Exception as exc:
                return self._studio_send_error(exc)

        if path == '/api/studio/mcp/config':
            try:
                config = dennis_studio_mcp.clear_config()
                dennis_studio_providers.REGISTRY.reset_mcp()
                return self.send_json({"config": config})
            except Exception as exc:
                return self._studio_send_error(exc)

        # Remove a Hermes Kanban task.
        if path.startswith('/api/hermes/tasks/'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            task_id = path[len('/api/hermes/tasks/'):]
            try:
                return self.send_json(HERMES.delete_task(task_id))
            except dennis_hermes.HermesError as e:
                return self.send_json({"error": str(e)}, 404)

        # Delete a project session (stops its live process first).
        if path.startswith('/api/project-sessions/'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            session_id = path[len('/api/project-sessions/'):]
            dennis_manager.MANAGER.stop(session_id)
            if not dennis_sessions.delete_session(owner, session_id):
                return self.send_json({"error": "Session not found"}, 404)
            return self.send_json({"success": True})

        # Clear Chat History (active session + legacy store)
        if path == '/api/chat/history':
            store = load_sessions()
            session = get_session(store, store.get("active_session_id"))
            if session:
                session["messages"] = []
                session["updated_at"] = _now_iso()
                save_sessions(store)
            save_conversation([])
            return self.send_json({"success": True, "history": []})

        # Delete a chat session
        if path.startswith('/api/sessions/'):
            session_id = path[len('/api/sessions/'):]
            store = load_sessions()
            if not get_session(store, session_id):
                return self.send_json({"error": "Session not found"}, 404)
            store["sessions"] = [s for s in store["sessions"] if s["id"] != session_id]
            if store.get("active_session_id") == session_id:
                store["active_session_id"] = store["sessions"][0]["id"] if store["sessions"] else None
            save_sessions(store)
            return self.send_json({
                "success": True,
                "active_session_id": store.get("active_session_id"),
                "sessions": [session_meta(s) for s in store["sessions"]]
            })

        if path == '/api/agents':
            agent_id = query_params.get("id", [None])[0]
            if not agent_id:
                return self.send_json({"error": "id parameter required"}, 400)

            if agent_id == "antigravity":
                return self.send_json({"error": "Cannot remove primary system agent 'Antigravity'"}, 403)

            agents = load_agents()
            updated_agents = [a for a in agents if a["id"] != agent_id]
            save_agents(updated_agents)

            # If deleted agent was active, revert to antigravity
            config = load_config()
            if config.get("active_agent_id") == agent_id:
                save_config({"active_agent_id": "antigravity"})

            return self.send_json({"success": True, "agents": updated_agents, "active_agent_id": "antigravity"})

    def detect_host_agents(self):
        """Scan the host for installed agent CLIs (shared probe table)."""
        registered_agents = load_agents()
        registered_ids = {a.get("id") for a in registered_agents}
        registered_bins = {os.path.basename(a.get("binary_path", "")).lower()
                           for a in registered_agents}

        binaries = {p.get("id"): p.get("binary") for p in dennis_agents.host_agent_probes()}
        detected = []
        for agent in dennis_agents.detect_installed(with_version=True):
            agent_id = agent.get("id")
            binary_path = agent.get("binary_path") or ""
            detected.append({
                "id": agent_id,
                "name": agent.get("name"),
                "binary": binaries.get(agent_id) or os.path.basename(binary_path),
                "binary_path": binary_path,
                "version": agent.get("version") or "Installed",
                "provider": agent.get("provider"),
                "category": agent.get("type"),
                "icon_char": agent.get("icon_char"),
                "color_theme": agent.get("color_theme"),
                "is_already_added": agent_id in registered_ids or
                                    os.path.basename(binary_path).lower() in registered_bins,
            })
        return detected

    def run_agent_diagnostic(self, binary_path, args):
        """Runs a test execution against an agent binary to verify functionality"""
        env = get_augmented_env()
        resolved = binary_path
        if not os.path.exists(binary_path):
            resolved = shutil.which(binary_path, path=env["PATH"])

        if not resolved or not os.path.exists(resolved):
            return {
                "success": False,
                "exit_code": -1,
                "error": f"Executable not found on system at '{binary_path}'.",
                "path": binary_path
            }

        try:
            res = subprocess.run([resolved] + args, capture_output=True, text=True, timeout=5, env=env)
            output = res.stdout.strip() or res.stderr.strip()
            return {
                "success": res.returncode == 0,
                "exit_code": res.returncode,
                "output": output or "Executed successfully (Exit code: 0)",
                "path": resolved
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "exit_code": -1, "error": "Diagnostic timed out after 5 seconds", "path": resolved}
        except Exception as e:
            return {"success": False, "exit_code": -1, "error": str(e), "path": resolved}

    def get_agent_catalog(self):
        """Catalog of discoverable agents with real-time host installation checks"""
        env = get_augmented_env()
        registered_agents = load_agents()
        registered_ids = {a["id"] for a in registered_agents}
        registered_bins = {os.path.basename(a.get("binary_path", "")).lower() for a in registered_agents}

        catalog = [
            {
                "id": "claude-code",
                "name": "Claude Code",
                "binary": "claude",
                "provider": "Anthropic",
                "description": "Agentic coding tool that lives in your terminal and understands your entire codebase.",
                "type": "Coding CLI",
                "icon_char": "C",
                "color_theme": "amber",
                "package_manager": "npm",
                "install_command": "npm install -g @anthropic-ai/claude-code",
                "docs_url": "https://docs.anthropic.com/en/docs/agents-and-tools/claude-code"
            },
            {
                "id": "opencode",
                "name": "OpenCode",
                "binary": "opencode",
                "provider": "OpenCode AI",
                "description": "Open source AI terminal coding assistant with multi-model provider support.",
                "type": "Coding CLI",
                "icon_char": "O",
                "color_theme": "blue",
                "package_manager": "curl",
                "install_command": "curl -fsSL https://opencode.ai/install.sh | bash",
                "docs_url": "https://opencode.ai"
            },
            {
                "id": "codex",
                "name": "Codex CLI",
                "binary": "codex",
                "provider": "OpenAI",
                "description": "Terminal pair programming agent powered by OpenAI reasoning & code models.",
                "type": "CLI Agent",
                "icon_char": "X",
                "color_theme": "emerald",
                "package_manager": "npm",
                "install_command": "npm install -g @openai/codex",
                "docs_url": "https://openai.com"
            },
            {
                "id": "hermes-cli",
                "name": "Hermes Agent",
                "binary": "hermes",
                "provider": "Nous Research",
                "description": "Autonomous agent with tool calling, skills, memory, gateway and multi-agent orchestration.",
                "type": "Autonomous Agent & Orchestrator",
                "icon_char": "H",
                "color_theme": "amber",
                "package_manager": "curl",
                "install_command": "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash",
                # The official installer provisions Python/Node/tools and can
                # take several minutes, unlike the npm one-liners above.
                "install_timeout": 600,
                "docs_url": "https://hermes-agent.nousresearch.com/docs/getting-started/installation"
            },
            {
                "id": "gemini-cli",
                "name": "Gemini CLI",
                "binary": "gemini",
                "provider": "Google",
                "description": "Command line interface for Gemini 2.5 Flash & Pro models with multimodal code reasoning.",
                "type": "CLI Agent",
                "icon_char": "G",
                "color_theme": "indigo",
                "package_manager": "npm",
                "install_command": "npm install -g @google/gemini-cli",
                "docs_url": "https://ai.google.dev"
            },
            {
                "id": "aider",
                "name": "Aider",
                "binary": "aider",
                "provider": "Paul Gauthier",
                "description": "AI pair programming in your terminal, automatically editing files and git committing.",
                "type": "Pair Programmer",
                "icon_char": "Ai",
                "color_theme": "purple",
                "package_manager": "pip",
                "install_command": "pip install aider-chat",
                "docs_url": "https://aider.chat"
            },
            {
                "id": "goose",
                "name": "Goose",
                "binary": "goose",
                "provider": "Block",
                "description": "Open-source, extensible on-machine AI agent with pluggable toolkits and MCP support.",
                "type": "Autonomous Agent",
                "icon_char": "Gs",
                "color_theme": "cyan",
                "package_manager": "curl",
                "install_command": "curl -fsSL https://github.com/block/goose/releases/latest/download/goose-mac.sh | bash",
                "docs_url": "https://block.github.io/goose"
            },
            {
                "id": "devin-cli",
                "name": "Devin CLI",
                "binary": "devin",
                "provider": "Cognition AI",
                "description": "Autonomous software engineer agent integration for long-running workflows.",
                "type": "Autonomous Engineer",
                "icon_char": "D",
                "color_theme": "blue",
                "package_manager": "curl",
                "install_command": "curl -fsSL https://preview.devin.ai/install.sh | bash",
                "docs_url": "https://devin.ai"
            },
            {
                "id": "custom-agent",
                "name": "Custom Agent / MCP Server",
                "binary": "",
                "provider": "Custom / Self-Hosted",
                "description": "Connect any custom binary, local script, or Model Context Protocol (MCP) server.",
                "type": "Custom Protocol",
                "icon_char": "+",
                "color_theme": "cyan",
                "package_manager": "custom",
                "install_command": "",
                "docs_url": "https://modelcontextprotocol.io"
            }
        ]

        for item in catalog:
            binary = item.get("binary")
            if binary:
                path = shutil.which(binary, path=env["PATH"])
                item["is_installed"] = bool(path)
                item["installed_path"] = path or ""
                item["is_already_added"] = item["id"] in registered_ids or binary.lower() in registered_bins
            else:
                item["is_installed"] = False
                item["installed_path"] = ""
                item["is_already_added"] = False

        return catalog

class DennisHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        # SSE clients disconnect abruptly all the time; don't spam tracebacks.
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def _ensure_denma_runtime():
    """Re-launch under an interpreter that can host the DenMa engine in-process.

    The OS server is stdlib-only and runs under any Python 3, but the embedded
    Trading Den engine needs ccxt/aiohttp/aiodns. When those are missing from the
    current interpreter we exec the same script with a suitable one so that the
    whole system stays a single process / single server.
    """
    if os.environ.get("DENNIS_NO_DENMA", "").lower() in ("1", "true", "yes"):
        return
    if os.environ.get("DENNIS_DENMA_REEXEC") == "1":
        return
    if sys.gettrace() is not None:
        # A debugger is attached; re-exec would detach it. Fall back to the
        # supervised child process instead of replacing the process image.
        return
    if dennis_denma.current_has_deps():
        return
    python = dennis_denma.find_python()
    if not python or os.path.abspath(python) == os.path.abspath(sys.executable):
        return
    os.environ["DENNIS_DENMA_REEXEC"] = "1"
    print(f"[dennis] Re-launching under {python} so the embedded Trading Den "
          f"runs in-process (single server).")
    sys.stdout.flush()
    os.execv(python, [python, os.path.abspath(__file__), *sys.argv[1:]])


def run(port=PORT):
    atexit.register(dennis_deepseek_native.stop)
    dennis_denma.BACKEND.start()
    atexit.register(dennis_denma.BACKEND.stop)
    STUDIO_RUNNER.recover()
    atexit.register(STUDIO_RUNNER.shutdown)
    bind_host = "127.0.0.1" if os.environ.get("DENNIS_DESKTOP") == "1" else ""
    with DennisHTTPServer((bind_host, port), DennisOSHandler) as httpd:
        print(f"==========================================================")
        print(f"  DENNIS Agentic OS Server with In-OS Installer running at:")
        print(f"  http://localhost:{port}/")
        print(f"  Trading Den (DenMa) runs in-process — one server, one port "
              f"(set DENNIS_NO_DENMA=1 to disable)")
        print(f"==========================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")
            try:
                dennis_manager.MANAGER.stop_all()
            except Exception:
                pass
            dennis_denma.BACKEND.stop()
            httpd.server_close()

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    _ensure_denma_runtime()
    run(port)
