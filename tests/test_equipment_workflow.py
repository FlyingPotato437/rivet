"""Real upload -> correction -> serialized files -> independent recheck workflow."""

from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

import ezdxf
import pytest
from fastapi import HTTPException
from openpyxl import load_workbook
from test_equipment_api import new_order, upload

from backend.domain.service import get
from backend.equipment import workflow
from backend.equipment.demo_documents import build_demo_corpus
from backend.identity import Identity, identity_scope
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Job, Order, OrderEvent


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("equipment-workflow-originals")
    manifest = build_demo_corpus(root)
    return {
        item["id"]: {**item, "file": root / item["path"]}
        for item in manifest["sources"]
    }


def correctable(client, corpus):
    oid = new_order(client)
    for key in [
        "requirements.pdf",
        *[
            f"{folder}.{kind}"
            for folder in ("01-baseline-C", "04-titleblock-only")
            for kind in ("drawing.pdf", "drawing.dxf", "bom.xlsx")
        ],
    ]:
        upload(client, oid, corpus, [key])
    response = client.get(f"/api/orders/{oid}/equipment/corrections")
    assert response.status_code == 200, response.text
    return oid, response.json()


def prepare(client, oid, view, key=None):
    return client.post(
        f"/api/orders/{oid}/equipment/corrections/prepare",
        json={
            "expected_version": view["version"],
            "expected_plan_id": view["plan"]["plan_id"],
        },
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def test_actual_native_and_bom_values_are_updated_and_rechecked_without_changing_originals(
    client, corpus
):
    oid, before = correctable(client, corpus)
    assert before["plan"]["status"] == "ready"
    assert len(before["plan"]["edits"]) == 2
    with Session() as s:
        originals = {
            d.blob: sha256((db.BLOBS / d.blob).read_bytes()).hexdigest()
            for d in s.query(Document).filter_by(
                project_id=get(s, Order, oid).project_id
            )
        }
    response = prepare(client, oid, before)
    assert response.status_code == 200, response.text
    result = response.json()
    bundle = result["bundles"][0]
    assert bundle["verification"]["summary"]["outcome"] == "supported_fields_match"
    assert bundle["verification"]["summary"]["remaining"] == 0
    assert bundle["verification"]["summary"]["verified"] == 2
    assert bundle["attachable"] and bundle["current"]
    assert (
        bundle["engineering_approved"] is False
        and bundle["production_released"] is False
    )
    assert all("path" not in a for a in bundle["artifacts"])
    by_role = {a["role"]: a for a in bundle["artifacts"]}
    with Session() as s:
        order = get(s, Order, oid)
        path, _ = workflow.artifact(s, order, bundle["id"], by_role["drawing"]["name"])
        drawing = ezdxf.readfile(path)
        breaker = next(
            b
            for b in drawing.modelspace().query("INSERT")
            if b.get_attrib_text("EQUIPMENT_TAG") == "CB-12"
        )
        assert breaker.get_attrib_text("TRIP_UNIT") == "TU-3000"
        assert breaker.get_attrib_text("NAMEPLATE") == "DATA HALL A"
        bom_path, _ = workflow.artifact(s, order, bundle["id"], by_role["bom"]["name"])
        workbook = load_workbook(bom_path, data_only=False)
        rows = list(workbook["Equipment BOM"].values)
        headers = list(rows[0])
        line = next(row for row in rows[1:] if row[0] == "CB-12")
        assert line[headers.index("trip_unit")] == "TU-3000"
        assert line[headers.index("nameplate")] == "DATA HALL A"
        workbook.close()
        assert order.status != "released"
        assert len(order.state["equipment_corrections"]["bundles"]) == 1
    for blob, checksum in originals.items():
        assert sha256((db.BLOBS / blob).read_bytes()).hexdigest() == checksum
    for item in bundle["artifacts"]:
        data = client.get("/api" + item["url"])
        assert data.status_code == 200
        assert sha256(data.content).hexdigest() == item["sha256"]


def test_prepare_retries_reuse_one_bundle_and_ingestion_queues_durable_work(
    client, corpus
):
    oid, before = correctable(client, corpus)
    key = str(uuid4())
    first = prepare(client, oid, before, key)
    assert first.status_code == 200, first.text
    retry = prepare(client, oid, before, key)
    assert retry.json() == first.json()
    again = prepare(client, oid, first.json())
    assert again.status_code == 200, again.text
    assert again.json()["bundles"][0]["id"] == first.json()["bundles"][0]["id"]
    with Session() as s:
        order = get(s, Order, oid)
        jobs = (
            s.query(Job)
            .filter_by(project_id=order.project_id, kind="equipment_corrections")
            .all()
        )
        assert jobs and all(len(j.key) <= 160 for j in jobs)
        assert (
            s.query(OrderEvent)
            .filter_by(order_id=oid, kind="equipment_corrected")
            .count()
            == 1
        )


def test_missing_device_is_not_invented_and_client_cannot_supply_edits(client, corpus):
    oid = new_order(client)
    for key in [
        "requirements.pdf",
        *[
            f"{folder}.{kind}"
            for folder in ("01-baseline-C", "02-partial-D")
            for kind in ("drawing.pdf", "drawing.dxf", "bom.xlsx")
        ],
    ]:
        upload(client, oid, corpus, [key])
    view = client.get(f"/api/orders/{oid}/equipment/corrections").json()
    assert view["plan"]["status"] == "blocked"
    response = prepare(client, oid, view)
    assert response.status_code == 409
    assert not client.get(f"/api/orders/{oid}/equipment/corrections").json()["bundles"]
    invented = client.post(
        f"/api/orders/{oid}/equipment/corrections/prepare",
        json={
            "expected_version": view["version"],
            "expected_plan_id": view["plan"]["plan_id"],
            "edits": [{"field": "geometry", "after": "fake"}],
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert invented.status_code == 422


def test_tampered_output_cannot_be_downloaded_or_attached_and_status_is_not_current(
    client, corpus
):
    oid, before = correctable(client, corpus)
    ready = prepare(client, oid, before).json()
    bundle = ready["bundles"][0]
    drawing = next(a for a in bundle["artifacts"] if a["role"] == "drawing")
    with Session() as s:
        path, _ = workflow.artifact(
            s, get(s, Order, oid), bundle["id"], drawing["name"]
        )
    path.write_bytes(b"tampered native file")
    assert client.get("/api" + drawing["url"]).status_code == 409
    assert (
        client.get(f"/api/orders/{oid}/equipment/corrections").json()["bundles"][0][
            "attachable"
        ]
        is False
    )
    response = client.post(
        f"/api/orders/{oid}/equipment/corrections/{bundle['id']}/outlook",
        json={"expected_version": ready["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 409


def test_input_change_or_cross_order_cannot_reuse_corrected_files(client, corpus):
    oid, before = correctable(client, corpus)
    ready = prepare(client, oid, before).json()
    bundle = ready["bundles"][0]
    other = new_order(client)
    file = bundle["artifacts"][0]
    assert (
        client.get(
            f"/api/orders/{other}/equipment/corrections/{bundle['id']}/files/{file['name']}"
        ).status_code
        == 404
    )
    with Session() as s:
        order = get(s, Order, oid)
        source = (
            s.query(Document)
            .filter_by(project_id=order.project_id, kind="bom")
            .all()[-1]
        )
        (db.BLOBS / source.blob).write_bytes(b"source changed")
        with pytest.raises(HTTPException) as changed:
            workflow.outlook_bundle(s, order, bundle["id"])
        assert changed.value.status_code == 409
    with (
        identity_scope(Identity(str(uuid4()), "Other company", "org:admin")),
        Session() as s,
    ):
        with pytest.raises(HTTPException) as denied:
            get(s, Order, oid)
        assert denied.value.status_code == 404


def test_private_outlook_checkpoint_is_not_exposed_in_public_bundle(client, corpus):
    oid, before = correctable(client, corpus)
    bundle = prepare(client, oid, before).json()["bundles"][0]
    with Session.begin() as s:
        order = get(s, Order, oid)
        order.state = {
            **order.state,
            "equipment_outlook": {
                bundle["id"]: {
                    "id": "native-draft",
                    "status": "created",
                    "web_link": "https://outlook.office.com/mail/drafts",
                    "lease": "private-lease",
                    "graph_draft_id": "private-graph-id",
                }
            },
        }
    result = client.get(f"/api/orders/{oid}/equipment/corrections").json()
    receipt = result["bundles"][0]["outlook"]["draft"]
    assert receipt["id"] == "native-draft"
    assert "lease" not in receipt and "graph_draft_id" not in receipt


def test_uncommitted_file_bundle_recovers_without_rewriting_outputs(client, corpus):
    oid, before = correctable(client, corpus)
    with Session() as s:
        order = get(s, Order, oid)
        first = deepcopy(workflow.prepare(s, order, before["plan"]["plan_id"]))
        # Process stops after files were persisted, before its DB transaction commits.
        s.rollback()
    result = prepare(client, oid, before)
    assert result.status_code == 200, result.text
    assert result.json()["bundles"][0]["id"] == first["id"]
    assert {a["sha256"] for a in result.json()["bundles"][0]["artifacts"]} == {
        a["sha256"] for a in first["artifacts"]
    }


def test_crash_before_workflow_journal_preserves_orphan_and_regenerates_from_originals(
    client, corpus
):
    oid, before = correctable(client, corpus)
    with Session() as s:
        order = get(s, Order, oid)
        first = deepcopy(workflow.prepare(s, order, before["plan"]["plan_id"]))
        directory = workflow._location(order, first["id"])
        (directory / "_rivet-workflow.json").unlink()
        s.rollback()
    result = prepare(client, oid, before)
    assert result.status_code == 200, result.text
    assert (
        result.json()["bundles"][0]["verification"]["summary"]["outcome"]
        == "supported_fields_match"
    )
    assert list(directory.parent.glob(first["id"] + "-interrupted-*"))


def test_typed_model_keeps_requirements_separate_from_observed_build(client, corpus):
    oid, before = correctable(client, corpus)
    response = client.get(f"/api/orders/{oid}/equipment/model")
    assert response.status_code == 200, response.text
    model = response.json()
    breaker = next(device for device in model["devices"] if device["tag"] == "CB-12")
    trip = breaker["fields"]["trip_unit"]
    assert {str(value["value"]) for value in trip["specified"]} == {"TU-3000"}
    assert {str(value["value"]) for value in trip["build"]} == {"TU-2500"}
    assert breaker["connectivity"]["status"] == "unknown"
    assert model["version"] == before["source_fingerprint"]


def test_project_status_opens_actual_correction_draft_and_withholds_changed_files(
    client, corpus
):
    from backend.project_flow.service import project_view

    oid, before = correctable(client, corpus)
    prepared = prepare(client, oid, before).json()["bundles"][0]
    with Session.begin() as s:
        order = get(s, Order, oid)
        order.state = {
            **order.state,
            "equipment_outlook": {
                prepared["id"]: {
                    "id": "native-correction",
                    "status": "created",
                    "error": "",
                    "web_link": "https://outlook.office.com/mail/deeplink/read/known-draft",
                    "attachments": [
                        {"name": item["name"], "sha256": item["sha256"]}
                        for item in prepared["artifacts"]
                        if item["role"] in {"drawing", "bom", "preview"}
                    ],
                }
            },
        }
        current = project_view(s, order)
        assert current["status"] == "ready"
        assert current["outlook"]["url"].endswith("known-draft")
        file = next(a for a in prepared["artifacts"] if a["role"] == "bom")
        path, _ = workflow.artifact(s, order, prepared["id"], file["name"])
        path.write_bytes(b"changed workbook")
        stale = project_view(s, order)
        assert stale["status"] == "needs_you"
        assert stale["equipment_correction"]["current"] is False
