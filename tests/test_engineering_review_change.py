"""Synthetic API-to-file review controls; these are not real-design accuracy.

Only original source documents/spans and a fake mailbox connection are seeded.
Every requirement confirmation, model change, generation and handoff uses the
real API/services. The mailbox transport is fake; no provider or customer send
is permitted. Run with a dedicated RIVET_TEST_DATABASE_URL.
"""

import base64
import csv
import io
import json
from hashlib import sha256
from uuid import uuid4

import ezdxf
import pymupdf
import pytest
from openpyxl import load_workbook
from test_equipment_outlook import DraftMailbox

from backend.domain.service import get
from backend.equipment import outlook
from backend.equipment.evidence.models import EquipmentEvidenceClaim
from backend.ingestion.parser import persist_blob
from backend.mailbox.models import MailboxConnection
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Order, OrderRevision, Span

SPECIFICATION = """Main bus: 600 A
Voltage: 480 V
Current type: AC
Frequency: 60 Hz
Phases: 3
Wires: 4
Main type: circuit_breaker
Main frame: 600 A
Main trip: 600 A
Device: FDR-1 | Frame: 250 A | Trip: 225 A | Trip type: LI | Poles: 3
"""
RETURNED_COMMENT = "Comment 1: Change FDR-1 trip from 225 A to 200 A."


@pytest.fixture(autouse=True)
def offline_readers(monkeypatch):
    monkeypatch.setenv("RIVET_ENGINEERING_TEXT_ENABLED", "false")
    monkeypatch.setenv("RIVET_ENGINEERING_VISION_ENABLED", "false")
    monkeypatch.setenv("RIVET_VISION_ENABLED", "false")


def _post(client, url, body, *, key=None, status=200):
    response = client.post(
        url,
        json=body,
        headers={"Idempotency-Key": key or str(uuid4())},
    )
    assert response.status_code == status, response.text
    return response.json()


def _original(order_id, name, text, *, role):
    """Source-only fixture setup; never writes an equipment model or decision."""
    with Session.begin() as session:
        order = get(session, Order, order_id)
        blob, checksum = persist_blob(text.encode(), ".txt")
        document = Document(
            project_id=order.project_id,
            name=name,
            kind="markup" if role == "markup" else "schedule",
            blob=blob,
            sha256=checksum,
            state="parsed",
            coverage=[{"page": 1, "state": "native"}],
            meta={"order_role": role, "revision_label": "CUSTOMER-1"},
        )
        session.add(document)
        session.flush()
        span = Span(document_id=document.id, text=text, location={"page": 1})
        session.add(span)
        session.flush()
        return {"document_id": document.id, "span_id": span.id, "sha256": checksum}


def _prepared(client):
    created = _post(
        client,
        "/api/orders",
        {
            "title": "Synthetic source-backed comment to model control",
            "customer": "Software control only",
            "synthetic": True,
        },
    )
    oid = created["order"]["id"]
    source = _original(
        oid, "synthetic-customer-requirements.txt", SPECIFICATION, role="specification"
    )
    comment_source = _original(
        oid, "synthetic-returned-comments.txt", RETURNED_COMMENT, role="markup"
    )
    root = f"/api/orders/{oid}/engineering"
    view = client.get(root).json()
    view = _post(
        client,
        root + "/sources",
        {
            "expected_version": view["version"],
            "source_roles": {source["document_id"]: "specification"},
        },
    )
    view = _post(client, root + "/extract", {"expected_version": view["version"]})
    assert view["requirements"]["items"]
    assert all(
        row["source_refs"][0]["sha256"] == source["sha256"]
        for row in view["requirements"]["items"]
    )
    view = _post(
        client,
        root + "/requirements",
        {
            "expected_version": view["version"],
            "decisions": [
                {"id": row["id"], "value": row["value"]}
                for row in view["requirements"]["items"]
            ],
            "reason": "Synthetic engineer explicitly checked the original formatted text",
        },
    )
    view = _post(client, root + "/configure", {"expected_version": view["version"]})
    choices = view["configuration"]["configurations"]
    assert choices
    view = _post(
        client,
        root + "/design",
        {
            "expected_version": view["version"],
            "design_id": choices[0]["id"],
        },
    )
    view = _post(client, root + "/generate", {"expected_version": view["version"]})
    package = view["packages"][-1]
    view = _post(
        client,
        root + "/review",
        {
            "expected_version": view["version"],
            "package_id": package["id"],
        },
    )
    record_root = f"/api/orders/{oid}/record"
    record = client.get(record_root).json()
    comment = next(
        row
        for row in record["comments"]
        if comment_source["span_id"] in row["source_ids"]
    )
    record = _post(
        client,
        record_root + "/comments/" + comment["id"],
        {
            "expected_version": record["version"],
            "actor": "Synthetic engineer",
            "reason": "Checked the original customer comment before editing the model",
            "number": comment["number"],
            "text": comment["text"],
            "reviewed": True,
        },
    )
    assert next(row for row in record["comments"] if row["id"] == comment["id"])[
        "reviewed"
    ]
    view = client.get(root).json()
    requirement = next(
        row
        for row in view["requirements"]["items"]
        if row["subject_id"] == "FDR-1" and row["key"] == "trip_a"
    )
    assert requirement["value"] == 225 and requirement["confirmed"]
    main = next(d for d in view["selected_design"]["devices"] if d["role"] == "main")
    assert main["id"] == "main" and main.get("tag") is None
    return {
        "oid": oid,
        "root": root,
        "view": view,
        "source": source,
        "comment_source": comment_source,
        "comment_id": comment["id"],
        "requirement": requirement,
        "package": view["packages"][-1],
    }


