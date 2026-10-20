"""Actual originals through authenticated order APIs, never comment assertions."""

from uuid import uuid4

import pytest

from backend.domain.service import get
from backend.equipment.demo_documents import build_demo_corpus
from backend.identity import Identity, identity_scope
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Order, OrderEvent, OrderRevision


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("equipment-api-originals")
    manifest = build_demo_corpus(root)
    return {
        item["id"]: {**item, "file": root / item["path"]}
        for item in manifest["sources"]
    }


def new_order(client):
    result = client.post(
        "/api/orders",
        json={
            "title": "Fictional switchboard revision check",
            "customer": "Fictional equipment test",
            "synthetic": True,
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 200, result.text
    return result.json()["order"]["id"]


def upload(client, order_id, corpus, keys, *, key=None, expected_status=200):
    sources = [corpus[k] for k in keys]
    source = sources[0]
    result = client.post(
        f"/api/orders/{order_id}/equipment/packages",
        data={"role": source["role"], "revision": source["revision"]},
        files=[
            ("files", (item["name"], item["file"].read_bytes())) for item in sources
        ],
        headers={"Idempotency-Key": key or str(uuid4())},
    )
    assert result.status_code == expected_status, result.text
    return result


def add_pair(client, order_id, corpus, folder, kind="pdf"):
    upload(client, order_id, corpus, [folder + ".drawing." + kind])
    return upload(client, order_id, corpus, [folder + ".bom.xlsx"]).json()


def add_partial(client, corpus):
    order_id = new_order(client)
    upload(client, order_id, corpus, ["requirements.pdf"])
    add_pair(client, order_id, corpus, "01-baseline-C")
    view = add_pair(client, order_id, corpus, "02-partial-D")
    return order_id, view


def row(view, tag="CB-12", field="trip_unit"):
    return next(
        c
        for c in view["comparison"]["checks"]
        if c["device_tag"] == tag and c["field"] == field
    )


def test_actual_drawing_edit_stale_bom_and_removed_device_are_visible(client, corpus):
    order_id, view = add_partial(client, corpus)
    trip = row(view)
    assert trip["status"] == "mismatch"
    assert trip["baseline_drawing"]["value"] == "TU-2500"
    assert trip["target_drawing"]["value"] == "TU-3000"
    assert trip["target_bom"]["value"] == "TU-2500"
    assert row(view, "CB-14", "device_presence")["status"] == "missing"
    assert view["comparison"]["summary"]["remaining"] > 0
    assert view["comparison"]["coverage"]["complete_inventory_verified"] is False
    assert all("path" not in str(e.keys()) for e in trip["target_drawing"]["evidence"])
    saved = client.get(f"/api/orders/{order_id}/equipment").json()
    assert row(saved)["status"] == "mismatch"
    with Session() as s:
        events = (
            s.query(OrderEvent)
            .filter_by(order_id=order_id, kind="equipment_check")
            .all()
        )
        assert events and events[-1].data["equipment_check"]["summary"]["remaining"] > 0
        manifest = events[-1].data["equipment_check"]["source_manifest"]
        assert len(manifest["baseline"]) == 2 and len(manifest["target"]) == 2
        assert manifest["requirements"]
        assert all(
            "path" not in item and len(item["sha256"]) == 64
            for items in manifest.values()
            for item in items
        )


def test_complete_revision_checks_both_originals_and_keeps_history(client, corpus):
    order_id, before = add_partial(client, corpus)
    after = add_pair(client, order_id, corpus, "03-complete-E")
    assert row(after)["status"] == "verified"
    assert row(after)["target_bom"]["value"] == "TU-3000"
    assert after["comparison"]["summary"]["remaining"] == 0
    assert after["comparison"]["summary"]["outcome"] == "supported_fields_match"
    assert before["comparison"]["fingerprint"] != after["comparison"]["fingerprint"]
    with Session() as s:
        assert len(s.query(OrderRevision).filter_by(order_id=order_id).all()) > 2
        assert get(s, Order, order_id).status != "released"


def test_explicit_historical_pair_stays_selected_and_retries_are_idempotent(
    client, corpus
):
    order_id, _ = add_partial(client, corpus)
    current = add_pair(client, order_id, corpus, "03-complete-E")
    body = {
        "expected_version": current["version"],
        "baseline_revision": "C",
        "target_revision": "D",
    }
    key = str(uuid4())
    first = client.post(
        f"/api/orders/{order_id}/equipment/check",
        json=body,
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 200, first.text
    retry = client.post(
        f"/api/orders/{order_id}/equipment/check",
        json=body,
        headers={"Idempotency-Key": key},
    )
    assert retry.json() == first.json()
    selected = client.get(f"/api/orders/{order_id}/equipment").json()
    assert selected["comparison"]["target_revision"] == "D"
    assert row(selected)["status"] == "mismatch"
    stale = client.post(
        f"/api/orders/{order_id}/equipment/check",
        json=body,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert stale.status_code == 409


def test_upload_retry_does_not_duplicate_originals(client, corpus):
    order_id = new_order(client)
    key = str(uuid4())
    first = upload(
        client, order_id, corpus, ["01-baseline-C.drawing.pdf"], key=key
    ).json()
    second = upload(
        client, order_id, corpus, ["01-baseline-C.drawing.pdf"], key=key
    ).json()
    assert first == second
    assert len(second["available_documents"]) == 1


def test_rendered_pdf_evidence_matches_the_hash_bound_observation(client, corpus):
    order_id, view = add_partial(client, corpus)
    citation = row(view)["target_drawing"]["evidence"][0]
    assert citation["page"] == 1 and len(citation["bbox"]) == 4
    png = client.get("/api" + citation["crop_url"])
    assert png.status_code == 200, png.text[:200] if png.status_code != 200 else ""
    assert png.content.startswith(b"\x89PNG")
    assert "no-store" in png.headers["cache-control"]
    original = client.get("/api" + citation["url"])
    assert original.content == corpus["02-partial-D.drawing.pdf"]["file"].read_bytes()
    fake = client.get(
        f"/api/orders/{order_id}/equipment/evidence",
        params={"document_id": citation["document_id"], "fact_id": "invented-fact"},
    )
    assert fake.status_code == 409


def test_cross_order_sources_and_cross_tenant_reads_are_rejected(client, corpus):
    order_id, view = add_partial(client, corpus)
    other = new_order(client)
    source_id = row(view)["target_drawing"]["evidence"][0]["document_id"]
    assert (
        client.get(f"/api/orders/{other}/equipment/files/{source_id}").status_code
        == 403
    )
    attempted = client.post(
        f"/api/orders/{other}/equipment/packages",
        data={
            "role": "drawing",
            "revision": "D",
            "document_ids": '["' + source_id + '"]',
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert attempted.status_code == 403
    with (
        identity_scope(Identity(str(uuid4()), "Other company", "org:admin")),
        Session() as s,
    ):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as forbidden:
            get(s, Order, order_id)
        assert forbidden.value.status_code == 404


def test_changed_original_never_reuses_cached_verified_result(client, corpus):
    order_id = new_order(client)
    upload(client, order_id, corpus, ["requirements.pdf"])
    add_pair(client, order_id, corpus, "01-baseline-C")
    good = add_pair(client, order_id, corpus, "03-complete-E")
    assert row(good)["status"] == "verified"
    citation = row(good)["target_drawing"]["evidence"][0]
    with Session() as s:
        doc = get(s, Document, citation["document_id"])
        path = db.BLOBS / doc.blob
    path.write_bytes(corpus["01-baseline-C.drawing.pdf"]["file"].read_bytes())
    changed = client.get(f"/api/orders/{order_id}/equipment").json()
    assert row(changed, "CB-12", "device_presence")["status"] == "unknown"
    assert changed["comparison"]["summary"]["revision_changes_verified"] is False
    assert changed["comparison"]["summary"]["outcome"] != "supported_fields_match"
    assert changed["comparison"]["fingerprint"] != good["comparison"]["fingerprint"]
    assert client.get("/api" + citation["url"]).status_code == 409


def test_native_dxf_checks_actual_attributes_with_excel(client, corpus):
    order_id = new_order(client)
    upload(client, order_id, corpus, ["requirements.pdf"])
    add_pair(client, order_id, corpus, "01-baseline-C", "dxf")
    view = add_pair(client, order_id, corpus, "02-partial-D", "dxf")
    assert row(view)["status"] == "mismatch"
    assert row(view)["target_drawing"]["evidence"][0]["cad_handle"]
    assert row(view)["target_bom"]["evidence"][0]["cell"]


def test_ordinary_document_uploads_are_checked_without_reupload(client, corpus):
    order_id = new_order(client)
    workspace = client.get(f"/api/orders/{order_id}").json()
    project_id = workspace["order"]["project_id"]
    for key in [
        "requirements.pdf",
        "01-baseline-C.drawing.pdf",
        "01-baseline-C.bom.xlsx",
        "02-partial-D.drawing.pdf",
        "02-partial-D.bom.xlsx",
    ]:
        item = corpus[key]
        kind = "approval_drawing" if item["role"] == "drawing" else item["role"]
        result = client.post(
            f"/api/projects/{project_id}/documents",
            data={"kind": kind, "revision_label": item["revision"]},
            files={"file": (item["name"], item["file"].read_bytes())},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert result.status_code == 202, result.text
    view = client.get(f"/api/orders/{order_id}/equipment").json()
    assert row(view)["status"] == "mismatch"
    assert row(view)["target_bom"]["value"] == "TU-2500"


def test_new_order_uploads_close_the_loop_without_starting_an_agent_task(
    client, corpus
):
    order_id, _ = add_partial(client, corpus)
    workspace = client.get(f"/api/orders/{order_id}").json()
    project_id = workspace["order"]["project_id"]
    for key in ["03-complete-E.drawing.pdf", "03-complete-E.bom.xlsx"]:
        item = corpus[key]
        response = client.post(
            f"/api/projects/{project_id}/documents",
            data={
                "kind": "approval_drawing" if item["role"] == "drawing" else "bom",
                "revision_label": "E",
            },
            files={"file": (item["name"], item["file"].read_bytes())},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 202, response.text
    with Session() as s:
        saved = get(s, Order, order_id).state["equipment_check"]
        assert saved["target_revision"] == "E"
        assert saved["summary"]["revision_changes_verified"] is True
        assert saved["summary"]["remaining"] == 0
        assert len(saved["source_manifest"]["target"]) == 2


def test_clients_cannot_supply_verified_facts_or_claim_engineering_release(client):
    order_id = new_order(client)
    result = client.post(
        f"/api/orders/{order_id}/equipment/check",
        json={
            "expected_version": 1,
            "baseline_revision": "C",
            "target_revision": "D",
            "verified": True,
            "facts": [{"trip_unit": "TU-3000"}],
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 422


def test_old_check_waivers_cannot_bypass_failed_equipment_comparison(
    client, corpus, monkeypatch
):
    from backend.orders import service as orders

    order_id, _ = add_partial(client, corpus)
    # Simulate the older ledger having all its required decisions signed. The
    # actual drawing/BOM failure must still block both readiness and release.
    monkeypatch.setattr(orders, "checks_for", lambda *_: ([], [{"status": "waived"}]))
    with Session.begin() as s:
        order = get(s, Order, order_id)
        order.state = {**order.state, "comments": [], "tasks": []}
        orders.commit(s, order, "Existing checks reviewed")
        assert order.status == "review"
        gate = orders.release_state(s, order, [{"status": "waived"}], [])
        assert gate["ready"] is False
        assert any("drawing and BOM" in b for b in gate["blockers"])
        body = {
            "expected_version": order.version,
            "snapshot_hash": order.snapshot_hash,
            "signer": "Test engineer",
            "reason": "All earlier ledger checks were waived for this fixture.",
        }
    response = client.post(
        f"/api/orders/{order_id}/release",
        json=body,
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 422, response.text
    assert "drawing and BOM" in response.text


def test_current_verified_check_becomes_a_release_blocker_if_original_changes(
    client, corpus
):
    from backend.equipment.service import release_blockers

    order_id = new_order(client)
    upload(client, order_id, corpus, ["requirements.pdf"])
    add_pair(client, order_id, corpus, "01-baseline-C")
    good = add_pair(client, order_id, corpus, "03-complete-E")
    with Session() as s:
        order = get(s, Order, order_id)
        assert release_blockers(s, order) == []
        doc = get(
            s, Document, row(good)["target_drawing"]["evidence"][0]["document_id"]
        )
        path = db.BLOBS / doc.blob
        path.write_bytes(corpus["01-baseline-C.drawing.pdf"]["file"].read_bytes())
        assert release_blockers(s, order)


def test_historical_comparison_cannot_authorize_a_newer_package(client, corpus):
    from backend.equipment.service import release_blockers, save_check

    order_id, _ = add_partial(client, corpus)
    add_pair(client, order_id, corpus, "03-complete-E")
    with Session.begin() as s:
        order = get(s, Order, order_id)
        assert release_blockers(s, order) == []
        save_check(s, order, "C", "D")
        assert any("historical" in b for b in release_blockers(s, order))
