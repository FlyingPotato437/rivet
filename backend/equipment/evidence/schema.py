"""Append-only evidence graph contracts, independent of the chosen extractor.

Public graphs are JSON. Mutations return a new deep copy; existing evidence IDs
cannot be replaced, and no parser call grants engineering approval or release.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from typing import Any

SCHEMA_VERSION = "rivet.evidence-graph.v1"
STATES = frozenset({"observed", "proposed", "approved", "released"})
NODE_KINDS = frozenset(
    {
        "lineup",
        "section",
        "compartment",
        "device",
        "terminal",
        "connectivity_node",
        "logical_node",
        "requirement",
        "unclassified_position",
        "drawing_sheet",
    }
)
EDGE_KINDS = frozenset(
    {
        "contains",
        "equipment_terminal",
        "terminal_connectivity_node",
        "logical_function_of",
        "identity_candidate",
        "requirement_applies_to",
    }
)


def stable_id(*parts: Any) -> str:
    encoded = json.dumps(
        parts,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()[:32]


def new_graph(scope_id: str, *, metadata: dict | None = None) -> dict:
    if not scope_id or not isinstance(scope_id, str):
        raise ValueError("An explicit tenant/order/package scope is required")
    return {
        "schema_version": SCHEMA_VERSION,
        "scope_id": scope_id,
        "nodes": [],
        "edges": [],
        "claims": [],
        "aliases": [],
        "rules": [],
        "scope_coverage": [],
        "coverage": {
            "complete": False,
            "identity_complete": False,
            "connectivity_complete": False,
        },
        "metadata": {
            **deepcopy(metadata or {}),
            "engineering_approved": False,
            "production_released": False,
        },
    }


def source_ref(source: dict, **location: Any) -> dict:
    """Require hash-bound source identity; local storage paths never escape."""
    ref = {
        "source_id": str(source.get("source_id") or source.get("id") or ""),
        "sha256": str(source.get("sha256") or "").lower(),
    }
    if not ref["source_id"] or not re.fullmatch(r"[a-f0-9]{64}", ref["sha256"]):
        raise ValueError("Evidence needs a source ID and SHA-256")
    allowed = {
        "page",
        "bbox",
        "coordinate_system",
        "sheet",
        "row",
        "column",
        "cell",
        "table",
        "entity",
        "cad_handle",
        "attribute_handle",
        "xml_path",
        "text",
    }
    for key in allowed:
        value = location.get(key, source.get(key))
        if value is not None:
            ref[key] = deepcopy(value)
    if "page" in ref and (not isinstance(ref["page"], int) or ref["page"] < 1):
        raise ValueError("Physical PDF page numbers are one-based integers")
    if "bbox" in ref:
        box = ref["bbox"]
        if (
            not isinstance(box, (list, tuple))
            or len(box) != 4
            or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in box)
            or box[0] >= box[2]
            or box[1] >= box[3]
        ):
            raise ValueError("A source bbox must have four finite ordered coordinates")
    if not any(
        key in ref
        for key in ("page", "sheet", "cell", "entity", "cad_handle", "xml_path")
    ):
        raise ValueError("Evidence needs a page/cell/CAD entity source location")
    return ref


def make_claim(
    subject_id: str,
    feature: str,
    value: Any,
    *,
    source: dict,
    extractor: dict,
    raw_value: Any = None,
    raw_confidence: Any = None,
    unit: str | None = None,
    state: str = "observed",
    conditions: dict | None = None,
    independence_group: str | None = None,
    derived_from: list[str] | None = None,
    normalization: dict | None = None,
    claim_id: str | None = None,
) -> dict:
    if state not in STATES:
        raise ValueError("Unknown evidence lifecycle state")
    if not subject_id or not feature or not extractor.get("name"):
        raise ValueError("Claim subject, feature and extractor name are required")
    ref = source_ref(source)
    ancestry = list(derived_from or [])
    # Extractor self-confidence is retained for diagnostics, never converted to
    # the probability that this equipment value is correct.
    observation = {
        "subject_id": subject_id,
        "feature": feature,
        "value": deepcopy(value),
        "unit": unit,
        "raw_value": deepcopy(value if raw_value is None else raw_value),
        "raw_confidence": deepcopy(raw_confidence),
        "source": ref,
        "extractor": {
            "name": extractor["name"],
            "version": extractor.get("version", "unspecified"),
            "run_id": extractor.get("run_id"),
        },
        "state": state,
        "conditions": deepcopy(conditions or {}),
        "independence_group": independence_group
        or stable_id(
            "source-page-ancestry",
            ref["sha256"],
            ref.get("page"),
            ref.get("sheet"),
            None
            if ref.get("page") or ref.get("sheet")
            else ref.get("entity", ref.get("xml_path")),
        ),
        "derived_from": ancestry,
        "normalization": deepcopy(
            normalization
            or {
                "valid": value is not None,
                "reason": None if value is not None else "unknown",
            }
        ),
    }
    observation["id"] = claim_id or stable_id("claim", observation)
    return observation


def append_record(graph: dict, collection: str, record: dict) -> dict:
    """Idempotent append only. A same-ID replacement is a corruption error."""
    if collection not in {"nodes", "edges", "claims", "aliases"}:
        raise ValueError("Not an append-only evidence collection")
    if not record.get("id"):
        raise ValueError("Append-only records need an ID")
    result = deepcopy(graph)
    existing = next((x for x in result[collection] if x["id"] == record["id"]), None)
    if existing is not None and existing != record:
        raise ValueError(f"Cannot overwrite existing {collection} evidence")
    if existing is None:
        result[collection].append(deepcopy(record))
    return result


def append_claim(graph: dict, claim: dict) -> dict:
    source_ref(claim["source"])
    if claim.get("state") not in STATES:
        raise ValueError("Claim state is required")
    if claim["subject_id"] not in {node["id"] for node in graph["nodes"]}:
        raise ValueError("Claim subject must be a graph node")
    return append_record(graph, "claims", claim)


def transition_claim(
    graph: dict,
    claim_id: str,
    state: str,
    *,
    actor: str,
    authority: str,
    evidence: dict,
) -> dict:
    """Append an authorized lifecycle claim without modifying its observation.

    The caller must independently check RBAC/signature authority. This helper
    requires that evidence, rather than interpreting a document stamp as consent.
    """
    if state not in {"proposed", "approved", "released"} or not actor or not authority:
        raise ValueError("An explicit actor and approval authority are required")
    original = next((x for x in graph["claims"] if x["id"] == claim_id), None)
    if original is None:
        raise ValueError("Unknown original claim")
    transition = make_claim(
        original["subject_id"],
        original["feature"],
        original["value"],
        source=evidence,
        extractor={"name": "authorized_lifecycle_event", "version": "1"},
        raw_value=original["raw_value"],
        unit=original.get("unit"),
        state=state,
        derived_from=[claim_id],
        independence_group=original["independence_group"],
        conditions={
            **original.get("conditions", {}),
            "actor": actor,
            "authority": authority,
        },
    )
    return append_claim(graph, transition)
