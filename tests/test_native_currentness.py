"""Cached native verification never authorizes changed source or output bytes."""

from uuid import uuid4

import ezdxf
import pytest
import test_native_outlook_workflow as native_workflow
from fastapi import HTTPException
from test_equipment_outlook import DraftMailbox

from backend.domain.service import get
from backend.equipment import outlook, workflow
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Order

corpus = native_workflow.corpus


def _prepare_and_warm_native_cache(client, corpus):
    oid, _ = native_workflow.register(client, corpus, 1)
    view = client.get(f"/api/orders/{oid}/equipment/corrections").json()
    response = client.post(
        f"/api/orders/{oid}/equipment/corrections/prepare",
        json={
            "expected_version": view["version"],
            "expected_plan_id": view["plan"]["plan_id"],
        },
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    bundle = response.json()["bundles"][0]
    assert bundle["geometry_changed"] and bundle["current"] and bundle["attachable"]
    assert bundle["native_verification"]["status"] == "verified"
    before = workflow._verified_native_package.cache_info().hits
    for _ in range(2):
        warmed = client.get(f"/api/orders/{oid}/equipment/corrections")
        assert warmed.status_code == 200, warmed.text
        assert warmed.json()["bundles"][0]["current"]
    assert workflow._verified_native_package.cache_info().hits >= before + 2
    return oid, bundle


def _move_actual_relay(path):
    drawing = ezdxf.readfile(path)
    relay = next(
        entity
        for entity in drawing.modelspace().query("INSERT")
        if entity.get_attrib_text("EQUIPMENT_TAG") == "KA-1"
    )
    relay.translate(7, 0, 0)
    drawing.saveas(path)


def _assert_no_stale_handoff(client, oid, bundle):
    public = client.get(f"/api/orders/{oid}/equipment/corrections")
    assert public.status_code == 200, public.text
    stale = next(item for item in public.json()["bundles"] if item["id"] == bundle["id"])
    assert stale["current"] is False and stale["attachable"] is False
    graph = DraftMailbox()
    with pytest.raises(HTTPException) as failure:
        outlook.save(oid, bundle["id"], client_factory=graph)
    assert failure.value.status_code == 409
    assert graph.calls == []
    assert graph.messages == {}


def test_warmed_native_cache_does_not_authorize_tampered_output(client, corpus):
    oid, bundle = _prepare_and_warm_native_cache(client, corpus)
    drawing = next(item for item in bundle["artifacts"] if item["role"] == "drawing")
    with Session() as session:
        path, _ = workflow.artifact(
            session, get(session, Order, oid), bundle["id"], drawing["name"]
        )
    _move_actual_relay(path)
    direct = client.get("/api" + drawing["url"])
    assert direct.status_code == 409, direct.text
    _assert_no_stale_handoff(client, oid, bundle)


def test_warmed_native_cache_does_not_authorize_changed_registered_source(client, corpus):
    oid, bundle = _prepare_and_warm_native_cache(client, corpus)
    with Session() as session:
        order = get(session, Order, oid)
        sources = session.query(Document).filter_by(project_id=order.project_id).all()
        source = next(
            row
            for row in sources
            if row.name.lower().endswith(".dxf")
            and row.meta.get("equipment_package", {}).get("revision")
            == bundle["source_revision"]
        )
        original_registered_sha = source.sha256
        source_path = db.BLOBS / source.blob
        _move_actual_relay(source_path)
        # The registration and stored verification are unchanged. Only the
        # current bytes reveal that the earlier native check is no longer valid.
        assert source.sha256 == original_registered_sha
    _assert_no_stale_handoff(client, oid, bundle)