def _change_body(case, **changes):
    return {
        "expected_version": case["view"]["version"],
        "package_id": case["package"]["id"],
        "comment_id": case["comment_id"],
        "evidence_id": case["comment_source"]["span_id"],
        "requirement_id": case["requirement"]["id"],
        "value": 200,
        "reason": "Engineer accepts the original returned feeder trip change",
        **changes,
    }


def _download_package(client, package):
    files = {}
    for artifact in package["artifacts"]:
        response = client.get("/api" + artifact["url"])
        assert response.status_code == 200, response.text
        assert sha256(response.content).hexdigest() == artifact["sha256"]
        assert str(artifact["model_version"]) == str(package["model_version"])
        files[artifact["name"]] = response.content
    return files


def _assert_file_values(files, version):
    snapshot = json.loads(files["model-snapshot.json"])
    assert str(snapshot["model_version"]) == str(version)
    assert (
        next(d for d in snapshot["devices"] if d["role"] == "main").get("tag") is None
    )
    assert next(d for d in snapshot["devices"] if d["tag"] == "FDR-1")["trip_a"] == 200
    for name in ("one-line.dxf", "front-elevation.dxf"):
        drawing = ezdxf.read(io.StringIO(files[name].decode()))
        devices = [
            e
            for e in drawing.modelspace().query("INSERT")
            if e.has_attrib("EQUIPMENT_TAG")
        ]
        feeder = next(
            e for e in devices if e.get_attrib_text("EQUIPMENT_TAG") == "FDR-1"
        )
        assert feeder.get_attrib_text("TRIP_A") == "200 A"
        assert all(e.get_attrib_text("MODEL_VERSION") == str(version) for e in devices)
        main = next(
            e for e in devices if e.get_attrib_text("MODEL_DEVICE_ID") == "main"
        )
        assert main.get_attrib_text("IDENTITY_SCOPE") == "model_local"
        assert main.get_attrib_text("SOURCE_LABEL") == "UNKNOWN"
        assert main.get_attrib_text("MODEL_ID") == snapshot["model_id"]
        assert main.get_attrib_text("EQUIPMENT_TAG")
    workbook = load_workbook(io.BytesIO(files["bom.xlsx"]), data_only=False)
    rows = list(workbook["Equipment BOM"].values)
    values = [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]
    assert (
        next(row for row in values if row["equipment_tag"] == "FDR-1")["trip_a"] == 200
    )
    assert all(str(row["model_version"]) == str(version) for row in values)
    main = next(row for row in values if row["model_device_id"] == "main")
    assert main["identity_scope"] == "model_local" and main["source_label"] is None
    assert main["model_id"] == snapshot["model_id"] and main["equipment_tag"]
    assert len({row["equipment_tag"] for row in values}) == len(values)
    workbook.close()
    for name in ("bom.csv", "nameplates.csv"):
        values = list(csv.DictReader(io.StringIO(files[name].decode())))
        assert values and all(row["model_version"] == str(version) for row in values)
    with pymupdf.open(stream=files["submittal.pdf"], filetype="pdf") as pdf:
        assert pdf.page_count >= 5
        assert all(
            f"MODEL VERSION {version}" in page.get_text()
            and "PROPOSED" in page.get_text()
            for page in pdf
        )


