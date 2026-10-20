"""Equipment originals keep immutable role/revision provenance."""

import io
import json
from uuid import uuid4

import pytest
from reportlab.pdfgen import canvas

from backend.domain.service import get
from backend.storage.db import Session
from backend.storage.models import Document


def registered_original(client):
    created = client.post(
        "/api/orders",
        json={
            "title": "Equipment registration test",
            "customer": "Fictional test customer",
            "synthetic": True,
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert created.status_code == 200, created.text
    order_id = created.json()["order"]["id"]
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(50, 750, "CB-12")
    pdf.drawString(50, 720, "Trip unit: TU-2500")
    pdf.drawString(50, 690, "Frame: 2500 A")
    pdf.save()
    added = client.post(
        f"/api/orders/{order_id}/equipment/packages",
        data={"role": "drawing", "revision": "C"},
        files=[("files", ("E-10-Rev-C.pdf", buffer.getvalue(), "application/pdf"))],
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert added.status_code == 200, added.text
    return order_id, added.json()["available_documents"][0]["id"]


@pytest.mark.parametrize(
    ("role", "revision"), [("drawing", "D"), ("specification", "C")]
)
def test_registered_original_cannot_be_reassigned(client, role, revision):
    order_id, document_id = registered_original(client)
    response = client.post(
        f"/api/orders/{order_id}/equipment/packages",
        data={
            "role": role,
            "revision": revision,
            "document_ids": json.dumps([document_id]),
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 409, response.text
    assert "preserve its checked history" in response.json()["detail"]
    with Session() as session:
        original = get(session, Document, document_id)
        assert original.meta["equipment_package"] == {
            "role": "drawing",
            "revision": "C",
        }
    view = client.get(f"/api/orders/{order_id}/equipment").json()
    assert [package["revision"] for package in view["packages"]] == ["C"]


def test_identical_registration_is_allowed_without_duplicate_original(client):
    order_id, document_id = registered_original(client)
    response = client.post(
        f"/api/orders/{order_id}/equipment/packages",
        data={
            "role": "drawing",
            "revision": "C",
            "document_ids": json.dumps([document_id]),
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["available_documents"]) == 1
    assert response.json()["available_documents"][0]["id"] == document_id
    with Session() as session:
        original = get(session, Document, document_id)
        assert original.meta["equipment_package"] == {
            "role": "drawing",
            "revision": "C",
        }


def test_identical_registration_preserves_explicit_package_status(client):
    order_id, document_id = registered_original(client)
    with Session.begin() as session:
        original = get(session, Document, document_id)
        original.meta = {
            **original.meta,
            "equipment_package": {
                **original.meta["equipment_package"],
                "active": False,
                "superseded_by": "explicit-replacement",
            },
        }
    response = client.post(
        f"/api/orders/{order_id}/equipment/packages",
        data={
            "role": "drawing",
            "revision": "C",
            "document_ids": json.dumps([document_id]),
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    with Session() as session:
        package = get(session, Document, document_id).meta["equipment_package"]
        assert package["active"] is False
        assert package["superseded_by"] == "explicit-replacement"


def equipment_pdf(value, *, specification=False):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(
        50,
        780,
        "ACCEPTED EQUIPMENT REQUIREMENTS"
        if specification
        else "FICTIONAL DEVICE DRAWING",
    )
    pdf.drawString(50, 750, "CB-12")
    pdf.drawString(50, 720, f"Trip unit: {value}")
    if not specification:
        pdf.drawString(50, 690, "Frame: 2500 A")
    pdf.save()
    return buffer.getvalue()


def upload_test_source(client, order_id, role, revision, name, content):
    response = client.post(
        f"/api/orders/{order_id}/equipment/packages",
        data={"role": role, "revision": revision},
        files=[("files", (name, content))],
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "supersession",
    ["document_active", "package_active", "document_superseded", "package_superseded"],
)
def test_only_explicit_specification_supersession_clears_conflict_and_invalidates_fingerprint(
    client, supersession
):
    order_id, baseline_drawing_id = registered_original(client)
    upload_test_source(
        client,
        order_id,
        "bom",
        "C",
        "BOM-C.csv",
        b"tag,trip_unit,frame_a\nCB-12,TU-2500,2500\n",
    )
    old = upload_test_source(
        client,
        order_id,
        "specification",
        "S1",
        "Accepted-S1.pdf",
        equipment_pdf("TU-2500", specification=True),
    )
    old_id = next(
        item["id"]
        for item in old["available_documents"]
        if item["name"] == "Accepted-S1.pdf"
    )
    latest = upload_test_source(
        client,
        order_id,
        "specification",
        "S2",
        "Accepted-S2.pdf",
        equipment_pdf("TU-3000", specification=True),
    )
    latest_id = next(
        item["id"]
        for item in latest["available_documents"]
        if item["name"] == "Accepted-S2.pdf"
    )
    upload_test_source(
        client, order_id, "drawing", "D", "E-10-Rev-D.pdf", equipment_pdf("TU-3000")
    )
    before = upload_test_source(
        client,
        order_id,
        "bom",
        "D",
        "BOM-D.csv",
        b"tag,trip_unit,frame_a\nCB-12,TU-3000,2500\n",
    )
    trip_before = next(
        item
        for item in before["comparison"]["checks"]
        if item["device_tag"] == "CB-12" and item["field"] == "trip_unit"
    )
    assert trip_before["status"] == "unknown"
    assert before["comparison"]["summary"]["outcome"] == "cannot_verify"
    with Session.begin() as session:
        original = get(session, Document, old_id)
        metadata = dict(original.meta or {})
        package = dict(metadata.get("equipment_package", {}))
        if supersession == "document_active":
            metadata["active"] = False
        elif supersession == "package_active":
            package["active"] = False
        elif supersession == "document_superseded":
            metadata["superseded_by"] = latest_id
        else:
            package["superseded_by"] = latest_id
        metadata["equipment_package"] = package
        original.meta = metadata
        # The same generic flag on historical actual drawing evidence must
        # not remove it from a before/after comparison.
        baseline = get(session, Document, baseline_drawing_id)
        baseline.meta = {**(baseline.meta or {}), "active": False}
    response = client.get(f"/api/orders/{order_id}/equipment")
    assert response.status_code == 200, response.text
    after = response.json()
    trip_after = next(
        item
        for item in after["comparison"]["checks"]
        if item["device_tag"] == "CB-12" and item["field"] == "trip_unit"
    )
    assert trip_after["status"] == "verified"
    assert trip_after["baseline_drawing"]["value"] == "TU-2500"
    assert after["comparison"]["summary"]["outcome"] == "supported_fields_match"
    assert after["comparison"]["fingerprint"] != before["comparison"]["fingerprint"]
    retained = next(
        item for item in after["available_documents"] if item["id"] == old_id
    )
    assert retained["active"] is False
    assert retained["inactive_reason"]
    assert len(after["available_documents"]) == 6
