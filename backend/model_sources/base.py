"""Shared model-source interface and helpers."""

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")          # CSI sequences
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")   # OSC (title etc.)
_ESC_RE = re.compile(r"\x1b[()#][0-9A-Za-z]|\x1b[=><78cDEHMZ]")  # lone escapes


class ModelListError(Exception):
    """Raised when a CLI's model list cannot be loaded or parsed.

    The UI turns this into "Couldn't load models for <agent>" plus a Retry
    action. Callers must never substitute a fabricated list.
    """


def strip_ansi(text):
    text = _OSC_RE.sub("", text or "")
    text = _ANSI_RE.sub("", text)
    return _ESC_RE.sub("", text)


class ModelSource:
    """One implementation per agent CLI.

    ``ctx`` is an execution context supplied by the session sandbox. It must
    expose::

        ctx.exec(argv, cwd=..., timeout=...) -> (returncode, stdout, stderr)
        ctx.capture_interactive(argv, cwd=..., send=..., settle=..., timeout=...)
            -> str   # raw PTY output, for CLIs with an interactive picker only
    """

    agent_id = ""

    def list_models(self, ctx, cwd=None):
        raise NotImplementedError

    def current_model(self, session):
        """Best-known current model for a session.

        Read back from what the backend last confirmed with the CLI. CLIs that
        expose no query command still only ever report the model the backend
        actually launched with, never a value the UI sent that failed.
        """
        return (session or {}).get("model_id")

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _norm(identifier, name=None, description="", is_default=False,
              available=True, reason=None, provider=""):
        """Normalise one model row.

        ``provider`` is the provider the CLI itself associates with the model
        (OpenCode ids are ``provider/model``; Hermes lists one provider's
        cache). It is empty when the CLI exposes no provider information, so
        the UI can offer an explicit "All providers" entry instead of guessing.
        """
        return {
            "id": identifier,
            "name": name or identifier,
            "description": description or "",
            "is_default": bool(is_default),
            "available": bool(available),
            "reason": reason,
            "provider": (provider or "").strip(),
        }
