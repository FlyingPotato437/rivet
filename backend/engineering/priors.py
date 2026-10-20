"""Explainable layout preferences from cited, recorded engineer approvals.

These are company-local frequency counts over approved model snapshots, not
model-weight training or engineering rules. The configurator applies them only
after its known constraints have rejected infeasible configurations.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256

VERSION = "rivet.approved-layout-priors.v1"
MAX_JOBS = 10000


class PriorScopeError(ValueError):
    """Approved history is not bound to one explicitly identifiable company."""


def _model_hash(model):
    # Matches backend.domain.service.digest, used by the recorded approval.
    return sha256(json.dumps(model, sort_keys=True, default=str).encode()).hexdigest()


def _same(left, right):
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and " ".join(left.casefold().split()) == " ".join(right.casefold().split())
    )


def _timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Approval service writes timezone-aware timestamps. Do not invent a zone.
    return parsed.astimezone(UTC) if parsed.tzinfo else None


def _version(value):
    return (
        str(value).strip()
        if isinstance(value, (str, int)) and not isinstance(value, bool)
        else ""
    )


def _history_refs(job, order_id, org_id, model, approval):
    declared = job.get("source_refs") or []
    if not isinstance(declared, list) or len(declared) > 1000:
        return []
    refs = list(declared)
    if job.get("approval_ref"):
        refs.append(job["approval_ref"])
    accepted = []
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("order_id") != order_id:
            continue
        if ref.get("organization_id") not in (None, org_id):
            continue
        # A catalog is not evidence that this company approved this design.
        if not any(
            ref.get(field) for field in ("revision_id", "approval_id", "source_id")
        ):
            continue
        pin = ref.get("checksum") or ref.get("sha256") or ref.get("model_hash")
        if not isinstance(pin, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", pin):
            continue
        if ref.get("model_hash") and ref["model_hash"] != approval["model_hash"]:
            continue
        if ref.get("model_version") is not None and _version(
            ref["model_version"]
        ) != _version(model["model_version"]):
            continue
        accepted.append(deepcopy(ref))
    return accepted


def _verified(job, *, organization_id, manufacturer, family):
    if (
        not isinstance(job, dict)
        or job.get("organization_id") != organization_id
        or job.get("synthetic") is not False
    ):
        return None
    order_id = job.get("order_id") or job.get("id")
    snapshot = job.get("state")
    data = (
        job.get("engineering")
        or (snapshot.get("engineering") if isinstance(snapshot, dict) else None)
        or job
    )
    if (
        not isinstance(data, dict)
        or not isinstance(order_id, str)
        or not order_id
        or data.get("engineering_approved") is not True
    ):
        return None
    model, approval = data.get("selected_design"), data.get("approval")
    if not isinstance(model, dict) or not isinstance(approval, dict):
        return None
    if model.get("synthetic") is True or model.get("organization_id") not in (
        None,
        organization_id,
    ):
        return None
    if not _same(model.get("manufacturer"), manufacturer) or not _same(
        model.get("family"), family
    ):
        return None
    if job.get("manufacturer") and not _same(job["manufacturer"], manufacturer):
        return None
    if job.get("family") and not _same(job["family"], family):
        return None
    if approval.get("kind") != "engineer_recorded_approval" or not all(
        approval.get(field) for field in ("actor", "signer", "reason")
    ):
        return None
    approved_at = _timestamp(approval.get("at"))
    version = _version(data.get("model_version"))
    if (
        approved_at is None
        or not version
        or any(
            _version(candidate) != version
            for candidate in (model.get("model_version"), approval.get("model_version"))
        )
    ):
        return None
    try:
        json.dumps(model, allow_nan=False)
        if approval.get("model_hash") != _model_hash(model):
            return None
    except (TypeError, ValueError):
        return None
    refs = _history_refs(job, order_id, organization_id, model, approval)
    if not refs:
        return None
    sections, devices = model.get("sections"), model.get("devices")
    if (
        not isinstance(sections, list)
        or not isinstance(devices, list)
        or len(sections) > 500
        or len(devices) > 2000
    ):
        return None
    if any(
        not isinstance(section, dict)
        or not isinstance(section.get("id"), str)
        or not section.get("id")
        for section in sections
    ):
        return None
    if len({section["id"] for section in sections}) != len(sections):
        return None
    return {
        "order_id": order_id,
        "model": model,
        "approval": approval,
        "approved_at": approved_at,
        "source_refs": refs,
    }


def _observations(job):
    model = job["model"]
    observations = []
    for index, section in enumerate(model["sections"]):
        section_id = section["id"]
        members = [
            device
            for device in model["devices"]
            if isinstance(device, dict) and device.get("section_id") == section_id
        ]
        frames = [str(device["frame"]) for device in members if device.get("frame")]
        if len(members) >= 2 and len(frames) == len(members) and len(set(frames)) == 1:
            observations.append(
                {
                    "kind": "same_frame_group",
                    "section_id": section_id,
                    "frame": frames[0],
                    "device_count": len(members),
                    "effect": "explanation_only",
                }
            )
        if str(section.get("kind", "")).casefold() == "metering" or any(
            str(device.get("role", "")).casefold() in {"metering", "meter"}
            for device in members
        ):
            observations.append(
                {
                    "kind": "metering_position",
                    "section_id": section_id,
                    "model_section_index": index,
                    "effect": "explanation_only",
                    "limitation": "Model list order is retained; physical left/right location is not inferred.",
                }
            )
        prepared = [
            position
            for position in section.get("positions") or []
            if isinstance(position, dict)
            and str(position.get("status", "")).casefold()
            in {"spare", "prepared", "future", "prepared_space"}
        ]
        if prepared:
            observations.append(
                {
                    "kind": "explicit_prepared_positions",
                    "section_id": section_id,
                    "position_ids": [position.get("id") for position in prepared],
                    "count": len(prepared),
                    "effect": "explanation_only",
                }
            )
        spare = section.get("spare_space_in")
        if (
            isinstance(spare, (int, float))
            and not isinstance(spare, bool)
            and 0 < spare < float("inf")
        ):
            observations.append(
                {
                    "kind": "explicit_spare_space",
                    "section_id": section_id,
                    "spare_space_in": spare,
                    "effect": "explanation_only",
                }
            )
    return observations


def derive_layout_priors(approved_jobs, *, manufacturer, family, organization_id=None):
    """Return rank-only section preferences from approved company-local history.

    Each job is an authenticated caller's serialized snapshot with organization,
    order ID, explicit synthetic=False, selected_design, model_version,
    engineering_approved=True, recorded approval and hash-pinned source_refs.
    `engineering` or `state.engineering` wrappers are supported. A record ref
    must identify this order and its approval/revision/source, plus checksum.

    The helper verifies model hash/version and source bindings; it cannot verify
    a signer's professional licence or independently authenticate caller data.
    The caller must read these records from its authorized tenant store.
    """
    if not isinstance(approved_jobs, (list, tuple)) or len(approved_jobs) > MAX_JOBS:
        raise ValueError(
            f"approved_jobs must be a bounded list of at most {MAX_JOBS} snapshots."
        )
    if (
        not isinstance(manufacturer, str)
        or not manufacturer.strip()
        or not isinstance(family, str)
        or not family.strip()
    ):
        raise ValueError("A specific manufacturer and product family are required.")
    if organization_id is None:
        organizations = {
            job.get("organization_id")
            for job in approved_jobs
            if isinstance(job, dict)
            and isinstance(job.get("organization_id"), str)
            and job.get("organization_id")
        }
        if len(organizations) > 1:
            raise PriorScopeError(
                "Approved layout history crosses companies; supply the authenticated organization explicitly."
            )
        if not organizations:
            return []
        organization_id = next(iter(organizations))
    if not isinstance(organization_id, str) or not organization_id:
        raise PriorScopeError("A known company scope is required.")
    candidates = defaultdict(list)
    for job in approved_jobs:
        accepted = _verified(
            job,
            organization_id=organization_id,
            manufacturer=manufacturer,
            family=family,
        )
        if accepted:
            candidates[accepted["order_id"]].append(accepted)
    # A project contributes at most once. Use its newest genuine approval; equal
    # timestamps with differing models are ambiguous and confer no preference.
    verified = []
    for values in candidates.values():
        latest = max(job["approved_at"] for job in values)
        newest = [job for job in values if job["approved_at"] == latest]
        if len({job["approval"]["model_hash"] for job in newest}) != 1:
            continue
        verified.append(newest[0])
    if not verified:
        return []
    supporting = defaultdict(list)
    for job in verified:
        by_type = defaultdict(list)
        for section in job["model"]["sections"]:
            section_type = section.get("section_type_id")
            if (
                section.get("kind") != "distribution"
                or not isinstance(section_type, str)
                or not section_type.strip()
                or section_type.casefold() in {"unknown", "unspecified"}
            ):
                continue
            by_type[section_type].append(section["id"])
        for section_type, ids in by_type.items():
            supporting[section_type].append({"job": job, "section_ids": ids})
    priors = []
    for section_type, support in sorted(supporting.items()):
        count = len(support)
        references = []
        observations = []
        for item in sorted(support, key=lambda value: str(value["job"]["order_id"])):
            job = item["job"]
            references.append(
                {
                    "order_id": job["order_id"],
                    "model_version": job["model"]["model_version"],
                    "model_hash": job["approval"]["model_hash"],
                    "approval_at": job["approval"]["at"],
                    "section_ids": item["section_ids"],
                    "source_refs": job["source_refs"],
                }
            )
            observations += [
                {
                    **observation,
                    "order_id": job["order_id"],
                    "model_hash": job["approval"]["model_hash"],
                }
                for observation in _observations(job)
            ]
        identity = {
            "version": VERSION,
            "organization_id": organization_id,
            "manufacturer": manufacturer.casefold().strip(),
            "family": family.casefold().strip(),
            "section_type": section_type,
            "support": [(ref["order_id"], ref["model_hash"]) for ref in references],
        }
        prior_id = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[
            :32
        ]
        priors.append(
            {
                "id": prior_id,
                "version": VERSION,
                "approved_job": True,
                "organization_id": organization_id,
                "manufacturer": manufacturer,
                "family": family,
                "preferred_section_type": section_type,
                "weight": count / len(verified),
                "counts": {
                    "supporting_jobs": count,
                    "eligible_approved_jobs": len(verified),
                    "approved_sections": sum(
                        len(item["section_ids"]) for item in support
                    ),
                },
                "source_ref": {
                    "kind": "recorded_approved_job_snapshots",
                    "organization_id": organization_id,
                    "approved_models": references,
                },
                "explanation": f"{count} of {len(verified)} distinct approved {family} jobs used distribution section type {section_type}. Each job counts once; this only ranks configurations that pass the current rules.",
                "layout_observations": observations,
                "observation_counts": dict(
                    Counter(observation["kind"] for observation in observations)
                ),
                "effect": "rank_only",
                "can_override_rules": False,
                "model_weight_training": False,
            }
        )
    return sorted(
        priors, key=lambda item: (-item["weight"], item["preferred_section_type"])
    )
