"""Real persistence and artifact tests for the local order-coordination lifecycle."""

import json
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import TypeAdapter

from backend.domain.service import digest, get
from backend.identity import Identity, identity_scope
from backend.lifecycle import adapters, service
from backend.lifecycle.schemas import DecisionWrite, PolicyWrite, Prepare
from backend.orders.service import lock_order, new_order
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import (
    Document,
    Order,
    OrderEvent,
    OrderRecord,
    Project,
    Span,
)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    with Session.begin() as s:
        order = new_order(
            s,
            {
                "title": "Lifecycle integration replay",
                "customer": "Fictional test customer",
                "number": "LIFECYCLE-001",
                "synthetic": True,
            },
        )
        order_id = order.id
        project_id = order.project_id

    def source(value, *, kind="specification", release="", name="source.json"):
        with Session.begin() as s:
            doc = Document(
                project_id=project_id,
                name=name,
                kind=kind,
                blob="fixture.json",
                sha256=uuid4().hex * 2,
                state="ready",
                meta={"order_role": kind, "release_status": release},
                coverage=[{"label": "Page 1", "state": "complete"}],
            )
            s.add(doc)
            s.flush()
            if kind in {"erp_price", "erp_order", "accepted_exception"}:
                doc.meta = {
                    **doc.meta,
                    "lifecycle_trust": {
                        "origin": "backend_local_demo",
                        "organization_id": doc.organization_id,
                        "order_id": order_id,
                        "document_sha256": doc.sha256,
                        "purposes": ["commercial_scope"]
                        if kind == "accepted_exception"
                        else ["pricing", "lead_time"],
                    },
                }
            span = Span(
                document_id=doc.id,
                text=json.dumps(value) if not isinstance(value, str) else value,
                location={"page": 1},
            )
            s.add(span)
            s.flush()
            return doc.id, span.id

    return order_id, source


def prepare(order_id, **values):
    with Session.begin() as s:
        order = lock_order(s, order_id)
        body = TypeAdapter(Prepare).validate_python(
            {"expected_version": order.version, **values}
        )
        return service.prepare(s, order, body)


def decision(order_id, package_id, gate, source_id):
    with Session.begin() as s:
        order = lock_order(s, order_id)
        return service.decide(
            s,
            order,
            package_id,
            DecisionWrite(
                expected_version=order.version,
                gate=gate,
                decision="approve",
                reason="Reviewed the exact cited package and source evidence.",
                source_ids=[source_id],
            ),
        )


def approve_all(order_id, package, source_id):
    result = None
    for gate in package["gates"]:
        result = decision(order_id, package["id"], gate, source_id)
    return result


def price_fixture(fixture):
    order_id, source = fixture
    _, price = source(
        {
            "tag": "CB-12",
            "unit_cost": "1200.00",
            "currency": "USD",
            "gross_margin_percent": "25.00",
        },
        kind="erp_price",
    )
    result = prepare(
        order_id,
        operation="quote",
        lines=[
            {
                "tag": "CB-12",
                "description": "Fictional breaker",
                "quantity": "2",
                "unit_cost": "1200.00",
                "gross_margin_percent": "25.00",
                "source_ids": [price],
            }
        ],
    )
    return result, price


def review_fixture(fixture):
    order_id, source = fixture
    _, evidence = source("Returned comment: confirm nameplate text.")
    with Session.begin() as s:
        s.add(
            OrderRecord(
                order_id=order_id,
                data={
                    "comments": [
                        {
                            "id": "c1",
                            "number": "1",
                            "revision": "C",
                            "text": "Confirm nameplate text.",
                        }
                    ]
                },
            )
        )
    result = prepare(
        order_id,
        operation="revision_review",
        revision="C",
        comments=[
            {
                "comment_id": "c1",
                "kind": "answer",
                "source_ids": [evidence],
                "response": "The nameplate text is shown in the cited source; please confirm.",
            }
        ],
    )
    return result, evidence


