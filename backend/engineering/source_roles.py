"""Explicit engineer roles for one hash/page/object-scoped source observation.

Pure proposal transformation only. The caller authenticates the engineer and
checks that the requirements/source belong to the current order and source
version. No labels, panel MCBs or bare ampere values are interpreted as roles.
"""

from __future__ import annotations

import re
from copy import deepcopy

from backend.equipment.evidence.schema import make_claim, stable_id

from .requirements import find_conflicts

VERSION = "rivet.explicit-source-equipment-role.v1"
ROLES = frozenset({"main", "feeder", "reference"})
DEVICE_KEYS = frozenset(
    {"rated_current_a", "trip_a", "frame_a", "poles", "frame", "kind"}
)


def _matches(row, *, source_id, source_sha, page, source_object_key):
    return row.get("source_object_key") == source_object_key and any(
        ref.get("source_id") == source_id
        and str(ref.get("sha256", "")).lower() == source_sha
        and type(ref.get("page")) is int
        and ref.get("page") == page
        for ref in row.get("source_refs", [])
    )


def _physical_main_groups(items, selected_ids):
    groups = set()
    for row in items:
        if row.get("id") in selected_ids or row.get("dismissed") or row.get("excluded"):
            continue
        if (
            row.get("configuration_eligible") is False
            or row.get("unresolved_role")
            or row.get("equipment_role") == "reference"
        ):
            continue
        if row.get("subject_id") != "order" or not row.get("key", "").startswith(
            "main."
        ):
            continue
        # A general specification's main requirement is not another observed
        # physical main. Only another source-object group blocks reassignment.
        if row.get("source_object_key"):
            for ref in row.get("source_refs", []):
                groups.add(
                    (
                        ref.get("source_id"),
                        ref.get("sha256"),
                        ref.get("page"),
                        row["source_object_key"],
                    )
                )
    return groups


