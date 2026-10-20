"""Synthetic workflow integrity: configured model -> actual files -> old review.

This exercises the real configurator/export/import services, not design accuracy
on a manufacturer job. No mailbox, model-provider or engineering approval call is
made. Run with a dedicated RIVET_TEST_DATABASE_URL.
"""

import base64
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from backend.domain.service import get
from backend.engineering import service as engineering
from backend.engineering.configurator import configure
from backend.engineering.requirements import to_config_inputs
from backend.equipment import corrections
from backend.equipment import service as equipment
from backend.equipment.comparison import compare_packages
from backend.ingestion.parser import persist_blob
from backend.orders import service as orders
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Span


def _requirements():
    return {
        "service_rating_a": 600,
        "voltage_v": 480,
        "current_type": "AC",
        "frequency_hz": 60,
        "phases": 3,
        "wires": 4,
        "metering": False,
        "spd": False,
        "confirmed": True,
        "main": {
            "kind": "circuit_breaker",
            "trip_a": 600,
            "poles": 3,
            "trip_type": "LI",
        },
        "feeders": [
            {
                "id": "FDR-1",
                "tag": "FDR-1",
                "trip_a": 225,
                "poles": 3,
                "trip_type": "LI",
            }
        ],
        "constraints": {"enclosure": "NEMA1", "line_load_connection": "opposite_side"},
    }


def _typed_requirements():
    """Explicit software inputs, without pretending they have real source evidence."""
    inputs = _requirements()
    items = []

    def add(subject, key, value):
        items.append(
            {
                "id": subject + ":" + key,
                "key": key,
                "subject_id": subject,
                "value": value,
                "confirmed": True,
                "source_refs": [],
                "source_claim_ids": [],
            }
        )

    for key, value in inputs.items():
        if key not in {"confirmed", "main", "feeders", "constraints"}:
            add("order", key, value)
    for key, value in inputs["main"].items():
        add("order", "main." + key, value)
    for feeder in inputs["feeders"]:
        for key, value in feeder.items():
            if key not in {"id", "tag"}:
                add(feeder["id"], key, value)
    return {
        "items": items,
        "evidence_claims": [],
        "conflicts": [],
        "unreadable_pages": [],
        "warnings": [],
    }


def _seed_selected(s):
    order = orders.new_order(
        s,
        {
            "title": "Synthetic generated-package integration",
            "customer": "Software control",
            "number": "TEST-GEN-1",
            "synthetic": True,
        },
    )
    text = "Comment 1: Retain the existing customer review loop."
    blob, checksum = persist_blob(text.encode(), ".txt")
    legacy = Document(
        project_id=order.project_id,
        name="legacy-review-comments.txt",
        kind="markup",
        blob=blob,
        sha256=checksum,
        state="parsed",
        meta={"order_role": "markup", "revision_label": "A"},
        coverage=[],
    )
    s.add(legacy)
    s.flush()
    s.add(Span(document_id=legacy.id, text=text, location={"page": 1}))
    s.flush()
    orders.refresh(s, order)
    data = engineering.state(order)
    data["model_version"] = 1
    # Earlier requirement-stage versioning has its own endpoint tests. This
    # fixture starts at an explicitly selected configurator model.
    data["requirements"] = _typed_requirements()
    data["source_signature"] = engineering.source_signature(s, order, data)
    solved = configure(
        to_config_inputs(data["requirements"]),
        model_id=data["model_id"],
        model_version=1,
        max_options=1,
    )
    assert solved["configurations"]
    data["configuration"] = solved
    data["selected_design"] = solved["configurations"][0]
    order.state = {**order.state, "engineering": data}
    orders.commit(s, order, "Synthetic selection fixture", "engineering")
    return order


def _registered_inputs(s, order, package):
    docs = [get(s, Document, identifier) for identifier in package["document_ids"]]
    return [
        equipment.source_input(doc, equipment.document_info(doc, order))
        for doc in docs
        if equipment.document_info(doc, order)["role"] in {"drawing", "bom"}
    ]


