"""Company rules are actual delivery gates, bound to owned reviewed originals."""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException
from reportlab.pdfgen.canvas import Canvas
from test_equipment_api import new_order
from test_equipment_outlook import DraftMailbox
from test_native_outlook_workflow import register

from backend.domain.service import get
from backend.equipment import outlook, profile_service, workflow
from backend.equipment.native_demo import build_native_corpus
from backend.equipment.profile_models import ManufacturerProfileVersion
from backend.identity import Identity, current_org, identity_scope
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Order


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("profile-native-originals")
    manifest = build_native_corpus(root)
    return manifest, {
        item["id"]: {**item, "file": root / item["path"]}
        for item in manifest["sources"]
    }


def request(client, url, body, method="post"):
    return getattr(client, method)(
        url, json=body, headers={"Idempotency-Key": str(uuid4())}
    )


def guide(client):
    oid = new_order(client)
    stream = BytesIO()
    canvas = Canvas(stream)
    text = "Fictional company design guide: KA-1 relay center must be within 600 mm."
    canvas.drawString(40, 750, text)
    canvas.save()
    data = stream.getvalue()
    result = client.post(
        f"/api/orders/{oid}/equipment/packages",
        data={"role": "specification", "revision": "GUIDE-1"},
        files=[("files", ("Fictional-guide.pdf", data))],
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert result.status_code == 200, result.text
    with Session() as s:
        doc = (
            s.query(Document).filter_by(project_id=get(s, Order, oid).project_id).one()
        )
        source = {
            "document_id": doc.id,
            "name": doc.name,
            "revision": "GUIDE-1",
            "role": "specification",
            "sha256": sha256(data).hexdigest(),
            "location": {"page": 1, "text": text},
        }
    return source


def proposed(client, *, maximum=600, source=None, conditions=None, expected_status=200):
    source = source or guide(client)
    manifest = {
        "profile_id": "fictional-switchboard",
        "tenant_id": "forged-tenant",
        "manufacturer_name": "Fictional test manufacturer",
        "name": "Panel layout rules",
        "status": "approved",
        "review": {"reviewer_id": "forged", "reviewed_at": "2026-01-01T00:00:00Z"},
        "sources": [source],
        "aliases": [],
        "rules": [
            {
                "rule_id": "relay-layout",
                "label": "Relay center limit",
                "status": "approved",
                "device_tag": "KA-1",
                "source_ids": [source["document_id"]],
                "constraint": {"op": "dimension_bounds", "x_max": maximum},
                "when": conditions or [],
            }
        ],
    }
    response = request(client, "/api/equipment/profiles", {"manifest": manifest})
    assert response.status_code == expected_status, response.text
    return response.json()


def approved(client, **kwargs):
    proposal = proposed(client, **kwargs)
    response = request(
        client,
        f"/api/equipment/profiles/{proposal['id']}/review",
        {"expected_version_hash": proposal["version_hash"]},
    )
    assert response.status_code == 200, response.text
    return proposal, response.json()


def bind(client, oid, profile):
    view = client.get(f"/api/orders/{oid}/equipment/corrections").json()
    response = request(
        client,
        f"/api/orders/{oid}/equipment/profile",
        {
            "expected_version": view["version"],
            "profile_version_id": profile["id"],
            "reason": "Use this explicitly reviewed fictional company profile.",
        },
        "put",
    )
    assert response.status_code == 200, response.text
    return client.get(f"/api/orders/{oid}/equipment/corrections").json()


def prepare(client, oid, view):
    return request(
        client,
        f"/api/orders/{oid}/equipment/corrections/prepare",
        {
            "expected_version": view["version"],
            "expected_plan_id": view["plan"]["plan_id"],
        },
    )


def test_proposal_cannot_forge_approval_and_review_is_an_immutable_owned_version(
    client,
):
    proposal, reviewed = approved(client)
    assert proposal["manifest"]["status"] == "proposed"
    assert proposal["manifest"]["review"] is None
    assert proposal["manifest"]["tenant_id"] == current_org()
    assert all(rule["status"] == "proposed" for rule in proposal["manifest"]["rules"])
    assert reviewed["id"] != proposal["id"] and reviewed["parent_id"] == proposal["id"]
    assert reviewed["manifest"]["status"] == "approved"
    assert reviewed["manifest"]["review"]["reviewer_id"] == "Local estimator"
    assert reviewed["version_hash"] != proposal["version_hash"]
    assert reviewed["source_urls"]
    with Session() as s:
        assert (
            get(s, ManufacturerProfileVersion, proposal["id"]).manifest
            == proposal["manifest"]
        )
    with identity_scope(Identity(str(uuid4()), "other tenant")), Session() as s:
        assert profile_service.overview(s) == {"profiles": []}
        with pytest.raises(HTTPException) as exc:
            profile_service.review(s, reviewed["id"], reviewed["version_hash"])
        assert exc.value.status_code == 404
    with (
        identity_scope(Identity(current_org(), "ordinary member", "org:member")),
        Session() as s,
    ):
        with pytest.raises(HTTPException) as exc:
            profile_service.revoke(
                s, reviewed["id"], "Must require company administration."
            )
        assert exc.value.status_code == 403


def test_unquoted_or_changed_guide_cannot_be_reviewed(client):
    source = guide(client)
    bad = deepcopy(source)
    bad["location"]["text"] = "The document never authorizes this fabricated rule."
    proposed(client, source=bad, expected_status=409)
    proposal = proposed(client, source=source)
    with Session() as s:
        document = get(s, Document, source["document_id"])
        (db.BLOBS / document.blob).write_bytes(b"changed guide bytes")
    response = request(
        client,
        f"/api/equipment/profiles/{proposal['id']}/review",
        {"expected_version_hash": proposal["version_hash"]},
    )
    assert response.status_code == 409


@pytest.mark.parametrize("entries", [["bad entry"], [None], "not a list"])
def test_malformed_rule_entries_return_a_validation_error(client, entries):
    response = request(
        client, "/api/equipment/profiles", {"manifest": {"rules": entries}}
    )
    assert response.status_code == 422


def test_inactive_or_wrong_crop_cannot_authorize_a_company_rule(client):
    source = guide(client)
    cropped = deepcopy(source)
    cropped["location"]["bbox"] = [0.1, 0.5, 0.5, 0.7]
    proposed(client, source=cropped, expected_status=409)
    proposal = proposed(client, source=source)
    with Session.begin() as s:
        document = get(s, Document, source["document_id"])
        document.meta = {**document.meta, "active": False}
    response = request(
        client,
        f"/api/equipment/profiles/{proposal['id']}/review",
        {"expected_version_hash": proposal["version_hash"]},
    )
    assert response.status_code == 409


def test_company_geometry_rule_is_rechecked_on_saved_files_and_revocation_blocks_outlook(
    client, corpus
):
    oid, prior_payload = register(client, corpus, 2)
    _, profile = approved(client)
    view = bind(client, oid, profile)
    assert workflow.process(
        prior_payload,
        client_factory=lambda *_: pytest.fail(
            "A superseded plan cannot write to Outlook."
        ),
    ) == {"status": "superseded"}
    assert view["plan"]["company_profile"]["version"] == profile["version_hash"]
    response = prepare(client, oid, view)
    assert response.status_code == 200, response.text
    bundle = response.json()["bundles"][0]
    assert bundle["attachable"] and bundle["company_rules"]["status"] == "pass"
    result = bundle["company_rules"]["results"][0]
    assert result["status"] == "pass" and result["evidence"]
    assert (
        result["rule_sources"][0]["document_id"]
        == profile["manifest"]["sources"][0]["document_id"]
    )
    graph = DraftMailbox()
    saved = outlook.save(oid, bundle["id"], client_factory=graph)
    assert saved["draft"]["status"] == "created"
    calls = len(graph.calls)
    revoked = request(
        client,
        f"/api/equipment/profiles/{profile['id']}/revoke",
        {"reason": "Fictional guide withdrawn after company review."},
    )
    assert revoked.status_code == 200
    after = client.get(f"/api/orders/{oid}/equipment/corrections").json()
    assert after["bundles"][0]["current"] is False
    assert after["bundles"][0]["attachable"] is False
    with pytest.raises(HTTPException):
        outlook.save(oid, bundle["id"], client_factory=graph)
    assert len(graph.calls) == calls


def test_company_rule_can_fail_the_corrected_layout_before_any_outlook_write(
    client, corpus
):
    oid, _ = register(client, corpus, 2)
    _, profile = approved(client, maximum=499)
    view = bind(client, oid, profile)
    response = prepare(client, oid, view)
    assert response.status_code == 409, response.text
    report = response.json()["detail"]["company_rules"]
    assert report["status"] == "fail"
    assert report["results"][0]["status"] == "fail"
    with Session() as s:
        order = get(s, Order, oid)
        assert not order.state.get("equipment_outlook")
        assert not order.state.get("equipment_corrections", {}).get("bundles")


def test_explicitly_inapplicable_rule_is_not_an_approval_claim_or_an_unrelated_delivery_block(
    client, corpus
):
    oid, _ = register(client, corpus, 2)
    _, profile = approved(
        client, conditions=[{"field": "quantity", "equals": 999, "unit": "each"}]
    )
    response = prepare(client, oid, bind(client, oid, profile))
    assert response.status_code == 200, response.text
    bundle = response.json()["bundles"][0]
    assert bundle["attachable"]
    rules = bundle["company_rules"]
    assert rules["status"] == "unknown"
    assert rules["results"][0]["applicable"] is False
    assert rules["results"][0]["status"] == "unknown"
    assert rules["engineering_approved"] is False
