"""E2E: session_manager starts opencode with a model; footer must show it."""
import os, sys, time, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend import agent_registry, session_manager

project = tempfile.mkdtemp(prefix="oc-e2e-")
with open(os.path.join(project, "a.txt"), "w") as fh:
    fh.write("hello\n")

mgr = session_manager.SessionManager()
record = {"id": "e2e-oc-1", "agent_id": "opencode", "project_path": project,
          "model_id": None, "cli_session_id": None}
sess = mgr.get_or_create(record)
agent = agent_registry.get_agent("opencode")
ok, info = sess.start(agent, "groq/qwen/qwen3.8-27b", rows=40, cols=160)
print("start ok:", ok, info)
print("model launch:", sess._model_launch and sess._model_launch.env)

# Standalone boot is slow; poll the screen for the footer model line.
deadline = time.time() + 75
seen = ""
while time.time() < deadline:
    time.sleep(4)
    seen = sess._screen.snapshot()
    if "groq" in seen.lower() or "qwen" in seen.lower():
        break
lines = [ln.strip() for ln in seen.splitlines() if ln.strip()]
print("\n".join(lines[-10:]))
found = any(("qwen" in ln.lower() or "groq" in ln.lower()) for ln in lines)
print("\nMODEL SHOWN IN TUI:", found)
sess.stop()
mgr.stop_all()