def test_reviewed_customer_comment_changes_model_files_checker_and_unsent_draft(
    client, monkeypatch
):
    case = _prepared(client)
    before = _download_package(client, case["package"])
    key = str(uuid4())
    body = _change_body(case)
    changed = _post(client, case["root"] + "/review-change", body, key=key)
    assert changed == _post(client, case["root"] + "/review-change", body, key=key)
    assert changed["model_version"] == case["view"]["model_version"] + 1
    requirement = next(
        row
        for row in changed["requirements"]["items"]
        if row["id"] == case["requirement"]["id"]
    )
    assert requirement["value"] == 200 and requirement["confirmed"]
    assert set(case["requirement"]["source_claim_ids"]) < set(
        requirement["source_claim_ids"]
    )
    assert (
        next(d for d in changed["selected_design"]["devices"] if d["tag"] == "FDR-1")[
            "trip_a"
        ]
        == 200
    )
    assert (
        next(
            d for d in changed["selected_design"]["devices"] if d["role"] == "main"
        ).get("kind")
        == "circuit_breaker"
    )
    assert (
        changed["engineering_approved"] is False
        and changed["production_released"] is False
    )
    assert len(changed["packages"]) == 2 and len(changed["review_deliveries"]) == 1
    package = changed["packages"][-1]
    assert package["id"] != case["package"]["id"]
    assert package["revision"] == f"GEN-{changed['model_version']}"
    files = _download_package(client, package)
    _assert_file_values(files, changed["model_version"])
    assert files["one-line.dxf"] != before["one-line.dxf"]
    assert _download_package(client, case["package"]) == before
    assert changed["precheck"]["model_version"] == changed["model_version"]
    assert changed["precheck"]["saved_drawing_bom_check"]["status"] == "verified"
    assert changed["precheck"]["source_conformance"]["status"] != "fail"
    check = client.get(f"/api/orders/{case['oid']}/equipment").json()["comparison"]
    trip = next(
        row
        for row in check["checks"]
        if row["device_tag"] == "FDR-1" and row["field"] == "trip_a"
    )
    assert float(trip["baseline_drawing"]["value"]) == 225
    assert (
        float(trip["target_drawing"]["value"])
        == float(trip["target_bom"]["value"])
        == 200
    )
    with Session.begin() as session:
        order = get(session, Order, case["oid"])
        saved = order.state["equipment_check"]
        assert saved["baseline_revision"] == case["package"]["revision"]
        assert saved["target_revision"] == package["revision"]
        historical = (
            session.query(OrderRevision)
            .filter_by(order_id=order.id, version=case["view"]["version"])
            .one()
        )
        old = next(
            row
            for row in historical.snapshot["state"]["engineering"]["requirements"][
                "items"
            ]
            if row["id"] == requirement["id"]
        )
        assert old["value"] == 225
        claims = (
            session.query(EquipmentEvidenceClaim).filter_by(order_id=order.id).all()
        )
        old_claims = {
            row["id"]: row for row in case["view"]["requirements"]["evidence_claims"]
        }
        assert all(
            row.payload == old_claims[row.claim_id]
            for row in claims
            if row.claim_id in old_claims
        )
        new_claims = [row.payload for row in claims if row.claim_id not in old_claims]
        assert any(
            c["feature"] == "trip_a"
            and c["value"] == 200
            and set(case["requirement"]["source_claim_ids"]).issubset(c["derived_from"])
            for c in new_claims
        )
        assert any(
            c["source"]["source_id"] == case["comment_source"]["document_id"]
            and c["source"]["sha256"] == case["comment_source"]["sha256"]
            for c in new_claims
        )
        connection = MailboxConnection(
            mailbox="engineer@example.com", microsoft_tenant_id=str(uuid4())
        )
        session.add(connection)
        session.flush()
        cid = connection.id
    graph = DraftMailbox()
    monkeypatch.setattr(outlook, "_client", graph)
    delivery = changed["review_deliveries"][-1]
    assert (
        delivery["package_id"] == package["id"]
        and delivery["model_version"] == changed["model_version"]
    )
    _post(
        client,
        f"/api/orders/{case['oid']}/equipment/corrections/{delivery['id']}/outlook",
        {
            "expected_version": changed["version"],
            "connection_id": cid,
        },
    )
    draft = graph.messages["native-correction"]
    assert draft["isDraft"] is True
    assert {a["name"] for a in draft["attachments"]} == {
        "one-line.dxf",
        "front-elevation.dxf",
        "bom.xlsx",
        "submittal.pdf",
    }
    for attached in draft["attachments"]:
        assert base64.b64decode(attached["contentBytes"]) == files[attached["name"]]
    assert not any("/send" in call.url.path for call in graph.calls)
    calls = len(graph.calls)
    newer = _post(
        client,
        case["root"] + "/requirements",
        {
            "expected_version": changed["version"],
            "decisions": [{"id": requirement["id"], "value": 200}],
            "reason": "Engineer reconfirmation invalidates the older generated package",
        },
    )
    _post(
        client,
        f"/api/orders/{case['oid']}/equipment/corrections/{delivery['id']}/outlook",
        {
            "expected_version": newer["version"],
            "connection_id": cid,
        },
        status=409,
    )
    assert len(graph.calls) == calls
    assert graph.messages["native-correction"] == draft