def check_fixture(
    fixture, *, release="released", disposition="answered", part="TU-2500-DEMO"
):
    order_id, source = fixture
    _, evidence = review_fixture(fixture)
    with Session.begin() as s:
        record = s.query(OrderRecord).filter_by(order_id=order_id).one()
        record.data = {
            "comments": [
                {
                    **record.data["comments"][0],
                    "status": "responded",
                    "reviewed": True,
                    "response": "The fictional PM checked the nameplate text against its source and answered.",
                    "responder": "Fictional PM",
                }
            ]
        }
    _, approved = source(
        {"tag": "CB-12", "quantity": "1", "part_number": "TU-2500-DEMO"},
        kind="bom",
        release="approved",
    )
    _, revised = source(
        {"tag": "CB-12", "quantity": "1", "part_number": part}, kind="bom"
    )
    drawing, _ = source("Released drawing E-10 Rev D", kind="drawing", release=release)
    result = prepare(
        order_id,
        operation="revision_check",
        revision="D",
        resolutions=[
            {
                "comment_id": "c1",
                "source_ids": [evidence],
                "disposition": disposition,
                "note": "Response checked against source evidence.",
            }
        ],
        approved_bom=[
            {
                "tag": "CB-12",
                "quantity": "1",
                "part_number": "TU-2500-DEMO",
                "source_ids": [approved],
            }
        ],
        revision_bom=[
            {
                "tag": "CB-12",
                "quantity": "1",
                "part_number": part,
                "source_ids": [revised],
            }
        ],
        outbound_document_ids=[drawing],
    )
    return result, evidence


def test_quote_uses_decimal_rule_and_persists_real_artifact(fixture):
    result, source = price_fixture(fixture)
    package = result["packages"][0]
    assert package["payload"]["total"] == "3200.00"
    assert package["gates"] == ["money", "scope", "customer_commitment"]
    with Session() as s:
        order = get(s, Order, result["order_id"])
        artifact = next(a for a in package["artifacts"] if a["name"] == "quote.json")
        content = adapters.read_artifact(
            order.organization_id, order.id, package["id"], artifact
        )
        assert json.loads(content)["lines"][0]["unit_price"] == "1600.00"
        assert (
            s.query(OrderEvent)
            .filter(
                OrderEvent.order_id == order.id, OrderEvent.kind == "lifecycle_prepared"
            )
            .count()
            == 1
        )
    approved = approve_all(result["order_id"], package, source)
    assert approved["packages"][0]["status"] == "approved"


def test_unrelated_citation_cannot_launder_a_price(fixture):
    order_id, source = fixture
    _, id = source("A breaker is required, but no price is stated.")
    with pytest.raises(HTTPException, match="structured ERP price"):
        prepare(
            order_id,
            operation="quote",
            lines=[
                {
                    "tag": "CB-12",
                    "description": "Breaker",
                    "quantity": "1",
                    "unit_cost": "5",
                    "gross_margin_percent": "25",
                    "source_ids": [id],
                }
            ],
        )


def test_purchase_order_mismatch_drafts_clarification_without_external_writes(fixture):
    order_id, source = fixture
    quote, price = price_fixture(fixture)
    approve_all(order_id, quote["packages"][0], price)
    _, po = source(
        {
            "tag": "CB-12",
            "quantity": "3",
            "unit_price": "1600.00",
            "purchase_order": "PO-DEMO-1",
        },
        kind="purchase_order",
    )
    result = prepare(
        order_id,
        operation="kickoff",
        purchase_order_number="PO-DEMO-1",
        lines=[
            {
                "tag": "CB-12",
                "quantity": "3",
                "unit_price": "1600.00",
                "source_ids": [po],
            }
        ],
    )
    package = result["packages"][0]
    assert package["status"] == "blocked"
    assert package["payload"]["mismatches"][0]["tag"] == "CB-12"
    assert {r["system"] for r in package["receipts"]} == {"SAP", "SharePoint"}
    assert all(r["status"] == "prepared_local_demo" for r in package["receipts"])


