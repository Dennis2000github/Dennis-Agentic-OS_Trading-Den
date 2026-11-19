"""Model source for Cursor Agent.

``cursor-agent --list-models`` prints a plain ``id - Display Name`` list and
exits, so the picker no longer has to be driven interactively. The old
interactive path is kept as a fallback for older builds without the flag, and
it fails honestly when Cursor stops at its Workspace Trust prompt -- that
prompt blocks every later keystroke, including ``/model``, so the picker can
never be read until the folder is trusted once.
"""

import re

from .base import ModelListError, ModelSource, strip_ansi

# ``Available models`` header, then rows like ``auto - Auto (default)``.
_LIST_HEADER = "availablemodels"
_LIST_ENTRY_RE = re.compile(r"^(\S+)\s+-\s+(.+)$")
_NOISE = ("model", "select", "login", "sign in", "press", "esc")

# Conditions that mean the picker could not be reached; surfaced verbatim so
# the user can act on them instead of seeing a generic parse failure.
_BLOCKERS = (
    ("trustthisworkspace", "Cursor is asking to trust this workspace. Open Cursor Agent once here and choose [a] Trust this workspace, then Retry."),
    ("trustthisfolder", "Cursor is asking to trust this workspace. Open Cursor Agent once here and choose [a] Trust this workspace, then Retry."),
    ("doyoutrust", "Cursor is asking to trust this workspace. Open Cursor Agent once here and choose [a] Trust this workspace, then Retry."),
    ("workspacetrust", "Cursor is asking to trust this workspace. Open Cursor Agent once here and choose [a] Trust this workspace, then Retry."),
    ("pleaselogin", "Cursor is not logged in. Run `cursor-agent login`, sign in, then Retry."),
    ("notloggedin", "Cursor is not logged in. Run `cursor-agent login`, sign in, then Retry."),
)


def _fold(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _parse_list_output(text):
    models = []
    seen = set()
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned or _fold(cleaned) == _LIST_HEADER:
            continue
        match = _LIST_ENTRY_RE.match(cleaned)
        if not match:
            continue
        ident = match.group(1).strip().strip("`[]")
        name = match.group(2).strip()
        if not ident or ident.lower() in seen or len(ident) > 120:
            continue
        seen.add(ident.lower())
        provider = ident.split("/", 1)[0] if "/" in ident else "Cursor"
        models.append(ModelSource._norm(
            ident, name=name or ident, provider=provider))
    return models


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
        command = self._command(ctx)

        # Preferred: the non-interactive list command.
        rc, out, err = ctx.exec([command, "--list-models"], cwd=cwd, timeout=30)
        if rc == 0:
            models = _parse_list_output(out)
            if models:
                return models
            # A zero-row list falls through to the picker so a banner-only or
            # changed format still gets a chance, and so the failure below
            # reports the real blocker instead of a silent empty list.

        if not hasattr(ctx, "capture_interactive"):
            detail = (err or out or "").strip()[:300]
            raise ModelListError(
                "Cursor reported no models and interactive capture is "
                "unavailable." + (": " + detail if detail else ""))

        try:
            raw = ctx.capture_interactive(
                [command], cwd=cwd, send="/model\r", settle=3.0,
                timeout=30, ready_for=("cursor", "model"), ready_timeout=15.0,
            )
        except Exception as exc:
            raise ModelListError(f"Failed to open Cursor's /model picker: {exc}")

        text = strip_ansi(raw)
        folded = _fold(text)
        for needle, message in _BLOCKERS:
            if needle in folded:
                raise ModelListError(message)

        models = []
        seen = set()
        for line in text.splitlines():
            cleaned = line.strip().strip("│╭╰─┃|").strip()
            match = re.match(
                r"^\s*(?:(?:[>❯●•]\s*)?\d+[.)]\s*|[>❯●•]\s*)(\S+)", cleaned)
            if not match:
                continue
            ident = match.group(1).strip().strip("`[]")
            folded_ident = ident.lower()
            if not ident or any(word in folded_ident for word in _NOISE):
                continue
            if ident in seen or len(ident) > 120:
                continue
            seen.add(ident)
            provider = ident.split("/", 1)[0] if "/" in ident else "Cursor"
            models.append(self._norm(ident, name=ident, provider=provider))
        if not models:
            raise ModelListError("Could not read Cursor's /model picker")
        return models
