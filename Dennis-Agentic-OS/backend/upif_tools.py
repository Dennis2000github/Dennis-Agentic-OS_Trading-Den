"""UPIF agent tools (Phase 7).

Every authorized agent calls the SAME shared generation service through these
tools. No agent ever holds a provider client, secret or balance. Provider
details stay available for debugging/audit but are not needed by the agent.

Tools:
    upif_discover_models, upif_model_capabilities, upif_check_availability,
    upif_check_entitlement, upif_generate_image, upif_edit_image,
    upif_generate_video, upif_animate_image, upif_generation_status,
    upif_cancel_generation, upif_retrieve_asset, upif_dashboard
"""

from __future__ import annotations

import json

from .upif_contracts import UPIFError
from .upif_service import SERVICE

MUTATING = frozenset((
    "upif_generate_image", "upif_edit_image", "upif_generate_video",
    "upif_animate_image", "upif_cancel_generation",
))

TOOLS = {
    "upif_discover_models": {
        "description": "Discover programmatically available generation models, "
                       "optionally filtered by provider, capability or free-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider_id": {"type": "string"},
                "capability": {"type": "string",
                               "enum": ["text_to_image", "image_edit",
                                        "text_to_video", "image_to_video"]},
                "free_only": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "mutating": False,
    },
    "upif_model_capabilities": {
        "description": "Get the explicit capability + modality set of one model.",
        "inputSchema": {
            "type": "object",
            "properties": {"model_id": {"type": "string"}},
            "required": ["model_id"],
            "additionalProperties": False,
        },
        "mutating": False,
    },
    "upif_check_availability": {
        "description": "Check whether a model's programmatic route is available.",
        "inputSchema": {
            "type": "object",
            "properties": {"model_id": {"type": "string"}},
            "required": ["model_id"],
            "additionalProperties": False,
        },
        "mutating": False,
    },
    "upif_check_entitlement": {
        "description": "Probe a model's live free/paid entitlement classification.",
        "inputSchema": {
            "type": "object",
            "properties": {"model_id": {"type": "string"}},
            "required": ["model_id"],
            "additionalProperties": False,
        },
        "mutating": False,
    },
    "upif_generate_image": {
        "description": "Generate an image through the shared UPIF router (FREE FIRST default).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "provider_id": {"type": "string"},
                "model_id": {"type": "string"},
                "policy": {"type": "string",
                           "enum": ["free_first", "free_only", "quality_first",
                                    "lowest_cost", "fastest_available",
                                    "manual_model", "manual_provider", "local_first"]},
                "free_only": {"type": "boolean"},
                "aspect_ratio": {"type": "string"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
        "mutating": True,
    },
    "upif_edit_image": {
        "description": "Edit an image using public reference URLs through UPIF.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "reference_urls": {"type": "array", "items": {"type": "string"}},
                "provider_id": {"type": "string"},
                "model_id": {"type": "string"},
                "free_only": {"type": "boolean"},
            },
            "required": ["prompt", "reference_urls"],
            "additionalProperties": False,
        },
        "mutating": True,
    },
    "upif_generate_video": {
        "description": "Generate a video through the shared UPIF router.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "provider_id": {"type": "string"},
                "model_id": {"type": "string"},
                "free_only": {"type": "boolean"},
                "duration": {"type": "integer"},
                "aspect_ratio": {"type": "string"},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
        "mutating": True,
    },
    "upif_animate_image": {
        "description": "Animate a reference image (image-to-video) through UPIF.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference_url": {"type": "string"},
                "provider_id": {"type": "string"},
                "model_id": {"type": "string"},
                "free_only": {"type": "boolean"},
            },
            "required": ["reference_url"],
            "additionalProperties": False,
        },
        "mutating": True,
    },
    "upif_generation_status": {
        "description": "Get the status of a UPIF generation job.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "mutating": False,
    },
    "upif_cancel_generation": {
        "description": "Cancel a running UPIF generation job.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "mutating": True,
    },
    "upif_retrieve_asset": {
        "description": "Retrieve a generated asset reference from a completed job.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "asset_index": {"type": "integer", "default": 0},
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "mutating": False,
    },
    "upif_dashboard": {
        "description": "Show the Generation Router dashboard: policy, free providers, "
                       "available models, quotas, queue and failures.",
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
        "mutating": False,
    },
}


def _check_args(tool_name, arguments):
    schema = TOOLS[tool_name]["inputSchema"]
    required = schema.get("required", [])
    for field in required:
        if arguments.get(field) in (None, "", []):
            raise UPIFError("missing_field", "%s requires %s." % (tool_name, field), 400)
    return arguments


def run_tool(tool_name, arguments, agent_id="agent-router"):
    """Execute one UPIF tool; returns a JSON-safe result."""
    if tool_name not in TOOLS:
        raise UPIFError("unknown_tool", "Unknown UPIF tool: %s" % tool_name, 404)
    args = _check_args(tool_name, arguments or {})
    try:
        if tool_name == "upif_discover_models":
            return {"models": SERVICE.discover_generation_models(
                provider_id=args.get("provider_id"),
                capability=args.get("capability"),
                free_only=bool(args.get("free_only")))}
        if tool_name == "upif_model_capabilities":
            return SERVICE.get_model_capabilities(args["model_id"])
        if tool_name == "upif_check_availability":
            return SERVICE.check_model_availability(args["model_id"])
        if tool_name == "upif_check_entitlement":
            return SERVICE.check_model_entitlement(args["model_id"])
        if tool_name == "upif_generate_image":
            return SERVICE.generate_image(
                args["prompt"], provider_id=args.get("provider_id"),
                model_id=args.get("model_id"), policy=args.get("policy"),
                free_only=args.get("free_only"), agent_id=agent_id,
                params={k: args[k] for k in ("aspect_ratio", "width", "height")
                        if k in args})
        if tool_name == "upif_edit_image":
            return SERVICE.edit_image(
                args["prompt"], reference_urls=args.get("reference_urls") or [],
                provider_id=args.get("provider_id"), model_id=args.get("model_id"),
                free_only=args.get("free_only"), agent_id=agent_id)
        if tool_name == "upif_generate_video":
            return SERVICE.generate_video(
                args["prompt"], provider_id=args.get("provider_id"),
                model_id=args.get("model_id"), free_only=args.get("free_only"),
                agent_id=agent_id,
                params={k: args[k] for k in ("duration", "aspect_ratio") if k in args})
        if tool_name == "upif_animate_image":
            return SERVICE.animate_image(
                args["reference_url"], provider_id=args.get("provider_id"),
                model_id=args.get("model_id"), free_only=args.get("free_only"),
                agent_id=agent_id)
        if tool_name == "upif_generation_status":
            return SERVICE.get_generation_status(args["job_id"])
        if tool_name == "upif_cancel_generation":
            return SERVICE.cancel_generation(args["job_id"])
        if tool_name == "upif_retrieve_asset":
            return SERVICE.retrieve_generated_asset(args["job_id"],
                                                    args.get("asset_index", 0))
        if tool_name == "upif_dashboard":
            return SERVICE.dashboard()
    except UPIFError as exc:
        return {"error": exc.as_error()}
    except Exception as exc:  # defensive: never leak a secret in the message
        return {"error": {"code": "upif_error", "message": str(exc)[:300]}}
    return {"error": {"code": "unreachable", "message": tool_name}}


TOOL_NAMES = sorted(TOOLS.keys())
