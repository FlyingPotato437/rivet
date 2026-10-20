"""Explicit engineer comment decisions on one versioned requirement/model.

This is a pure proposal transformation, not comment interpretation or CAD editing.
The caller authenticates the engineer, checks order ownership and actual comment
source bytes, advances the model version, revalidates catalog/structure, persists
the append-only claims, and uses the existing generation/review/Outlook flow.
"""

from __future__ import annotations

import re
from copy import deepcopy

from backend.equipment.evidence.schema import make_claim, source_ref, stable_id

from .configurator import _digest, _system
from .requirements import find_conflicts, reviewed_value, to_config_inputs

VERSION = "rivet.explicit-comment-model-decision.v1"
DEVICE_FIELDS = {
    "frame_a": "frame_a",
    "trip_a": "trip_a",
    "poles": "pole_count",
    "trip_type": "trip_type",
    "nameplate": "nameplate",
    "part_number": "part_number",
}
SYSTEM_NUMERIC = frozenset(
    {
        "service_rating_a",
        "voltage_v",
        "voltage_to_ground_v",
        "frequency_hz",
        "phases",
        "wires",
        "available_fault_ka",
        "minimum_interrupting_ka",
        "short_time_withstand_ka",
    }
)
SYSTEM_FIELDS = SYSTEM_NUMERIC | {
    "current_type",
    "grounding",
    "service_disconnect",
    "code_edition",
}
STALE_VALIDATION_FIELDS = frozenset(
    {
        "rule_results",
        "unresolved_questions",
        "has_known_rule_violations",
        "validity",
        "ranking",
    }
)


def _comment_anchor(comment_ref):
    if not isinstance(comment_ref, dict):
        raise TypeError("A checked current same-order comment source is required.")
    if comment_ref.get("same_order_checked") is not True:
        raise ValueError(
            "The caller must check that the comment and source belong to this order."
        )
    for key in ("source_id", "comment_id", "text"):
        value = comment_ref.get(key)
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > (20000 if key == "text" else 180)
        ):
            raise ValueError(f"The comment needs a bounded explicit {key}.")
    page = comment_ref.get("page")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError(
            "The comment needs its original one-based physical source page."
        )
    if not re.fullmatch(r"[a-fA-F0-9]{64}", str(comment_ref.get("sha256", ""))):
        raise ValueError("The comment needs the checked original source SHA-256.")
    evidence_ids = comment_ref.get("evidence_ids")
    if (
        not isinstance(evidence_ids, list)
        or not 1 <= len(evidence_ids) <= 100
        or any(
            not isinstance(identifier, str)
            or not identifier.strip()
            or len(identifier) > 180
            for identifier in evidence_ids
        )
    ):
        raise ValueError("The comment needs explicit current evidence IDs.")
    anchor = source_ref(comment_ref)
    anchor["comment_id"] = comment_ref["comment_id"]
    anchor["evidence_ids"] = list(dict.fromkeys(evidence_ids))
    return anchor


