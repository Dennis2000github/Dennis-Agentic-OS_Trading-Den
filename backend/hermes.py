"""Hermes - the orchestrator agent for DENNIS Agentic OS.

Hermes owns the pipeline that turns a goal into work performed by the
registered CLI *sub-agents*::

    goal -> plan (decompose) -> route (assign roles) -> delegate (parallel
    fan-out) -> aggregate -> remember (Vault "second brain")

The design keeps *decisions* away from *process spawning* so the orchestration
can be unit-tested without any CLI installed:

- ``planner``       : ``callable(goal, context) -> list[raw_task] | None``.
  The default routes the goal through an installed CLI agent in print mode and
  parses a JSON task list; when that is unavailable it falls back to
  :func:`decompose` (a deterministic five-role pipeline).
- ``runner_factory``: ``callable(task, agent, emit, done) -> runner``.
  The default spawns the agent CLI as a streamed subprocess.
- ``vault``         : the Obsidian vault module used as the persistent second
  brain (plans, results and free-form memories are stored as notes).

Nothing here imports ``server``; ``server`` imports this module.
"""

import json
import os
import re
import subprocess
import threading
import time
import uuid

from . import agent_registry, config, vault

# Kanban columns, in workflow order. The board renders exactly these.
COLUMNS = [
    {"id": "backlog", "label": "Backlog"},
    {"id": "assigned", "label": "Assigned"},
    {"id": "running", "label": "Running"},
    {"id": "review", "label": "Review"},
    {"id": "done", "label": "Done"},
]
COLUMN_IDS = [c["id"] for c in COLUMNS]

# Roles Hermes assigns to sub-agents. ``keywords`` drive deterministic routing
# and the fallback planner; ``verify`` never reuses the implementing agent.
ROLES = {
    "research": {"label": "Research", "keywords": ["research", "investigate", "explore", "find", "analyze", "audit", "survey"]},
    "plan": {"label": "Plan", "keywords": ["plan", "design", "architect", "spec", "outline", "propose"]},
    "implement": {"label": "Implement", "keywords": ["implement", "build", "create", "add", "write", "fix", "refactor", "wire", "develop"]},
    "verify": {"label": "Verify", "keywords": ["verify", "test", "review", "validate", "check", "qa", "lint"]},
    "document": {"label": "Document", "keywords": ["document", "docs", "readme", "explain", "changelog", "guide"]},
}

ORCHESTRATOR_ID = "hermes"
ORCHESTRATOR_NAME = "Hermes"
MEMORY_DIR = "Hermes"
PLAN_DIR = "Hermes/Plans"
RUN_DIR = "Hermes/Runs"

DEFAULT_CONCURRENCY = int(os.environ.get("DENNIS_HERMES_CONCURRENCY", "3"))
_MAX_RESULT = 200_000  # characters of task output kept in state
_MAX_BUFFER_LINES = 4000  # live replay buffer for a running task
_MAX_RUNS = 100  # finished runs kept in hermes.json (active runs are never trimmed)

HERMES_FILE = os.path.join(config.DATA_DIR, "hermes.json")


class HermesError(Exception):
    """Raised for invalid orchestration requests."""


def _now():
    return config.now_iso()


def _slug(text, fallback="note"):
    text = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (text or fallback)[:60]


def decompose(goal):
    """Deterministically split a goal into a five-role task pipeline.

    Used as the planner fallback and as a sanity filter for CLI plans. Returns
    a list of ``{title, detail, role}`` dicts with no ids/status.
    """
    goal = (goal or "").strip()
    if not goal:
        raise HermesError("A goal is required")
    short = goal if len(goal) <= 80 else goal[:77] + "…"
    template = [
        ("research", "Research context and constraints", "Gather the facts, files and constraints needed for: " + short),
        ("plan", "Design the approach", "Produce an ordered implementation plan for: " + short),
        ("implement", "Implement the change", "Carry out the plan for: " + short),
        ("verify", "Verify the result", "Independently review, test and validate the work for: " + short),
        ("document", "Document the work", "Write up what changed and how to use it for: " + short),
    ]
    return [{"title": t, "detail": d, "role": r} for r, t, d in template]


def _normalize_plan(raw, goal):
    """Coerce a planner result into well-formed task dicts (or fall back)."""
    if not isinstance(raw, list) or not raw:
        return decompose(goal)
    tasks = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()
        role = str(item.get("role") or "").strip().lower()
        if role not in ROLES:
            role = "implement"
        if not title:
            continue
        tasks.append({
            "title": title[:120],
            "detail": str(item.get("detail") or item.get("description") or title).strip()[:2000],
            "role": role,
        })
    return tasks or decompose(goal)