def test_source_change_invalidates_exact_approval_and_downstream_work(fixture):
    result, source_id = price_fixture(fixture)
    package = result["packages"][0]
    with Session.begin() as s:
        get(s, Span, source_id).text = "Changed source price"
    with pytest.raises(HTTPException, match="evidence changed"):
        decision(result["order_id"], package["id"], "money", source_id)
    with Session() as s:
        value = service.view(s, get(s, Order, result["order_id"]))
        assert value["packages"][0]["status"] == "stale"


def test_cannot_omit_revision_comment_or_claim_draft_answers_are_verified(fixture):
    order_id, _ = fixture
    result, evidence = review_fixture(fixture)
    with Session.begin() as s:
        record = s.query(OrderRecord).filter_by(order_id=order_id).one()
        record.data = {
            "comments": record.data["comments"]
            + [{"id": "c2", "revision": "C", "text": "A difficult unresolved item."}]
        }
    with pytest.raises(HTTPException, match="every comment"):
        prepare(
            order_id,
            operation="revision_review",
            revision="C",
            comments=[
                {
                    "comment_id": "c1",
                    "kind": "answer",
                    "source_ids": [evidence],
                    "response": "Only the easy answer.",
                }
            ],
        )
    assert result["packages"][0]["payload"]["comments"][0]["status"] == "prepared"


@pytest.mark.parametrize(
    "release,part,expected",
    [
        ("draft", "TU-2500-DEMO", "Unreleased outbound"),
        ("released", "TU-3000-DEMO", "bill of materials differs"),
    ],
)
def test_revision_check_blocks_draft_attachments_and_bom_conflicts(
    fixture, release, part, expected
):
    result, evidence = check_fixture(fixture, release=release, part=part)
    package = result["packages"][0]
    assert any(expected in value for value in package["blockers"])
    with pytest.raises(HTTPException, match="source blockers"):
        decision(result["order_id"], package["id"], "engineering", evidence)


def test_release_and_closeout_need_exact_engineering_and_customer_evidence(fixture):
    order_id, source = fixture
    result, evidence = check_fixture(fixture)
    check = result["packages"][0]
    assert check["payload"]["addressed_comments"] == 0
    approved = decision(order_id, check["id"], "engineering", evidence)
    assert approved["packages"][0]["payload"]["addressed_comments"] == 1
    review = next(
        package
        for package in approved["packages"]
        if package["operation"] == "revision_review"
    )
    approve_all(order_id, review, evidence)
    customer_doc, customer = source(
        "Customer approves exact Rev D.", kind="customer_approval"
    )
    engineer_doc, engineer = source(
        "Engineer approves exact Rev D.", kind="engineering_approval"
    )
    with pytest.raises(HTTPException, match="exact revision"):
        prepare(
            order_id,
            operation="release",
            customer_approval_source_ids=[customer],
            engineering_approval_source_ids=[engineer],
        )
    with Session.begin() as s:
        hashes = [
            get(s, Document, id).sha256
            for id in check["payload"]["outbound_document_ids"]
        ]
        for id in (customer_doc, engineer_doc):
            doc = get(s, Document, id)
            doc.meta = {
                **doc.meta,
                "approved_revision": "D",
                "approved_document_sha256": hashes,
            }
    release = prepare(
        order_id,
        operation="release",
        customer_approval_source_ids=[customer],
        engineering_approval_source_ids=[engineer],
    )
    assert release["packages"][0]["status"] == "awaiting_decision"
    release = approve_all(order_id, release["packages"][0], engineer)
    report, _ = source("Fictional signed test report.", kind="test_report")
    drawing, _ = source("Fictional as-built drawing.", kind="as_built")
    # Closeout gathers the approved physical source bytes, not metadata placeholders.
    with Session.begin() as s:
        for document_id, content, revision in (
            (report, b"Fictional approved test record; no physical test claimed.", "0"),
            (drawing, b"Fictional as-built Rev D drawing record.", "D"),
        ):
            document = get(s, Document, document_id)
            document.blob = document.id + ".txt"
            db.BLOBS.mkdir(parents=True, exist_ok=True)
            (db.BLOBS / document.blob).write_bytes(content)
            document.sha256 = sha256(content).hexdigest()
            document.meta = {
                **document.meta,
                "revision_label": revision,
                "release_status": "released" if document_id == drawing else "approved",
            }
    closeout = prepare(
        order_id,
        operation="closeout",
        test_report_document_ids=[report],
        as_built_document_ids=[drawing],
    )
    assert len(closeout["packages"][0]["payload"]["documents"]) == 2
    assert closeout["packages"][0]["mode"] == "local_demo"
    with Session() as s:
        assert (
            get(s, Order, order_id).status != "released"
        )  # No false production release.


