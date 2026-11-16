"""Universal Skill Registry — acceptance tests (directive Phase 9).

Covers the 12 acceptance criteria at the service layer:

 1. one skill installed once           test_install_once_no_duplication
 2. appears in the central registry     test_search_and_resolve
 3. multiple agents discover it         test_compatibility_matrix
 4. two agents load the same skill      test_two_agents_load_same_canonical
 5. source not duplicated               test_install_once_no_duplication
 6. incompatible agents rejected        test_incompatible_agents_rejected
 7. dependencies checked                test_dependency_gate
 8. execution via existing manager      test_execute_approval_then_run
 9. artifacts return to workspace       test_validate_output_artifact
10. updates without breaking refs       test_update_keeps_references
11. disable blocks invocations          test_disabled_skill_blocks_load
12. existing workflows keep working     (run alongside tests.test_backend etc.)

Plus the security spine: traversal, secret refusal, approval gating,
cancel/release, honest instruction-only levels, external source lifecycle.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import skill_access
from backend import skill_capabilities
from backend import skill_execution
from backend import skill_registry
from backend import skill_sources_registry
from backend import hermes


SKILL_MD = """---
name: demo-skill
description: A deterministic demo skill for the universal registry tests.
version: 1.0.0
---

# Demo skill

Does nothing dangerous. Uses only the standard interpreter.

## Steps