@pytest.mark.parametrize(
    "boundary,status",
    [
        ("stale_version", 409),
        ("wrong_package", 409),
        ("wrong_evidence", 409),
        ("foreign_comment", 409),
        ("unknown_requirement", 422),
    ],
)
def test_review_change_rejects_stale_or_unowned_selection_without_mutation(
    client, boundary, status
):
    case = _prepared(client)
    replacement = {
        "stale_version": {"expected_version": case["view"]["version"] - 1},
        "wrong_package": {"package_id": str(uuid4())},
        "wrong_evidence": {"evidence_id": case["source"]["span_id"]},
        "foreign_comment": {},
        "unknown_requirement": {"requirement_id": str(uuid4())},
    }[boundary]
    if boundary == "foreign_comment":
        other = _post(
            client,
            "/api/orders",
            {
                "title": "Other synthetic order",
                "customer": "Software control",
                "synthetic": True,
            },
        )["order"]["id"]
        original = _original(
            other, "foreign-customer-comments.txt", RETURNED_COMMENT, role="markup"
        )
        record = client.get(f"/api/orders/{other}/record").json()
        comment = next(
            row
            for row in record["comments"]
            if original["span_id"] in row["source_ids"]
        )
        replacement = {"comment_id": comment["id"], "evidence_id": original["span_id"]}
    response = client.post(
        case["root"] + "/review-change",
        json=_change_body(case, **replacement),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == status, response.text
    after = client.get(case["root"]).json()
    assert after["model_version"] == case["view"]["model_version"]
    assert after["requirements"] == case["view"]["requirements"]
    assert len(after["packages"]) == 1 and not after.get("review_deliveries")


def test_changed_customer_comment_original_cannot_change_model(client):
    case = _prepared(client)
    with Session() as session:
        document = get(session, Document, case["comment_source"]["document_id"])
        (db.BLOBS / document.blob).write_bytes(b"Tampered customer original")
    _post(client, case["root"] + "/review-change", _change_body(case), status=409)
    after = client.get(case["root"]).json()
    assert after["requirements"] == case["view"]["requirements"]
    assert len(after["packages"]) == 1 and not after.get("review_deliveries")


def test_changed_generated_file_cannot_be_used_as_review_change_baseline(client):
    from backend.engineering import service

    case = _prepared(client)
    with Session() as session:
        order = get(session, Order, case["oid"])
        path = service.artifact(session, order, case["package"]["id"], "one-line.dxf")
    path.write_bytes(b"Tampered generated drawing")
    _post(client, case["root"] + "/review-change", _change_body(case), status=409)
    after = client.get(case["root"]).json()
    assert after["requirements"] == case["view"]["requirements"]
    assert len(after["packages"]) == 1 and not after.get("review_deliveries")


def test_constraint_violating_comment_does_not_emit_new_package_or_claims(client):
    case = _prepared(client)
    with Session() as session:
        count = (
            session.query(EquipmentEvidenceClaim)
            .filter_by(order_id=case["oid"])
            .count()
        )
    _post(
        client,
        case["root"] + "/review-change",
        _change_body(case, value=300),
        status=409,
    )
    after = client.get(case["root"]).json()
    assert after["requirements"] == case["view"]["requirements"]
    assert after["model_version"] == case["view"]["model_version"]
    assert len(after["packages"]) == 1 and not after.get("review_deliveries")
    with Session() as session:
        assert (
            session.query(EquipmentEvidenceClaim)
            .filter_by(order_id=case["oid"])
            .count()
            == count
        )