def test_purchasing_six_weeks_is_only_a_proposed_date(fixture):
    order_id, source = fixture
    review_fixture(fixture)
    _, evidence = source(
        {"part_number": "CB-EXTRA", "lead_time_days": 42}, kind="erp_order"
    )
    result = prepare(
        order_id,
        operation="purchasing",
        part_number="CB-EXTRA",
        lead_time_days=42,
        order_date="2026-10-15",
        current_ship_date="2026-11-01",
        source_ids=[evidence],
    )
    payload = result["packages"][0]["payload"]
    assert payload["proposed_ship_date"] == "2026-11-26"
    assert payload["current_ship_date"] == "2026-11-01"
    assert payload["schedule_status"] == "proposal"


def test_tenant_sources_and_autonomy_never_remove_human_gates(fixture):
    order_id, _ = fixture
    with Session.begin() as s:
        settings = service.set_policy(
            s,
            PolicyWrite(
                level=4, reason="Local workflow experiment; commitments stay protected."
            ),
        )
        assert settings["level"] == 4
        assert settings["customer_sending_supported"] is False
        assert settings["external_writes_enabled"] is False
    result, _ = price_fixture(fixture)
    assert "money" in result["packages"][0]["gates"]
    with identity_scope(Identity(str(uuid4()), "different tenant")), Session() as s:
        with pytest.raises(HTTPException) as error:
            get(s, Order, order_id)
        assert error.value.status_code == 404


def test_api_replays_idempotently_and_rejects_changed_download(fixture, client):
    order_id, source = fixture
    _, price = source(
        {
            "tag": "CB-12",
            "unit_cost": "1200.00",
            "currency": "USD",
            "gross_margin_percent": "25.00",
        },
        kind="erp_price",
    )
    current = client.get(f"/api/orders/{order_id}/lifecycle").json()
    body = {
        "operation": "quote",
        "expected_version": current["version"],
        "lines": [
            {
                "tag": "CB-12",
                "description": "Fictional breaker",
                "quantity": "1",
                "unit_cost": "1200.00",
                "gross_margin_percent": "25.00",
                "source_ids": [price],
            }
        ],
    }
    url = f"/api/orders/{order_id}/lifecycle/prepare"
    first = client.post(
        url, json=body, headers={"Idempotency-Key": "exact-quote-request"}
    )
    assert first.status_code == 200, first.text
    replay = client.post(
        url, json=body, headers={"Idempotency-Key": "exact-quote-request"}
    )
    assert replay.status_code == 200
    assert replay.json() == first.json()
    package = first.json()["packages"][0]
    artifact = next(a for a in package["artifacts"] if a["name"] == "quote.json")
    download = client.get(artifact["url"])
    assert download.status_code == 200
    assert download.json()["total"] == "1600.00"
    with Session() as s:
        order = get(s, Order, order_id)
        path = (
            adapters.root(order.organization_id, order.id)
            / package["id"]
            / artifact["name"]
        )
    path.write_text("changed after preparation")
    assert client.get(artifact["url"]).status_code == 409


