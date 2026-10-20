"""Order-owned engineering stages built on the existing revision/evidence store.

Every decision is an OrderRevision snapshot. Originals, earlier configurations,
generated files and evidence claims are never overwritten by a later decision.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select

from backend.domain.service import digest, fail, get, scoped
from backend.identity import current_actor, current_org
from backend.orders import service as orders
from backend.storage import db
from backend.storage.models import Document, Order, OrderRevision, Project, Span, now

from .requirements import (
    CUSTOMER_ROLES,
    SOURCE_ROLES,
    extract_requirements,
    find_conflicts,
    reviewed_value,
    to_config_inputs,
)


def state(order):
    return deepcopy(
        order.state.get(
            "engineering",
            {
                "schema_version": "rivet.engineering-order.v1",
                "model_id": "engineering:" + order.id,
                "model_version": 0,
                "source_roles": {},
                "requirements": None,
                "configuration": None,
                "selected_design": None,
                "packages": [],
                "approval": None,
                "rule_history": [],
                "engineering_approved": False,
                "production_released": False,
            },
        )
    )


def _documents(s, order):
    return list(
        s.scalars(
            scoped(Document)
            .where(Document.project_id == order.project_id)
            .order_by(Document.created_at, Document.id)
        )
    )


def source_signature(s, order, data):
    ids = {k for k, role in data["source_roles"].items() if role in CUSTOMER_ROLES}
    # Scalar queries bypass cached ORM objects after a concurrent parser write.
    documents = s.execute(
        select(
            Document.id,
            Document.sha256,
            Document.state,
            Document.coverage,
            Document.blob,
        )
        .where(
            Document.organization_id == current_org(),
            Document.project_id == order.project_id,
            Document.id.in_(ids),
        )
        .order_by(Document.id)
    ).all()
    rows = []
    for identifier, checksum, status, coverage, blob in documents:
        spans = s.execute(
            select(Span.id, Span.text, Span.location)
            .where(
                Span.organization_id == current_org(), Span.document_id == identifier
            )
            .order_by(Span.id)
        ).all()
        path = db.BLOBS / blob
        actual = (
            sha256(path.read_bytes()).hexdigest()
            if path.is_file() and not path.is_symlink()
            else "missing"
        )
        rows.append(
            [
                identifier,
                data["source_roles"].get(identifier),
                checksum,
                actual,
                status,
                coverage,
                [[sid, digest(text), location] for sid, text, location in spans],
            ]
        )
    return digest(rows)


def _save(s, order, data, summary, *, advance=True):
    if advance:
        data["model_version"] += 1
        data["approval"] = None
        data["engineering_approved"] = False
        data["production_released"] = False
        data.pop("release_export", None)
    data["updated_at"] = now().isoformat()
    order.state = {**order.state, "engineering": deepcopy(data)}
    orders.commit(
        s,
        order,
        summary,
        "engineering",
        {"model_id": data["model_id"], "model_version": data["model_version"]},
    )


def _current(s, order, data):
    if data.get("requirements") is None:
        fail("Classify customer inputs and extract requirements first.", 409)
    if data.get("source_signature") != source_signature(s, order, data):
        fail(
            "Customer inputs changed. Extract and confirm current requirements before continuing.",
            409,
        )


def _effective_rules(data):
    from .configurator import load_rule_pack

    return data.get("rule_pack") or load_rule_pack()


def _generation_assets(s, order, data):
    """Resolve reviewed mappings to owned, unchanged originals; never take paths."""
    from .generation import GenerationError, _select_cut_sheets, _snapshot, _template

    settings = data.get("generation_settings") or {}
    documents = {doc.id: doc for doc in _documents(s, order)}

    def original(identifier, checksum, role, suffix):
        doc = documents.get(identifier)
        if not doc or data["source_roles"].get(identifier) != role:
            fail("A drawing asset must be classified on this order.", 409)
        path = db.BLOBS / doc.blob
        if (
            doc.sha256 != checksum
            or path.suffix.lower() != suffix
            or not path.is_file()
            or path.is_symlink()
            or sha256(path.read_bytes()).hexdigest() != checksum
        ):
            fail(
                "A mapped drawing asset changed. Review and save its mapping again.",
                409,
            )
        return doc, path

    template = deepcopy(settings.get("template"))
    if template:
        doc, path = original(
            template.pop("library_document_id"),
            template.pop("library_sha256"),
            "manufacturer_reference",
            ".dxf",
        )
        template.update(library_path=str(path), library_sha256=doc.sha256)
        template = _template(template)
        import ezdxf

        library = ezdxf.readfile(path)
        if any(name not in library.blocks for name in template["blocks"].values()):
            raise GenerationError(
                "The selected block names do not exist in the owned DXF library."
            )
    cut_sheets = []
    for mapping in settings.get("cut_sheets", []):
        doc, path = original(
            mapping["document_id"],
            mapping["document_sha256"],
            "component_cut_sheet",
            ".pdf",
        )
        cut_sheets.append(
            {
                "path": str(path),
                "sha256": doc.sha256,
                "component_ids": mapping["component_ids"],
                "pages": mapping["pages"],
                "identity": mapping["identity"],
                "verified": True,
                "source_ref": {
                    "source_id": doc.id,
                    "sha256": doc.sha256,
                    "pages": mapping["pages"],
                    "reviewed_by": mapping["reviewed_by"],
                    "reviewed_at": mapping["reviewed_at"],
                    "reason": mapping["reason"],
                },
            }
        )
    if cut_sheets:
        if not data.get("selected_design"):
            fail("Select a model before mapping supplied-component cut sheets.", 409)
        _, excluded = _select_cut_sheets(_snapshot(data["selected_design"]), cut_sheets)
        if excluded:
            raise GenerationError(
                "Cut-sheet mapping rejected: "
                + "; ".join(e["reason"] for e in excluded)
            )
    signature = digest(
        {"settings": settings, "template": template, "cut_sheets": cut_sheets}
    )
    return template, cut_sheets, signature


def generation_settings(s, order, template, cut_sheets, reason):
    """Version engineer-reviewed asset selection without changing source requirements."""
    data = state(order)
    _current(s, order, data)
    if len(reason.strip()) < 8:
        fail("Explain the drawing asset identity review.")
    own = {doc.id: doc for doc in _documents(s, order)}
    selected_template = deepcopy(template)
    if selected_template:
        allowed = {"id", "version", "library_document_id", "blocks", "elevation_mode"}
        if set(selected_template) - allowed:
            fail("Use an owned DXF document, not a filesystem path or remote library.")
        doc = own.get(selected_template.get("library_document_id"))
        if not doc:
            fail("The drawing block library belongs to another order.", 403)
        selected_template["library_sha256"] = doc.sha256
    mappings = []
    for item in cut_sheets:
        if set(item) - {
            "document_id",
            "component_ids",
            "pages",
            "identity",
            "verified",
        }:
            fail("Unknown cut-sheet mapping field.")
        doc = own.get(item.get("document_id"))
        if not doc:
            fail("The component cut sheet belongs to another order.", 403)
        if item.get("verified") is not True:
            fail(
                "An engineer must explicitly verify the component-to-cut-sheet identity."
            )
        mappings.append(
            {
                **deepcopy(item),
                "document_sha256": doc.sha256,
                "reviewed_by": current_actor(),
                "reviewed_at": now().isoformat(),
                "reason": reason.strip(),
            }
        )
    data["generation_settings"] = {
        "template": selected_template,
        "cut_sheets": mappings,
        "reason": reason.strip(),
        "reviewed_by": current_actor(),
        "reviewed_at": now().isoformat(),
    }
    _generation_assets(s, order, data)
    if data.get("selected_design"):
        data["selected_design"]["model_version"] = data["model_version"] + 1
    _save(s, order, data, "Versioned drawing library and supplied-component cut sheets")


def _precheck_signature(s, order, data):
    """Bind decisions to current evidence, rules, implementation and actual files."""
    package = next(
        (
            p
            for p in reversed(data["packages"])
            if p["model_version"] == data["model_version"]
        ),
        None,
    )
    if not package:
        fail("Generate the current model before precheck or approval.", 409)
    actual = []
    for entry in package["artifacts"]:
        name = Path(entry.get("path", entry.get("name", ""))).name
        path = artifact(s, order, package["id"], name)
        actual.append([name, sha256(path.read_bytes()).hexdigest()])
    implementation = [
        [name, sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()]
        for name in (
            "service.py",
            "requirements.py",
            "readers.py",
            "text_reader.py",
            "configurator.py",
            "generation.py",
            "drawing_verification.py",
            "artifact_inventory.py",
            "precheck.py",
            "source_roles.py",
            "review_model.py",
        )
    ]
    implementation += [
        [
            name,
            sha256(
                (
                    Path(__file__).parents[1] / "equipment" / "evidence" / name
                ).read_bytes()
            ).hexdigest(),
        ]
        for name in ("drawing_reader.py", "table_reader.py", "schema.py")
    ]
    implementation += [
        [
            name,
            sha256(
                (Path(__file__).parents[1] / "equipment" / name).read_bytes()
            ).hexdigest(),
        ]
        for name in ("readers.py", "native_bom.py", "native_model.py")
    ]
    from backend.company_context.brief import order_brief

    customer_history = order_brief(s, order.id)["snapshot"]
    _, _, asset_signature = _generation_assets(s, order, data)
    observation_signature = digest(_source_drawing_observations(order, data))
    return digest(
        [
            data["model_id"],
            data["model_version"],
            data["selected_design"],
            data["requirements"],
            source_signature(s, order, data),
            _effective_rules(data),
            actual,
            implementation,
            customer_history,
            asset_signature,
            observation_signature,
        ]
    )


def _coverage_blockers(requirements):
    blockers = list(requirements.get("unreadable_pages", []))
    blockers += [
        w
        for w in requirements.get("warnings", [])
        if w.get("verification_required")
        or w.get("deferred_span_ids")
        or "failed" in str(w.get("reason", "")).lower()
    ]
    blockers += [
        coverage
        for coverage in requirements.get("reader_coverage", [])
        if coverage.get("state") in {"partial", "failed"}
    ]
    return blockers


def _source_drawing_observations(order, data):
    import json

    root = _package_root(order, "source-readings")
    reports = []
    for entry in data.get("source_reading_files", []):
        relative = Path(entry["relative_path"])
        path = root / relative
        if relative.is_absolute() or ".." in relative.parts or path.is_symlink():
            fail("The recorded source drawing evidence path is invalid.", 409)
        if (
            not path.is_file()
            or sha256(path.read_bytes()).hexdigest() != entry["sha256"]
        ):
            fail(
                "Source drawing observations changed. Extract current inputs again.",
                409,
            )
        report = json.loads(path.read_text())
        if (
            report.get("source_id") != entry["source_id"]
            or report.get("sha256") != entry["source_sha256"]
        ):
            fail("Source drawing observations have a different original binding.", 409)
        reports.append(report)
    return reports


def view(s, order):
    from typing import get_args

    from .text_reader import Key

    data = state(order)
    data["requirement_fields"] = sorted(
        set(get_args(Key))
        | {"part_number", "supplied", "main.part_number", "main.supplied", "main.poles"}
    )
    project = get(s, Project, order.project_id)
    data["version"] = order.version
    data["order_id"] = order.id
    data["number"] = order.number
    data["title"] = project.title
    data["stale"] = bool(
        data.get("requirements")
        and data.get("source_signature") != source_signature(s, order, data)
    )
    data["documents"] = [
        {
            "id": d.id,
            "name": d.name,
            "sha256": d.sha256,
            "state": d.state,
            "role": data["source_roles"].get(d.id, "unclassified"),
            "coverage": d.coverage,
            "url": f"/documents/{d.id}/content",
        }
        for d in _documents(s, order)
    ]
    from backend.equipment.outlook import metadata as outlook_metadata
    from backend.lifecycle.service import _comment_map

    comments = list(_comment_map(s, order).values())
    source_ids = {sid for comment in comments for sid in comment.get("source_ids", [])}
    spans = (
        {
            span.id: span
            for span in s.scalars(
                scoped(Span)
                .join(Document, Document.id == Span.document_id)
                .where(Document.project_id == order.project_id, Span.id.in_(source_ids))
            )
        }
        if source_ids
        else {}
    )
    data["review_comments"] = [
        {
            "id": comment["id"],
            "text": comment.get("text", ""),
            "revision": comment.get("revision"),
            "source_refs": [
                {
                    "id": sid,
                    "document_id": spans[sid].document_id,
                    "page": spans[sid].location.get("page"),
                }
                for sid in comment.get("source_ids", [])
                if sid in spans
            ],
        }
        for comment in comments
    ]
    data["outlook_deliveries"] = outlook_metadata(s, order)
    observations = {}
    for item in (data.get("requirements") or {}).get("items", []):
        if not item.get("source_object_key") or not item.get("source_refs"):
            continue
        ref = item["source_refs"][0]
        key = digest(
            [
                ref.get("source_id"),
                ref.get("sha256"),
                ref.get("page"),
                item["source_object_key"],
            ]
        )
        group = observations.setdefault(
            key,
            {
                "id": key,
                "source_id": ref.get("source_id"),
                "source_sha": ref.get("sha256"),
                "page": ref.get("page"),
                "source_object_key": item["source_object_key"],
                "display_tag": item.get("display_tag"),
                "raw_label": item.get("raw_label"),
                "role": item.get("equipment_role"),
                "unresolved_role": bool(item.get("unresolved_role")),
                "values": [],
            },
        )
        group["values"].append(
            {"key": item["key"], "value": item["value"], "unit": item.get("unit")}
        )
    data["equipment_observations"] = list(observations.values())
    for package in data["packages"]:
        for artifact in package.get("artifacts", []):
            name = artifact.pop("path", None)
            artifact["name"] = artifact.get("name") or Path(name or "").name
            artifact["url"] = (
                f"/orders/{order.id}/engineering/packages/{package['id']}/files/{quote(artifact['name'], safe='')}"
            )
    data["history"] = [
        {
            "version": r.version,
            "summary": r.summary,
            "actor": r.actor,
            "created_at": r.created_at.isoformat(),
        }
        for r in s.scalars(
            scoped(OrderRevision)
            .where(OrderRevision.order_id == order.id)
            .order_by(OrderRevision.version.desc())
            .limit(30)
        )
        if r.snapshot.get("state", {}).get("engineering")
    ]
    return data


def decide_source_role(s, order, **decision):
    from backend.equipment.evidence.schema import new_graph
    from backend.equipment.evidence.store import append_graph

    from .source_roles import propose_source_role_decision

    data = state(order)
    _current(s, order, data)
    doc = get(s, Document, decision["source_id"])
    if (
        doc.project_id != order.project_id
        or doc.sha256 != decision["source_sha"]
        or data["source_roles"].get(doc.id) != "customer_one_line"
    ):
        fail("Classify equipment from this order's current customer one-line.", 409)
    proposed = propose_source_role_decision(data["requirements"], **decision)
    data["requirements"] = proposed["requirements_updated"]
    data["requirements"]["version"] = data["model_version"] + 1
    record = {**proposed["decision"], "actor": current_actor(), "at": now().isoformat()}
    data.setdefault("equipment_role_decisions", []).append(record)
    graph = new_graph(
        order.id,
        metadata={"purpose": "engineer_source_equipment_role", "decision": record},
    )
    graph["claims"] = proposed["derived_claims"]
    graph["nodes"] = [
        {"id": subject, "kind": "requirement"}
        for subject in sorted({c["subject_id"] for c in graph["claims"]})
    ]
    append_graph(s, order, graph)
    data["configuration"] = data["selected_design"] = None
    _save(s, order, data, "Engineer classified source equipment: " + decision["role"])


def apply_review_change(
    s, order, *, package_id, comment_id, evidence_id, requirement_id, value, reason
):
    """One explicit engineer decision updates the model, then reuses review tools."""
    from backend.equipment.evidence.schema import new_graph
    from backend.equipment.evidence.store import append_graph
    from backend.lifecycle.service import _comment_map, _sources
    from backend.storage.models import uid

    from .configurator import _digest, _normalized_inputs, validate_configuration
    from .review_model import propose_comment_model_edit

    data = state(order)
    _current(s, order, data)
    package = next((p for p in data["packages"] if p["id"] == package_id), None)
    if (
        not package
        or package["model_version"] != data["model_version"]
        or not data.get("selected_design")
    ):
        fail("Apply this comment to the current generated model package.", 409)
    for entry in package["artifacts"]:
        artifact(s, order, package_id, entry["name"])
    comment = _comment_map(s, order).get(comment_id)
    if not comment or evidence_id not in comment.get("source_ids", []):
        fail("Choose this order's comment and its exact source evidence.", 409)
    _sources(s, order, [evidence_id])
    span = get(s, Span, evidence_id)
    doc = get(s, Document, span.document_id)
    path = db.BLOBS / doc.blob
    if (
        doc.project_id != order.project_id
        or doc.meta.get("active") is False
        or not path.is_file()
        or path.is_symlink()
        or sha256(path.read_bytes()).hexdigest() != doc.sha256
    ):
        fail(
            "The returned comment source changed or no longer belongs to this order.",
            409,
        )
    page = span.location.get("page")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        fail(
            "This comment needs an original physical page anchor before a model change.",
            409,
        )
    proposed = propose_comment_model_edit(
        data["requirements"],
        data["selected_design"],
        requirement_id=requirement_id,
        value=value,
        reason=reason,
        comment_ref={
            "source_id": doc.id,
            "sha256": doc.sha256,
            "page": page,
            "comment_id": comment_id,
            "text": span.text,
            "evidence_ids": [span.id],
            "same_order_checked": True,
        },
    )
    data["requirements"] = proposed["requirements_updated"]
    data["requirements"]["version"] = data["model_version"] + 1
    design = proposed["design_updated"]
    design["model_version"] = data["model_version"] + 1
    design["requirements_digest"] = _digest(
        _normalized_inputs(to_config_inputs(data["requirements"]))
    )
    design.update(
        validate_configuration(
            design,
            to_config_inputs(data["requirements"]),
            rule_pack=_effective_rules(data),
        )
    )
    if design["has_known_rule_violations"]:
        fail(
            {
                "message": "This comment conflicts with a known model constraint. Reconfigure before generating files.",
                "checks": [c for c in design["rule_results"] if c["status"] == "fail"],
            },
            409,
        )
    design.pop("validation_pending", None)
    data["selected_design"] = design
    decision = {
        **proposed["decision"],
        "actor": current_actor(),
        "at": now().isoformat(),
        "package_id": package_id,
    }
    data.setdefault("review_decisions", []).append(decision)
    graph = new_graph(
        order.id,
        metadata={
            "purpose": "engineer_comment_model_change",
            "actor": current_actor(),
            "decision": decision,
        },
    )
    graph["claims"] = proposed["derived_claims"]
    graph["nodes"] = [
        {"id": subject, "kind": "requirement"}
        for subject in sorted({c["subject_id"] for c in graph["claims"]})
    ]
    append_graph(s, order, graph)
    _save(
        s, order, data, "Engineer applied comment to equipment model: " + reason[:120]
    )
    generate(s, order)
    latest = state(order)["packages"][-1]
    register_package(s, order, latest["id"])
    precheck(s, order)
    data = state(order)
    data.setdefault("review_deliveries", []).append(
        {
            "id": uid(),
            "package_id": latest["id"],
            "model_version": data["model_version"],
            "comment_id": comment_id,
            "requirement_id": requirement_id,
            "decision_id": decision["id"],
            "created_at": now().isoformat(),
            "status": "prepared",
        }
    )
    _save(
        s,
        order,
        data,
        "Regenerated model package ready for existing Outlook draft handoff",
        advance=False,
    )


def review_outlook_bundle(s, order, bundle_id):
    """Adapt current model artifacts to the existing draft-only attachment transport."""
    import shutil

    data = state(order)
    _current(s, order, data)
    entry = next(
        (e for e in data.get("review_deliveries", []) if e["id"] == bundle_id), None
    )
    if not entry or entry["model_version"] != data["model_version"]:
        fail("This regenerated draft is no longer the current model.", 409)
    checked = data.get("precheck") or {}
    if (
        checked.get("signature") != _precheck_signature(s, order, data)
        or checked.get("saved_drawing_bom_check", {}).get("status") != "verified"
        or checked.get("source_conformance", {}).get("status") == "fail"
    ):
        fail("Recheck the regenerated saved files before placing them in Outlook.", 409)
    package = next(p for p in data["packages"] if p["id"] == entry["package_id"])
    destination = (
        db.BLOBS / "equipment-corrections" / current_org() / order.id / bundle_id
    )
    if destination.is_symlink() or any(p.is_symlink() for p in destination.parents):
        fail("The draft attachment storage is unavailable.", 409)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    outputs = []
    for artifact_entry in package["artifacts"]:
        name = artifact_entry["name"]
        if name not in {
            "one-line.dxf",
            "front-elevation.dxf",
            "bom.xlsx",
            "submittal.pdf",
        }:
            continue
        source = artifact(s, order, package["id"], name)
        target = destination / name
        if (
            target.is_symlink()
            or target.exists()
            and sha256(target.read_bytes()).hexdigest() != artifact_entry["sha256"]
        ):
            fail(
                "A saved draft attachment changed. Preserve it and regenerate the handoff.",
                409,
            )
        if not target.exists():
            shutil.copyfile(source, target)
            target.chmod(0o600)
        outputs.append(
            {
                "id": bundle_id + ":" + name,
                "name": name,
                "path": str(target),
                "sha256": artifact_entry["sha256"],
                "role": "bom"
                if name.endswith(".xlsx")
                else "preview"
                if name.endswith(".pdf")
                else "drawing",
                "content_type": "application/pdf"
                if name.endswith(".pdf")
                else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if name.endswith(".xlsx")
                else "application/dxf",
            }
        )
    return {
        "id": bundle_id,
        "order_id": order.id,
        "status": "draft",
        "current": True,
        "engineering_approved": False,
        "production_released": False,
        "fingerprint": digest(
            [entry, checked["signature"], [a["sha256"] for a in outputs]]
        ),
        "source_fingerprint": checked["signature"],
        "outputs": outputs,
        "subject": f"{order.number} · proposed revised model {data['model_version']} · review required",
        "body": "Please review the attached regenerated one-line, elevation, BOM and submittal PDF. The comment was applied to the versioned equipment model; the saved files were reread and checked for supported-field consistency. All outputs remain proposed. Engineering, scope and production release remain subject to human approval.",
        "recheck": {
            "summary": {"outcome": "supported_fields_match"},
            "scope": "Saved-file consistency; source and engineering unknowns remain visible in Rivet.",
        },
    }


def classify(s, order, source_roles):
    data = state(order)
    own = {doc.id for doc in _documents(s, order)}
    if any(key not in own for key in source_roles):
        fail("An engineering input belongs to another order.", 403)
    if any(
        role not in SOURCE_ROLES | {"unclassified"} for role in source_roles.values()
    ):
        fail("Unknown engineering source role.")
    data["source_roles"].update(source_roles)
    data["configuration"] = data["selected_design"] = None
    _save(s, order, data, "Classified engineering inputs")


def extract(s, order):
    data = state(order)
    before_signature = source_signature(s, order, data)
    documents = []
    for doc in _documents(s, order):
        role = data["source_roles"].get(doc.id)
        if role not in CUSTOMER_ROLES:
            continue
        if doc.state not in {"parsed", "mapped", "ready"}:
            fail(
                f"Wait for {doc.name} to finish reading before extracting requirements.",
                409,
            )
        path = db.BLOBS / doc.blob
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256(path.read_bytes()).hexdigest() != doc.sha256
        ):
            fail(
                "A customer source original is missing or changed. Restore the original before extraction.",
                409,
            )
        documents.append(
            {
                "id": doc.id,
                "name": doc.name,
                "path": str(db.BLOBS / doc.blob),
                "sha256": doc.sha256,
                "role": role,
                "coverage": doc.coverage,
                "spans": [
                    {"id": span.id, "text": span.text, "location": span.location}
                    for span in s.scalars(
                        scoped(Span).where(Span.document_id == doc.id).order_by(Span.id)
                    )
                ],
            }
        )
    if not documents:
        fail(
            "Select at least one customer specification, one-line, addendum, exception or purchase order."
        )
    requirements = extract_requirements(
        documents, version=data["model_version"] + 1, scope_id=data["model_id"]
    )
    from .text_reader import read_customer_text

    requirements, _ = read_customer_text(
        requirements, documents, output_dir=_package_root(order, "source-text-readings")
    )
    from .readers import read_customer_drawings

    requirements, drawing_reports = read_customer_drawings(
        requirements,
        documents,
        output_dir=_package_root(order, "source-readings"),
        read_notes=True,
    )
    requirements["drawing_readings"] = [
        {
            "source_id": r["source_id"],
            "sha256": r["sha256"],
            "pages": [p["page"] for p in r["pages"]],
        }
        for r in drawing_reports
    ]
    data["source_reading_files"] = []
    for report in drawing_reports:
        for page in report["pages"]:
            relative = (
                Path(report["sha256"][:16])
                / f"page-{page['page']}"
                / "drawing-report.json"
            )
            path = _package_root(order, "source-readings") / relative
            if path.is_file() and not path.is_symlink():
                data["source_reading_files"].append(
                    {
                        "source_id": report["source_id"],
                        "source_sha256": report["sha256"],
                        "relative_path": relative.as_posix(),
                        "sha256": sha256(path.read_bytes()).hexdigest(),
                    }
                )
    if source_signature(s, order, data) != before_signature:
        fail(
            "Customer sources changed during extraction. Their readings are preserved; rerun against the current sources.",
            409,
        )
    # Reuse the immutable evidence graph tables instead of a second claims store.
    from backend.equipment.evidence.schema import new_graph
    from backend.equipment.evidence.store import append_graph

    graph = new_graph(order.id, metadata={"purpose": "customer_requirements"})
    graph["nodes"] = [
        {"id": subject, "kind": "requirement"}
        for subject in sorted(
            {c["subject_id"] for c in requirements["evidence_claims"]}
        )
    ]
    graph["claims"] = requirements["evidence_claims"]
    append_graph(s, order, graph)
    data["requirements"] = requirements
    data["source_signature"] = source_signature(s, order, data)
    data["configuration"] = data["selected_design"] = None
    _save(
        s, order, data, f"Extracted {len(requirements['items'])} proposed requirements"
    )


def confirm(s, order, decisions, reason):
    from backend.equipment.evidence.schema import make_claim, new_graph, stable_id
    from backend.equipment.evidence.store import append_graph

    data = state(order)
    _current(s, order, data)
    requirements = data["requirements"]
    rows = {row["id"]: row for row in requirements["items"]}
    graph = new_graph(
        order.id,
        metadata={
            "purpose": "engineer_requirement_decision",
            "actor": current_actor(),
            "reason": reason,
        },
    )
    for decision in decisions:
        old = rows.get(decision["id"])
        if not old:
            fail("The requirement is not in the current model.", 409)
        if decision.get("dismissed"):
            old["dismissed"] = True
            old["decision"] = {
                "actor": current_actor(),
                "reason": reason,
                "at": now().isoformat(),
            }
            continue
        value = decision.get("value", old["value"])
        if value is None or (isinstance(value, str) and not value.strip()):
            fail(
                "Unknown requirements cannot be confirmed. Correct or dismiss the item."
            )
        value = reviewed_value(old["key"], value)
        # A correction appends evidence derived from the original observation.
        # It never mutates a claim already in the append-only evidence store.
        claim = make_claim(
            old["subject_id"],
            old["key"],
            value,
            unit=old.get("unit"),
            source=old["source_refs"][0],
            extractor={
                "name": "engineer-confirmation",
                "version": "1",
                "run_id": stable_id(current_actor(), reason, order.version),
            },
            raw_value=old["value"],
            derived_from=old["source_claim_ids"],
            state="proposed",
            normalization={
                "valid": True,
                "reason": reason,
                "reviewed_by": current_actor(),
            },
        )
        graph["claims"].append(claim)
        graph["nodes"].append({"id": old["subject_id"], "kind": "requirement"})
        old.update(
            {
                "value": value,
                "confirmed": True,
                "source_claim_ids": [*old["source_claim_ids"], claim["id"]],
                "decision": {
                    "actor": current_actor(),
                    "reason": reason,
                    "at": now().isoformat(),
                },
            }
        )
        requirements["evidence_claims"].append(claim)
    graph["nodes"] = list({node["id"]: node for node in graph["nodes"]}.values())
    if graph["claims"]:
        append_graph(s, order, graph)
    requirements["conflicts"] = find_conflicts(requirements["items"])
    requirements["version"] = data["model_version"] + 1
    data["configuration"] = data["selected_design"] = None
    _save(s, order, data, "Engineer reviewed requirements: " + reason[:160])


def add_requirement(s, order, *, key, subject_id, value, source_id, page, reason):
    """Explicit source-bound human fallback for a missed requirement, never an AI accuracy credit."""
    from typing import get_args

    from backend.equipment.evidence.schema import make_claim, new_graph, stable_id
    from backend.equipment.evidence.store import append_graph

    from .requirements import UNITS
    from .text_reader import Key

    data = state(order)
    _current(s, order, data)
    allowed = set(get_args(Key)) | {
        "part_number",
        "supplied",
        "main.part_number",
        "main.supplied",
        "main.poles",
    }
    if key not in allowed or not subject_id.strip() or len(subject_id) > 180:
        fail("Choose a supported explicit requirement field and subject.")
    if key.startswith("main.") and subject_id != "order":
        fail(
            "Main requirements belong to the order's explicit main, not a guessed feeder."
        )
    doc = get(s, Document, source_id)
    role = data["source_roles"].get(source_id)
    if doc.project_id != order.project_id or role not in CUSTOMER_ROLES:
        fail("Cite one of this order's classified customer input originals.", 403)
    path = db.BLOBS / doc.blob
    if (
        not path.is_file()
        or path.is_symlink()
        or sha256(path.read_bytes()).hexdigest() != doc.sha256
    ):
        fail("The cited original changed. Extract current sources again.", 409)
    if path.suffix.lower() == ".pdf":
        import pymupdf

        with pymupdf.open(path) as pdf:
            if not 1 <= page <= len(pdf):
                fail("Choose an original physical PDF page.")
    elif not any(
        span.location.get("page") == page
        for span in s.scalars(scoped(Span).where(Span.document_id == doc.id))
    ):
        fail("The cited source has no evidence on that physical page.")
    if any(
        row["key"] == key
        and row["subject_id"] == subject_id
        and not row.get("dismissed")
        for row in data["requirements"]["items"]
    ):
        fail("That requirement already exists. Review its current value instead.", 409)
    typed = reviewed_value(
        "spd" if key in {"supplied", "main.supplied"} else key, value
    )
    ref = {
        "source_id": doc.id,
        "sha256": doc.sha256,
        "page": page,
        "human_entered": True,
        "reviewed_by": current_actor(),
        "reason": reason,
    }
    claim = make_claim(
        subject_id,
        key,
        typed,
        source=ref,
        unit=UNITS.get(key),
        state="proposed",
        raw_value=value,
        extractor={
            "name": "explicit-engineer-source-entry",
            "version": "1",
            "run_id": stable_id(order.id, order.version, subject_id, key, typed, ref),
        },
        normalization={
            "valid": True,
            "method": "explicit_human_entry",
            "reason": reason,
        },
    )
    graph = new_graph(
        order.id,
        metadata={"purpose": "manual_missing_requirement", "actor": current_actor()},
    )
    graph["nodes"] = [{"id": subject_id, "kind": "requirement"}]
    graph["claims"] = [claim]
    append_graph(s, order, graph)
    data["requirements"]["evidence_claims"].append(claim)
    data["requirements"]["items"].append(
        {
            "id": stable_id("manual-requirement", claim["id"]),
            "key": key,
            "subject_id": subject_id,
            "value": typed,
            "unit": UNITS.get(key),
            "source_claim_ids": [claim["id"]],
            "source_refs": [ref],
            "confirmed": True,
            "state": "proposed",
            "origin_role": role,
            "value_origin": "explicit_engineer_entry",
            "decision": {
                "actor": current_actor(),
                "reason": reason,
                "at": now().isoformat(),
            },
        }
    )
    data["requirements"]["conflicts"] = find_conflicts(data["requirements"]["items"])
    data["requirements"]["version"] = data["model_version"] + 1
    data["configuration"] = data["selected_design"] = None
    _save(s, order, data, "Engineer added source-bound missing requirement: " + key)


def configure(s, order):
    from .configurator import configure as solve
    from .priors import derive_layout_priors

    data = state(order)
    _current(s, order, data)
    pack = _effective_rules(data)
    jobs = []
    for revision in s.scalars(
        scoped(OrderRevision)
        .join(Order, Order.id == OrderRevision.order_id)
        .join(Project, Project.id == Order.project_id)
        .where(
            Order.organization_id == current_org(),
            Project.organization_id == current_org(),
            Project.synthetic.is_(False),
            Order.id != order.id,
        )
        .order_by(OrderRevision.created_at.desc())
        .limit(1000)
    ):
        history = revision.snapshot.get("state", {}).get("engineering")
        if history and history.get("engineering_approved"):
            jobs.append(
                {
                    "order_id": revision.order_id,
                    "organization_id": current_org(),
                    "synthetic": False,
                    "engineering": history,
                    "source_refs": [
                        {
                            "order_id": revision.order_id,
                            "revision_id": revision.id,
                            "checksum": revision.checksum,
                        }
                    ],
                }
            )
    data["design_priors"] = derive_layout_priors(
        jobs,
        manufacturer=pack["manufacturer"],
        family=pack["family"],
        organization_id=current_org(),
    )
    data["configuration"] = solve(
        to_config_inputs(data["requirements"]),
        rule_pack=pack,
        priors=data.get("design_priors"),
        model_id=data["model_id"],
        model_version=data["model_version"] + 1,
    )
    options = data["configuration"].get("configurations", [])
    data["selected_design"] = options[0] if len(options) == 1 else None
    _save(s, order, data, f"Proposed {len(options)} switchboard configurations")


def select_design(s, order, design_id=None, design=None):
    data = state(order)
    _current(s, order, data)
    selected = _validated_design(data, design_id, design)
    selected["schema_version"] = "rivet.proposed-switchboard-design.v1"
    data["selected_design"] = selected
    _save(s, order, data, "Selected and revalidated proposed configuration")


def preview_design(s, order, design_id=None, design=None):
    data = state(order)
    _current(s, order, data)
    design = _validated_design(data, design_id, design)
    return {
        "order_id": order.id,
        "version": order.version,
        "design": design,
        "validation": {
            key: design[key]
            for key in (
                "rule_results",
                "unresolved_questions",
                "has_known_rule_violations",
                "fully_configured",
            )
        },
        "persisted": False,
    }


def _validated_design(data, design_id=None, design=None):
    from .configurator import validate_configuration

    options = (data.get("configuration") or {}).get("configurations", [])
    selected = deepcopy(
        design or next((d for d in options if d["id"] == design_id), None)
    )
    if not selected:
        fail("Select a current configuration before editing it.", 409)
    # Revalidate structural content; no client-supplied pass/approval can survive.
    for key in (
        "rule_results",
        "engineering_approved",
        "production_released",
        "fully_configured",
        "unresolved_questions",
    ):
        selected.pop(key, None)
    selected.update(
        {
            "model_id": data["model_id"],
            "model_version": data["model_version"] + 1,
            "state": "proposed",
        }
    )
    validation = validate_configuration(
        selected,
        to_config_inputs(data["requirements"]),
        rule_pack=data.get("rule_pack"),
    )
    selected.update(validation)
    selected["engineering_approved"] = selected["production_released"] = False
    return selected


def update_rules(s, order, rule_pack, reason):
    from .configurator import validate_rule_pack

    validate_rule_pack(rule_pack)
    data = state(order)
    data["rule_history"].append(
        {
            "hash": digest(rule_pack),
            "rule_pack": deepcopy(rule_pack),
            "reason": reason,
            "reviewed_by": current_actor(),
            "reviewed_at": now().isoformat(),
        }
    )
    data["rule_pack"] = deepcopy(rule_pack)
    data["configuration"] = data["selected_design"] = None
    _save(s, order, data, "Engineer versioned switchboard rules: " + reason[:160])


def _package_root(order, package_id):
    root = db.BLOBS / "engineering" / current_org() / order.id / package_id
    if (
        root.is_symlink()
        or any(p.is_symlink() for p in root.parents)
        or not root.resolve().is_relative_to(db.BLOBS.resolve())
    ):
        fail("The engineering package location is unavailable.", 409)
    return root


def generate(s, order):
    from backend.storage.models import uid

    from .generation import generate_package

    data = state(order)
    _current(s, order, data)
    design = data.get("selected_design")
    if not design:
        fail("Choose a proposed configuration before generating drawings.", 409)
    from .configurator import validate_configuration

    validation = validate_configuration(
        design, to_config_inputs(data["requirements"]), rule_pack=_effective_rules(data)
    )
    if validation["has_known_rule_violations"]:
        fail(
            "The proposed model violates a known constraint. Correct or reconfigure it before generating files.",
            409,
        )
    template, cut_sheets, asset_signature = _generation_assets(s, order, data)
    existing = next(
        (
            p
            for p in reversed(data["packages"])
            if p["model_version"] == data["model_version"]
            and p.get("source_signature") == data["source_signature"]
        ),
        None,
    )
    if existing:
        if existing.get("generation_input_signature") not in (None, asset_signature):
            fail(
                "Drawing assets changed. Version their reviewed mappings before regenerating.",
                409,
            )
        # One model version has one immutable package. Repeated clicks reuse it;
        # registering it again must not manufacture a second identical revision.
        for entry in existing["artifacts"]:
            artifact(
                s,
                order,
                existing["id"],
                Path(entry.get("path", entry.get("name", ""))).name,
            )
        return
    # Generation writes a new immutable directory. The snapshot and all files
    # carry the same selected model version, never a fresh version per sheet.
    package_id = uid()
    result = generate_package(
        design,
        output_dir=_package_root(order, package_id),
        template=template,
        cut_sheets=cut_sheets,
    )
    result["id"] = package_id
    result["model_version"] = data["model_version"]
    result["source_signature"] = data["source_signature"]
    result["generation_input_signature"] = asset_signature
    data["packages"].append(result)
    data["approval"] = None
    data["engineering_approved"] = False
    _save(s, order, data, "Generated proposed drawing and BOM package", advance=False)


def artifact(s, order, package_id, name):
    package = next((p for p in state(order)["packages"] if p["id"] == package_id), None)
    if not package or not any(
        Path(a.get("path", a.get("name", ""))).name == name
        for a in package["artifacts"]
    ):
        fail("Generated file not found.", 404)
    path = _package_root(order, package_id) / Path(name).name
    entry = next(
        a
        for a in package["artifacts"]
        if Path(a.get("path", a.get("name", ""))).name == name
    )
    if (
        not path.is_file()
        or path.is_symlink()
        or sha256(path.read_bytes()).hexdigest() != entry["sha256"]
    ):
        fail("The generated file is missing or changed; regenerate the package.", 409)
    return path


def register_package(s, order, package_id):
    """Route generated files through the existing revision/correction workflow."""
    from backend.equipment import service as equipment
    from backend.ingestion.parser import parse, persist_blob

    data = state(order)
    _current(s, order, data)
    package = next((p for p in data["packages"] if p["id"] == package_id), None)
    if not package or package["model_version"] != data["model_version"]:
        fail("Generate the current model before starting its review.", 409)
    if package.get("document_ids"):
        return
    revision = f"GEN-{data['model_version']}"
    docs = []
    for entry in package["artifacts"]:
        name = Path(entry.get("path", entry.get("name", ""))).name
        if name not in {
            "one-line.dxf",
            "front-elevation.dxf",
            "bom.xlsx",
            "submittal.pdf",
        }:
            continue
        path = artifact(s, order, package_id, name)
        # The existing native correction executor edits one authoritative DXF
        # and its BOM. The elevation remains a generated supporting sheet and
        # is regenerated from model edits, not silently treated as a second
        # independently editable native original.
        role = (
            "bom"
            if name == "bom.xlsx"
            else "reference"
            if name in {"front-elevation.dxf", "submittal.pdf"}
            else "drawing"
        )
        metadata = {
            "engineering_generated": {
                "model_id": data["model_id"],
                "model_version": data["model_version"],
                "package_id": package_id,
            },
            "equipment_package": {"role": role, "revision": revision},
            "order_role": role,
            "revision_label": revision,
            "review_status": "proposed",
        }
        if path.suffix == ".dxf":
            doc = equipment.add_native_dxf(
                s, order, f"{revision}-{name}", path.read_bytes(), metadata
            )
        else:
            blob, checksum = persist_blob(path.read_bytes(), path.suffix)
            doc = Document(
                project_id=order.project_id,
                name=f"{revision}-{name}",
                blob=blob,
                sha256=checksum,
                kind="bom" if role == "bom" else "approval_drawing",
                meta=metadata,
            )
            s.add(doc)
            s.flush()
            parse(s, doc)
        docs.append(doc.id)
    package["document_ids"] = docs
    package["revision"] = revision
    # add_native_dxf may refresh other equipment state; retain it and only set
    # this engineering snapshot. Older review originals stay registered.
    _save(
        s,
        order,
        data,
        "Added generated package to existing revision review",
        advance=False,
    )
    previous = next(
        (
            p
            for p in reversed(data["packages"])
            if p.get("revision") and p["revision"] != revision
        ),
        None,
    )
    if previous:
        equipment.save_check(s, order, previous["revision"], revision)


def precheck(s, order):
    from backend.company_context.brief import order_brief

    from .configurator import validate_configuration

    data = state(order)
    _current(s, order, data)
    if not data.get("selected_design"):
        fail("Select a model before running precheck.", 409)
    checked = validate_configuration(
        data["selected_design"],
        to_config_inputs(data["requirements"]),
        rule_pack=_effective_rules(data),
    )
    brief = order_brief(s, order.id)
    package = next(
        (
            p
            for p in reversed(data["packages"])
            if p["model_version"] == data["model_version"]
        ),
        None,
    )
    if not package:
        fail("Generate the current model before precheck.", 409)
    from backend.equipment.native_bom import verify_inventory

    from .artifact_inventory import read_generated_inventory
    from .precheck import precheck_source_conformance

    file_check = verify_inventory(
        artifact(s, order, package["id"], "one-line.dxf"),
        artifact(s, order, package["id"], "bom.xlsx"),
    )
    native_inventory = read_generated_inventory(
        drawing_path=artifact(s, order, package["id"], "one-line.dxf"),
        bom_path=artifact(s, order, package["id"], "bom.xlsx"),
        model_id=data["model_id"],
        model_version=data["model_version"],
    )
    observations = _source_drawing_observations(order, data)
    conformance = precheck_source_conformance(
        data["requirements"],
        data["selected_design"],
        generated_inventory=native_inventory,
        customer_brief=brief,
        drawing_observations=observations,
    )
    data["precheck"] = {
        "model_version": data["model_version"],
        "validation": checked,
        "saved_drawing_bom_check": file_check,
        "source_conformance": conformance,
        "signature": _precheck_signature(s, order, data),
        "coverage_blockers": _coverage_blockers(data["requirements"]),
        "conflicts": data["requirements"].get("conflicts", []),
        "unreadable_pages": data["requirements"].get("unreadable_pages", []),
        "customer_preferences": brief.get("preferences", []),
        "customer_history_snapshot": brief["snapshot"],
        "customer_memory_checked": conformance["customer_memory_checked"],
        "customer_memory_note": "Explicit approved customer preferences are checked where typed evidence is available; prose preferences remain an engineer checklist.",
        "customer_one_line_checked": False,
        "customer_one_line_note": "Source-linked scalar/device checks and independently reread saved-file checks are listed separately. Complete one-line topology remains unverified.",
        "engineering_approved": False,
    }
    _save(
        s,
        order,
        data,
        "Prechecked proposed model against extracted requirements",
        advance=False,
    )


def approve(s, order, signer, reason):
    data = state(order)
    _current(s, order, data)
    design = data.get("selected_design") or {}
    if not design:
        fail(
            "Select and precheck a current model before recording engineer approval.",
            409,
        )
    from .configurator import validate_configuration

    validation = validate_configuration(
        design, to_config_inputs(data["requirements"]), rule_pack=_effective_rules(data)
    )
    precheck = data.get("precheck") or {}
    if (
        not validation.get("fully_configured")
        or not precheck
        or precheck["model_version"] != data["model_version"]
    ):
        fail(
            "Resolve unknown/failed rules and run a current precheck before recording engineer approval.",
            409,
        )
    if (
        precheck.get("signature") != _precheck_signature(s, order, data)
        or precheck.get("saved_drawing_bom_check", {}).get("status") != "verified"
    ):
        fail(
            "The files, rules or precheck changed or did not verify. Run a current precheck before approval.",
            409,
        )
    source_check = precheck.get("source_conformance") or {}
    if not source_check or source_check.get("status") == "fail":
        fail(
            "Source conformance found a drawing/BOM or customer requirement mismatch. Resolve it before approval.",
            409,
        )
    if _coverage_blockers(data["requirements"]):
        fail(
            "Extraction coverage or unhandled engineering clauses still need verification before approval.",
            409,
        )
    if data["requirements"].get("conflicts") or data["requirements"].get(
        "unreadable_pages"
    ):
        fail(
            "Requirements still contain unresolved conflicts or unreadable source coverage.",
            409,
        )
    if any(
        not row.get("confirmed")
        for row in data["requirements"]["items"]
        if not row.get("dismissed") and row.get("equipment_role") != "reference"
    ):
        fail(
            "Confirm the current requirements before recording engineer approval.", 409
        )
    if not any(p["model_version"] == data["model_version"] for p in data["packages"]):
        fail("Generate the current proposed model before recording approval.", 409)
    data["approval"] = {
        "signer": signer,
        "actor": current_actor(),
        "reason": reason,
        "at": now().isoformat(),
        "model_version": data["model_version"],
        "model_hash": digest(design),
        "precheck_signature": precheck["signature"],
        "kind": "engineer_recorded_approval",
    }
    data["engineering_approved"] = True
    _save(
        s, order, data, "Engineer recorded approval of model for export", advance=False
    )


def release_export(s, order):
    data = state(order)
    _current(s, order, data)
    approval = data.get("approval")
    if (
        not approval
        or approval["model_version"] != data["model_version"]
        or approval["model_hash"] != digest(data["selected_design"])
    ):
        fail(
            "Record engineer approval of the current model before exporting release files.",
            409,
        )
    if approval.get("precheck_signature") != _precheck_signature(
        s, order, data
    ) or _coverage_blockers(data["requirements"]):
        fail(
            "The approval is stale or extraction coverage is incomplete. Precheck and record a current engineer decision.",
            409,
        )
    # Deliberately exports model data only. No SAP/ERP/vault writes or release.
    package = next(
        (
            p
            for p in reversed(data["packages"])
            if p["model_version"] == data["model_version"]
        ),
        None,
    )
    if not package:
        fail("The approved model has no generated package.", 409)
    for name in ("bom.csv", "nameplates.csv"):
        artifact(s, order, package["id"], name)
    data["release_export"] = {
        "package_id": package["id"],
        "model_version": data["model_version"],
        "approved_by": approval["signer"],
        "files": ["bom.csv", "nameplates.csv"],
        "erp_written": False,
        "production_released": False,
        "exported_at": now().isoformat(),
    }
    _save(
        s,
        order,
        data,
        "Exported engineer-approved BOM and nameplate data; no ERP write",
        advance=False,
    )
