"""Server-controlled model choice; source documents cannot buy an escalation.

Selection changes reasoning capacity, never tool permissions or review gates.
No provider call, retry or automatic fallback is performed here.
"""

from __future__ import annotations

import os

PROFILES = {"routine", "technical_review"}


def reasoning_options(model: str, *, technical: bool = False) -> dict:
    """Official Responses API options, shared by text and image readers."""
    if model.startswith("gpt-6"):
        return {
            "reasoning": {
                "effort": "high" if technical or "astra" in model.casefold() else "low"
            }
        }
    return {}


def select_route(profile: str | None = None) -> dict:
    profile = profile or os.getenv("RIVET_AGENT_PROFILE", "routine")
    if profile not in PROFILES:
        raise ValueError("Unknown Rivet model profile")
    routine = os.getenv("RIVET_AGENT_MODEL") or "gpt-6-luna"
    technical = os.getenv("RIVET_TECHNICAL_REVIEW_MODEL") or routine
    model = technical if profile == "technical_review" else routine
    return {
        "profile": profile,
        "model": model,
        "comment_model": routine,
        **reasoning_options(model, technical=profile == "technical_review"),
        "selection": "authenticated_request_or_server_configuration",
        "technical_model_configured": bool(os.getenv("RIVET_TECHNICAL_REVIEW_MODEL")),
        "automatic_fallback": False,
    }