def test_real_cad_edits_need_registered_originals_distinct_cited_changes_and_current_hashes(
    fixture, tmp_path
):
    from backend.drafting import DraftRevisionRequest, prepare_revision, sha256_file
    from scripts.build_drafting_demo import make_request, make_sources

    order_id, source = fixture
    drawing, bom, handle = make_sources(tmp_path / "cad-originals")
    cad_doc, _ = source("Native E-10", kind="drawing")
    bom_doc, _ = source("BOM source", kind="bom")
    ids = [
        source(f"Comment {i + 1}: engineer-confirmed draft change.", kind="markup")[1]
        for i in range(4)
    ]
    with Session.begin() as s:
        order = get(s, Order, order_id)
        organization_id = order.organization_id
        get(s, Document, cad_doc).sha256 = sha256_file(drawing)
        get(s, Document, bom_doc).sha256 = sha256_file(bom)
        s.add(
            OrderRecord(
                order_id=order_id,
                data={
                    "comments": [
                        {
                            "id": f"c{i + 1}",
                            "revision": "C",
                            "text": f"Requested change {i + 1}",
                        }
                        for i in range(4)
                    ]
                },
            )
        )
    request = make_request(
        drawing, bom, handle, organization_id=organization_id, order_id=order_id
    )
    value = request.model_dump(mode="json")
    for i, edit in enumerate(value["edits"]):
        edit["evidence_id"] = ids[i]
    value["layout_tasks"][0]["evidence_id"] = ids[3]
    request = DraftRevisionRequest.model_validate(value)
    destination = (
        db.DATA / "drafting" / organization_id / order_id / str(request.bundle_id)
    )
    bundle = prepare_revision(
        request, drawing_path=drawing, bom_path=bom, destination=destination
    )
    plans = [
        {
            "comment_id": f"c{i + 1}",
            "kind": "text_edit",
            "source_ids": [ids[i]],
            "draft_bundle_id": str(bundle.bundle_id),
            "draft_change_id": change["id"],
        }
        for i, change in enumerate(bundle.changes)
    ]
    task = bundle.layout_tasks[0]
    plans.append(
        {
            "comment_id": "c4",
            "kind": "layout_task",
            "source_ids": [ids[3]],
            "draft_bundle_id": str(bundle.bundle_id),
            "layout_task_id": task["id"],
            "layout_location": {"sheet": "E-10", "x": 99999, "y": 99999},
        }
    )
    with pytest.raises(HTTPException, match="CAD entity anchor"):
        prepare(order_id, operation="revision_review", revision="C", comments=plans)
    plans[-1]["layout_location"] = {}
    result = prepare(
        order_id, operation="revision_review", revision="C", comments=plans
    )
    assert result["packages"][0]["payload"]["counts"]["text_edit"] == 3
    assert (
        result["packages"][0]["payload"]["comments"][-1]["layout_location"]["x"]
        == task["anchor_xyz"][0]
    )
    assert (
        len(
            result["packages"][0]["payload"]["comments"][0]["draft_bundle"]["artifacts"]
        )
        >= 4
    )
    (destination / "after.svg").write_text("tampered")
    with Session() as s:
        current = service.view(s, get(s, Order, order_id))
        assert current["packages"][0]["status"] == "stale"


def test_customer_uploaded_json_cannot_establish_costs_or_margin(fixture):
    order_id, source = fixture
    _, fake = source(
        {
            "tag": "CB-12",
            "unit_cost": "1.00",
            "currency": "USD",
            "gross_margin_percent": "0.00",
            "lifecycle_trust": {
                "origin": "backend_local_demo",
                "purposes": ["pricing"],
            },
        },
        kind="purchase_order",
    )
    with pytest.raises(HTTPException, match="trusted structured ERP price"):
        prepare(
            order_id,
            operation="quote",
            lines=[
                {
                    "tag": "CB-12",
                    "description": "Customer's spoofed price",
                    "quantity": "1",
                    "unit_cost": "1.00",
                    "gross_margin_percent": "0.00",
                    "source_ids": [fake],
                }
            ],
        )


