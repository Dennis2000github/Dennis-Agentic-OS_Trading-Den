"""Model source for OpenClaw (``openclaw models list --json``)."""

import json

from .base import ModelListError, ModelSource


def _provider(item, model_id):
    value = item.get("provider") or item.get("provider_id")
    if value:
        return str(value)
    return model_id.split("/", 1)[0] if "/" in model_id else "OpenClaw"


class OpenClawModelSource(ModelSource):
    agent_id = "openclaw"

    def list_models(self, ctx, cwd=None):
        rc, out, err = ctx.exec(
            ["openclaw", "models", "list", "--json"], cwd=cwd, timeout=30
        )
        if rc != 0:
            raise ModelListError((err or out or "openclaw models list failed").strip()[:300])
        try:
            payload = json.loads(out)
        except Exception as exc:
            raise ModelListError(f"Unparseable OpenClaw model catalog: {exc}")

        raw_models = payload.get("models") if isinstance(payload, dict) else payload
        if not isinstance(raw_models, list):
            raise ModelListError("OpenClaw model catalog missing 'models' list")

        models = []
        seen = set()
        for item in raw_models:
            if isinstance(item, str):
                item = {"id": item}
            if not isinstance(item, dict):
                continue
            ident = item.get("key") or item.get("id") or item.get("model")
            if not ident:
                continue
            ident = str(ident).strip()
            if not ident or ident in seen:
                continue
            seen.add(ident)
            status = str(item.get("status") or "").lower()
            available = item.get("available", item.get("enabled", True))
            if status in ("unavailable", "disabled", "blocked", "hidden"):
                available = False
            models.append(self._norm(
                ident,
                name=item.get("name") or item.get("displayName") or ident,
                description=item.get("description") or item.get("auth") or "",
                is_default=bool(item.get("isDefault") or item.get("default")),
                available=available,
                reason=None if available else (item.get("reason") or "Unavailable in OpenClaw"),
                provider=_provider(item, ident),
            ))
        if not models:
            raise ModelListError("No models parsed from `openclaw models list`")
        return models
