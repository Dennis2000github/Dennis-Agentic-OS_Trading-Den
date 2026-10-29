"""Model source for OpenCode (``opencode models``)."""

from .base import ModelListError, ModelSource, strip_ansi


class OpenCodeModelSource(ModelSource):
    agent_id = "opencode"

    def list_models(self, ctx, cwd=None):
        rc, out, err = ctx.exec(["opencode", "models"], cwd=cwd, timeout=30)
        if rc != 0:
            raise ModelListError((err or out or "opencode models failed").strip()[:300])

        models = []
        seen = set()
        for raw in strip_ansi(out).splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # Model ids are ``provider/model``. Anything else is status noise.
            if "/" not in line or " " in line:
                continue
            if line in seen:
                continue
            seen.add(line)
            provider = line.split("/", 1)[0]
            models.append(self._norm(line, name=line, description=provider,
                                     provider=provider))
        if not models:
            raise ModelListError("No models parsed from `opencode models`")
        return models