def propose_source_role_decision(
    requirements, *, source_id, source_sha, page, source_object_key, role, reason
):
    """Assign one exact observed object, retaining its values and immutable claims.

    Selecting main/feeder confirms this group's current typed observations by an
    explicit engineer decision; it never converts rated current to frame/trip,
    selects a catalog product, merges repeated labels, or assigns other objects.
    A reference decision leaves the group visible and confirmed as reference,
    while excluding it from Configure. Another physical main must first receive
    its own role decision before this group can become the common main.
    """
    if not isinstance(requirements, dict):
        raise TypeError("A requirements mapping is required.")
    if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 180:
        raise ValueError("Choose an explicit source ID.")
    if not isinstance(source_sha, str) or not re.fullmatch(
        r"[a-fA-F0-9]{64}", source_sha
    ):
        raise ValueError("The exact current source SHA-256 is required.")
    source_sha = source_sha.lower()
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("Choose the original one-based physical source page.")
    if (
        not isinstance(source_object_key, str)
        or not source_object_key.strip()
        or len(source_object_key) > 500
    ):
        raise ValueError("Choose an exact source object key, not a circuit label.")
    if role not in ROLES:
        raise ValueError("Source equipment role must be main, feeder or reference.")
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 2000:
        raise ValueError("An explicit engineer role-decision reason is required.")
    result = deepcopy(requirements)
    selected = [
        row
        for row in result.get("items", [])
        if _matches(
            row,
            source_id=source_id,
            source_sha=source_sha,
            page=page,
            source_object_key=source_object_key,
        )
    ]
    if not selected:
        raise ValueError(
            "No current source-equipment observation matches this exact source/hash/page/object."
        )
    if len({row.get("id") for row in selected}) != len(selected):
        raise ValueError("Duplicate observation IDs prevent a scoped role decision.")
    for row in selected:
        if (
            row.get("origin_role") != "customer_one_line"
            or row.get("source_object_kind") == "busbar"
        ):
            raise ValueError(
                "Only customer one-line device observations can receive a source equipment role."
            )
        if row.get("dismissed") or row.get("excluded"):
            raise ValueError(
                "Dismissed/excluded observations need a separate engineer scope decision."
            )
        if row.get("key", "").removeprefix("main.") not in DEVICE_KEYS:
            raise ValueError(
                "The selected object includes an unsupported non-device observation."
            )
    selected_ids = {row["id"] for row in selected}
    if role == "main" and _physical_main_groups(result["items"], selected_ids):
        raise ValueError(
            "A different source object already supplies the main; explicitly decide its role first."
        )
    existing = result.setdefault("evidence_claims", [])
    known_ids = {claim.get("id") for claim in existing}
    if any(
        not isinstance(row.get("source_claim_ids"), list)
        or not row["source_claim_ids"]
        or any(
            not isinstance(identifier, str) or identifier not in known_ids
            for identifier in row["source_claim_ids"]
        )
        for row in selected
    ):
        raise ValueError(
            "Every selected observation needs its original immutable source claims."
        )
    old_ids = list(
        dict.fromkeys(
            identifier
            for row in selected
            for identifier in row.get("source_claim_ids", [])
        )
    )
    if not old_ids or any(identifier not in known_ids for identifier in old_ids):
        raise ValueError(
            "Original immutable source claims are required for this role decision."
        )
    local_subject = "drawing:" + stable_id(source_sha, page, source_object_key)
    decision_id = stable_id(
        VERSION,
        source_id,
        source_sha,
        page,
        source_object_key,
        role,
        old_ids,
        reason.strip(),
    )
    matched_ref = next(
        ref
        for ref in selected[0]["source_refs"]
        if ref.get("source_id") == source_id
        and str(ref.get("sha256", "")).lower() == source_sha
        and ref.get("page") == page
    )
    conditions = {
        "scope": "explicit_engineer_source_equipment_role_decision",
        "decision_id": decision_id,
        "source_object_key": source_object_key,
        "role": role,
        "engineering_approved": False,
        "production_released": False,
    }
    role_claim = make_claim(
        local_subject,
        "source_equipment_role",
        role,
        source=matched_ref,
        extractor={
            "name": "explicit-engineer-source-role",
            "version": VERSION,
            "run_id": decision_id,
        },
        state="proposed",
        derived_from=old_ids,
        conditions=conditions,
        normalization={"valid": True, "reason": reason.strip()},
    )
    derived = [role_claim]
    for row in selected:
        key = row["key"].removeprefix("main.")
        subject = "order" if role == "main" else local_subject
        key = "main." + key if role == "main" else key
        row_ref = next(
            ref
            for ref in row["source_refs"]
            if ref.get("source_id") == source_id
            and str(ref.get("sha256", "")).lower() == source_sha
            and ref.get("page") == page
        )
        claim = make_claim(
            subject,
            key,
            row["value"],
            unit=row.get("unit"),
            source=row_ref,
            extractor={
                "name": "explicit-engineer-source-role",
                "version": VERSION,
                "run_id": decision_id,
            },
            state="proposed",
            raw_value=row["value"],
            derived_from=[*row["source_claim_ids"], role_claim["id"]],
            conditions=conditions,
            normalization={"valid": row["value"] is not None, "reason": reason.strip()},
        )
        derived.append(claim)
        row.setdefault(
            "source_observation",
            {
                "subject_id": row["subject_id"],
                "key": row["key"],
                "source_claim_ids": deepcopy(row["source_claim_ids"]),
            },
        )
        row.update(
            subject_id=subject,
            key=key,
            equipment_role=role,
            confirmed=row["value"] is not None,
            unresolved_role=False,
            configuration_eligible=role != "reference",
            role_engineering_verified=True,
            state="proposed",
            role_basis="explicit_engineer_source_equipment_role_decision",
            source_role_decision_id=decision_id,
            source_claim_ids=list(
                dict.fromkeys([*row["source_claim_ids"], role_claim["id"], claim["id"]])
            ),
        )
    for claim in derived:
        prior = next(
            (stored for stored in existing if stored.get("id") == claim["id"]), None
        )
        if prior is not None and prior != claim:
            raise ValueError("An immutable evidence claim cannot be replaced.")
        if prior is None:
            existing.append(deepcopy(claim))
    preserved = [
        deepcopy(conflict)
        for conflict in requirements.get("conflicts", [])
        if not (selected_ids & set(conflict.get("item_ids", [])))
    ]
    result["conflicts"] = list(
        {
            conflict.get("id") or stable_id(conflict): conflict
            for conflict in [*preserved, *find_conflicts(result["items"])]
        }.values()
    )
    decision = {
        "id": decision_id,
        "source_id": source_id,
        "source_sha": source_sha,
        "page": page,
        "source_object_key": source_object_key,
        "source_local_subject": local_subject,
        "role": role,
        "reason": reason.strip(),
        "item_ids": [row["id"] for row in selected],
        "source_claim_ids": old_ids,
        "derived_claim_ids": [claim["id"] for claim in derived],
        "scope": "explicit_engineer_role_and_retained_value_confirmation",
        "engineering_approved": False,
        "production_released": False,
    }
    result.setdefault("source_role_decisions", []).append(deepcopy(decision))
    result["engineering_approved"] = False
    return {
        "schema_version": VERSION,
        "requirements_updated": result,
        "derived_claims": derived,
        "decision": decision,
        "requires_reconfiguration": True,
        "scope": "One source-object role decision; original observations remain immutable and other objects are unchanged.",
    }
