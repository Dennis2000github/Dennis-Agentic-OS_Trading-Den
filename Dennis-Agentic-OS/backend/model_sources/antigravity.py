"""Model source for Antigravity (``agy models``).

Output is ``<id>\t<Display Name>`` per line, preceded by a status line.
"""

from .base import ModelListError, ModelSource, strip_ansi

_STATUS_PREFIXES = ("fetching", "loading", "available models")


class AntigravityModelSource(ModelSource):
    agent_id = "antigravity"

    def list_models(self, ctx, cwd=None):
        rc, out, err = ctx.exec(["agy", "models"], cwd=cwd, timeout=30)
        if rc != 0:
            raise ModelListError((err or out or "agy models failed").strip()[:300])

        models = []
        seen = set()
        for raw in strip_ansi(out).splitlines():
            line = raw.strip()
            if not line:
                continue
            low = line.lower()
            if low.startswith(_STATUS_PREFIXES):
                continue
            if "\t" in line:
                ident, name = line.split("\t", 1)
            else:
                parts = line.split(None, 1)
                ident = parts[0]
                name = parts[1] if len(parts) > 1 else parts[0]
            ident = ident.strip()
            if not ident or ident in seen:
                continue
            seen.add(ident)
            models.append(self._norm(ident, name=name.strip(),
                                     provider="Google DeepMind"))
        if not models:
            raise ModelListError("No models parsed from `agy models`")
        return models
