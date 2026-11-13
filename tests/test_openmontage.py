"""Focused tests for the isolated OpenMontage lifecycle boundary."""

import os
import tempfile
import unittest
from unittest import mock

from backend import openmontage


class OpenMontageSupervisorTests(unittest.TestCase):
    def tearDown(self):
        openmontage.stop()

    def test_status_is_safe_when_checkout_is_missing(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(
            os.environ, {"DENNIS_OPENMONTAGE_ROOT": os.path.join(root, "missing")}, clear=False
        ), mock.patch.object(openmontage, "_health", return_value=None):
            state = openmontage.status()
        self.assertFalse(state["running"])
        self.assertFalse(state["configured"])
        self.assertNotIn("api_key", state)
        self.assertNotIn("secret", state)

    def test_redacts_credential_shaped_environment_values(self):
        with mock.patch.dict(os.environ, {"OPENMONTAGE_TEST_API_KEY": "mock-secret-value"}, clear=False):
            safe = openmontage._redact("provider returned mock-secret-value")
        self.assertNotIn("mock-secret-value", safe)
        self.assertIn("[REDACTED]", safe)

    def test_start_uses_native_backlot_command(self):
        class FakeProcess:
            stdout = []
            pid = 1234

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 0

            def terminate(self):
                return None

            def kill(self):
                return None

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as projects:
            os.makedirs(os.path.join(root, ".venv", "bin"))
            interpreter = os.path.join(root, ".venv", "bin", "python")
            with open(interpreter, "w", encoding="utf-8") as handle:
                handle.write("mock")
            environment = {
                "DENNIS_OPENMONTAGE_ROOT": root,
                "DENNIS_OPENMONTAGE_PORT": "4761",
                "OPENMONTAGE_PROJECTS_DIR": projects,
            }
            with mock.patch.dict(os.environ, environment, clear=False), \
                    mock.patch.object(openmontage, "_health", side_effect=[None, None, {"ok": True}, {"ok": True}]), \
                    mock.patch.object(openmontage.subprocess, "Popen", return_value=FakeProcess()) as popen, \
                    mock.patch.object(openmontage.time, "sleep"):
                state = openmontage.start()

            command = popen.call_args.args[0]
            self.assertEqual(os.path.realpath(command[0]), os.path.realpath(interpreter))
            self.assertEqual(command[1:3], ["-m", "backlot"])
            self.assertEqual(command[3:5], ["serve", "--port"])
            self.assertEqual(command[5], "4761")
            self.assertTrue(state["running"])


if __name__ == "__main__":
    unittest.main()
