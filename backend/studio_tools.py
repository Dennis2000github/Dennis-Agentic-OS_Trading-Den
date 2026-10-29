"""Agent-facing tool surface for Studio, with an owner approval queue.

Read-only tools run immediately. Mutating tools (spend credits, cancel work)
create a pending approval the owner must explicitly approve in the UI or over
the API. This is the first integration step for agents; the bundled MCP shim
(``scripts/studio_mcp_shim.py``) exposes the same surface to MCP clients.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

from . import config
from . import studio_contracts as contracts

STUDIO_DIR = os.path.join(config.DATA_DIR, "studio")
APPROVALS_FILE = os.path.join(STUDIO_DIR, "approvals.json")
MAX_APPROVALS = 200

MUTATING = frozenset(("studio_create_job", "studio_cancel_job"))


class ToolError(Exception):
    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)

    def as_error(self):
        return {"code": self.code, "message": self.message}


TOOLS = {
    "studio_list_models": {
        "description": "List Studio models with documented inputs and capabilities.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["higgsfield", "higgsfield_mcp"]},
                "kind": {"type": "string", "enum": ["image", "video"]},
                "query": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "mutating": False,
    },
    "studio_get_model": {
        "description": "Get one Studio model's full parameter schema and normalized fields.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model_id": {"type": "string"},
                "provider": {"type": "string", "enum": ["higgsfield", "higgsfield_mcp"]},
            },
            "required": ["model_id"],
            "additionalProperties": False,
        },
        "mutating": False,
    },
    "studio_create_job": {
        "description": "Submit a generation job. Requires owner approval (spends credits).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "enum": ["higgsfield", "higgsfield_mcp"]},
                "model_id": {"type": "string"},
                "params": {"type": "object"},
                "auto_download": {"type": "boolean"},
            },
            "required": ["model_id", "params"],
            "additionalProperties": False,
        },
        "mutating": True,
    },
    "studio_get_job": {
        "description": "Get a Studio job's normalized state, error and outputs.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "mutating": False,
    },
    "studio_list_jobs": {
        "description": "List recent Studio jobs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "state": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "mutating": False,
    },
    "studio_cancel_job": {
        "description": "Cancel a queued Studio job. Requires owner approval.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "mutating": True,
    },
    "studio_list_library": {
        "description": "List downloaded Studio outputs in the local media library.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["image", "video", "audio", "file"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        "mutating": False,
    },
    "studio_video_reverse_engineering": {
        "description": (
            "Analyze a reference video, avatar, and product kit using visual analysis tools "
            "(eyes and ears) to synthesize a faithful recreation prompt following the "
            "reference-video-recreation skill."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference": {"type": "string", "description": "URL or local media path of reference video/image"},
                "avatar": {"type": "string", "description": "Optional URL or local path of avatar/character reference"},
                "product": {"type": "string", "description": "Optional URL or local path of product/props reference"},
                "prompt": {"type": "string", "description": "Optional creative direction or user constraints"},
            },
            "required": ["reference"],
            "additionalProperties": False,
        },
        "mutating": False,
    },
}


def _check_args(tool_name, arguments):
    schema = TOOLS[tool_name]["inputSchema"]
    if not isinstance(arguments, dict):
        raise ToolError("invalid_arguments", "Tool arguments must be an object.")
    props = schema.get("properties") or {}
    for key in arguments:
        if key not in props:
            raise ToolError("unknown_argument", "%s is not a supported argument." % key)
    for key in schema.get("required") or []:
        if key not in arguments:
            raise ToolError("missing_argument", "%s is required." % key)
    return arguments


class ApprovalStore:
    def __init__(self, path=APPROVALS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._items = []
        self._load()

    def _load(self):
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print("[studio] could not read approvals: %s" % exc)
            return
        items = data.get("approvals") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if item.get("status") == "pending":
                # Arguments are redacted on disk, so a pending action cannot be
                # replayed after a restart without risking a mangled request.
                item["status"] = "expired"
                item["error"] = {
                    "code": "server_restarted",
                    "message": "The server restarted before this action was approved. Create it again.",
                }
            self._items.append(item)

    def _persist_locked(self):
        directory = os.path.dirname(self.path) or STUDIO_DIR
        os.makedirs(directory, mode=0o700, exist_ok=True)
        safe = []
        for item in self._items:
            record = dict(item)
            record["arguments"] = contracts.redact(item.get("arguments") or {})
            safe.append(record)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "approvals": safe}, fh, indent=2)
        os.replace(tmp, self.path)

    def create(self, tool, arguments, preview):
        record = {
            "id": "apv_" + uuid.uuid4().hex[:16],
            "tool": tool,
            "arguments": arguments,
            "preview": contracts.redact(preview),
            "status": "pending",
            "created_at": time.time(),
            "created_iso": config.now_iso(),
            "decided_at": None,
            "result": None,
            "error": None,
        }
        with self._lock:
            self._items.insert(0, record)
            self._items = self._items[:MAX_APPROVALS]
            self._persist_locked()
        return self._public(record)

    @staticmethod
    def _public(item):
        record = dict(item)
        record["arguments"] = contracts.redact(item.get("arguments") or {})
        return record

    def get(self, approval_id):
        raw = self._get_raw(approval_id)
        return self._public(raw) if raw else None

    def _get_raw(self, approval_id):
        with self._lock:
            for item in self._items:
                if item["id"] == approval_id:
                    return dict(item)
        return None

    def update(self, approval_id, **fields):
        with self._lock:
            for item in self._items:
                if item["id"] == approval_id:
                    item.update(fields)
                    self._persist_locked()
                    return self._public(item)
        return None

    def list(self, status=None, limit=50):
        with self._lock:
            items = list(self._items)
        if status:
            items = [x for x in items if x.get("status") == status]
        return [self._public(x) for x in items[: max(1, min(int(limit or 50), MAX_APPROVALS))]]


class StudioTools:
    def __init__(self, registry, runner, approvals=None):
        self.registry = registry
        self.runner = runner
        self.approvals = approvals or ApprovalStore()

    # -- discovery ---------------------------------------------------------
    def list_tools(self):
        return [
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
                "mutating": spec["mutating"],
            }
            for name, spec in TOOLS.items()
        ]

    def list_approvals(self, status=None):
        return self.approvals.list(status=status)

    # -- invocation --------------------------------------------------------
    def call(self, name, arguments=None):
        if name not in TOOLS:
            raise ToolError("unknown_tool", "Unknown Studio tool: %s" % name, 404)
        args = _check_args(name, arguments or {})
        if TOOLS[name]["mutating"]:
            preview = self._preview(name, args)
            approval = self.approvals.create(name, args, preview)
            return {
                "status": "approval_required",
                "approval": approval,
                "message": "The owner must approve this action before it runs.",
            }
        return {"status": "ok", "result": self._execute(name, args)}

    def decide(self, approval_id, decision):
        approval = self.approvals._get_raw(approval_id)
        if not approval:
            raise ToolError("approval_not_found", "Approval not found.", 404)
        if approval.get("status") != "pending":
            raise ToolError("approval_decided", "This approval has already been decided.", 409)
        if decision not in ("approve", "reject"):
            raise ToolError("invalid_decision", "decision must be approve or reject.")
        if decision == "reject":
            return self.approvals.update(
                approval_id, status="rejected", decided_at=time.time()
            )
        try:
            result = self._execute(approval["tool"], approval["arguments"])
        except Exception as exc:
            error = exc.as_error() if hasattr(exc, "as_error") else {
                "code": "unexpected_error",
                "message": contracts.redact_text(str(exc)),
            }
            updated = self.approvals.update(
                approval_id,
                status="failed",
                decided_at=time.time(),
                error=error,
            )
            return updated
        return self.approvals.update(
            approval_id,
            status="approved",
            decided_at=time.time(),
            result=result,
        )

    # -- previews and execution -------------------------------------------
    def _preview(self, name, args):
        if name == "studio_cancel_job":
            job = self.runner.store.get(args["job_id"])
            if not job:
                raise ToolError("job_not_found", "Job not found.", 404)
            return {
                "action": "cancel_job",
                "job_id": job["id"],
                "model": job.get("model_label"),
                "state": job.get("state"),
                "note": "Only queued jobs can be cancelled.",
            }
        provider_id = args.get("provider") or "higgsfield"
        provider = self.registry.get(provider_id)
        model = provider.model(args["model_id"])
        clean = contracts.validate_params(model, args["params"])
        return {
            "action": "create_job",
            "provider": provider_id,
            "model_id": args["model_id"],
            "model": model.get("label") or args["model_id"],
            "kind": model.get("kind"),
            "mode": model.get("mode"),
            "params": contracts.redact(clean),
            "prompt": str(clean.get("prompt") or "")[:300],
            "auto_download": bool(args.get("auto_download", True)),
            "note": "Approving submits a real generation request; it may spend credits.",
        }

    def _execute(self, name, args):
        if name == "studio_list_models":
            provider_id = args.get("provider") or "higgsfield"
            return {
                "provider": provider_id,
                "models": self.registry.get(provider_id).models(
                    kind=args.get("kind"), query=args.get("query")
                ),
            }
        if name == "studio_get_model":
            provider_id = args.get("provider") or "higgsfield"
            return self.registry.get(provider_id).model_detail(args["model_id"])
        if name == "studio_create_job":
            job, duplicate = self.runner.submit(
                args.get("provider") or "higgsfield",
                args["model_id"],
                args["params"],
                auto_download=bool(args.get("auto_download", True)),
            )
            return {"job": job, "duplicate": duplicate}
        if name == "studio_get_job":
            job = self.runner.store.get(args["job_id"])
            if not job:
                raise ToolError("job_not_found", "Job not found.", 404)
            from . import studio_jobs

            return studio_jobs.public_job(job)
        if name == "studio_list_jobs":
            from . import studio_jobs

            return {
                "jobs": [
                    studio_jobs.public_job(job)
                    for job in self.runner.store.list(
                        limit=args.get("limit") or 50, state=args.get("state")
                    )
                ]
            }
        if name == "studio_cancel_job":
            return self.runner.cancel(args["job_id"])
        if name == "studio_list_library":
            from . import studio_media

            return {
                "items": studio_media.list_items(
                    kind=args.get("kind"), role="output", limit=args.get("limit") or 30
                )
            }
        if name == "studio_video_reverse_engineering":
            from . import studio_videokit

            return studio_videokit.analyze_video_kit(
                reference=args["reference"],
                avatar=args.get("avatar"),
                product=args.get("product"),
                prompt=args.get("prompt") or ""
            )
        raise ToolError("unknown_tool", "Unknown Studio tool: %s" % name, 404)
