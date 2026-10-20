"""Preserve designation aspects; only explicit, scoped evidence joins identity.

ANSI/IEEE function numbers classify a function and do not identify a particular
breaker. IEC reference-designation aspect prefixes are retained separately;
parsing a prefix does not certify that a local drawing follows IEC 81346.
"""

from __future__ import annotations

import re
from copy import deepcopy

from .schema import stable_id

FUNCTION_NUMBERS = {
    "52": {
        "meaning": "ac_circuit_breaker",
        "source": "https://ieeexplore.ieee.org/document/9984140",
    },
    "86": {
        "meaning": "lockout",
        "source": "https://www.eaton.com/us/en-us/catalog/electrical-circuit-protection/generator-relays.html",
    },
}
ASPECTS = {"=": "function", "-": "product", "+": "location", "%": "type"}


def normalize_tag(raw: str | None) -> str | None:
    if raw is None or not str(raw).strip():
        return None
    # Preserve punctuation, prefix and internal numeric groups: FDR 12, 52-12
    # and BKR-12 are not automatically equivalent spellings of one device.
    return re.sub(r"\s+", " ", str(raw).strip()).upper()


def describe_identity(raw: str | None) -> dict:
    normalized = normalize_tag(raw)
    references = []
    if normalized and normalized[0] in ASPECTS:
        for match in re.finditer(r"([=+%\-])([^=+%\s\-]+)", normalized):
            references.append(
                {
                    "aspect": ASPECTS[match[1]],
                    "prefix": match[1],
                    "raw": match[0],
                    "designation": match[2],
                    "conformance_verified": False,
                }
            )
    functions = []
    # Never infer the embedded 86 in a local identifier such as 1086M.
    if normalized:
        match = re.fullmatch(
            r"(52|86)(?:[- ]?[A-Z][A-Z0-9]*|[- ]\d+[A-Z0-9-]*)?", normalized
        )
        if match:
            number = match[1]
            functions.append(
                {
                    "number": number,
                    **FUNCTION_NUMBERS[number],
                    "identity_verified": False,
                }
            )
    return {
        "raw_tag": raw,
        "normalized_tag": normalized,
        "function_numbers": functions,
        "reference_designations": references,
    }


def validate_alias(alias: dict, scope_id: str) -> bool:
    return bool(
        alias.get("verified") is True
        and alias.get("scope_key", alias.get("scope_id")) == scope_id
        and alias.get("from")
        and alias.get("to")
        and alias.get("source_refs")
        and all(
            ref.get("sha256") and ref.get("source_id") for ref in alias["source_refs"]
        )
    )


def entity_identity(
    scope_id: str,
    source_id: str,
    local_id: str,
    *,
    tag: str | None = None,
    section: object = None,
    circuit: object = None,
    verified_identities: list[dict] | None = None,
    source_context: dict | None = None,
) -> tuple[str, dict]:
    """Use caller-verified links; otherwise retain a source-local entity ID.

    A verified link requires source ID + local ID/tag, a canonical ID and cited
    evidence. Position links additionally require a verified lineup assertion.
    The caller must actually verify these links; they cannot be model votes.
    """
    context = source_context or {}
    for identity in verified_identities or []:
        if (
            identity.get("verified") is not True
            or identity.get("scope_key", identity.get("scope_id")) != scope_id
            or not identity.get("canonical_id")
            or not identity.get("evidence")
        ):
            continue
        if identity.get("source_id") != source_id:
            continue
        local_match = identity.get("entity_id") == local_id
        tag_match = bool(
            tag
            and identity.get("tag")
            and normalize_tag(identity["tag"]) == normalize_tag(tag)
        )
        position_match = bool(
            context.get("lineup_verified") is True
            and context.get("lineup_id")
            and context.get("lineup_evidence")
            and identity.get("lineup_id") == context["lineup_id"]
            and section is not None
            and circuit is not None
            and str(identity.get("section")) == str(section)
            and str(identity.get("circuit")) == str(circuit)
        )
        if local_match or tag_match or position_match:
            canonical = str(identity["canonical_id"])
            return stable_id("verified-entity", scope_id, canonical), {
                "identity_verified": True,
                "identity_key": canonical,
                "identity_evidence": deepcopy(identity["evidence"]),
                "source_local": False,
            }
    return stable_id("source-entity", scope_id, source_id, local_id), {
        "identity_verified": False,
        "source_local": True,
        "source_entity_id": local_id,
    }
