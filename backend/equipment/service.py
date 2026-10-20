"""Order-scoped equipment packages and checks of actual, immutable originals.

This is an evidence projection, not an engineering approval or a CAD authoring
service. A saved response, comment status or model assertion never supplies the
observed value of a drawing or BOM.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode

from backend.domain.service import digest, fail, get, scoped
from backend.orders import service as orders
from backend.storage import db
from backend.storage.models import Document, Project, now

ROLES = {"drawing", "bom", "specification", "markup"}
ROLE_ALIASES = {
    "approval_drawing": "drawing",
    "one_line": "drawing",
    "markups": "markup",
    "spec": "specification",
}
LIMITATIONS = [
    "Checks explicit device tags and supported fields in the selected drawing and BOM originals. Comments and replies are not evidence that a change was made.",
    "Observed devices are not a certified complete equipment inventory. Unreadable pages, ambiguous tags and unsupported fields remain unverified.",
    "This check does not validate circuit topology, protection coordination, clearances, regulatory compliance or production release. Engineering approval remains separate.",
]


def documents(s, order):
    return list(
        s.scalars(
            scoped(Document)
            .where(Document.project_id == order.project_id)
            .order_by(Document.created_at, Document.id)
        )
    )


def document_info(document, order):
    meta = document.meta or {}
    registration = meta.get("equipment_package", {})
    old = next(
        (d for d in order.state.get("documents", []) if d.get("id") == document.id), {}
    )
    role = (
        registration.get("role")
        or meta.get("order_role")
        or old.get("role")
        or document.kind
    )
    role = ROLE_ALIASES.get(role, role)
    # Specification supersession must be deliberate. The older order
    # projection's active flag can reflect arrival order, so it must not
    # discard historical drawing/BOM originals from a before/after check.
    superseded = bool(meta.get("superseded_by") or registration.get("superseded_by"))
    active = not (
        role == "specification"
        and (
            meta.get("active") is False
            or registration.get("active") is False
            or superseded
        )
    )
    return {
        "id": document.id,
        "name": document.name,
        "role": role if role in ROLES else "unknown",
        "revision": str(
            registration.get("revision")
            or meta.get("revision_label")
            or old.get("revision_label")
            or ""
        ),
        "sha256": document.sha256,
        "state": document.state,
        "created_at": document.created_at.isoformat(),
        "format": Path(document.name).suffix.lower().lstrip("."),
        "active": active,
        "inactive_reason": (
            "Superseded specification" if superseded else "Inactive specification"
        )
        if not active
        else None,
    }


def original_path(document):
    path = (db.BLOBS / document.blob).resolve()
    if not path.is_relative_to(db.BLOBS.resolve()) or not path.is_file():
        fail("The equipment source original is unavailable.", 409)
    return path


def source_input(document, info):
    return {
        "id": document.id,
        "name": document.name,
        "role": info["role"],
        "revision": info["revision"] or "Unspecified",
        "sha256": document.sha256,
        "path": str(original_path(document)),
        "lifecycle": (document.meta or {}).get("review_status", "draft"),
    }


def selected_sources(s, order, baseline_revision=None, target_revision=None):
    from .comparison import SCHEMA_VERSION

    docs = documents(s, order)
    infos = [document_info(d, order) for d in docs]
    packages = {}
    for info in infos:
        if info["role"] in {"drawing", "bom"} and info["revision"]:
            packages.setdefault(info["revision"], []).append(info)
    revisions = list(packages)
    saved = order.state.get("equipment_check", {})
    target = (
        target_revision
        or saved.get("target_revision")
        or (revisions[-1] if revisions else "")
    )
    baseline = (
        baseline_revision
        or saved.get("baseline_revision")
        or (revisions[-2] if len(revisions) > 1 else "")
    )
    # New files in a new revision should be checked without a PM starting a task.
    # Explicit comparisons remain selectable through the check endpoint.
    if (
        target_revision is None
        and revisions
        and target != revisions[-1]
        and revisions[-1] not in saved.get("known_revisions", [])
    ):
        target = revisions[-1]
        baseline = revisions[-2] if len(revisions) > 1 else ""
    if target and target not in packages:
        fail("Select a revision that has drawing or BOM originals.")
    if baseline and baseline not in packages:
        fail("The selected baseline revision is unavailable.")
    if baseline and baseline == target:
        fail("Choose two different revisions.")
    sources = {"baseline": [], "target": [], "requirements": []}
    for document, info in zip(docs, infos):
        group = (
            "requirements"
            if info["role"] == "specification" and info["active"]
            else "baseline"
            if baseline
            and info["revision"] == baseline
            and info["role"] in {"drawing", "bom", "markup"}
            else "target"
            if target
            and info["revision"] == target
            and info["role"] in {"drawing", "bom", "markup"}
            else None
        )
        if group:
            try:
                sources[group].append(source_input(document, info))
            except Exception as exc:
                from fastapi import HTTPException

                if not isinstance(exc, HTTPException):
                    raise
                # Missing originals still enter the engine as missing evidence.
                sources[group].append(
                    {
                        "id": document.id,
                        "name": document.name,
                        "role": info["role"],
                        "revision": info["revision"] or "Unspecified",
                        "sha256": document.sha256,
                        "path": str(db.BLOBS / "__unavailable_equipment_original__"),
                        "lifecycle": "draft",
                    }
                )
    if sum(map(len, sources.values())) > 60:
        fail(
            "This equipment check supports up to 60 source files. Split the package into named drawing sets."
        )
    signature_parts = []
    for group, inputs in sources.items():
        for item in inputs:
            path = Path(item["path"])
            actual = (
                sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
            )
            signature_parts.append(
                [
                    group,
                    item["id"],
                    item["sha256"],
                    actual,
                    item["role"],
                    item["revision"],
                ]
            )
    fingerprint = digest(
        {
            "schema_version": SCHEMA_VERSION,
            "sources": signature_parts,
            "baseline": baseline,
            "target": target,
        }
    )
    return (
        infos,
        [{"revision": r, "documents": ds} for r, ds in packages.items()],
        sources,
        baseline,
        target,
        fingerprint,
    )


@lru_cache(maxsize=24)
def _compare_cached(fingerprint, encoded):
    import json

    from .comparison import compare_packages

    data = json.loads(encoded)
    return compare_packages(
        data["baseline"],
        data["target"],
        data["requirements"],
        scope_id=data.get("scope_id"),
        source_context=data.get("source_context"),
        identity_profile=data.get("identity_profile"),
        identity_source_catalog=data.get("identity_source_catalog"),
    )


def raw_comparison(
    sources,
    fingerprint,
    *,
    scope_id=None,
    source_context=None,
    identity_profile=None,
    identity_source_catalog=None,
):
    import json

    encoded = {
        **sources,
        "scope_id": scope_id,
        "source_context": source_context,
        "identity_profile": identity_profile.model_dump(mode="json")
        if hasattr(identity_profile, "model_dump")
        else identity_profile,
        "identity_source_catalog": identity_source_catalog,
    }
    return deepcopy(_compare_cached(fingerprint, json.dumps(encoded, sort_keys=True)))


def raw_for_order(s, order, sources, fingerprint):
    """Bind decisions to the owned order and its current reviewed identity profile."""
    from . import profile_service

    profile = profile_service.context(s, order)
    return raw_comparison(
        sources,
        fingerprint,
        scope_id=f"order:{order.organization_id}:{order.id}",
        identity_profile=profile["profile"] if not profile["issues"] else None,
        identity_source_catalog=profile["source_catalog"],
    )


def all_facts(raw):
    return [
        f
        for side in ("baseline", "target", "requirements")
        for f in raw.get(side, {}).get("facts", [])
        + raw.get(side, {}).get("requested_changes", [])
    ]


def evidence(order_id, fact, info):
    loc = fact.get("evidence", {})
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    return {
        "fact_id": fact["id"],
        "document_id": fact["source_id"],
        "name": info.get("name", "Source original"),
        "role": fact.get("role", info.get("role")),
        "revision": fact.get("revision", info.get("revision")),
        "sha256": fact.get("sha256", info.get("sha256")),
        **loc,
        "excerpt": fact.get("raw_value") or str(fact.get("value", "")),
        "url": f"/orders/{order_id}/equipment/files/{fact['source_id']}",
        "crop_url": (
            f"/orders/{order_id}/equipment/evidence?"
            + urlencode({"document_id": fact["source_id"], "fact_id": fact["id"]})
        )
        if info.get("format") == "pdf" and loc.get("page")
        else None,
    }


def normalize_comparison(order, raw, infos, baseline, target, fingerprint):
    from .evidence.integration import decision_for, ensure_reconciled

    raw = ensure_reconciled(raw)
    info_by_id = {i["id"]: i for i in infos}
    groups = {
        side: raw.get(side, {}).get("facts", [])
        for side in ("baseline", "target", "requirements")
    }

    def cell(side, tag, field, role=None):
        facts = [
            f
            for f in groups[side]
            if f.get("device_tag") == tag
            and f.get("field") == field
            and (not role or f.get("role") == role)
        ]
        decision = decision_for(raw, tag, field, side)
        return {
            "value": str(decision["value"])
            if decision and decision.get("value") is not None
            else None,
            "unit": decision.get("unit") if decision else None,
            "decision": decision,
            "raw_values": [
                {"value": f.get("value"), "unit": f.get("unit")} for f in facts
            ],
            "evidence": [
                evidence(order.id, f, info_by_id.get(f["source_id"], {})) for f in facts
            ],
        }

    checks = []
    for check in raw.get("checks", []):
        tag, field = check.get("device_tag", ""), check.get("field", "")
        status = check.get("status", "unknown")

        def observed(key, side, role=None, check=check, tag=tag, field=field):
            observation = check.get(key)
            if observation and observation.get("evidence"):
                values = observation.get("values", [])
                value = observation.get("value")
                if value is None and values and not observation.get("decision"):
                    value = " / ".join(str(v.get("value", "")) for v in values)
                return {
                    "value": str(value) if value is not None else None,
                    "unit": observation.get("unit"),
                    "decision": observation.get("decision"),
                    "raw_values": observation.get("raw_values", values),
                    "evidence": [
                        evidence(order.id, f, info_by_id.get(f["source_id"], {}))
                        for f in observation["evidence"]
                    ],
                }
            return cell(side, tag, field, role)

        required = observed("required", "requirements")
        if not required["evidence"] and check.get("requested", {}).get("evidence"):
            required = observed("requested", "target")
        checks.append(
            {
                **check,
                "label": check.get("label") or field.replace("_", " ").capitalize(),
                "baseline_drawing": observed("baseline_drawing", "baseline", "drawing"),
                "baseline_bom": observed("baseline_bom", "baseline", "bom"),
                "target_drawing": observed("target_drawing", "target", "drawing"),
                "target_bom": observed("target_bom", "target", "bom"),
                "required": required,
                "status": status,
                "reason": check.get("summary") or check.get("reason", ""),
            }
        )
    summary = {
        "verified": sum(c["status"] == "verified" for c in checks),
        "remaining": sum(c["status"] in {"mismatch", "missing"} for c in checks),
        "cannot_verify": sum(
            c["status"] not in {"verified", "mismatch", "missing", "unchanged"}
            for c in checks
        ),
        "unchanged": sum(c["status"] == "unchanged" for c in checks),
        "total": len(checks),
        "outcome": raw.get("summary", {}).get("outcome", "cannot_verify"),
        "source_issues": raw.get("summary", {}).get("source_issues", 0),
        "revision_changes_verified": raw.get("summary", {}).get(
            "revision_changes_verified", False
        ),
    }
    return {
        "baseline_revision": baseline,
        "target_revision": target,
        "summary": summary,
        "checks": checks,
        "coverage": raw.get("coverage")
        or {side: raw.get(side, {}).get("coverage", {}) for side in groups},
        "warnings": list(
            dict.fromkeys(
                w.get("message", "Source coverage needs review.")
                if isinstance(w, dict)
                else str(w)
                for w in raw.get("warnings", [])
            )
        ),
        "model": raw.get("target", {}).get("devices", []),
        "decisions": deepcopy(raw.get("decisions", [])),
        "reconciliation": deepcopy(raw.get("reconciliation", {})),
        "evidence_graph": deepcopy(raw.get("evidence_graph", {})),
        "checked_at": now().isoformat(),
        "stale": False,
        "fingerprint": fingerprint,
        "verification_scope": "Selected equipment fields in drawing and BOM originals; not engineering approval.",
    }


def view(s, order, baseline_revision=None, target_revision=None):
    infos, packages, sources, baseline, target, fingerprint = selected_sources(
        s, order, baseline_revision, target_revision
    )
    comparison = None
    if target:
        raw = raw_for_order(s, order, sources, fingerprint)
        comparison = normalize_comparison(
            order, raw, infos, baseline, target, fingerprint
        )
        saved = order.state.get("equipment_check", {})
        from .evidence.store import content_hash

        comparison["saved_check_current"] = saved.get(
            "fingerprint"
        ) == fingerprint and saved.get("evidence_receipt", {}).get(
            "graph_hash"
        ) == content_hash(comparison["evidence_graph"])
    return {
        "order_id": order.id,
        "version": order.version,
        "available_documents": infos,
        "packages": packages,
        "comparison": comparison,
        "limitations": LIMITATIONS,
    }


def save_check(s, order, baseline_revision, target_revision):
    result = view(s, order, baseline_revision, target_revision)
    report = result["comparison"]
    if not report:
        fail("Add drawing and BOM revisions before checking changes.")
    from .evidence.store import append_graph

    receipt = append_graph(s, order, report["evidence_graph"])
    report["evidence_receipt"] = receipt
    saved = order.state.get("equipment_check", {})
    if (
        saved.get("fingerprint") == report["fingerprint"]
        and saved.get("source_manifest")
        and saved.get("evidence_receipt", {}).get("graph_hash") == receipt["graph_hash"]
    ):
        report["checked_at"] = saved.get("checked_at", report["checked_at"])
        report["saved_check_current"] = True
        return result
    state = deepcopy(order.state)
    _, _, inputs, _, _, _ = selected_sources(
        s, order, baseline_revision, target_revision
    )
    from .model_service import project

    model = project(
        raw_for_order(s, order, inputs, report["fingerprint"]),
        report["fingerprint"],
        baseline_revision,
        target_revision,
        inputs["target"],
        inputs["baseline"],
    )
    state["equipment_model"] = {
        "version": model["version"],
        "schema_version": model["schema_version"],
        "baseline_revision": baseline_revision,
        "target_revision": target_revision,
        "observed_device_count": model["observed_device_count"],
        "model_hash": digest(model),
    }
    state["equipment_check"] = {
        "baseline_revision": baseline_revision,
        "target_revision": target_revision,
        "fingerprint": report["fingerprint"],
        "checked_at": report["checked_at"],
        "summary": report["summary"],
        "evidence_receipt": receipt,
        "known_revisions": [p["revision"] for p in result["packages"]],
        # Retain the exact selected source membership as later uploads can add
        # more files to the same named revision. Storage paths remain private.
        "source_manifest": {
            side: [
                {k: item[k] for k in ("id", "name", "role", "revision", "sha256")}
                for item in sources
            ]
            for side, sources in inputs.items()
        },
    }
    order.state = state
    counts = report["summary"]
    summary = (
        f"Rivet compared drawing and BOM Rev {baseline_revision} → {target_revision}: {counts['verified']} verified, {counts['remaining']} remaining, {counts['cannot_verify']} unverified."
        if any(counts[key] for key in ("verified", "remaining", "cannot_verify"))
        else f"Rivet read the drawing and BOM for Rev {baseline_revision} → {target_revision}."
    )
    orders.commit(
        s,
        order,
        summary,
        "equipment_check",
        {"equipment_check": deepcopy(state["equipment_check"])},
    )
    result["version"] = order.version
    report["saved_check_current"] = True
    from .workflow import schedule

    schedule(s, order)
    return result


def synchronize(s, order):
    """Recheck opted-in orders on normal upload/worker ingestion, without AI calls."""
    docs = documents(s, order)
    if not order.state.get("equipment_check") and not any(
        (d.meta or {}).get("equipment_package") for d in docs
    ):
        return
    _, packages, _, baseline, target, fingerprint = selected_sources(s, order)
    if (
        not baseline
        or not target
        or order.state.get("equipment_check", {}).get("fingerprint") == fingerprint
    ):
        return
    by_revision = {p["revision"]: {d["role"] for d in p["documents"]} for p in packages}
    if not all(
        {"drawing", "bom"} <= by_revision.get(r, set()) for r in (baseline, target)
    ):
        return
    save_check(s, order, baseline, target)


def release_blockers(s, order):
    """A comment decision cannot waive a failed or outdated original-file check.

    Legacy orders do not opt in merely by having old drawing records. For orders
    using equipment packages, recompute from the originals and require the saved
    check to cover the latest uploaded revision, even while viewing history.
    This supplements the ordinary engineering/signature gates, never replaces them.
    """
    docs = documents(s, order)
    saved = order.state.get("equipment_check", {})
    if not saved and not any((d.meta or {}).get("equipment_package") for d in docs):
        return []
    if not saved:
        return ["The drawing and BOM revision comparison is not complete."]
    _, packages, sources, baseline, target, fingerprint = selected_sources(s, order)
    if not packages or target != packages[-1]["revision"]:
        return [
            "Check the latest drawing and BOM revision before release; the saved comparison is historical."
        ]
    if not baseline or saved.get("fingerprint") != fingerprint:
        return [
            "The drawing or BOM sources changed. Recheck the current revision before release."
        ]
    from .evidence.integration import ensure_reconciled

    # A source fingerprint alone cannot validate a cached result after the
    # solver, catalog, calibration or evidence projection has changed.
    raw = ensure_reconciled(raw_for_order(s, order, sources, fingerprint))
    result = raw.get("summary", {})
    if (
        result.get("outcome") != "supported_fields_match"
        or not result.get("revision_changes_verified")
        or result.get("source_issues", 0)
        or not raw.get("coverage", {}).get("baseline_comparison_available")
    ):
        return [
            "The drawing and BOM comparison still has changes or evidence that could not be verified."
        ]
    return []


def register_existing(s, order, document_ids, role, revision):
    for document_id in document_ids:
        document = get(s, Document, document_id)
        if document.project_id != order.project_id:
            fail("The equipment source belongs to another order.", 403)
        registration = (document.meta or {}).get("equipment_package")
        if registration and (
            registration.get("role") != role or registration.get("revision") != revision
        ):
            fail(
                "This original is already registered to an equipment role and revision. "
                "Upload a separate revision copy to preserve its checked history.",
                409,
            )
        document.meta = {
            **(document.meta or {}),
            "equipment_package": {
                **(registration or {}),
                "role": role,
                "revision": revision,
            },
        }
    s.flush()


def add_native_dxf(s, order, name, data, metadata):
    """Register native CAD without pretending the ordinary PDF parser read it."""
    import io

    import ezdxf

    from backend.api.main import invalidate
    from backend.ingestion.parser import persist_blob
    from backend.storage.models import Event, Quote

    try:
        text = data.decode("utf-8-sig")
        drawing = ezdxf.read(io.StringIO(text))
        if len(drawing.modelspace()) > 30000:
            fail("The DXF exceeds this check's 30,000-entity limit.")
    except Exception as exc:
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            raise
        fail("This DXF cannot be read. Export a text DXF with device block attributes.")
    name = Path(name).name[:240]
    checksum = sha256(data).hexdigest()
    for existing in s.scalars(
        scoped(Document).where(
            Document.project_id == order.project_id,
            Document.name == name,
            Document.sha256 == checksum,
        )
    ):
        if (existing.meta or {}).get("equipment_package") == metadata[
            "equipment_package"
        ]:
            return existing
    blob, checksum = persist_blob(data, ".dxf")
    document = Document(
        project_id=order.project_id,
        name=name,
        kind="approval_drawing",
        blob=blob,
        sha256=checksum,
        state="parsed",
        meta=metadata,
        coverage=[
            {
                "label": "Native DXF",
                "state": "partial",
                "detail": "Explicit device block attributes only. No circuit topology or geometry validation.",
            }
        ],
    )
    s.add(document)
    project = get(s, Project, order.project_id, True)
    project.input_revision += 1
    quote = s.scalar(
        scoped(Quote).where(Quote.project_id == project.id).with_for_update()
    )
    invalidate(s, quote)
    s.add(Event(project_id=project.id, summary="Uploaded " + name, kind="upload"))
    s.flush()
    orders.refresh(s, order)
    return document
