"""Model source for Codex CLI (``codex debug models`` -> JSON)."""

import json

from .base import ModelListError, ModelSource


class CodexModelSource(ModelSource):
    agent_id = "codex"

    def list_models(self, ctx, cwd=None):
        rc, out, err = ctx.exec(["codex", "debug", "models"], cwd=cwd, timeout=30)
        if rc != 0:
            raise ModelListError((err or out or "codex debug models failed").strip()[:300])
        try:
            data = json.loads(out)
        except Exception as exc:
            raise ModelListError(f"Unparseable codex model catalog: {exc}")

        raw_models = data.get("models") if isinstance(data, dict) else data
        if not isinstance(raw_models, list):
            raise ModelListError("codex model catalog missing 'models' list")

        models = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            ident = item.get("slug") or item.get("id")
            if not ident:
                continue
            visibility = str(item.get("visibility", "list")).lower()
            available = visibility not in ("hidden", "hide", "none")
            models.append(self._norm(
                ident,
                name=item.get("display_name") or ident,
                description=item.get("description") or "",
                available=available,
                reason=None if available else "Not selectable in this CLI",
                provider="OpenAI",
            ))
        if not models:
            raise ModelListError("No models parsed from codex catalog")
        return models