class HermesOrchestrator:
    """Stateful orchestrator for the sub-agent swarm."""

    def __init__(self, path=None, vault_module=None, registry=None,
                 planner=None, runner_factory=None, concurrency=None):
        self.path = path or HERMES_FILE
        self.vault = vault_module if vault_module is not None else vault
        self.registry = registry if registry is not None else agent_registry
        self._planner = planner
        self._runner_factory = runner_factory
        self.concurrency = max(1, int(concurrency or DEFAULT_CONCURRENCY))
        self._lock = threading.RLock()
        self._runners = {}          # task_id -> live runner
        self._subscribers = {}      # task_id -> set of sinks
        self._state = self._load()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def _load(self):
        data = config.read_json(self.path, default=None)
        if not isinstance(data, dict):
            data = {}
        data.setdefault("version", 1)
        data.setdefault("tasks", [])
        data.setdefault("runs", [])
        return data

    def _save(self):
        with self._lock:
            config.write_json(self.path, self._state)

    # ------------------------------------------------------------------
    # sub-agents
    # ------------------------------------------------------------------
    def subagents(self):
        """Registered CLI agents Hermes may delegate to (orchestrator excluded)."""
        out = []
        try:
            agents = self.registry.available_agents()
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[hermes] agent registry unavailable: {exc}")
            agents = []
        for a in agents:
            if a.get("id") == ORCHESTRATOR_ID:
                continue
            out.append({
                "id": a.get("id"),
                "name": a.get("name"),
                "provider": a.get("provider"),
                "icon_char": a.get("icon_char"),
                "available": bool(a.get("available")),
                "reason": a.get("reason", ""),
                "role": "subagent",
            })
        return out

    def _available_ids(self):
        return [a["id"] for a in self.subagents()
                if a["available"] and a.get("id")]

    def identity(self):
        vault_dir = getattr(self.vault, "VAULT_DIR", "") or ""
        try:
            brain = os.path.relpath(vault_dir, config.DIRECTORY) if vault_dir else "vault"
        except ValueError:  # pragma: no cover - different drives (windows)
            brain = vault_dir or "vault"
        return {
            "id": ORCHESTRATOR_ID,
            "name": ORCHESTRATOR_NAME,
            "role": "orchestrator",
            "core": "HERMES Apollo Core",
            "brain": brain,
            "concurrency": self.concurrency,
        }

    # ------------------------------------------------------------------
    # routing
    # ------------------------------------------------------------------
    def route(self, task, available=None, in_use=None):
        """Pick the best available sub-agent id for ``task`` (or None).

        Deterministic policy, stable under test:
        - verify prefers an agent that is *not* the implementer;
        - otherwise prefer the least-loaded available agent;
        - return None when nothing is available.
        """
        avail = list(available if available is not None else self._available_ids())
        if not avail:
            return None
        in_use = list(in_use or [])
        role = (task or {}).get("role") or "implement"

        pool = avail
        if role == "verify":
            impl = (task or {}).get("implemented_by")
            others = [a for a in avail if a != impl]
            if others:
                pool = others
        # least-loaded, tie-break by registration order for determinism
        return min(pool, key=lambda a: (in_use.count(a), avail.index(a)))

    def _assign(self, task, agent_id, assignment, in_use=None):
        if agent_id in (None, "", "autonomous"):
            chosen = self.route(task, in_use=in_use)
            task["agent_id"] = chosen
            task["assignment"] = "autonomous"
        else:
            task["agent_id"] = agent_id
            task["assignment"] = "manual"
        task["updated_at"] = _now()
        return task

    def assign_task(self, task_id, agent_id):
        with self._lock:
            task = self._get(task_id)
            if not task:
                raise HermesError("Task not found")
            return self._assign(task, agent_id, None)

    # ------------------------------------------------------------------
    # task CRUD
    # ------------------------------------------------------------------
    def _get(self, task_id):
        return next((t for t in self._state["tasks"] if t["id"] == task_id), None)

    def create_task(self, title, detail="", role="implement", agent_id=None,
                    assignment=None, status="backlog"):
        title = (title or "").strip()
        if not title:
            raise HermesError("Task title is required")
        if role not in ROLES:
            role = "implement"
        if status not in COLUMN_IDS:
            status = "backlog"
        task = {
            "id": "task-" + uuid.uuid4().hex[:10],
            "title": title[:120],
            "detail": (detail or "").strip()[:2000],
            "role": role,
            "status": status,
            "agent_id": None,
            "assignment": "unassigned",
            "depends_on": [],
            "result": "",
            "error": "",
            "run_id": None,
            "implemented_by": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        with self._lock:
            self._state["tasks"].append(task)
            if agent_id or assignment:
                self._assign(task, agent_id, assignment)
                if task["agent_id"]:
                    task["status"] = "assigned" if status == "backlog" else status
        self._save()
        return task

    def update_task(self, task_id, **fields):
        allowed = {"title", "detail", "role", "status", "depends_on", "agent_id",
                   "result", "error"}
        with self._lock:
            task = self._get(task_id)
            if not task:
                raise HermesError("Task not found")
            for key, value in fields.items():
                if key in allowed:
                    task[key] = value
            task["updated_at"] = _now()
        self._save()
        return task

    def set_status(self, task_id, status):
        if status not in COLUMN_IDS:
            raise HermesError(f"Unknown status '{status}'")
        return self.update_task(task_id, status=status)

    def delete_task(self, task_id):
        with self._lock:
            task = self._get(task_id)
            if not task:
                raise HermesError("Task not found")
            for other in self._state["tasks"]:
                if task_id in other.get("depends_on", []):
                    other["depends_on"].remove(task_id)
            self._state["tasks"] = [t for t in self._state["tasks"] if t["id"] != task_id]
        self._save()
        return {"deleted": task_id}

    def list_tasks(self):
        return list(self._state["tasks"])

    def board(self):
        tasks = list(self._state["tasks"])
        columns = []
        for col in COLUMNS:
            items = [t for t in tasks if t["status"] == col["id"]]
            columns.append({**col, "count": len(items), "tasks": items})
        return {"columns": columns, "column_order": COLUMN_IDS}

    # ------------------------------------------------------------------
    # planning
    # ------------------------------------------------------------------
    def _cli_plan(self, goal, context):
        """Route the goal through an installed CLI agent in print mode."""
        ids = self._available_ids()
        if not ids:
            return None
        for agent_id in ids:
            agent = self.registry.get_agent(agent_id)
            if not agent:
                continue
            argv = agent_registry.one_shot_argv(agent, self._plan_prompt(goal, context))
            if not argv:
                continue
            try:
                res = subprocess.run(argv, capture_output=True, text=True,
                                     timeout=90, cwd=config.DIRECTORY,
                                     env=agent_registry.get_augmented_env())
            except Exception:
                continue
            if res.returncode != 0:
                continue
            parsed = _extract_json_array(res.stdout)
            if parsed:
                return parsed
        return None

    def _context_from_vault(self, goal, limit=6, max_chars=4000):
        """Recall relevant notes from the Vault second brain for a goal."""
        try:
            hits = self.vault.search(goal)[:limit]
        except Exception:
            return ""
        parts = []
        for hit in hits:
            snippet = (hit.get("snippet") or "").strip()
            if snippet:
                parts.append(f"- {hit.get('path')}: {snippet}")
        return "\n".join(parts)[:max_chars]

    @staticmethod
    def _plan_prompt(goal, context):
        ctx = ("\n\nContext from the second brain:\n" + context) if context else ""
        return (
            "You are Hermes, an orchestration planner. Decompose the GOAL into a "
            "small ordered task list for CLI sub-agents. Reply with ONLY a JSON "
            'array like [{"title": "...", "detail": "...", "role": "research|'
            'plan|implement|verify|document"}].\n\nGOAL: ' + goal + ctx
        )

    def plan(self, goal, context="", remember=True, brain="vault"):
        """Decompose ``goal`` into tasks, persisted as a run + vault plan note.

        ``brain`` selects the planning strategy:
        - ``"vault"`` (default): deterministic five-role pipeline seeded with
          context recalled from the Vault second brain. Fast and offline.
        - ``"cli"``: route the goal through an installed sub-agent CLI in print
          mode and parse its JSON plan (falls back to the pipeline).
        """
        goal = (goal or "").strip()
        if not goal:
            raise HermesError("A goal is required")

        if not context:
            context = self._context_from_vault(goal)

        planner = (self._planner or self._cli_plan) if brain == "cli" else None
        raw, source = None, "vault"
        if planner is not None:
            try:
                raw = planner(goal, context)
                if raw:
                    source = "cli"
            except Exception as exc:  # a broken brain must never block planning
                print(f"[hermes] planner failed ({exc}); using deterministic pipeline")
        tasks_spec = _normalize_plan(raw, goal)

        run = {
            "id": "run-" + uuid.uuid4().hex[:10],
            "goal": goal,
            "status": "planned",
            "planner": source,
            "task_ids": [],
            "started_at": _now(),
            "finished_at": None,
            "summary": "",
        }
        created = []
        with self._lock:
            for spec in tasks_spec:
                task = self.create_task(spec["title"], spec["detail"], spec["role"])
                task["run_id"] = run["id"]
                created.append(task)
            run["task_ids"] = [t["id"] for t in created]
            self._state["runs"].append(run)
            self._trim_runs()
            # autonomous routing for the whole plan, spreading the load
            in_use = []
            for task in created:
                self._assign(task, None, None, in_use=in_use)
                if task["agent_id"]:
                    task["status"] = "assigned"
                    in_use.append(task["agent_id"])
        self._save()
        if remember:
            self._remember_plan(run, created, goal, context)
        return {"run": run, "tasks": created}

    def _remember_plan(self, run, tasks, goal, context):
        try:
            self.vault.ensure_vault()
            lines = [f"# Hermes Plan - {run['goal']}", "",
                     f"- Run: `{run['id']}`",
                     f"- Planner: {run['planner']}",
                     f"- Created: {run['started_at']}", ""]
            if context:
                lines += ["## Context", "", context, ""]
            lines += ["## Tasks", ""]
            for t in tasks:
                who = t.get("agent_id") or "unassigned"
                lines.append(f"- [{t['role']}] {t['title']} -> [[{who}]] _({t['status']})_")
            self.vault.write_note(f"{PLAN_DIR}/{_slug(run['goal'], 'plan')}-{run['id'][-6:]}.md",
                                  "\n".join(lines) + "\n")
        except Exception as exc:  # second brain is best-effort
            print(f"[hermes] could not write plan note: {exc}")

    # ------------------------------------------------------------------
    # delegation / execution
    # ------------------------------------------------------------------
    def delegate(self, task_ids=None, run_id=None, executor=None, wait=False):
        """Fan out tasks to sub-agents in parallel, bounded by concurrency."""
        with self._lock:
            if task_ids:
                tasks = [self._get(t) for t in task_ids]
                tasks = [t for t in tasks if t]
            elif run_id:
                run = self._get_run(run_id)
                if not run:
                    raise HermesError("Run not found")
                tasks = [self._get(t) for t in run["task_ids"]]
                tasks = [t for t in tasks if t]
            else:
                tasks = [t for t in self._state["tasks"]
                         if t["status"] in ("backlog", "assigned")]
            in_use = [t.get("agent_id") for t in tasks if t.get("agent_id")]
            for task in tasks:
                if not task.get("agent_id"):
                    self._assign(task, None, None, in_use=in_use)
                    if task.get("agent_id"):
                        in_use.append(task["agent_id"])
                if task.get("agent_id") and task["status"] == "backlog":
                    task["status"] = "assigned"
                if task.get("agent_id"):
                    task["run_id"] = task.get("run_id") or run_id
            run = self._get_run(run_id) if run_id else None

        ready, blocked = [], []
        for task in tasks:
            if task["status"] in ("done", "running"):
                continue
            if not task.get("agent_id"):
                blocked.append(task["id"])
                continue
            ready.append(task["id"])

        if run:
            run["status"] = "running"
        self._save()

        worker = executor or self._run_dispatch
        thread = threading.Thread(target=worker, args=(ready,), daemon=True)
        thread.start()
        if wait:
            thread.join()
        return {"run_id": run["id"] if run else None,
                "dispatched": ready, "blocked": blocked,
                "concurrency": self.concurrency}

    def _run_dispatch(self, task_ids):
        """Start one subprocess per task, at most ``concurrency`` at once."""
        pending = list(task_ids)
        active = {}
        cond = threading.Condition()

        def make_done(tid):
            def done(result, error):
                with cond:
                    active.pop(tid, None)
                    self._finish(tid, result, error)
                    cond.notify_all()
            return done
        while True:
            with cond:
                while pending and len(active) < self.concurrency:
                    tid = pending.pop(0)
                    task = self._get(tid)
                    if not task:
                        continue
                    runner = self._start_task(task, make_done(tid))
                    if runner is None:
                        self._finish(tid, "", "Could not start sub-agent")
                    else:
                        active[tid] = runner
                if not active and not pending:
                    break
                cond.wait(timeout=0.25)
        self._finalize_runs()
        self._save()

    def _start_task(self, task, done):
        agent = self.registry.get_agent(task["agent_id"])
        if not agent:
            return None
        emit = lambda chunk: self._emit(task["id"], chunk)  # noqa: E731
        factory = self._runner_factory or _default_runner_factory
        try:
            runner = factory(task, agent, emit, done)
        except Exception as exc:
            print(f"[hermes] runner factory failed for {task['id']}: {exc}")
            return None
        task["status"] = "running"
        task["updated_at"] = _now()
        self._emit(task["id"], f"▶ Hermes delegating to {agent.get('name')} "
                               f"[{task['role']}]: {task['title']}\n")
        if not runner.start():
            return None
        with self._lock:
            self._runners[task["id"]] = runner
        return runner

    def _finish(self, task_id, result, error):
        with self._lock:
            task = self._get(task_id)
            if not task:
                return
            task["result"] = (result or "")[-_MAX_RESULT:]
            task["error"] = error or ""
            task["updated_at"] = _now()
            if error:
                status = "review"
            elif task["role"] in ("verify", "document"):
                status = "done"
            else:
                status = "review"
            task["status"] = status
            if task["role"] == "implement" and not error:
                task["implemented_by"] = task.get("agent_id")
            self._runners.pop(task_id, None)
        self._emit(task_id, {"type": "done", "status": task["status"],
                             "error": task["error"]})
        self._save()

    def _finalize_runs(self):
        with self._lock:
            for run in self._state["runs"]:
                if run["status"] not in ("planned", "running"):
                    continue
                tasks = [self._get(t) for t in run["task_ids"]]
                tasks = [t for t in tasks if t]
                if tasks and all(t["status"] in ("done", "review") for t in tasks):
                    run["status"] = "complete"
                    run["finished_at"] = _now()
                    run["summary"] = self._summarize(run, tasks)
                    self._remember_run(run, tasks)
            self._trim_runs()

    def _trim_runs(self):
        """Bound hermes.json growth: keep the newest finished runs, never drop
        an active (planned/running) run. Caller must hold self._lock."""
        runs = self._state["runs"]
        excess = len(runs) - _MAX_RUNS
        if excess <= 0:
            return
        keep = []
        for run in runs:
            if excess > 0 and run.get("status") not in ("planned", "running"):
                excess -= 1
                continue
            keep.append(run)
        self._state["runs"] = keep

    def _summarize(self, run, tasks):
        done = [t for t in tasks if t["status"] == "done"]
        review = [t for t in tasks if t["status"] == "review"]
        return (f"{len(done)}/{len(tasks)} tasks complete"
                + (f", {len(review)} awaiting review" if review else ""))

    def _remember_run(self, run, tasks):
        try:
            self.vault.ensure_vault()
            lines = [f"# Hermes Run - {run['goal']}", "",
                     f"- Run: `{run['id']}`",
                     f"- Status: {run['status']}",
                     f"- Summary: {run['summary']}", ""]
            for t in tasks:
                lines += [f"## {t['title']}  _({t['role']}, {t['status']})_", ""]
                lines += [f"- Agent: `{t.get('agent_id') or 'unassigned'}`", ""]
                if t.get("error"):
                    lines += [f"- Error: {t['error']}", ""]
                body = (t.get("result") or "").strip()
                if body:
                    lines += ["```", body[:4000], "```", ""]
            self.vault.write_note(f"{RUN_DIR}/{_slug(run['goal'], 'run')}-{run['id'][-6:]}.md",
                                  "\n".join(lines) + "\n")
        except Exception as exc:
            print(f"[hermes] could not write run note: {exc}")

    def cancel(self, task_id):
        with self._lock:
            runner = self._runners.pop(task_id, None)
        if not runner:
            raise HermesError("Task is not running")
        runner.stop()
        with self._lock:
            task = self._get(task_id)
            if task:
                task["status"] = "backlog"
                task["error"] = "cancelled"
                task["updated_at"] = _now()
        self._save()
        return {"cancelled": task_id}

    def run(self, goal, context="", wait=False, brain="vault"):
        """Plan then delegate a goal end to end."""
        result = self.plan(goal, context=context, brain=brain)
        run_id = result["run"]["id"]
        self.delegate(run_id=run_id, wait=wait)
        return result

    # ------------------------------------------------------------------
    # streaming
    # ------------------------------------------------------------------
    def subscribe(self, task_id, sink):
        with self._lock:
            self._subscribers.setdefault(task_id, set()).add(sink)
            task = self._get(task_id)
            replay = ""
            runner = self._runners.get(task_id)
            if runner is not None:
                replay = runner.buffer()
            elif task and task.get("result"):
                replay = task["result"]
        if replay:
            try:
                sink.send_text({"type": "replay", "data": replay})
            except Exception:
                self.unsubscribe(task_id, sink)
        self._emit(task_id, {"type": "status", "status": (task or {}).get("status")})

    def unsubscribe(self, task_id, sink):
        with self._lock:
            sinks = self._subscribers.get(task_id)
            if sinks:
                sinks.discard(sink)

    def _emit(self, task_id, message):
        with self._lock:
            sinks = list(self._subscribers.get(task_id, ()))
        for sink in sinks:
            try:
                sink.send_text(message if isinstance(message, dict)
                               else {"type": "data", "data": message})
            except Exception:
                self.unsubscribe(task_id, sink)

    # ------------------------------------------------------------------
    # second brain (Vault)
    # ------------------------------------------------------------------
    def remember(self, title, body=""):
        try:
            self.vault.ensure_vault()
            rel = f"{MEMORY_DIR}/{_slug(title, 'memory')}.md"
            text = f"# {title}\n\n{body}\n" if title else (body or "")
            return self.vault.write_note(rel, text)
        except Exception as exc:
            raise HermesError(f"Could not write memory: {exc}")

    def recall(self, query, limit=20):
        try:
            return self.vault.search(query)[:limit]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # snapshots
    # ------------------------------------------------------------------
    def _get_run(self, run_id):
        return next((r for r in self._state["runs"] if r["id"] == run_id), None)

    def list_runs(self):
        return list(reversed(self._state["runs"]))

    def snapshot(self):
        return {
            "identity": self.identity(),
            "subagents": self.subagents(),
            "board": self.board(),
            "runs": self.list_runs()[:25],
            "concurrency": self.concurrency,
        }


# ----------------------------------------------------------------------
# default runner: streamed subprocess per task
# ----------------------------------------------------------------------
class SubprocessRunner:
    """Runs one agent CLI task, streaming stdout/stderr to subscribers."""

    def __init__(self, argv, cwd, env, on_output, on_done, timeout=600):
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.on_output = on_output
        self.on_done = on_done
        self.timeout = timeout
        self.proc = None
        self._lock = threading.Lock()
        self._buffer = []
        self._thread = None
        self._stop = threading.Event()
        self._done_called = False

    def buffer(self):
        with self._lock:
            return "".join(self._buffer)

    def start(self):
        try:
            self.proc = subprocess.Popen(
                self.argv, cwd=self.cwd, env=self.env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1)
        except Exception as exc:
            self.on_done("", f"Failed to launch sub-agent: {exc}")
            return False
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass

    def _pump(self):
        deadline = time.time() + self.timeout
        try:
            for line in self.proc.stdout:
                if self._stop.is_set() or time.time() > deadline:
                    self.stop()
                    break
                with self._lock:
                    self._buffer.append(line)
                    if len(self._buffer) > _MAX_BUFFER_LINES:
                        del self._buffer[0:len(self._buffer) - _MAX_BUFFER_LINES]
                self.on_output(line)
        except Exception:
            pass
        try:
            rc = self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
                rc = self.proc.wait(timeout=5)
            except Exception:
                rc = -1
        if self._done_called:
            return
        self._done_called = True
        result = self.buffer()
        if self._stop.is_set():
            self.on_done(result, "cancelled")
        elif rc != 0:
            self.on_done(result, f"Sub-agent exited with code {rc}")
        else:
            self.on_done(result, "")


def _default_runner_factory(task, agent, emit, done):
    argv = agent_registry.one_shot_argv(agent, _task_prompt(task))
    if not argv:
        raise HermesError("No runnable CLI for " + str(task.get("agent_id")))
    return SubprocessRunner(argv, config.DIRECTORY, agent_registry.get_augmented_env(),
                            emit, done)


def _task_prompt(task):
    role = ROLES.get(task.get("role"), {}).get("label", task.get("role"))
    return (f"[Hermes task | role: {role}]\n{task['title']}\n\n{task.get('detail') or ''}"
            "\n\nComplete this task and print a concise result.")


def _extract_json_array(text):
    """Pull the first JSON array out of a CLI's free-form output."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            candidate = text[start:end + 1]
    if not candidate:
        return None
    try:
        data = json.loads(candidate)
    except Exception:
        return None
    return data if isinstance(data, list) else None