def test_general_writer_cannot_approve_engineering_or_money(fixture):
    order_id, _ = fixture
    result, evidence = price_fixture(fixture)
    package = result["packages"][0]
    with Session() as s:
        organization_id = get(s, Order, order_id).organization_id
    with identity_scope(Identity(organization_id, "ordinary-writer", "org:member")):
        with pytest.raises(HTTPException) as denied:
            decision(order_id, package["id"], "money", evidence)
        assert denied.value.status_code == 403


def test_production_admin_needs_named_gate_mapping_and_local_prices_do_not_cross_over(
    fixture,
):
    order_id, _ = fixture
    result, evidence = price_fixture(fixture)
    package = result["packages"][0]
    with Session.begin() as s:
        order = get(s, Order, order_id)
        get(s, Project, order.project_id).synthetic = False
    with pytest.raises(HTTPException) as denied:
        decision(order_id, package["id"], "money", evidence)
    assert denied.value.status_code == 403
    with Session.begin() as s:
        service.set_policy(
            s,
            PolicyWrite(
                level=1,
                reason="Explicitly name the finance approver for this company.",
                gate_approvers={"money": ["Local estimator"]},
            ),
        )
    with pytest.raises(HTTPException, match="evidence changed"):
        decision(order_id, package["id"], "money", evidence)
    with pytest.raises(HTTPException, match="trusted structured ERP price"):
        prepare(
            order_id,
            operation="quote",
            lines=[
                {
                    "tag": "CB-12",
                    "description": "Local price cannot become production evidence",
                    "quantity": "1",
                    "unit_cost": "1200.00",
                    "gross_margin_percent": "25.00",
                    "source_ids": [evidence],
                }
            ],
        )


def test_explicit_named_production_approver_and_server_owned_erp_binding(
    fixture, monkeypatch
):
    from backend.connected_context import providers

    order_id, source = fixture
    document_id, evidence = source(
        {
            "tag": "CB-12",
            "unit_cost": "1200.00",
            "currency": "USD",
            "gross_margin_percent": "25.00",
        },
        kind="erp_price",
    )
    provenance = {
        "key": "authorized-pricebook",
        "kind": "erp",
        "mode": "live",
        "external_id": "pricebook/CB-12",
        "lifecycle": "approved",
    }
    binding = {
        "key": "authorized-pricebook",
        "kind": "erp",
        "mode": "live",
        "commercial_fact_purposes": ["pricing"],
    }
    monkeypatch.setattr(providers, "bindings", lambda number: [binding])
    with Session.begin() as s:
        order = get(s, Order, order_id)
        organization_id = order.organization_id
        get(s, Project, order.project_id).synthetic = False
        document = get(s, Document, document_id)
        document.meta = {
            "connected_source": provenance,
            "connected_identity": digest([provenance, document.sha256]),
            "active": True,
        }
        service.set_policy(
            s,
            PolicyWrite(
                level=1,
                reason="Named finance authority approved by the administrator.",
                gate_approvers={"money": ["named-finance-approver"]},
            ),
        )
    result = prepare(
        order_id,
        operation="quote",
        lines=[
            {
                "tag": "CB-12",
                "description": "Authorized pricebook value",
                "quantity": "1",
                "unit_cost": "1200.00",
                "gross_margin_percent": "25.00",
                "source_ids": [evidence],
            }
        ],
    )
    with identity_scope(
        Identity(organization_id, "named-finance-approver", "org:member")
    ):
        decided = decision(order_id, result["packages"][0]["id"], "money", evidence)
    assert decided["packages"][0]["decisions"][0]["actor"] == "named-finance-approver"
