"""Model source for Cursor Agent's interactive ``/model`` picker."""

import re

from .base import ModelListError, ModelSource, strip_ansi

# Require a picker marker so the Cursor startup banner is never exposed as a
# model. Cursor versions have rendered entries as arrows, bullets, or numbers.
_ENTRY_RE = re.compile(
    r"^\s*(?:(?:[>❯●•]\s*)?\d+[.)]\s*|[>❯●•]\s*)(\S+)"
)
_NOISE = ("model", "select", "login", "sign in", "press", "esc")


class CursorModelSource(ModelSource):
    agent_id = "cursor"

    @staticmethod
    def _command(ctx):
        # Cursor has used both `cursor-agent` and `agent` entry points. Probe
        # the documented name first, then support the newer short executable.
        for command in ("cursor-agent", "agent"):
            rc, _, _ = ctx.exec([command, "--version"], timeout=5)
            if rc == 0:
                return command
        return "cursor-agent"

    def list_models(self, ctx, cwd=None):
        if not hasattr(ctx, "capture_interactive"):
            raise ModelListError("Interactive model capture unavailable")
        command = self._command(ctx)
        try:
            raw = ctx.capture_interactive(
                [command], cwd=cwd, send="/model\r", settle=3.0,
                timeout=30, ready_for=("cursor", "model"), ready_timeout=15.0,
            )
        except Exception as exc:
            raise ModelListError(f"Failed to open Cursor's /model picker: {exc}")

        models = []
        seen = set()
        for line in strip_ansi(raw).splitlines():
            cleaned = line.strip().strip("│╭╰─┃|").strip()
            match = _ENTRY_RE.match(cleaned)
            if not match:
                continue
            ident = match.group(1).strip().strip("`[]")
            folded = ident.lower()
            if not ident or any(word in folded for word in _NOISE):
                continue
            if ident in seen or len(ident) > 120:
                continue
            seen.add(ident)
            provider = ident.split("/", 1)[0] if "/" in ident else "Cursor"
            models.append(self._norm(ident, name=ident, provider=provider))
        if not models:
            raise ModelListError("Could not read Cursor's /model picker")
        return models
