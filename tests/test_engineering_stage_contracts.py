"""Synthetic security/version controls for reviewed engineering stage inputs.

These check persisted source ownership and actual generated files, not design
accuracy. Provider calls and customer sends are never made.
"""

from copy import deepcopy
from uuid import uuid4

import pymupdf
import pytest
from test_engineering_integration import _seed_selected
from test_engineering_review_change import _original

from backend.domain.service import get
from backend.engineering import service
from backend.engineering.configurator import _digest, _normalized_inputs
from backend.engineering.requirements import to_config_inputs
from backend.ingestion.parser import persist_blob
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Order, OrderRevision


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("RIVET_ENGINEERING_TEXT_ENABLED", "false")
    monkeypatch.setenv("RIVET_ENGINEERING_VISION_ENABLED", "false")


def _post(client, url, body, status=200):
    response = client.post(url, json=body, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == status, response.text
    return response.json()


def _selected():
    with Session.begin() as session:
        order = _seed_selected(session)
        return order.id


def _cut_sheet(oid):
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((60, 70), "SYNTHETIC cut sheet: CONTROL-PART-225")
        content = pdf.tobytes()
    with Session.begin() as session:
        order = get(session, Order, oid)
        blob, checksum = persist_blob(content, ".pdf")
        doc = Document(
            project_id=order.project_id,
            name="control-cut-sheet.pdf",
            blob=blob,
            sha256=checksum,
            state="parsed",
            coverage=[],
        )
        session.add(doc)
        session.flush()
        data = service.state(order)
        data["source_roles"][doc.id] = "component_cut_sheet"
        device = next(
            d for d in data["selected_design"]["devices"] if d["id"] == "FDR-1"
        )
        # Explicit software fixture data, not a source-extraction accuracy claim.
        device.update(supplied=True, part_number="CONTROL-PART-225")
        for key, value in (("supplied", True), ("part_number", "CONTROL-PART-225")):
            data["requirements"]["items"].append(
                {
                    "id": "control:" + key,
                    "subject_id": "FDR-1",
                    "key": key,
                    "value": value,
                    "confirmed": True,
                    "source_claim_ids": [],
                    "source_refs": [],
                }
            )
        data["selected_design"]["requirements_digest"] = _digest(
            _normalized_inputs(to_config_inputs(data["requirements"]))
        )
        order.state = {**order.state, "engineering": data}
        return doc.id, checksum


def test_design_preview_cannot_change_saved_model_or_accept_client_pass_flags(client):
    oid = _selected()
    root = f"/api/orders/{oid}/engineering"
    before = client.get(root).json()
    design = deepcopy(before["selected_design"])
    device = next(d for d in design["devices"] if d["id"] == "FDR-1")
    device["trip_a"] = 99999
    design.update(fully_configured=True, engineering_approved=True, rule_results=[])
    with Session() as session:
        history_count = session.query(OrderRevision).filter_by(order_id=oid).count()
    preview = _post(
        client,
        root + "/design-preview",
        {"expected_version": before["version"], "design": design},
    )
    assert preview["persisted"] is False
    assert preview["validation"]["has_known_rule_violations"] is True
    assert preview["validation"]["fully_configured"] is False
    assert preview["design"]["engineering_approved"] is False
    after = client.get(root).json()
    assert after["version"] == before["version"]
    assert after["model_version"] == before["model_version"]
    assert after["selected_design"] == before["selected_design"]
    with Session() as session:
        assert (
            session.query(OrderRevision).filter_by(order_id=oid).count()
            == history_count
        )
    _post(
        client,
        root + "/design-preview",
        {"expected_version": before["version"] - 1, "design": design},
        409,
    )


def test_owned_verified_supplied_cut_sheet_is_appended_from_original_bytes(client):
    oid = _selected()
    did, checksum = _cut_sheet(oid)
    root = f"/api/orders/{oid}/engineering"
    before = client.get(root).json()
    mapping = {
        "document_id": did,
        "component_ids": ["FDR-1"],
        "pages": [1],
        "identity": {"part_number": "CONTROL-PART-225"},
        "verified": True,
    }
    after = _post(
        client,
        root + "/generation-settings",
        {
            "expected_version": before["version"],
            "cut_sheets": [mapping],
            "reason": "Engineer verified supplied part and exact cut-sheet page",
        },
    )
    assert after["model_version"] == before["model_version"] + 1
    after = _post(client, root + "/generate", {"expected_version": after["version"]})
    with Session() as session:
        order = get(session, Order, oid)
        data = service.state(order)
        saved = data["generation_settings"]["cut_sheets"][0]
        assert saved["document_sha256"] == checksum and saved["reviewed_by"]
        package = data["packages"][-1]
        filename = next(
            a["name"]
            for a in package["artifacts"]
            if a["name"].endswith("submittal.pdf")
        )
        path = service.artifact(session, order, package["id"], filename)
        with pymupdf.open(path) as pdf:
            assert "SYNTHETIC cut sheet: CONTROL-PART-225" in pdf[-1].get_text()
        assert not data["engineering_approved"]


@pytest.mark.parametrize(
    "problem,status",
    [
        ("foreign", 403),
        ("identity", 422),
        ("unverified", 422),
        ("page", 422),
        ("tampered", 409),
        ("unknown_supply", 422),
    ],
)
def test_cut_sheet_cannot_attach_foreign_tampered_or_unestablished_component(
    client, problem, status
):
    oid = _selected()
    did, _checksum = _cut_sheet(oid)
    if problem == "foreign":
        did, _ = _cut_sheet(_selected())
    mapping = {
        "document_id": did,
        "component_ids": ["FDR-1"],
        "pages": [1],
        "identity": {"part_number": "CONTROL-PART-225"},
        "verified": True,
    }
    if problem == "identity":
        mapping["identity"]["part_number"] = "OTHER-PART"
    elif problem == "unverified":
        mapping["verified"] = False
    elif problem == "page":
        mapping["pages"] = [999]
    elif problem == "tampered":
        with Session() as session:
            doc = get(session, Document, did)
            (db.BLOBS / doc.blob).write_bytes(b"not the reviewed PDF")
    elif problem == "unknown_supply":
        with Session.begin() as session:
            order = get(session, Order, oid)
            data = service.state(order)
            next(d for d in data["selected_design"]["devices"] if d["id"] == "FDR-1")[
                "supplied"
            ] = None
            order.state = {**order.state, "engineering": data}
    root = f"/api/orders/{oid}/engineering"
    before = client.get(root).json()
    _post(
        client,
        root + "/generation-settings",
        {
            "expected_version": before["version"],
            "cut_sheets": [mapping],
            "reason": "Synthetic mapping rejection boundary control",
        },
        status,
    )
    after = client.get(root).json()
    assert after["version"] == before["version"]
    with Session() as session:
        assert "generation_settings" not in service.state(get(session, Order, oid))


def test_template_rejects_filesystem_path_input(client):
    oid = _selected()
    root = f"/api/orders/{oid}/engineering"
    before = client.get(root).json()
    _post(
        client,
        root + "/generation-settings",
        {
            "expected_version": before["version"],
            "template": {"library_path": "/tmp/not-owned.dxf"},
            "reason": "Synthetic unsafe path boundary control",
        },
        422,
    )


def _with_customer_original(client):
    oid = _selected()
    original = _original(
        oid,
        "explicit-engineer-input.txt",
        "The incoming service side is front, explicit on page 1.",
        role="specification",
    )
    with Session.begin() as session:
        order = get(session, Order, oid)
        data = service.state(order)
        data["source_roles"][original["document_id"]] = "specification"
        # Source-only fixture insertion; requirements are unchanged until API review.
        data["source_signature"] = service.source_signature(session, order, data)
        order.state = {**order.state, "engineering": data}
    root = f"/api/orders/{oid}/engineering"
    before = client.get(root).json()
    body = {
        "expected_version": before["version"],
        "source_id": original["document_id"],
        "page": 1,
        "subject_id": "order",
        "key": "service_side",
        "value": "front",
        "reason": "Engineer explicitly read the original source page",
    }
    return oid, root, before, body


def test_missing_requirement_is_explicit_human_entry_with_immutable_original_claims(
    client,
):
    _oid, root, before, body = _with_customer_original(client)
    original_claims = deepcopy(before["requirements"]["evidence_claims"])
    after = _post(client, root + "/requirements/add", body)
    row = next(r for r in after["requirements"]["items"] if r["key"] == "service_side")
    assert row["value"] == "front" and row["confirmed"] is True
    assert row["source_refs"][0]["human_entered"] is True
    assert row["source_refs"][0]["page"] == 1
    assert (
        after["requirements"]["evidence_claims"][: len(original_claims)]
        == original_claims
    )
    claim = after["requirements"]["evidence_claims"][-1]
    assert claim["extractor"]["name"] == "explicit-engineer-source-entry"
    assert claim["state"] == "proposed"
    assert after["selected_design"] is None
    assert after["model_version"] == before["model_version"] + 1
    _post(
        client,
        root + "/requirements/add",
        {**body, "expected_version": after["version"]},
        409,
    )
    assert not after["engineering_approved"]


@pytest.mark.parametrize(
    "problem,status",
    [("foreign", 403), ("page", 422), ("key", 422), ("boolean", 422), ("subject", 422)],
)
def test_human_entry_cannot_create_uncited_untyped_or_wrong_subject_requirement(
    client, problem, status
):
    _oid, root, before, body = _with_customer_original(client)
    if problem == "foreign":
        original = _original(
            _selected(), "foreign.txt", "foreign", role="specification"
        )
        body["source_id"] = original["document_id"]
    elif problem == "page":
        body["page"] = 2
    elif problem == "key":
        body["key"] = "engineer_approved"
    elif problem == "boolean":
        body.update(key="main.supplied", value="maybe")
    else:
        body.update(key="main.part_number", subject_id="guessed-feeder")
    _post(client, root + "/requirements/add", body, status)
    assert client.get(root).json()["version"] == before["version"]