def test_generated_package_registers_actual_files_without_losing_review_history():
    with Session.begin() as s:
        order = _seed_selected(s)
        engineering.generate(s, order)
        package = engineering.state(order)["packages"][-1]
        assert package["model_version"] == 1
        engineering.register_package(s, order, package["id"])
        package = engineering.state(order)["packages"][-1]
        assert package["revision"] == "GEN-1"
        assert len(package["document_ids"]) == 4
        docs = [get(s, Document, identifier) for identifier in package["document_ids"]]
        assert {doc.name for doc in docs} == {
            "GEN-1-one-line.dxf",
            "GEN-1-front-elevation.dxf",
            "GEN-1-bom.xlsx",
            "GEN-1-submittal.pdf",
        }
        assert any(
            "Retain the existing customer review loop" in row["text"]
            for row in order.state["comments"]
        )
        for doc in docs:
            path = db.BLOBS / doc.blob
            assert (
                path.is_file() and sha256(path.read_bytes()).hexdigest() == doc.sha256
            )
            assert doc.meta["engineering_generated"]["model_version"] == 1
        before = {doc.id for doc in docs}
        engineering.register_package(s, order, package["id"])
        assert set(engineering.state(order)["packages"][-1]["document_ids"]) == before
        inputs = _registered_inputs(s, order, package)
        assert (
            len([item for item in inputs if Path(item["name"]).suffix == ".dxf"]) == 1
        )
        assert not engineering.state(order)["engineering_approved"]


def test_repeated_generation_reuses_one_immutable_package_and_revision():
    with Session.begin() as s:
        order = _seed_selected(s)
        engineering.generate(s, order)
        first = engineering.state(order)["packages"][-1]
        engineering.register_package(s, order, first["id"])
        engineering.generate(s, order)
        engineering.register_package(s, order, first["id"])
        packages = engineering.state(order)["packages"]
        assert len(packages) == 1 and packages[0]["id"] == first["id"]
        assert packages[0]["revision"] == "GEN-1"


def test_model_edit_regenerates_and_existing_revision_checker_observes_actual_trip():
    with Session.begin() as s:
        order = _seed_selected(s)
        engineering.generate(s, order)
        first = engineering.state(order)["packages"][-1]
        engineering.register_package(s, order, first["id"])
        first = engineering.state(order)["packages"][-1]
        data = engineering.state(order)
        data["model_version"] = 2
        design = deepcopy(data["selected_design"])
        design["model_version"] = 2
        feeder = next(
            device for device in design["devices"] if device["tag"] == "FDR-1"
        )
        feeder["trip_a"] = 200
        next(
            row
            for row in data["requirements"]["items"]
            if row["subject_id"] == "FDR-1" and row["key"] == "trip_a"
        )["value"] = 200
        from backend.engineering.configurator import _digest, _normalized_inputs

        design["requirements_digest"] = _digest(
            _normalized_inputs(to_config_inputs(data["requirements"]))
        )
        data["selected_design"] = design
        order.state = {**order.state, "engineering": data}
        engineering.generate(s, order)
        second = engineering.state(order)["packages"][-1]
        engineering.register_package(s, order, second["id"])
        second = engineering.state(order)["packages"][-1]
        raw = compare_packages(
            _registered_inputs(s, order, first), _registered_inputs(s, order, second)
        )
        actual = raw["target"]["facts"]
        trip = [
            fact
            for fact in actual
            if fact["device_tag"] == "FDR-1" and fact["field"] == "trip_a"
        ]
        assert {fact["value"] for fact in trip} == {200}
        assert {fact["role"] for fact in trip} >= {"drawing", "bom"}
        saved = order.state.get("equipment_check")
        assert (
            saved
            and saved["baseline_revision"] == "GEN-1"
            and saved["target_revision"] == "GEN-2"
        )
        assert any(
            "Retain the existing customer review loop" in row["text"]
            for row in order.state["comments"]
        )
        # Unknown exact part/rating fields remain unverified, never approved.
        assert engineering.state(order)["engineering_approved"] is False


