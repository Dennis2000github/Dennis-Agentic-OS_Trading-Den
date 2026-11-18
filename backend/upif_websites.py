"""UPIF website-only integration registry (Phase 10).

A provider that supports ONLY website generation is registered here. The
registry is explicit so the UI and router can never advertise it as
programmatically executable:

    WEBSITE ONLY — NOT AVAILABLE FOR PROGRAMMATIC AGENT EXECUTION

Browser integration stays optional, is never a prerequisite for model
discovery, and website credits are never represented as API credits. No
undocumented private endpoints, no auth bypass, no automatic conversion of
browser sessions into API credentials.
"""

from __future__ import annotations

WEBSITE_ONLY_NOTE = (
    "WEBSITE ONLY — NOT AVAILABLE FOR PROGRAMMATIC AGENT EXECUTION"
)

#: provider_id -> {service, website_url, note}
WEBSITE_ONLY = {
    "example_website_only": {
        "service": "website",
        "website_url": "",
        "note": "Registered as website-only; no programmatic route exists.",
        "programmatic": False,
    },
}


def is_website_only(provider_id):
    return provider_id in WEBSITE_ONLY


def register_website_only(provider_id, service, website_url="", note=""):
    WEBSITE_ONLY[provider_id] = {
        "service": service,
        "website_url": website_url,
        "note": note or WEBSITE_ONLY_NOTE,
        "programmatic": False,
    }


def list_website_only():
    return {pid: dict(entry) for pid, entry in WEBSITE_ONLY.items()}


def assert_programmatic(provider_id):
    if is_website_only(provider_id):
        raise ValueError(WEBSITE_ONLY_NOTE)
