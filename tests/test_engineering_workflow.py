"""Versioning, tenant isolation and preserving the existing review workflow."""

from uuid import uuid4

import pytest

from backend.domain.service import get
from backend.identity import Identity, identity_scope
from backend.storage.db import Session
from backend.storage.models import Document, Order, OrderRevision, Span


@pytest.fixture(autouse=True)
def offline_semantic_reader(monkeypatch):
    monkeypatch.setenv("RIVET_ENGINEERING_TEXT_ENABLED", "false")


def post(client, url, data):
    return client.post(url, json=data, headers={"Idempotency-Key": str(uuid4())})


def new_order(client):
    response = post(
        client,
        "/api/orders",
        {
            "title": "Synthetic engineering workflow",
            "customer": "Software check",
            "synthetic": True,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["order"]["id"]


def add_source(
    order_id,
    text="Main bus: 2000 A\nVoltage: 480 V\nPhases: 3\nWires: 4",
    name="spec.pdf",
):
    with Session.begin() as s:
        order = get(s, Order, order_id)
        from backend.ingestion.parser import persist_blob

        blob, checksum = persist_blob(text.encode(), ".pdf")
        doc = Document(
            project_id=order.project_id,
            name=name,
            blob=blob,
            sha256=checksum,
            state="ready",
            coverage=[],
        )
        s.add(doc)
        s.flush()
        s.add(Span(document_id=doc.id, text=text, location={"page": 1}))
        return doc.id


def setup_requirements(client):
    oid = new_order(client)
    doc = add_source(oid)
    root = f"/api/orders/{oid}/engineering"
    first = client.get(root).json()
    response = post(
        client,
        root + "/sources",
        {"expected_version": first["version"], "source_roles": {doc: "specification"}},
    )
    assert response.status_code == 200, response.text
    response = post(
        client, root + "/extract", {"expected_version": response.json()["version"]}
    )
    assert response.status_code == 200, response.text
    return oid, root, doc, response.json()


def test_requirements_and_claims_are_versioned_and_idempotent(client):
    oid, root, _doc, data = setup_requirements(client)
    assert data["requirements"]["items"]
    row = data["requirements"]["items"][0]
    original_value = row["value"]
    body = {
        "expected_version": data["version"],
        "decisions": [{"id": row["id"], "value": 2500}],
        "reason": "Engineer checked original requirement",
    }
    headers = {"Idempotency-Key": str(uuid4())}
    changed = client.post(root + "/requirements", json=body, headers=headers)
    assert changed.status_code == 200, changed.text
    assert (
        changed.json()
        == client.post(root + "/requirements", json=body, headers=headers).json()
    )
    after = changed.json()
    assert after["model_version"] == data["model_version"] + 1
    assert after["requirements"]["items"][0]["confirmed"]
    assert not after["engineering_approved"]
    with Session() as s:
        historical = (
            s.query(OrderRevision)
            .filter_by(order_id=oid, version=data["version"])
            .one()
        )
        assert (
            historical.snapshot["state"]["engineering"]["requirements"]["items"][0][
                "value"
            ]
            == original_value
        )
        from backend.equipment.evidence.models import EquipmentEvidenceClaim

        assert s.query(EquipmentEvidenceClaim).filter_by(order_id=oid).count() > len(
            data["requirements"]["evidence_claims"]
        )


def test_stale_mutations_and_cross_order_sources_rejected(client):
    _, root, _, data = setup_requirements(client)
    assert (
        post(
            client, root + "/configure", {"expected_version": data["version"] - 1}
        ).status_code
        == 409
    )
    other = new_order(client)
    foreign = add_source(other)
    assert (
        post(
            client,
            root + "/sources",
            {
                "expected_version": data["version"],
                "source_roles": {foreign: "specification"},
            },
        ).status_code
        == 403
    )
    with identity_scope(Identity(str(uuid4()), "other tenant")), Session() as s:
        import pytest
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            get(s, Order, other)


def test_changing_source_role_invalidates_prior_requirements(client):
    _, root, doc, data = setup_requirements(client)
    changed = post(
        client,
        root + "/sources",
        {"expected_version": data["version"], "source_roles": {doc: "purchase_order"}},
    ).json()
    assert changed["stale"]
    response = post(
        client, root + "/configure", {"expected_version": changed["version"]}
    )
    assert response.status_code == 409, response.text


def test_release_requires_real_current_engineer_decision_and_does_not_send(client):
    _, root, _, data = setup_requirements(client)
    response = post(
        client,
        root + "/approve",
        {
            "expected_version": data["version"],
            "signer": "Software fixture",
            "reason": "Should fail before a configured design exists",
        },
    )
    assert response.status_code == 409
    assert (
        post(
            client, root + "/release-export", {"expected_version": data["version"]}
        ).status_code
        == 409
    )


def test_legacy_comment_record_endpoint_stays_available(client):
    oid, _root, _, _data = setup_requirements(client)
    result = client.get(f"/api/orders/{oid}/record")
    assert result.status_code == 200, result.text
    assert "comments" in result.json()
