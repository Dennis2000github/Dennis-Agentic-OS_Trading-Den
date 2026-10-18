"""
DENNIS Agentic OS - Local Development Server
Provides static file serving and REST API endpoints for agent orchestration,
model registry, agent discovery, live in-OS installation, and framework routing.
"""

import http.server
import http.client
import mimetypes
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
from urllib.parse import urlparse, parse_qs, quote

# ---------------------------------------------------------------------------
# Agent-screen backend (project sessions, CLI-native models, sandboxed PTY/Docker)
# ---------------------------------------------------------------------------
from backend import auth as dennis_auth
from backend import projects as dennis_projects
from backend import session_store as dennis_sessions
from backend import agent_registry as dennis_agents
from backend import model_sources as dennis_models
from backend import session_manager as dennis_manager
from backend import ws as dennis_ws
from backend import preflight as dennis_preflight
from backend import denma as dennis_denma
from backend import runner as dennis_runner
from backend import mcp as dennis_mcp
from backend.sandbox import ExecContext

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

os.makedirs(DATA_DIR, exist_ok=True)

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
        with open(CONVERSATION_FILE, "w", encoding="utf-8") as f:
            json.dump(messages, f, indent=2)
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
        with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
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

    if cli_model:
        if aid == "opencode":
            argv += ["-m", cli_model]
        elif aid == "claude-code":
            argv += ["--model", cli_model]
        elif aid in ("codex", "codex-cli"):
            argv += ["-m", cli_model]
        elif aid == "gemini-cli":
            argv += ["--model", cli_model]
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
        with open(AGENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(agents, f, indent=2)
    except Exception as e:
        print(f"Error saving agents: {e}")

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
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
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
        self.queue = queue.Queue()
        self.lock = threading.Lock()
        self.running = False
        self._exit_code = 0

    def start(self, argv, cwd, env, rows=30, cols=100):
        with self.lock:
            self._stop_locked()
            # Release any clients still attached to the previous stream.
            self.queue.put(("exit", {"code": 0}))
            self.queue = queue.Queue()

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
                self.queue.put(("data", data.decode("utf-8", "replace")))
        # Ensure a client blocked on the stream wakes up if the PTY closes.
        self.queue.put(("exit", {"code": self._exit_code}))

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
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.end_headers()

    def send_json(self, data, status_code=200):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode('utf-8'))

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

    def _exec_ctx(self, project_path):
        return ExecContext(dennis_manager.MANAGER.runner,
                           dennis_agents.get_augmented_env(), project_path)

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

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Trading Den (DenMa) proxy: WS + its REST endpoints, same-origin.
        if path == '/ws':
            return self._proxy_denma_ws()
        if self._is_denma_request(path):
            return self._proxy_denma_http()

        # Trading Den / DenMa backend supervisor status
        if path == '/api/trading-den':
            return self.send_json(dennis_denma.BACKEND.status())

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
                "active_agent_id": config.get("active_agent_id", "antigravity")
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

        # ==================================================================
        # MCP CONNECTORS: discovery, policy gate and OAuth 2.1 workflow.
        # ==================================================================
        if path == '/api/mcp/servers':
            owner = self._require_owner(parsed)
            if not owner:
                return
            return self.send_json({
                "servers": dennis_mcp.list_public(),
                "presets": dennis_mcp.POLICY_PRESETS,
                "default_policy": dennis_mcp.default_policy(),
                "registry": dennis_mcp.REGISTRY_BASE,
            })

        if path == '/api/mcp/discover':
            owner = self._require_owner(parsed)
            if not owner:
                return
            qs = parse_qs(parsed.query)
            query = qs.get('q', [''])[0]
            limit = qs.get('limit', ['20'])[0]
            try:
                return self.send_json(dennis_mcp.discover(query, limit))
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 502)

        # OAuth 2.1 loopback redirect target. The provider redirects the
        # browser here; we hand the code back to the waiting modal.
        if path == '/api/mcp/oauth/callback':
            qs = parse_qs(parsed.query)
            payload = {
                "code": qs.get('code', [''])[0],
                "state": qs.get('state', [''])[0],
                "error": qs.get('error', [''])[0],
            }
            html = (
                "<!doctype html><meta charset=utf-8>"
                "<title>DENNIS Agentic OS - MCP Authorization</title>"
                "<body style='font-family:system-ui;background:#060913;color:#e2e8f0;"
                "display:flex;align-items:center;justify-content:center;height:100vh'>"
                "<div style='text-align:center'><h2 style='color:#00f0ff'>"
                "MCP authorization received</h2><p>You can close this tab and return "
                "to DENNIS Agentic OS.</p><p style='color:#64748b;font-size:12px'>"
                f"code={payload['code'][:8]}… state={payload['state'][:8]}…</p></div>"
                "<script>try{window.opener&&window.opener.postMessage("
                + json.dumps({"type": "dennis-mcp-oauth", **payload})
                + ",'*');}catch(e){}</script></body>")
            body = html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

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
            ctx = self._exec_ctx(project_path)
            try:
                models = dennis_models.list_models(agent_id, ctx, cwd=project_path, refresh=refresh)
            except dennis_models.ModelListError as e:
                return self.send_json({"error": str(e), "agent_id": agent_id, "retry": True}, 502)
            current = None
            live = None
            session_id = qs.get('session_id', [None])[0]
            if session_id:
                live = dennis_manager.MANAGER.summary(session_id)
                current = live["current_model"] if live else None
            prof = dennis_agents.profile(agent_id) or {}
            return self.send_json({
                "agent_id": agent_id,
                "models": models,
                "current_model": current,
                "session_status": live["status"] if live else None,
                "supports_live_model_switch": bool(prof.get("supports_live_model_switch")),
                "backend": dennis_manager.MANAGER.backend_name,
            })

        # Fallback to static files
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)

        try:
            body = json.loads(post_data.decode('utf-8')) if post_data else {}
        except Exception:
            body = {}

        # Trading Den proxy: forward DenMa REST writes (e.g. /api/bots/run).
        if path != '/ws' and self._is_denma_request(path):
            return self._proxy_denma_http(body=post_data)

        # Trading Den / DenMa backend supervisor controls
        if path == '/api/trading-den/restart':
            dennis_denma.BACKEND.restart()
            return self.send_json(dennis_denma.BACKEND.status())
        if path == '/api/trading-den/stop':
            dennis_denma.BACKEND.stop()
            return self.send_json(dennis_denma.BACKEND.status())

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

        # ==================================================================
        # MCP CONNECTORS: create / policy / authorize / complete / refresh.
        # ==================================================================
        if path == '/api/mcp/servers':
            owner = self._require_owner(parsed)
            if not owner:
                return
            try:
                server = dennis_mcp.create_server(body)
                return self.send_json({"success": True, "server": dennis_mcp._public(server)})
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)

        if path.startswith('/api/mcp/servers/') and path.endswith('/policy'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            server_id = path[len('/api/mcp/servers/'):-len('/policy')]
            try:
                server = dennis_mcp.update_policy(server_id, body.get("policy") or body)
                return self.send_json({"success": True, "server": dennis_mcp._public(server)})
            except KeyError:
                return self.send_json({"error": "Connector not found"}, 404)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)

        if path.startswith('/api/mcp/servers/') and path.endswith('/client-id'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            server_id = path[len('/api/mcp/servers/'):-len('/client-id')]
            try:
                dennis_mcp.set_client_id(server_id, body.get("client_id", ""))
                result = dennis_mcp.begin_oauth(server_id, body.get("client_id", ""))
                return self.send_json(result)
            except KeyError:
                return self.send_json({"error": "Connector not found"}, 404)
            except (ValueError, RuntimeError) as exc:
                return self.send_json({"error": str(exc)}, 400)

        if path.startswith('/api/mcp/servers/') and path.endswith('/authorize'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            server_id = path[len('/api/mcp/servers/'):-len('/authorize')]
            try:
                result = dennis_mcp.begin_oauth(server_id, body.get("client_id", ""))
                return self.send_json(result)
            except KeyError:
                return self.send_json({"error": "Connector not found"}, 404)
            except (ValueError, RuntimeError) as exc:
                return self.send_json({"error": str(exc)}, 400)

        if path.startswith('/api/mcp/servers/') and path.endswith('/callback'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            server_id = path[len('/api/mcp/servers/'):-len('/callback')]
            try:
                dennis_mcp.complete_oauth(
                    server_id, body.get("code", ""), body.get("state", ""))
                return self.send_json({"success": True, "server": dennis_mcp._public(dennis_mcp.get_server(server_id))})
            except KeyError:
                return self.send_json({"error": "Connector not found"}, 404)
            except (ValueError, RuntimeError) as exc:
                return self.send_json({"error": str(exc)}, 400)

        if path.startswith('/api/mcp/servers/') and path.endswith('/connect'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            server_id = path[len('/api/mcp/servers/'):-len('/connect')]
            try:
                server = dennis_mcp.complete_connection(server_id)
                return self.send_json({"success": True, "server": dennis_mcp._public(server)})
            except KeyError:
                return self.send_json({"error": "Connector not found"}, 404)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, 400)

        if path.startswith('/api/mcp/servers/') and path.endswith('/refresh'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            server_id = path[len('/api/mcp/servers/'):-len('/refresh')]
            try:
                server = dennis_mcp.refresh_token(server_id)
                return self.send_json({"success": True, "server": dennis_mcp._public(server)})
            except KeyError:
                return self.send_json({"error": "Connector not found"}, 404)
            except (ValueError, RuntimeError, OSError) as exc:
                return self.send_json({"error": str(exc)}, 400)

        # 1. Add / Register a New Agent
        if path == '/api/agents':
            agent_data = body.get("agent")
            if not agent_data or not agent_data.get("name"):
                return self.send_json({"error": "Agent name and details required"}, 400)

            # Auto-verify binary path exists on system to prevent misleading status
            binary_path = agent_data.get("binary_path", "").strip()
            env = get_augmented_env()
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

            env = get_augmented_env()
            try:
                # Run the real installation command
                res = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120, env=env)
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
                return self.send_json({"success": False, "error": "Installation timed out after 120 seconds"}, 408)
            except Exception as e:
                return self.send_json({"success": False, "error": str(e)}, 500)

        # 4. Pre-flight Test / Health Check for an Agent Binary
        if path == '/api/agents/test':
            binary_path = body.get("binary_path", "").strip()
            args = body.get("args", ["--version"])
            if not binary_path:
                return self.send_json({"success": False, "error": "binary_path required"}, 400)

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
            active_agent = next((a for a in agents if a["id"] == active_id), agents[0] if agents else DEFAULT_AGENTS[0])

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
                model = compatible[0] if compatible else {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "provider": "Google"}

            import time
            timestamp = time.strftime("%H:%M:%S")

            reply = ""
            steps = []
            env = get_augmented_env()

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
                            f"Model: {model['name']} ({model.get('provider', 'Provider')})",
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
                "model": model["name"],
                "model_id": model["id"],
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
            session["model_id"] = model["id"]
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
                return self.send_json({"success": False, "error": f"Agent '{active_id}' not found"}, 404)

            env = get_augmented_env()
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
                return self.send_json({"error": f"Agent '{agent_id}' not found"}, 404)

            env = get_augmented_env()
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

        # Apply a model to the current session through the CLI's own mechanism.
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
            ctx = self._exec_ctx(project_path)
            try:
                models = dennis_models.list_models(rec['agent_id'], ctx,
                                                   cwd=project_path, refresh=True)
            except dennis_models.ModelListError as e:
                return self.send_json({"error": str(e), "retry": True}, 502)
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

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query_params = parse_qs(parsed.query)

        # Remove an MCP connector (revokes its stored tokens).
        if path.startswith('/api/mcp/servers/'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            server_id = path[len('/api/mcp/servers/'):]
            if not dennis_mcp.delete_server(server_id):
                return self.send_json({"error": "Connector not found"}, 404)
            return self.send_json({"success": True})

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
        """Scans the system for installed agent CLIs"""
        probes = [
            {
                "id": "antigravity",
                "name": "Antigravity",
                "binary": "agy",
                "provider": "Google DeepMind",
                "category": "Coding & Orchestration",
                "icon_char": "A",
                "color_theme": "cyan",
                "default_args": ["--version"]
            },
            {
                "id": "claude-code",
                "name": "Claude Code",
                "binary": "claude",
                "provider": "Anthropic",
                "category": "Autonomous Coding Agent",
                "icon_char": "C",
                "color_theme": "amber",
                "default_args": ["--version"]
            },
            {
                "id": "opencode",
                "name": "OpenCode",
                "binary": "opencode",
                "provider": "OpenCode AI",
                "category": "Open-Source Coding Agent",
                "icon_char": "O",
                "color_theme": "blue",
                "default_args": ["--version"]
            },
            {
                "id": "codex",
                "name": "Codex CLI",
                "binary": "codex",
                "provider": "OpenAI",
                "category": "CLI Agent & Code Assistant",
                "icon_char": "X",
                "color_theme": "emerald",
                "default_args": ["--version"]
            },
            {
                "id": "gemini-cli",
                "name": "Gemini CLI",
                "binary": "gemini",
                "provider": "Google",
                "category": "CLI Multi-Modal Agent",
                "icon_char": "G",
                "color_theme": "indigo",
                "default_args": ["--version"]
            },
            {
                "id": "aider",
                "name": "Aider",
                "binary": "aider",
                "provider": "Paul Gauthier",
                "category": "Git-Integrated Pair Programming",
                "icon_char": "Ai",
                "color_theme": "purple",
                "default_args": ["--version"]
            },
            {
                "id": "goose",
                "name": "Goose",
                "binary": "goose",
                "provider": "Block",
                "category": "Autonomous Extensible Agent",
                "icon_char": "Gs",
                "color_theme": "cyan",
                "default_args": ["--version"]
            }
        ]

        detected = []
        registered_agents = load_agents()
        registered_ids = {a["id"] for a in registered_agents}
        registered_bins = {os.path.basename(a.get("binary_path", "")).lower() for a in registered_agents}

        env = get_augmented_env()

        for probe in probes:
            binary = probe["binary"]
            path = shutil.which(binary, path=env["PATH"])

            if path:
                version = "Installed"
                try:
                    res = subprocess.run([path] + probe["default_args"], capture_output=True, text=True, timeout=2, env=env)
                    raw_out = (res.stdout.strip() or res.stderr.strip()).splitlines()
                    if raw_out:
                        version = raw_out[0].strip()
                except Exception:
                    pass

                is_added = probe["id"] in registered_ids or binary.lower() in registered_bins

                detected.append({
                    "id": probe["id"],
                    "name": probe["name"],
                    "binary": binary,
                    "binary_path": path,
                    "version": version,
                    "provider": probe["provider"],
                    "category": probe["category"],
                    "icon_char": probe["icon_char"],
                    "color_theme": probe["color_theme"],
                    "is_already_added": is_added
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


def run(port=PORT):
    dennis_denma.BACKEND.start()
    atexit.register(dennis_denma.BACKEND.stop)
    with DennisHTTPServer(("", port), DennisOSHandler) as httpd:
        print(f"==========================================================")
        print(f"  DENNIS Agentic OS Server with In-OS Installer running at:")
        print(f"  http://localhost:{port}/")
        print(f"  Trading Den backend starts automatically on "
              f"{dennis_denma.BACKEND.url} (set DENNIS_NO_DENMA=1 to disable)")
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
    run(port)