def test_existing_literal_correction_planner_accepts_one_primary_native_drawing():
    with Session.begin() as s:
        order = _seed_selected(s)
        engineering.generate(s, order)
        first = engineering.state(order)["packages"][-1]
        engineering.register_package(s, order, first["id"])
        first = engineering.state(order)["packages"][-1]
        sources = _registered_inputs(s, order, first)
        plan = corrections.plan_corrections(sources, sources)
        assert not any(
            item["code"] == "editable_sources_required" for item in plan["blockers"]
        )
        assert plan["engineering_approved"] is False


def test_generated_corrected_rechecked_files_reach_unsent_outlook_draft(tmp_path):
    """Actual file bytes cross every module; Graph alone is a fake mailbox."""
    from test_company_context import identity
    from test_equipment_outlook import DraftMailbox

    from backend.equipment import outlook
    from backend.identity import current_org
    from backend.mailbox.models import MailboxConnection

    with identity(), Session.begin() as s:
        order = _seed_selected(s)
        engineering.generate(s, order)
        original = engineering.state(order)["packages"][-1]
        engineering.register_package(s, order, original["id"])
        package = engineering.state(order)["packages"][-1]
        sources = _registered_inputs(s, order, package)
        requirement = tmp_path / "software-control-literal-requirement.csv"
        requirement.write_text(
            "equipment_tag,trip_unit\nFDR-1,LSI\n"
        )
        requirements = [
            {
                "id": "software-control",
                "path": str(requirement),
                "name": requirement.name,
                "role": "specification",
                "revision": "REQ-1",
                "sha256": sha256(requirement.read_bytes()).hexdigest(),
            }
        ]
        bundle_id = str(uuid4())
        destination = (
            db.BLOBS / "equipment-corrections" / current_org() / order.id / bundle_id
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        plan = corrections.plan_corrections(sources, sources, requirements)
        assert plan["status"] == "ready", plan["blockers"]
        corrected = corrections.prepare_corrections(
            sources,
            sources,
            requirements,
            destination=destination,
            target_revision="GEN-1-R1",
        )
        assert corrected["verification"]["summary"]["remaining"] == 0
        outputs = [
            {
                "id": str(uuid4()),
                "name": a["name"],
                "path": str(destination / a["name"]),
                "sha256": a["sha256"],
                "content_type": a["media_type"],
                "role": a["role"],
            }
            for a in corrected["artifacts"]
            if a["role"] != "manifest"
        ]
        bundle = {
            "id": bundle_id,
            "order_id": order.id,
            "status": "draft",
            "current": True,
            "engineering_approved": False,
            "production_released": False,
            "fingerprint": corrected["plan_id"],
            "outputs": outputs,
            "subject": "Synthetic generated design · draft copies for review",
            "body": "Software control only. Proposed corrected files; engineering review required.",
            "recheck": corrected["verification"],
        }
        connection = MailboxConnection(
            mailbox="pm@example.com", microsoft_tenant_id=str(uuid4())
        )
        s.add(connection)
        s.flush()
        oid, cid = order.id, connection.id
    graph = DraftMailbox()
    with identity():
        result = outlook.save(
            oid,
            bundle_id,
            cid,
            client_factory=graph,
            bundle_provider=lambda _s, _o, requested: (
                deepcopy(bundle) if requested == bundle_id else None
            ),
        )
    assert result["draft"]["status"] == "created"
    attachments = graph.messages["native-correction"]["attachments"]
    assert len(attachments) == 3
    for attached in attachments:
        content = base64.b64decode(attached["contentBytes"])
        expected = next(a for a in outputs if a["name"] == attached["name"])
        assert sha256(content).hexdigest() == expected["sha256"]
    assert graph.messages["native-correction"]["isDraft"] is True
    assert not any("/send" in request.url.path for request in graph.calls)