1. Run `scripts/run.py`.
"""

PROBE_PY = """import os
print("PROBE_OK skill=%s ctx=%s" % (os.environ.get("SKILL_ID"), os.environ.get("SKILL_CONTEXT_ID")))
"""


class _ApprovingStore:
    def create(self, tool, arguments, preview):
        return {"id": "apv_test", "tool": tool, "arguments": arguments,
                "preview": preview, "status": "pending"}

    def get(self, approval_id):
        return {"id": approval_id, "status": "approved"}

    def update(self, approval_id, **fields):
        return {"id": approval_id, "status": "approved"}


class SkillAccessTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-skill-access-")
        self.vault_root = os.path.join(self.tmp, "vault", "Skills")
        self.demo = os.path.join(self.vault_root, "demo-skill")
        os.makedirs(os.path.join(self.demo, "scripts"))
        with open(os.path.join(self.demo, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(SKILL_MD)
        with open(os.path.join(self.demo, "scripts", "run.py"), "w", encoding="utf-8") as fh:
            fh.write(PROBE_PY)

        self.reg_file = os.path.join(self.tmp, "skill_registry.json")
        self.sources_dir = os.path.join(self.tmp, "skill-sources")
        os.makedirs(self.sources_dir, exist_ok=True)

        self._patches = [
            mock.patch.object(skill_access, "VAULT_SKILLS_ROOT", lambda: self.vault_root),
            mock.patch.object(skill_registry, "REGISTRY_FILE", self.reg_file),
            # Confine the external-source store to the sandbox too, so a
            # lifecycle test can never register a skill into the real Vault
            # (other suites count vault/Skills contents).
            mock.patch.object(skill_access.sources_registry, "SOURCES_DIR", self.sources_dir),
            mock.patch.object(skill_access.sources_registry, "_STATE_FILE",
                              os.path.join(self.sources_dir, "sources.json")),
            # vault paths are Vault-relative ("Skills/<name>/SKILL.md"); the
            # test Vault root is <tmp>/vault.
            mock.patch("backend.vault.resolve",
                       side_effect=lambda rel, must_exist=False:
                           os.path.join(self.tmp, "vault",
                                        str(rel).replace("\\", "/"))),
            mock.patch.object(hermes.HermesOrchestrator, "_cli_plan",
                              side_effect=RuntimeError("no CLI in tests")),
        ]
        for p in self._patches:
            p.start()
        self._old_approvals = skill_execution._APPROVALS
        skill_execution.set_approval_store(_ApprovingStore())

    def tearDown(self):
        skill_execution.set_approval_store(self._old_approvals)
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 2. registry discovery ------------------------------------------
    def test_search_and_resolve(self):
        found = skill_access.search_skill(query="demo deterministic")
        self.assertGreaterEqual(found["count"], 1)
        self.assertIn("demo-skill", [s["name"] for s in found["skills"]])
        resolved = skill_access.resolve_skill("skill://demo-skill")
        self.assertTrue(resolved["installed"])
        self.assertTrue(resolved["integrity"]["path_matches"])
        self.assertEqual(resolved["integrity"]["skill_md_size"], len(SKILL_MD.encode()))

    def test_vault_skill_is_indexed_without_migration(self):
        # No explicit register() call in setUp: search indexes the Vault.
        found = skill_access.search_skill(query="demo")
        self.assertIn("demo-skill", [s["name"] for s in found["skills"]])

    # -- 3/6. compatibility matrix --------------------------------------
    def _require_script(self):
        skill_registry.set_meta("demo-skill", {"execution_requirements": ["script"]})

    def test_compatibility_matrix(self):
        self._require_script()
        matrix = skill_access.compatibility_matrix("skill://demo-skill")
        levels = {row["agent_id"]: row["level"] for row in matrix["agents"]}
        self.assertEqual(levels["claude-code"], "executable")
        self.assertEqual(levels["deepseek-harness"], "instructions")
        unknown = skill_access.check_agent_compatibility("skill://demo-skill", "no-such-agent")
        self.assertEqual(unknown["level"], "none")

    def test_incompatible_agents_rejected(self):
        self._require_script()
        diag = skill_access.check_agent_compatibility("skill://demo-skill", "deepseek-harness")
        self.assertEqual(diag["level"], "instructions")
        self.assertIn("script", diag["requirements"])
        # The registry refuses to claim execution for instruction-only agents.
        self.assertFalse(skill_capabilities.can_execute("deepseek-harness"))

    # -- 4. two agents, one canonical source -----------------------------
    def test_two_agents_load_same_canonical(self):
        first = skill_access.load_skill("skill://demo-skill", agent_id="claude-code")
        second = skill_access.load_skill("skill://demo-skill", agent_id="cursor")
        self.assertEqual(first["skill_md"], second["skill_md"])
        self.assertNotEqual(first["context_id"], second["context_id"])
        skill_access.release_skill_context(first["context_id"])
        skill_access.release_skill_context(second["context_id"])

    def test_install_once_no_duplication(self):
        skill_sources_registry.register_source("demo-skill", "https://example.invalid/demo")
        canonical = skill_sources_registry.canonical_path("demo-skill")
        shutil.copytree(self.demo, canonical)
        agents_root = os.path.join(self.tmp, "agents")
        links = {}
        import backend.plugins as plugins_mod
        original = plugins_mod.AGENT_SKILL_DIRS
        plugins_mod.AGENT_SKILL_DIRS = {
            "claude-code": os.path.join(agents_root, "claude"),
            "cursor": os.path.join(agents_root, "cursor"),
        }
        try:
            for agent in ("claude-code", "cursor"):
                out = skill_sources_registry.install_for_agent("demo-skill", agent)
                self.assertTrue(os.path.islink(out["target"]))
                links[agent] = out["target"]
        finally:
            plugins_mod.AGENT_SKILL_DIRS = original
        self.assertEqual(os.path.realpath(links["claude-code"]),
                         os.path.realpath(links["cursor"]))
        self.assertEqual(os.path.realpath(links["claude-code"]),
                         os.path.realpath(canonical))
        # exactly one canonical tree, not two copies
        self.assertEqual(sorted(os.listdir(canonical)), ["SKILL.md", "scripts"])

    # -- 7. dependency gate ----------------------------------------------
    def test_dependency_gate(self):
        deps = skill_access.check_dependencies("skill://demo-skill")
        self.assertTrue(deps["ok"])
        self.assertEqual(deps["missing_mandatory"], [])
        # a mandatory binary that does not exist blocks execution
        skill_registry.set_meta("demo-skill", {
            "runtime_dependencies": [{"kind": "mandatory", "binary": "definitely-not-real-xyz"}],
        })
        deps = skill_access.check_dependencies("skill://demo-skill")
        self.assertFalse(deps["ok"])
        self.assertIn("definitely-not-real-xyz", deps["missing_mandatory"])

    # -- 8. execution through the existing spine -------------------------
    def test_execute_approval_then_run(self):
        ctx = skill_access.load_skill("skill://demo-skill", agent_id="claude-code")
        workspace = os.path.join(self.tmp, "workspace")
        os.makedirs(workspace, exist_ok=True)
        first = skill_access.execute_skill(ctx["context_id"], command="scripts/run.py",
                                           workspace=workspace)
        self.assertEqual(first["status"], "approval_required")
        self.assertEqual(first["approval"]["status"], "pending")
        second = skill_access.execute_skill(ctx["context_id"], command="scripts/run.py",
                                            workspace=workspace,
                                            approval_id=first["approval"]["id"])
        self.assertEqual(second["status"], "ok")
        self.assertEqual(second["execution"]["exit_code"], 0)
        out = [l["line"] for l in second["execution"]["log"] if "PROBE_OK" in l["line"]]
        self.assertEqual(len(out), 1)
        self.assertIn("skill=demo-skill", out[0])
        skill_access.release_skill_context(ctx["context_id"])

    def test_execute_rejects_unapproved_retry(self):
        # The second call must carry the approval id; a bare retry stays gated.
        ctx = skill_access.load_skill("skill://demo-skill", agent_id="claude-code")
        workspace = os.path.join(self.tmp, "workspace")
        os.makedirs(workspace, exist_ok=True)
        first = skill_access.execute_skill(ctx["context_id"], command="scripts/run.py",
                                           workspace=workspace)
        with self.assertRaises(skill_access.SkillAccessError) as ctx2:
            skill_access.execute_skill(ctx["context_id"], command="scripts/run.py",
                                       workspace=workspace)
        self.assertEqual(ctx2.exception.code, "approval_pending")
        skill_access.release_skill_context(ctx["context_id"])

    def test_instructions_only_is_honest(self):
        ctx = skill_access.load_skill("skill://demo-skill", agent_id="deepseek-harness")
        out = skill_access.execute_skill(ctx["context_id"])
        self.assertEqual(out["status"], "instructions_only")
        self.assertNotIn("execution", out)

    # -- 9. output validation --------------------------------------------
    def test_validate_output_artifact(self):
        ctx = skill_access.load_skill("skill://demo-skill", agent_id="claude-code")
        workspace = os.path.join(self.tmp, "workspace")
        os.makedirs(workspace, exist_ok=True)
        artifact = os.path.join(workspace, "out.json")
        with open(artifact, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"mesh": True}))
        report = skill_access.validate_output(ctx["context_id"], artifact_path=artifact,
                                              contract={"required_keys": ["mesh"]},
                                              workspace=workspace)
        self.assertTrue(report["ok"])
        self.assertEqual(report["artifact"]["path"], artifact)
        # a credential in the artifact quarantines it
        with open(artifact, "w", encoding="utf-8") as fh:
            fh.write("api_key = sk-abcdefghijklmnopqrstuvwxyz123456")
        report = skill_access.validate_output(ctx["context_id"], artifact_path=artifact,
                                              workspace=workspace)
        self.assertFalse(report["ok"])
        self.assertTrue(report["quarantined"])
        skill_access.release_skill_context(ctx["context_id"])

    # -- 10/11. update + disable ----------------------------------------
    def test_update_keeps_references(self):
        first = skill_registry.register("demo-skill", "Skills/demo-skill/SKILL.md")
        v1 = first["current_version"]
        second = skill_registry.register("demo-skill", "Skills/demo-skill/SKILL.md",
                                         version="1.1.0")
        self.assertEqual(second["current_version"], "1.1.0")
        # the id/path references stay valid across the update
        resolved = skill_access.resolve_skill("skill://demo-skill")
        self.assertEqual(resolved["id"], "skill://demo-skill")
        self.assertEqual(resolved["version"], "1.1.0")
        self.assertTrue(resolved["integrity"]["path_matches"])

    def test_disabled_skill_blocks_load(self):
        skill_access._ensure_vault_indexed()  # register the fixture skill
        skill_registry.set_enabled("demo-skill", False)
        with self.assertRaises(skill_access.SkillAccessError) as ctx:
            skill_access.load_skill("skill://demo-skill")
        self.assertEqual(ctx.exception.code, "disabled")
        skill_registry.set_enabled("demo-skill", True)
        ok = skill_access.resolve_skill("skill://demo-skill")
        self.assertTrue(ok["enabled"])

    def test_remove_keeps_vault_source(self):
        skill_access._ensure_vault_indexed()
        result = skill_registry.remove("demo-skill")
        self.assertTrue(result["removed"])
        self.assertTrue(result["vault_source_kept"])
        self.assertTrue(os.path.isfile(os.path.join(self.demo, "SKILL.md")))
        # Re-indexing would re-register from the still-present Vault source,
        # which is the intended single-canonical-source behaviour; the
        # registration itself (versions, sources) is gone.
        self.assertIsNone(skill_registry.get("demo-skill"))

    # -- security spine ---------------------------------------------------
    def test_path_traversal_refused(self):
        with self.assertRaises(skill_execution.SkillExecutionError) as ctx:
            skill_execution.safe_skill_path(self.demo, "../../etc/passwd")
        self.assertEqual(ctx.exception.code, "path_traversal")

    def test_secret_in_skill_refused(self):
        ctx = skill_access.load_skill("skill://demo-skill", agent_id="claude-code")
        skill_access._CONTEXTS[ctx["context_id"]]["skill_md"] = \
            "ok text\nANTHROPIC_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456\n"
        with self.assertRaises(skill_access.SkillAccessError) as ctx2:
            skill_access.execute_skill(ctx["context_id"], command="scripts/run.py")
        self.assertEqual(ctx2.exception.code, "secret_in_skill")
        skill_access.release_skill_context(ctx["context_id"])

    def test_missing_command_is_not_claimed_as_executed(self):
        ctx = skill_access.load_skill("skill://demo-skill", agent_id="claude-code")
        with self.assertRaises(skill_access.SkillAccessError) as ctx2:
            skill_access.execute_skill(ctx["context_id"], command="nope.sh")
        self.assertEqual(ctx2.exception.code, "command_not_found")

    def test_cancel_and_audit(self):
        ctx = skill_access.load_skill("skill://demo-skill", agent_id="claude-code")
        cancel = skill_access.cancel_execution(ctx["context_id"])
        self.assertFalse(cancel["cancel_requested"])
        released = skill_access.release_skill_context(ctx["context_id"])
        self.assertEqual(released["status"], "released")
        self.assertTrue(len(skill_access.audit_log()) >= 1)

    # -- external skill lifecycle (img2threejs pattern) -------------------
    def test_external_skill_lifecycle(self):
        skill_access.install_external_skill(
            "img2threejs", "https://github.com/img2threejs/img2threejs",
            meta={"version": "2.0.1", "capability_tags": ["image-to-3d"],
                  "execution_requirements": ["script"]})
        resolved = skill_access.resolve_skill("skill://img2threejs")
        self.assertTrue(resolved["installed"] is False)  # not fetched yet
        # fetch gate: the operator action is reported, never auto-run
        fetched = skill_access.sources_registry.source_record("img2threejs")
        self.assertFalse(fetched["fetched"])
        # after the operator fetches + stamps it, validation goes green
        canonical = skill_access.sources_registry.canonical_path("img2threejs")
        os.makedirs(canonical, exist_ok=True)
        with open(os.path.join(canonical, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: img2threejs\ndescription: Image to procedural Three.js.\nversion: 2.0.1\n---\n\n# img2threejs\n")
        os.makedirs(os.path.join(self.vault_root, "img2threejs"), exist_ok=True)
        with open(os.path.join(self.vault_root, "img2threejs", "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: img2threejs\ndescription: Image to procedural Three.js.\nversion: 2.0.1\n---\n\n# img2threejs\n")
        skill_access.sources_registry.mark_fetched("img2threejs", commit="abc1234")
        report = skill_access.validate_skill("skill://img2threejs")
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["validation_status"], "validated")

    def test_external_skill_lifecycle_full(self):
        # Same lifecycle but the Vault copy is created before validation.
        skill_access.install_external_skill(
            "external-demo", "https://example.invalid/external-demo",
            meta={"version": "0.1.0", "execution_requirements": ["script"]})
        vault_copy = os.path.join(self.vault_root, "external-demo")
        os.makedirs(vault_copy, exist_ok=True)
        with open(os.path.join(vault_copy, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: external-demo\ndescription: External demo skill.\n---\n\n# external-demo\n")
        canonical = skill_access.sources_registry.canonical_path("external-demo")
        os.makedirs(canonical, exist_ok=True)
        shutil.copy(os.path.join(vault_copy, "SKILL.md"), os.path.join(canonical, "SKILL.md"))
        skill_access.sources_registry.mark_fetched("external-demo", commit="feedface")
        report = skill_access.validate_skill("skill://external-demo")
        self.assertEqual(report["validation_status"], "validated")

    def test_execute_uses_existing_execution_manager(self):
        # Criterion 8: execution goes through the existing PTY execution
        # spine (skill_execution.SkillProcess), never a new ad-hoc runner.
        ctx = skill_access.load_skill("skill://demo-skill", agent_id="claude-code")
        workspace = os.path.join(self.tmp, "workspace")
        os.makedirs(workspace, exist_ok=True)
        with mock.patch.object(skill_access, "execenv") as fake_exec:
            fake_exec.scan_for_secrets.return_value = []
            fake_exec.check_binary.return_value = "/usr/bin/python3"
            fake_exec.skill_env.side_effect = lambda extra=None: dict(extra or {})
            fake_exec.SkillProcess.return_value.snapshot.return_value = {
                "status": "done", "exit_code": 0, "log": [], "argv": ["x"],
                "cwd": "/tmp", "started_at": 0, "finished_at": 1, "duration_s": 1,
                "log_lines": 0}
            result = skill_access.execute_skill(ctx["context_id"], command="scripts/run.py",
                                                workspace=workspace, approval_id="apv_test")
            self.assertEqual(result["status"], "ok")
            # the process was created with a bounded env + workspace cwd
            call_kwargs = fake_exec.SkillProcess.call_args
            self.assertEqual(call_kwargs.args[0][0], "/usr/bin/python3")
            self.assertTrue(call_kwargs.args[1].endswith("workspace") or
                            call_kwargs.args[1].endswith("data"))
        skill_access.release_skill_context(ctx["context_id"])

    # -- Hermes discovery hook (Phase 5) ----------------------------------
    def test_hermes_plan_sees_registry_skills(self):
        orchestrator = hermes.HermesOrchestrator(
            path=os.path.join(self.tmp, "hermes.json"), vault_module=None)
        block = orchestrator._skill_context("demo deterministic skill")
        self.assertIn("demo-skill", block)
        none = orchestrator._skill_context("zzzqqq-nothing")
        self.assertEqual(none, "")


if __name__ == "__main__":
    unittest.main()
