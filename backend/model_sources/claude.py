"""Model source for Claude Code.

Claude Code exposes no non-interactive model-list command; models are only
shown by the interactive ``/model`` picker. We therefore start a *short-lived,
separate* PTY in the same sandbox/project folder, wait for the TUI to render,
send ``/model``, capture the picker, parse it, and close it. The user's live
session is never touched.

Parsing notes (this is a TUI, not a machine interface):

* Claude's banner takes several seconds to appear. Sending ``/model`` too early
  means the keystrokes feed the *command palette* instead of executing the
  command, so we wait for the "auto mode on" status line first.
* The renderer moves the cursor by column instead of printing spaces, so the
  sanitised text collapses words (``OpusOpus5.5``, ````
  with the checkmark glued on). We therefore only trust the *numbered* picker
  rows, take the leading run of ``[A-Za-z0-9._-]`` as the token, strip any
  glued-on suffix, and collapse a duplicated name prefix (``OpusOpus5.5`` ->
  alias ``opus``, display ``Opus 5.5``).
* Only rows inside the ``Select model`` section are accepted, so banner/footer
  text can never be mistaken for a model. If nothing matches we raise so the UI
  can offer Retry -- never a fabricated list.
"""

import re

from .base import ModelListError, ModelSource, strip_ansi

# The picker body only starts after this heading (whitespace-folded).
_SECTION_MARKER = "selectmodel"
# Numbered picker row, e.g. "  2. Opus...", "> 1. Default...", "❯6. 9router...".
_ENTRY_RE = re.compile(r"^\s*(?:[>\u276f\u25cf\u2022]\s*)?(\d+)\s*[.)]\s*(\S+)")
# First character that is *not* part of a model token.
_NON_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]")
# Readiness needle: the status line Claude prints once the prompt is live.
_READY = ("automodeon", "shift+tabtocycle")

# Conditions that mean the picker could not be reached; surfaced verbatim so
# the user can act on them instead of seeing a generic failure.
_BLOCKERS = (
    ("trust this folder", "Claude is asking to trust this project folder. "
                          "Open Claude once in it, accept trust, then Retry."),
    ("do you trust", "Claude is asking to trust this project folder. "
                     "Open Claude once in it, accept trust, then Retry."),
    ("please log in", "Claude is not logged in. Run `claude` and sign in, then Retry."),
    ("not logged in", "Claude is not logged in. Run `claude` and sign in, then Retry."),
    ("authentication", "Claude needs authentication. Run `claude`, sign in, then Retry."),
)


def _fold(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _collapse(token):
    """Split a space-collapsed token into (alias, display name).

    ``OpusOpus5.5`` -> ``("opus", "Opus 5.5")``; a token with no doubled prefix
    (``freellmapi``) is returned unchanged (alias lower-cased).
    """
    n = len(token)
    for k in range(1, n // 2 + 1):
        if token[:k] == token[k:2 * k]:
            base = token[:k]
            rest = token[2 * k:]
            return base.lower(), (base + " " + rest if rest else base)
    return token.lower(), token


class ClaudeModelSource(ModelSource):
    agent_id = "claude-code"

    def list_models(self, ctx, cwd=None):
        if not hasattr(ctx, "capture_interactive"):
            raise ModelListError("Interactive model capture unavailable")
        try:
            raw = ctx.capture_interactive(
                ["claude"],
                cwd=cwd,
                send="/model\r",
                settle=5.0,
                timeout=30,
                ready_for=_READY,
                ready_timeout=15.0,
            )
        except Exception as exc:
            raise ModelListError(f"Failed to open /model picker: {exc}")

        text = strip_ansi(raw)
        folded = _fold(text)
        for needle, message in _BLOCKERS:
            if _fold(needle) in folded:
                raise ModelListError(message)

        models = []
        seen = set()
        in_section = False
        for line in text.splitlines():
            cleaned = line.strip().strip("│╭╰─┃|").strip()
            if not in_section:
                if _SECTION_MARKER in _fold(cleaned):
                    in_section = True
                continue
            m = _ENTRY_RE.match(cleaned)
            if not m:
                continue
            token = _NON_TOKEN_RE.split(m.group(2), 1)[0]
            if not token:
                continue
            mid, name = _collapse(token)
            if mid in seen:
                continue
            seen.add(mid)
            # FreeLLMAPI registers itself as a model alias in the Claude picker.
            # Preserve that source in the provider dropdown instead of labeling it
            # as Anthropic, while keeping native Claude models unchanged.
            provider = "FreeLLMAPI" if mid == "freellmapi" else "Anthropic"
            models.append(self._norm(mid, name=name, provider=provider))

        if not models:
            raise ModelListError("Could not read Claude's /model picker")
        return models
