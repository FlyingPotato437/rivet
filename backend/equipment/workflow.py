"""Durable, order-owned prepare -> independent recheck -> unsent draft workflow."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid4, uuid5

from backend.domain.service import digest, fail, scoped
from backend.identity import current_actor, current_org
from backend.orders import service as orders
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Job, now

from . import service


def _state(order):
    return order.state.get("equipment_corrections", {"bundles": []})


def _location(order, bundle_id):
    root = db.BLOBS / "equipment-corrections" / current_org() / order.id / bundle_id
    if (
        root.is_symlink()
        or any(parent.is_symlink() for parent in root.parents)
        or not root.resolve().is_relative_to(db.BLOBS.resolve())
    ):
        fail("The corrected file location is unavailable.", 409)
    return root


def _clean(value):
    if isinstance(value, dict):
        return {
            k: _clean(v)
            for k, v in value.items()
            if k not in {"path", "report_path", "destination", "file_path"}
        }
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


@lru_cache(maxsize=16)
def _compiled_plan(fingerprint, encoded):
    from .corrections import plan_corrections

    sources = json.loads(encoded)
    return plan_corrections(
        sources["baseline"], sources["target"], sources["requirements"]
    )


def plan(s, order):
    from . import profile_service

    _, packages, sources, baseline, target, fingerprint = service.selected_sources(
        s, order
    )
    # selected_sources hashes current original bytes before this cache lookup.
    # A changed file/registration produces a different key; consumers receive
    # copies, so no delivery/run can mutate another order's compiled plan.
    report = deepcopy(_compiled_plan(fingerprint, json.dumps(sources, sort_keys=True)))
    profile = profile_service.context(s, order)
    if profile["signature"]:
        report["file_plan_id"] = report["plan_id"]
        report["plan_id"] = digest([report["file_plan_id"], profile["signature"]])
        report["profile_signature"] = profile["signature"]
        report["company_profile"] = {
            "id": profile["profile_version_id"],
            "name": profile["profile"].name,
            "version": profile["profile"].version,
            "issues": profile["issues"],
        }
        if profile["issues"]:
            report["status"] = "blocked"
            report["blockers"] = [*report.get("blockers", []), *profile["issues"]]
    if not baseline or not target:
        report = {
            **report,
            "status": "blocked",
            "blockers": [
                *report.get("blockers", []),
                "Add an earlier drawing and BOM plus the current native drawing and BOM.",
            ],
        }
    if packages and target != packages[-1]["revision"]:
        report = {
            **report,
            "status": "blocked",
            "blockers": [
                *report.get("blockers", []),
                "This is a historical comparison. Select the latest issued source package before preparing corrected files.",
            ],
        }
    return report, sources, baseline, target, fingerprint


def _entry(s, order, bundle_id):
    bundle = next(
        (b for b in _state(order).get("bundles", []) if b["id"] == bundle_id), None
    )
    if not bundle:
        fail("This corrected package does not belong to the order.", 404)
    return bundle


def _current(s, order, bundle):
    from . import profile_service

    _, packages, _, _, target, fingerprint = service.selected_sources(s, order)
    return (
        fingerprint == bundle["source_fingerprint"]
        and target == bundle["source_revision"]
        and bool(packages)
        and target == packages[-1]["revision"]
        and profile_service.context(s, order)["signature"]
        == bundle.get("profile_signature")
    )


def artifact(s, order, bundle_id, name):
    bundle = _entry(s, order, bundle_id)
    item = next((a for a in bundle["artifacts"] if a["name"] == name), None)
    if not item or Path(name).name != name:
        fail("This file is not part of the corrected package.", 404)
    path = _location(order, bundle_id) / name
    if (
        path.is_symlink()
        or not path.resolve().is_relative_to(_location(order, bundle_id).resolve())
        or not path.is_file()
        or sha256(path.read_bytes()).hexdigest() != item["sha256"]
    ):
        fail(
            "The corrected file changed or is unavailable. Prepare a fresh package.",
            409,
        )
    return path, item


@lru_cache(maxsize=12)
def _verified_native_package(fingerprint, encoded):
    from .native_verification import recheck_package

    inputs = json.loads(encoded)
    return recheck_package(
        inputs["baseline"],
        inputs["outputs"],
        inputs["requirements"],
        original_target=inputs["target"],
        native_plan=inputs["native_plan"],
        literal_edits=inputs["edits"],
    )


def _recheck(s, order, bundle):
    if not _current(s, order, bundle):
        fail(
            "The source package changed. Prepare and check a fresh corrected revision.",
            409,
        )
    _, _, sources, _, _, _ = service.selected_sources(s, order)
    outputs = []
    for item in bundle["artifacts"]:
        path, _ = artifact(s, order, bundle["id"], item["name"])
        role = (
            "drawing"
            if item["role"] in {"drawing", "preview"}
            else "bom"
            if item["role"] == "bom"
            else None
        )
        if role:
            outputs.append(
                {
                    "id": f"{bundle['id']}:{item['name']}",
                    "name": item["name"],
                    "path": str(path),
                    "role": role,
                    "revision": bundle["target_revision"],
                    "sha256": item["sha256"],
                    "lifecycle": "draft",
                }
            )
    requirements = sources["requirements"] + [
        v
        for side in ("baseline", "target")
        for v in sources[side]
        if v["role"] == "markup"
    ]
    # Every input and output byte hash was checked above. Reuse only the pure
    # parse/comparison for the same immutable bytes while the UI polls progress.
    result = service.raw_comparison(
        {
            "baseline": sources["baseline"],
            "target": outputs,
            "requirements": requirements,
        },
        digest(
            [
                "corrected-files-v1",
                bundle["source_fingerprint"],
                [(o["id"], o["sha256"]) for o in outputs],
            ]
        ),
    )
    if bundle.get("geometry_changed"):
        report, _, _, _, _ = plan(s, order)
        if not report.get("native_plan") or report["plan_id"] != bundle["plan_id"]:
            fail(
                "The source-backed native design plan changed. Prepare a fresh draft.",
                409,
            )
        # _current and artifact checked every current source/output byte hash
        # above. Repeat reads of the identical immutable package may reuse its
        # complete pure native check; no timestamps or UI flags authorize it.
        result = deepcopy(
            _verified_native_package(
                digest(
                    [
                        bundle["source_fingerprint"],
                        bundle["plan_id"],
                        [(o["id"], o["sha256"]) for o in outputs],
                    ]
                ),
                json.dumps(
                    {
                        "baseline": sources["baseline"],
                        "target": sources["target"],
                        "outputs": outputs,
                        "requirements": requirements,
                        "native_plan": report["native_plan"],
                        "edits": report["edits"],
                    },
                    sort_keys=True,
                ),
            )
        )
    from .rules import evaluate

    result["data_rules"] = evaluate(result["target"])
    if (
        result["summary"]["outcome"] != "supported_fields_match"
        or result["data_rules"]["failed"]
        or result["data_rules"]["unknown"]
    ):
        fail(
            "The corrected drawing and BOM did not pass the independent check. Nothing can be attached yet.",
            409,
        )
    from . import model_service, profile_service

    company_rules = profile_service.check_model(
        s,
        order,
        model_service.project(
            result,
            digest([bundle["plan_id"], "saved-outputs"]),
            bundle["baseline_revision"],
            bundle["target_revision"],
            outputs,
        ),
        [*outputs, *requirements, *sources["baseline"]],
    )
    if company_rules:
        result["company_rules"] = company_rules
        applicable = [
            r
            for r in company_rules.get("results", [])
            if r.get("applicable") is not False
        ]
        if (
            not company_rules.get("active")
            or company_rules.get("blockers")
            or any(r["status"] != "pass" for r in applicable)
        ):
            fail(
                {
                    "message": "The saved files did not pass the company-reviewed design rules. No Outlook attachments were created.",
                    "company_rules": company_rules,
                },
                409,
            )
    return result


def public_bundle(s, order, bundle):
    from .outlook import metadata

    current = _current(s, order, bundle)
    verified = None
    if current:
        try:
            for item in bundle["artifacts"]:
                artifact(s, order, bundle["id"], item["name"])
            verified = _recheck(s, order, bundle)
        except Exception as exc:
            from fastapi import HTTPException

            if not isinstance(exc, HTTPException):
                raise
            current = False
    result = _clean(deepcopy(bundle))
    result.pop("source_manifest", None)
    result["current"] = current
    result["attachable"] = current and bundle.get("attachable") is True
    if verified:
        if verified.get("company_rules"):
            result["company_rules"] = verified["company_rules"]
        if verified.get("native_verification"):
            result["native_verification"] = _clean(verified["native_verification"])
        changed_fields = {
            (c["device_tag"], c["field"]) for c in bundle.get("changes", [])
        }
        result["verification"]["summary"] = verified["summary"]
        result["verification"]["checks"] = [
            {
                **{k: c[k] for k in ("device_tag", "field", "label", "status")},
                **{
                    role: {"value": c[role]["value"], "unit": c[role].get("unit")}
                    for role in (
                        "baseline_drawing",
                        "baseline_bom",
                        "target_drawing",
                        "target_bom",
                        "required",
                    )
                },
            }
            for c in verified["checks"]
            if (c["device_tag"], c["field"]) in changed_fields
        ]
    result["artifacts"] = [
        {
            **a,
            "url": f"/orders/{order.id}/equipment/corrections/{bundle['id']}/files/{quote(a['name'], safe='')}",
        }
        for a in result["artifacts"]
    ]
    native = metadata(s, order).get(bundle["id"])
    result["outlook"] = {"bundle_id": bundle["id"], "draft": native} if native else None
    return result


def view(s, order):
    report, _, baseline, target, fingerprint = plan(s, order)
    public_plan = _clean(report)
    # The ordinary equipment drawer already exposes all facts. This surface is
    # the executable edit plan and outcome, not a second enormous fact graph.
    comparison = public_plan.pop("comparison", {})
    public_plan["summary"] = comparison.get("summary", {})
    automation = deepcopy(_state(order).get("automation"))
    if automation and automation.get("job_id"):
        job = s.scalar(scoped(Job).where(Job.id == automation["job_id"]))
        if job and job.status in {"queued", "running", "failed"}:
            automation["status"] = "working" if job.status == "running" else job.status
            if job.status == "failed":
                automation["error"] = (
                    "Rivet saved its progress but could not finish this task. Open the draft details to continue."
                )
    return {
        "order_id": order.id,
        "version": order.version,
        "baseline_revision": baseline,
        "source_revision": target,
        "source_fingerprint": fingerprint,
        "plan": public_plan,
        "automation": automation,
        "bundles": [
            public_bundle(s, order, b)
            for b in reversed(_state(order).get("bundles", []))
        ],
    }


def prepare(s, order, expected_plan_id=None):
    from .corrections import CorrectionError, prepare_corrections

    report, sources, baseline, target, fingerprint = plan(s, order)
    generated_ids = {
        identifier
        for package in order.state.get("engineering", {}).get("packages", [])
        for identifier in package.get("document_ids", [])
    }
    target_ids = {
        source.get("id") if isinstance(source, dict) else source.id
        for source in sources["target"]
    }
    if generated_ids & target_ids:
        fail(
            "These files are generated from the equipment model. Apply the reviewed comment to the model in Review so every sheet and the BOM regenerate together.",
            409,
        )
    if expected_plan_id and report["plan_id"] != expected_plan_id:
        fail(
            "The source files or edit plan changed. Refresh before preparing the draft.",
            409,
        )
    for existing in _state(order).get("bundles", []):
        if (
            existing["plan_id"] == report["plan_id"]
            and existing["source_fingerprint"] == fingerprint
        ):
            _recheck(s, order, existing)
            return existing
    if report["status"] != "ready":
        fail(
            {
                "message": "Rivet cannot prepare this package automatically.",
                "blockers": _clean(report.get("blockers", [])),
            },
            409,
        )
    bundle_id = str(
        uuid5(
            NAMESPACE_URL,
            f"rivet-equipment:{current_org()}:{order.id}:{fingerprint}:{report['plan_id']}",
        )
    )
    destination = _location(order, bundle_id)
    journal = destination / "_rivet-workflow.json"
    target_revision = f"{target[:30]}-R1"
    if journal.is_file() and not journal.is_symlink():
        bundle = json.loads(journal.read_text())
        if (
            bundle.get("source_fingerprint") != fingerprint
            or bundle.get("id") != bundle_id
        ):
            fail(
                "An interrupted package cannot be reconciled. Original files are preserved.",
                409,
            )
    else:
        if destination.exists():
            # A crash may occur after native outputs were published but before
            # our durable manifest/DB checkpoint. Preserve that orphan for
            # inspection, then regenerate from authoritative originals. Never
            # adopt an unsigned orphan manifest as authority for CAD geometry.
            destination.rename(
                destination.with_name(bundle_id + "-interrupted-" + uuid4().hex[:12])
            )
        try:
            result = prepare_corrections(
                sources["baseline"],
                sources["target"],
                sources["requirements"],
                destination=destination,
                expected_plan_id=report.get("file_plan_id", report["plan_id"]),
                target_revision=target_revision,
            )
        except CorrectionError as exc:
            fail(
                {
                    "message": str(exc),
                    "blockers": _clean(
                        (getattr(exc, "plan", None) or {}).get("blockers", [])
                    ),
                },
                409,
            )
        bundle = {
            **result,
            "plan_id": report["plan_id"],
            "profile_signature": report.get("profile_signature"),
            "id": bundle_id,
            "order_id": order.id,
            "source_fingerprint": fingerprint,
            "baseline_revision": baseline,
            "source_revision": target,
            "target_revision": target_revision,
            "created_at": now().isoformat(),
            "prepared_by": current_actor(),
            "verification": {
                "summary": result["verification"]["summary"],
                "coverage": result["verification"].get("coverage", {}),
            },
        }
        temporary_journal = destination / "_rivet-workflow.tmp"
        temporary_journal.write_text(json.dumps(bundle, default=str, indent=2) + "\n")
        temporary_journal.replace(journal)
    rechecked = _recheck_unstored(s, order, bundle)
    bundle["verification"]["summary"] = rechecked["summary"]
    bundle["verification"]["data_rules"] = {
        k: v for k, v in rechecked["data_rules"].items() if k != "checks"
    }
    if rechecked.get("company_rules"):
        bundle["company_rules"] = rechecked["company_rules"]
    state = deepcopy(order.state)
    progress = deepcopy(_state(order))
    progress["bundles"] = [*progress.get("bundles", []), bundle]
    progress["automation"] = {
        "status": "checked",
        "bundle_id": bundle_id,
        "fingerprint": fingerprint,
        "at": now().isoformat(),
    }
    state["equipment_corrections"] = progress
    order.state = state
    orders.commit(
        s,
        order,
        f"Rivet corrected the drawing and BOM, then checked Rev {target_revision} against the requirements.",
        "equipment_corrected",
        {
            "bundle_id": bundle_id,
            "target_revision": target_revision,
            "changes": len(bundle.get("changes", [])),
            "verification": bundle["verification"],
        },
    )
    return bundle


def _recheck_unstored(s, order, bundle):
    # Artifact membership is checked through the same accessor before the new
    # bundle is committed; temporarily extending the in-memory state is undone.
    original = order.state
    order.state = {
        **original,
        "equipment_corrections": {
            **_state(order),
            "bundles": [*_state(order).get("bundles", []), bundle],
        },
    }
    try:
        return _recheck(s, order, bundle)
    finally:
        order.state = original


def outlook_bundle(s, order, bundle_id):
    if any(
        entry.get("id") == bundle_id
        for entry in order.state.get("engineering", {}).get("review_deliveries", [])
    ):
        from backend.engineering.service import review_outlook_bundle

        return review_outlook_bundle(s, order, bundle_id)
    bundle = _entry(s, order, bundle_id)
    if bundle.get("status") != "prepared_draft" or not bundle.get("attachable"):
        fail("Only independently checked draft copies can be placed in Outlook.", 409)
    recheck = _recheck(s, order, bundle)
    outputs = []
    for item in bundle["artifacts"]:
        if item["role"] not in {"drawing", "bom", "preview"}:
            continue
        path, _ = artifact(s, order, bundle_id, item["name"])
        outputs.append(
            {
                "id": f"{bundle_id}:{item['name']}",
                "name": item["name"],
                "path": str(path),
                "sha256": item["sha256"],
                "content_type": item["media_type"],
                "role": item["role"],
            }
        )
    changes = "\n".join(
        (
            f"- {change['requirement_evidence'][0]['text']}"
            if change.get("kind") and change.get("requirement_evidence")
            else f"- {change.get('device_tag', '')} {change.get('field', '').replace('_', ' ')}: {change.get('after', change.get('replacement_value', 'updated'))}"
        )
        for change in bundle.get("changes", [])
    )
    return {
        "id": bundle_id,
        "order_id": order.id,
        "status": "draft",
        "current": True,
        "fingerprint": digest(
            [bundle["source_fingerprint"], [a["sha256"] for a in outputs]]
        ),
        "source_fingerprint": bundle["source_fingerprint"],
        "outputs": outputs,
        "subject": f"{order.number} · corrected drawing and BOM · {bundle['target_revision']} · draft for review",
        "body": f"Please review the attached drawing and bill of materials, draft revision {bundle['target_revision']}.\n\nPrepared changes:\n{changes}\n\nRivet reopened and rechecked the saved files against the supplied requirements. The editable drawing, bill of materials and PDF review copy are attached. Engineering approval remains required before release.\n",
        "recheck": recheck,
        "engineering_approved": False,
        "production_released": False,
    }


def schedule(s, order):
    """Opted-in revision ingestion schedules bounded file work, never a send."""
    try:
        report, _, _, _, fingerprint = plan(s, order)
    except ImportError:
        return None
    if report.get("status") != "ready":
        return None
    if any(
        b.get("source_fingerprint") == fingerprint
        and b.get("plan_id") == report["plan_id"]
        for b in _state(order).get("bundles", [])
    ):
        return None
    key = "equipment-corrections:" + digest([order.id, fingerprint, report["plan_id"]])
    existing = s.scalar(scoped(Job).where(Job.key == key))
    if existing:
        return existing
    job = Job(
        project_id=order.project_id,
        kind="equipment_corrections",
        key=key,
        payload={
            "order_id": order.id,
            "source_fingerprint": fingerprint,
            "plan_id": report["plan_id"],
        },
    )
    s.add(job)
    s.flush()
    state = deepcopy(order.state)
    progress = deepcopy(_state(order))
    progress["automation"] = {
        "status": "queued",
        "job_id": job.id,
        "fingerprint": fingerprint,
        "at": now().isoformat(),
    }
    state["equipment_corrections"] = progress
    order.state = state
    return job


def process(payload, leased=lambda: True, *, client_factory=None):
    if not leased():
        return {"status": "cancelled"}
    with Session.begin() as s:
        order = orders.lock_order(s, payload["order_id"])
        current_plan, _, _, _, current = plan(s, order)
        if (
            current != payload["source_fingerprint"]
            or current_plan["plan_id"] != payload["plan_id"]
        ):
            return {"status": "superseded"}
        bundle = prepare(s, order, payload["plan_id"])
        bundle_id = bundle["id"]
        from backend.autopilot.service import settings

        if not settings(s)["draft_in_outlook"]:
            progress = deepcopy(_state(order))
            progress["automation"] = {
                **progress.get("automation", {}),
                "status": "checked",
                "detail": "The corrected files are checked. Automatic Outlook drafts are switched off in settings.",
            }
            order.state = {**order.state, "equipment_corrections": progress}
            return {"status": "checked", "bundle_id": bundle_id, "outlook": "disabled"}
    if not leased():
        return {"status": "checked", "bundle_id": bundle_id}
    from .outlook import save

    return save(payload["order_id"], bundle_id, client_factory=client_factory)


def sync_outcomes(limit=20):
    """Worker-owned tenant discovery; every mailbox read re-enters its tenant."""
    from sqlalchemy import select

    from backend.identity import Identity, identity_scope
    from backend.storage.models import Order

    from .outlook import sync_due

    with Session() as s:
        organizations = list(
            s.scalars(
                select(Order.organization_id)
                .where(Order.state.op("?")("equipment_outlook"))
                .distinct()
                .limit(limit)
            )
        )
    results = []
    for organization_id in organizations:
        with identity_scope(
            Identity(organization_id, "Rivet correction outcome tracker")
        ):
            results.extend(sync_due(limit=limit))
    return results