def _typed_value(key, value):
    # These explicit numeric quantities are not all in the older review unit map.
    numeric_key = key.removeprefix("main.")
    if numeric_key in {"available_fault_ka", "short_time_withstand_ka"}:
        return reviewed_value("minimum_interrupting_ka", value)
    if (
        numeric_key == "code_edition"
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        value = str(value)
    result = reviewed_value(key, value)
    if numeric_key in {
        "trip_type",
        "nameplate",
        "part_number",
        "current_type",
        "grounding",
        "code_edition",
    } and (not isinstance(result, str) or len(result) > 500):
        raise ValueError("This requirement needs an explicit bounded text value.")
    return result


def _target(design, row):
    subject, key = row.get("subject_id"), row.get("key")
    if subject == "order" and key in SYSTEM_FIELDS:
        return None, None
    if subject == "order" and isinstance(key, str) and key.startswith("main."):
        field = DEVICE_FIELDS.get(key.split(".", 1)[1])
        matches = [
            device
            for device in design.get("devices", [])
            if device.get("role") == "main"
        ]
    elif subject != "order" and key in DEVICE_FIELDS:
        field = DEVICE_FIELDS[key]
        # Source-local IDs are exact model IDs; a repeated circuit display label
        # is not an equipment identity and is never searched here.
        matches = [
            device
            for device in design.get("devices", [])
            if device.get("id") == subject
        ]
        if not matches:
            matches = [
                device
                for device in design.get("devices", [])
                if device.get("tag") == subject
            ]
    else:
        raise ValueError(
            "This requirement has no supported explicit model field; use the engineer configuration workflow."
        )
    if field is None:
        raise ValueError(
            "This main requirement has no supported explicit scalar model edit."
        )
    if len(matches) != 1:
        raise ValueError(
            "The requirement must identify exactly one model device; resolve equipment ownership first."
        )
    return matches[0], field


def _append_claim(claims, claim):
    existing = next((old for old in claims if old.get("id") == claim["id"]), None)
    if existing is not None and existing != claim:
        raise ValueError("An existing immutable evidence claim cannot be replaced.")
    if existing is None:
        claims.append(deepcopy(claim))


def propose_comment_model_edit(
    requirements, design, *, requirement_id, value, comment_ref, reason
):
    """Return a proposed scalar change after one explicit engineer decision.

    ``comment_ref`` requires source_id, sha256, original page, comment_id, exact
    text, evidence_ids and same_order_checked=True. That marker is a caller
    assertion, not independent authentication by this pure helper. Neither the
    value nor its device association is inferred from the comment's prose.

    Inputs are untouched. Existing observations/claims are retained; the new
    source-comment claim has observation scope and the derived requirement
    claim has explicit engineer-decision scope. Other source conflicts stay
    unresolved. Catalog/position validation and model versioning are the caller's
    responsibility before regeneration.
    """
    if not isinstance(requirements, dict) or not isinstance(design, dict):
        raise TypeError("Requirements and selected design must be mappings.")
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 2000:
        raise ValueError(
            "An explicit engineer decision reason of 3–2000 characters is required."
        )
    anchor = _comment_anchor(comment_ref)
    updated_requirements, updated_design = deepcopy(requirements), deepcopy(design)
    rows = [
        row
        for row in updated_requirements.get("items", [])
        if row.get("id") == requirement_id
    ]
    if len(rows) != 1:
        raise ValueError("Choose exactly one requirement in the current model.")
    row = rows[0]
    if row.get("dismissed") or row.get("excluded") or row.get("unresolved_role"):
        raise ValueError(
            "Dismissed, excluded or ambiguously owned requirements need a separate engineer scope decision."
        )
    if not row.get("subject_id") or not isinstance(row.get("key"), str):
        raise ValueError("The requirement needs an explicit subject and typed field.")
    old_ids = row.get("source_claim_ids", [])
    existing_claims = updated_requirements.setdefault("evidence_claims", [])
    known_ids = {claim.get("id") for claim in existing_claims}
    if (
        not old_ids
        or not isinstance(old_ids, list)
        or any(
            not isinstance(identifier, str) or identifier not in known_ids
            for identifier in old_ids
        )
    ):
        raise ValueError(
            "The requirement must retain its original immutable evidence claims."
        )
    if not row.get("source_refs"):
        raise ValueError("The original requirement source references must be retained.")
    device, field = _target(updated_design, row)
    typed = _typed_value(row["key"], value)
    subject = row["subject_id"]
    decision_id = stable_id(
        VERSION, requirement_id, old_ids, typed, anchor, reason.strip()
    )
    observation = make_claim(
        subject,
        "customer_comment_text",
        anchor["text"],
        source=anchor,
        extractor={
            "name": "checked-comment-source",
            "version": VERSION,
            "run_id": decision_id,
        },
        state="observed",
        raw_value=anchor["text"],
        derived_from=anchor["evidence_ids"],
        conditions={
            "scope": "verbatim_source_comment_observation",
            "comment_id": anchor["comment_id"],
            "evidence_ids": anchor["evidence_ids"],
            "engineering_interpretation_verified": False,
        },
    )
    derived = make_claim(
        subject,
        row["key"],
        typed,
        source=anchor,
        unit=row.get("unit"),
        raw_value={
            "prior_requirement_value": deepcopy(row.get("value")),
            "comment_text": anchor["text"],
        },
        extractor={
            "name": "explicit-engineer-comment-model-decision",
            "version": VERSION,
            "run_id": decision_id,
        },
        state="proposed",
        derived_from=[*old_ids, observation["id"]],
        conditions={
            "scope": "explicit_engineer_requirement_decision",
            "comment_id": anchor["comment_id"],
            "requirement_id": requirement_id,
            "decision_id": decision_id,
            "engineering_approved": False,
            "production_released": False,
        },
        normalization={
            "valid": True,
            "reason": reason.strip(),
            "method": "explicit_typed_engineer_decision",
        },
    )
    for claim in (observation, derived):
        _append_claim(existing_claims, claim)
    decision = {
        "id": decision_id,
        "kind": "explicit_engineer_comment_model_edit",
        "requirement_id": requirement_id,
        "comment_id": anchor["comment_id"],
        "subject_id": subject,
        "key": row["key"],
        "before": deepcopy(row.get("value")),
        "after": deepcopy(typed),
        "reason": reason.strip(),
        "comment_ref": deepcopy(anchor),
        "source_claim_ids": [*old_ids, observation["id"], derived["id"]],
        "model_device_id": device.get("id") if device else None,
        "model_field": field or "system." + row["key"],
        "requires_revalidation": True,
        "engineering_approved": False,
        "production_released": False,
    }
    row.update(
        value=typed,
        confirmed=True,
        state="proposed",
        decision=deepcopy(decision),
        value_origin="explicit_engineer_comment_decision",
        source_claim_ids=list(dict.fromkeys(decision["source_claim_ids"])),
    )
    if anchor not in row["source_refs"]:
        row["source_refs"].append(deepcopy(anchor))
    if device is not None:
        device[field] = deepcopy(typed)
        device["state"] = "proposed"
        device["source_claim_ids"] = list(
            dict.fromkeys(
                [*device.get("source_claim_ids", []), observation["id"], derived["id"]]
            )
        )
        refs = device.setdefault("source_refs", [])
        if anchor not in refs:
            refs.append(deepcopy(anchor))
    preserved_conflicts = [
        deepcopy(conflict)
        for conflict in requirements.get("conflicts", [])
        if requirement_id not in conflict.get("item_ids", [])
    ]
    recomputed_conflicts = find_conflicts(updated_requirements["items"])
    updated_requirements["conflicts"] = list(
        {
            conflict.get("id") or stable_id(conflict): conflict
            for conflict in [*preserved_conflicts, *recomputed_conflicts]
        }.values()
    )
    updated_requirements["engineering_approved"] = False
    inputs = to_config_inputs(updated_requirements)
    updated_design["system"] = _system(inputs)
    updated_design["requirements_digest"] = _digest(inputs)
    for key in STALE_VALIDATION_FIELDS:
        updated_design.pop(key, None)
    updated_design.update(
        state="proposed",
        engineering_approved=False,
        production_released=False,
        fully_configured=False,
        validation_pending=True,
    )
    return {
        "schema_version": VERSION,
        "requirements_updated": updated_requirements,
        "design_updated": updated_design,
        "derived_claims": [observation, derived],
        "decision": decision,
        "requires_revalidation": True,
        "scope": "One explicit engineer scalar decision; no inferred comment resolution, catalog approval, geometry edit, regeneration or email action.",
    }
