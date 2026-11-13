"""DeepSeek Harness - a native DeepSeek chat agent for the Agent dropdown.

Unlike the host CLIs (OpenCode, Codex, ...), this agent needs no binary and no
PTY. It talks to DeepSeek's OpenAI-compatible Chat Completions API directly and
keeps its own conversation history, so it can render a native chat dashboard
inside the workspace instead of a terminal screen.

Design constraints, matching the rest of the OS:

* stdlib only (``http.client`` streams the SSE response);
* the API key never reaches the browser - only ``configured``/``source``/``hint``
  are returned, and the stored file is mode 0600 inside the git-ignored
  ``data/`` directory;
* conversations are atomic JSON writes guarded by a lock, because the threaded
  HTTP server calls these helpers concurrently;
* no fabricated state: an unconfigured key is reported as such, and an upstream
  error is surfaced verbatim (shortened) instead of pretending to answer.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit

from . import config

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
MAX_CONTENT_CHARS = 120_000
MAX_MESSAGES = 400
MAX_CONVERSATIONS = 100
HISTORY_WINDOW = 80          # messages replayed into the model
HTTP_TIMEOUT = 180.0

STATIC_MODELS = (
    {
        "id": "deepseek-chat",
        "name": "DeepSeek Chat",
        "description": "Fast general-purpose DeepSeek model.",
    },
    {
        "id": "deepseek-reasoner",
        "name": "DeepSeek Reasoner",
        "description": "Deeper reasoning model; may think before answering.",
    },
)

SYSTEM_PROMPT = (
    "You are DeepSeek Harness, a concise, capable assistant running inside "
    "DENNIS Agentic OS. Be direct and technical. Use Markdown when it helps, "
    "and keep answers focused on the user's request."
)


class ChatError(Exception):
    """Base class for expected chat failures (shown to the user)."""


class NotConfigured(ChatError):
    pass


class ChatBusy(ChatError):
    pass


class NotFound(ChatError):
    pass


# ---------------------------------------------------------------------------
# Paths / IO
# ---------------------------------------------------------------------------
def _dir():
    return os.path.join(config.DATA_DIR, "deepseek")


def _credentials_file():
    return os.path.join(_dir(), "credentials.json")


def _conversations_file():
    return os.path.join(_dir(), "conversations.json")


def _ensure_dir():
    os.makedirs(_dir(), mode=0o700, exist_ok=True)
    try:
        os.chmod(_dir(), 0o700)
    except OSError:
        pass


def _atomic_write(path, data, mode=None):
    _ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        if mode is not None:
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        if mode is not None:
            os.chmod(path, mode)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
def base_url():
    return (os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def default_model():
    return (os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def api_key():
    """Return ``(key, source)``; environment wins over the stored file."""
    env = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if env:
        return env, "env"
    data = _read_json(_credentials_file(), {})
    key = data.get("api_key")
    if isinstance(key, str) and key.strip():
        return key.strip(), "stored"
    return None, "none"


def _key_status():
    key, source = api_key()
    if not key:
        return {
            "configured": False,
            "source": "none",
            "hint": None,
            "updated_at": None,
        }
    data = _read_json(_credentials_file(), {})
    return {
        "configured": True,
        "source": source,
        "hint": ("..." + key[-4:]) if len(key) >= 4 else None,
        "updated_at": data.get("updated_at") if source == "stored" else None,
    }


def set_api_key(value):
    if not isinstance(value, str) or not value.strip():
        raise ChatError("An API key is required.")
    value = value.strip()
    if len(value) > 4096:
        raise ChatError("That API key is too long.")
    if any(ch in value for ch in ("\n", "\r", "\x00")) or not value.isprintable():
        raise ChatError("That API key contains an invalid character.")
    _atomic_write(
        _credentials_file(),
        {"api_key": value, "updated_at": _now()},
        mode=stat.S_IRUSR | stat.S_IWUSR,
    )
    return status()


def clear_api_key():
    path = _credentials_file()
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ChatError("Could not remove the stored key: %s" % exc)
    return status()


def status():
    result = {
        "agent_id": "deepseek-harness",
        "base_url": base_url(),
        "default_model": default_model(),
        "models": models(),
    }
    result.update(_key_status())
    return result


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
def models():
    """Curated model list (deterministic and free of network stalls).

    ``DEEPSEEK_MODEL`` picks the default (even a custom id), and
    ``DEEPSEEK_MODELS`` adds comma-separated ids for private deployments.
    """
    default = default_model()
    entries = []
    seen = set()
    for item in STATIC_MODELS:
        entry = dict(item)
        if entry["id"] in seen:
            continue
        seen.add(entry["id"])
        entry.update({
            "is_default": entry["id"] == default,
            "available": True,
            "reason": None,
        })
        entries.append(entry)
    for raw in (os.environ.get("DEEPSEEK_MODELS") or "").split(","):
        mid = raw.strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        entries.append({
            "id": mid,
            "name": mid,
            "description": "Configured via DEEPSEEK_MODELS.",
            "is_default": mid == default,
            "available": True,
            "reason": None,
        })
    # Exactly one default: the configured id when present, else the first.
    if not any(e["is_default"] for e in entries):
        entries[0]["is_default"] = True
    else:
        hit = False
        for entry in entries:
            if entry["is_default"]:
                if hit:
                    entry["is_default"] = False
                hit = True
    return entries


def has_model(model_id):
    return any(m["id"] == model_id for m in models())


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------
_STORE_LOCK = threading.RLock()


def _load_store():
    data = _read_json(_conversations_file(), {})
    if not isinstance(data.get("owners"), dict):
        data = {"version": 1, "owners": {}}
    return data


def _owner_bucket(data, owner, create=False):
    owners = data.setdefault("owners", {})
    if create:
        return owners.setdefault(owner, {"conversations": []})
    return owners.get(owner) or {"conversations": []}


def _save_store(data):
    _atomic_write(_conversations_file(), data)


def _new_id():
    return "ds-" + uuid.uuid4().hex[:12]


def _title_from(text):
    one = " ".join((text or "").split())
    if not one:
        return "New chat"
    return one if len(one) <= 64 else one[:63].rstrip() + "\u2026"


def _meta(conv):
    last = ""
    for msg in reversed(conv.get("messages") or []):
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            last = " ".join(msg["content"].split())[:90]
            break
    return {
        "id": conv.get("id"),
        "title": conv.get("title") or "New chat",
        "model_id": conv.get("model_id"),
        "created_at": conv.get("created_at"),
        "updated_at": conv.get("updated_at"),
        "message_count": len(conv.get("messages") or []),
        "preview": last,
    }


def list_conversations(owner):
    with _STORE_LOCK:
        data = _load_store()
        items = list(_owner_bucket(data, owner).get("conversations") or [])
    items.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
    return [_meta(c) for c in items]


def get_conversation(owner, conversation_id):
    with _STORE_LOCK:
        data = _load_store()
        for conv in _owner_bucket(data, owner).get("conversations") or []:
            if conv.get("id") == conversation_id:
                return json.loads(json.dumps(conv))
    return None


def create_conversation(owner, model_id=None, title=None):
    model_id = (model_id or default_model()).strip()
    if not has_model(model_id):
        raise ChatError("'%s' is not a DeepSeek model offered here." % model_id)
    now = _now()
    conv = {
        "id": _new_id(),
        "title": (title or "").strip() or "New chat",
        "model_id": model_id,
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    with _STORE_LOCK:
        data = _load_store()
        bucket = _owner_bucket(data, owner, create=True)
        bucket.setdefault("conversations", []).insert(0, conv)
        # Cap the rail so the file cannot grow without bound.
        bucket["conversations"] = bucket["conversations"][:MAX_CONVERSATIONS]
        _save_store(data)
    return json.loads(json.dumps(conv))


def delete_conversation(owner, conversation_id):
    with _STORE_LOCK:
        data = _load_store()
        bucket = _owner_bucket(data, owner, create=True)
        before = len(bucket.get("conversations") or [])
        bucket["conversations"] = [
            c for c in bucket.get("conversations") or []
            if c.get("id") != conversation_id
        ]
        if len(bucket["conversations"]) == before:
            return False
        _save_store(data)
        return True


def _append_message(owner, conversation_id, role, content, reasoning="",
                    interrupted=False):
    content = (content or "").strip()
    if not content and not reasoning:
        return None
    msg = {
        "id": "dm-" + uuid.uuid4().hex[:12],
        "role": role,
        "content": content,
        "created_at": _now(),
    }
    if reasoning:
        msg["reasoning"] = reasoning
    if interrupted:
        msg["interrupted"] = True
    with _STORE_LOCK:
        data = _load_store()
        for conv in _owner_bucket(data, owner, create=True).get("conversations") or []:
            if conv.get("id") != conversation_id:
                continue
            conv.setdefault("messages", []).append(msg)
            if len(conv["messages"]) > MAX_MESSAGES:
                conv["messages"] = conv["messages"][-MAX_MESSAGES:]
            if role == "user" and conv.get("title") in (None, "", "New chat"):
                conv["title"] = _title_from(content)
            conv["updated_at"] = _now()
            conv.pop("_busy", None)
            _save_store(data)
            return json.loads(json.dumps(msg))
    return None


def _set_model(owner, conversation_id, model_id):
    with _STORE_LOCK:
        data = _load_store()
        for conv in _owner_bucket(data, owner, create=True).get("conversations") or []:
            if conv.get("id") == conversation_id:
                conv["model_id"] = model_id
                conv["updated_at"] = _now()
                _save_store(data)
                return True
    return False


# ---------------------------------------------------------------------------
# Upstream streaming
# ---------------------------------------------------------------------------
def _endpoint():
    parts = urlsplit(base_url())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ChatError("DEEPSEEK_BASE_URL must be an http(s) URL.")
    path = (parts.path or "").rstrip("/")
    if path.endswith("/chat/completions"):
        endpoint = path
    elif path.endswith("/v1"):
        endpoint = path + "/chat/completions"
    else:
        endpoint = path + "/chat/completions"
    return parts, endpoint or "/chat/completions"


def _friendly_http_error(code, raw):
    detail = ""
    try:
        obj = json.loads(raw)
        err = obj.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or "")
        elif err:
            detail = str(err)
        detail = detail or str(obj.get("message") or "")
    except (ValueError, TypeError):
        detail = (raw or "").strip()
    detail = " ".join(detail.split())[:300]
    hints = {
        400: "DeepSeek rejected the request",
        401: "DeepSeek API key is invalid",
        402: "DeepSeek account has insufficient balance",
        403: "DeepSeek denied access to this model",
        404: "DeepSeek model or endpoint was not found",
        422: "DeepSeek rejected the request parameters",
        429: "DeepSeek rate limit reached; try again shortly",
        500: "DeepSeek service error",
        503: "DeepSeek is busy; try again shortly",
    }
    head = hints.get(code, "DeepSeek request failed (HTTP %s)" % code)
    return head + (": " + detail if detail else ".")


def _completion_events(messages, model_id, key):
    """Yield ``(kind, text)`` tuples from DeepSeek's streaming response.

    ``kind`` is ``"content"`` or ``"reasoning"``. HTTP/JSON errors raise
    :class:`ChatError`. A non-SSE JSON response (some gateways) is accepted and
    yielded whole.
    """
    parts, endpoint = _endpoint()
    import http.client

    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = cls(parts.hostname, parts.port, timeout=HTTP_TIMEOUT)
    try:
        payload = {
            "model": model_id,
            "messages": messages,
            "stream": True,
            "temperature": 0.7,
        }
        conn.request(
            "POST", endpoint, body=json.dumps(payload),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": "Bearer " + key,
                "User-Agent": "DENNIS-Agentic-OS/DeepSeekHarness",
            },
        )
        resp = conn.getresponse()
        if resp.status >= 400:
            raw = resp.read(8192).decode("utf-8", "replace")
            raise ChatError(_friendly_http_error(resp.status, raw))
        ctype = (resp.getheader("Content-Type") or "").lower()
        if "text/event-stream" not in ctype:
            raw = resp.read().decode("utf-8", "replace")
            try:
                obj = json.loads(raw)
            except ValueError:
                raise ChatError("DeepSeek returned an unreadable response.")
            message = ((obj.get("choices") or [{}])[0].get("message") or {})
            for kind, text in _message_parts(message):
                if text:
                    yield kind, text
            return

        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            choice = (obj.get("choices") or [{}])[0]
            delta = choice.get("delta") or choice.get("message") or {}
            for kind, text in _message_parts(delta):
                if text:
                    yield kind, text
            if choice.get("finish_reason"):
                # Keep reading for a trailing usage frame only if it arrives
                # immediately; most servers close right after [DONE].
                pass
    except ChatError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise ChatError("Could not reach DeepSeek: %s" % exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _message_parts(message):
    if not isinstance(message, dict):
        return []
    return [
        ("reasoning", message.get("reasoning_content") or message.get("reasoning") or ""),
        ("content", message.get("content") or ""),
    ]


# ---------------------------------------------------------------------------
# One exchange (validation -> stream -> persistence)
# ---------------------------------------------------------------------------
_ACTIVE_LOCK = threading.Lock()
_ACTIVE = {}  # (owner, conversation_id) -> True


class Exchange:
    """A prepared chat turn.

    Validation (conversation, key, model, busy state) happens in
    :func:`prepare_exchange` so the HTTP layer can still send a real JSON error
    before switching the response to ``text/event-stream``. Iterating
    :meth:`events` streams the answer and persists both messages.
    """

    def __init__(self, owner, conversation, content, model_id, key):
        self.owner = owner
        self.conversation_id = conversation["id"]
        self.content = content
        self.model_id = model_id
        self.key = key
        self.messages = list(conversation.get("messages") or [])
        self.content_out = ""
        self.reasoning_out = ""
        self.user_message = None
        self.assistant_message = None
        self._closed = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        with _ACTIVE_LOCK:
            _ACTIVE.pop((self.owner, self.conversation_id), None)

    def _persist_assistant(self, interrupted=False):
        if not (self.content_out or self.reasoning_out):
            return None
        self.assistant_message = _append_message(
            self.owner, self.conversation_id, "assistant",
            self.content_out, reasoning=self.reasoning_out,
            interrupted=interrupted,
        )
        return self.assistant_message

    def events(self):
        try:
            self.user_message = _append_message(
                self.owner, self.conversation_id, "user", self.content)
            self.messages.append(self.user_message or {
                "role": "user", "content": self.content,
            })
            yield {
                "type": "start",
                "conversation_id": self.conversation_id,
                "model": self.model_id,
                "user_message": self.user_message,
            }

            payload = [{"role": "system", "content": SYSTEM_PROMPT}]
            for msg in self.messages[-HISTORY_WINDOW:]:
                role = msg.get("role")
                content = msg.get("content") or ""
                if role in ("user", "assistant") and content:
                    payload.append({"role": role, "content": content})

            for kind, text in _completion_events(payload, self.model_id, self.key):
                if kind == "reasoning":
                    self.reasoning_out += text
                    yield {"type": "reasoning", "text": text}
                else:
                    self.content_out += text
                    yield {"type": "delta", "text": text}

            message = self._persist_assistant(interrupted=False)
            _set_model(self.owner, self.conversation_id, self.model_id)
            conv = get_conversation(self.owner, self.conversation_id)
            yield {
                "type": "done",
                "conversation_id": self.conversation_id,
                "message": message,
                "conversation": _meta(conv) if conv else None,
            }
        except GeneratorExit:
            self._persist_assistant(interrupted=True)
            raise
        except Exception as exc:
            self._persist_assistant(interrupted=True)
            yield {"type": "error", "message": str(exc)}
        finally:
            self.close()


def prepare_exchange(owner, conversation_id, content, model_id=None):
    """Validate a turn and return an :class:`Exchange` ready to stream."""
    content = (content or "").strip()
    if not content:
        raise ChatError("A message is required.")
    if len(content) > MAX_CONTENT_CHARS:
        raise ChatError("That message is too long (limit %d characters)." % MAX_CONTENT_CHARS)
    conv = get_conversation(owner, conversation_id)
    if not conv:
        raise NotFound("Conversation not found.")
    key, _source = api_key()
    if not key:
        raise NotConfigured("Add a DeepSeek API key to start chatting.")
    model_id = (model_id or conv.get("model_id") or default_model()).strip()
    if not has_model(model_id):
        raise ChatError("'%s' is not a DeepSeek model offered here." % model_id)
    token = (owner, conversation_id)
    with _ACTIVE_LOCK:
        if token in _ACTIVE:
            raise ChatBusy("DeepSeek is already replying in this chat.")
        _ACTIVE[token] = True
    return Exchange(owner, conv, content, model_id, key)
