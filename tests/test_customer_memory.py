"""Historical preferences follow explicit customer identity, never display names."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_company_context import ORG_B, identity, order, query

from backend.company_context import customers
from backend.company_context import service as company
from backend.company_context.models import CompanyContextEvent, CustomerMemory
from backend.company_context.schemas import (
    CustomerBindingWrite,
    CustomerNoteReview,
    CustomerNoteWrite,
    CustomerWrite,
)
from backend.storage.db import Session
from backend.storage.models import Document, OrderRecord, Project, now


def customer(s, key="north", name="North Electrical"):
    return customers.create_customer(s, CustomerWrite(key=key, name=name))


def link(s, o, c, version=0):
    return customers.bind_order(
        s,
        o.id,
        CustomerBindingWrite(
            customer_id=c["id"] if c else None,
            expected_version=version,
            reason="PM confirmed the purchasing customer from this order.",
        ),
    )


def note(s, c, o, source, **kw):
    args = {
        "key": "cable-entry",
        "title": "Cable entry preference",
        "text": "This customer requested bottom cable entry on the source order.",
        "kind": "preference",
        "topics": ["cable-entry"],
        "category": "Switchgear",
        "applicability_note": "Check site constraints on each new order; this source only confirms the previous project.",
        "source_order_id": o.id,
        "source_ids": [source.id],
        "provenance_note": "PM checked the marked-up source drawing.",
    }
    args.update(kw)
    return customers.create_note(s, c["id"], CustomerNoteWrite(**args))


def review(s, n, approve=True, version=None):
    return customers.review_note(
        s,
        n["id"],
        CustomerNoteReview(
            expected_version=version or n["version"],
            reason="Reviewed original source and historical applicability.",
        ),
        approve=approve,
    )


def memory(s, o, **kw):
    return query(s, o, **kw)["customer_memory"]


def reviewed_comment(s, o, source, **kw):
    comment = {
        "id": str(uuid4()),
        "number": "7",
        "text": "Please use bottom cable entry on this panel.",
        "original_text": "Please use bottom cable entry on this panel.",
        "source_ids": [source.id],
        "document_id": source.document_id,
        "revision": "C",
        "reviewed": True,
        "response": "Engineering recorded bottom cable entry for this project.",
        "responder": "Reviewed by PM",
        "status": "responded",
    }
    comment.update(kw)
    record = OrderRecord(order_id=o.id, data={"comments": [comment], "approvals": []})
    s.add(record)
    s.flush()
    return record, comment


def test_no_customer_memory_until_explicit_binding_even_identical_names():
    with identity(), Session.begin() as s:
        c = customer(s)
        duplicate_name = customer(s, "north-other", c["name"])
        prior, _, source = order(s)
        target, _, _ = order(s)
        s.get(Project, target.project_id).customer = c["name"]
        link(s, prior, c)
        n = note(s, c, prior, source)
        review(s, n)
        assert memory(s, target)["customer"] is None
        link(s, target, duplicate_name)
        assert not memory(s, target)["notes"]
        link(s, target, c, version=1)
        result = memory(s, target)
        assert result["customer"]["id"] == c["id"]
        assert result["notes"][0]["id"] == n["id"]
        assert result["notes"][0]["applicability"] == "historical_reference"
        assert "not a current project specification" in result["guardrails"]


def test_candidate_is_excluded_until_explicit_approval_and_revoke_changes_hash():
    with identity(), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        link(s, o, c)
        n = note(s, c, o, source)
        assert n["status"] == "candidate" and not memory(s, o)["notes"]
        before = query(s, o)["context_hash"]
        approved = review(s, n)
        assert approved["reviewed_by"]
        assert query(s, o)["context_hash"] != before
        review(s, approved, approve=False)
        assert not memory(s, o)["notes"]
        assert query(s, o)["context_hash"] == before


def test_every_customer_api_and_source_rejects_other_tenant_ids():
    with identity(), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        link(s, o, c)
        n = note(s, c, o, source)
        review(s, n)
    with identity(ORG_B), Session.begin() as s:
        own = customer(s)  # Same customer code is valid in a different tenant.
        other, _, other_source = order(s)
        link(s, other, own)
        assert not memory(s, other)["notes"]
        assert [row["id"] for row in customers.list_customers(s)["customers"]] == [
            own["id"]
        ]
        for action in (
            lambda: customers.customer_detail(s, c["id"]),
            lambda: customers.binding_view(s, o.id),
            lambda: link(s, other, c, 1),
            lambda: note(s, own, o, source),
            lambda: note(s, own, other, source),
            lambda: review(s, n),
            lambda: customers.refresh_customer_candidates(s, c["id"]),
            lambda: memory(s, o),
        ):
            with pytest.raises(HTTPException) as error:
                action()
            assert error.value.status_code == 404
        with pytest.raises(HTTPException):
            note(s, own, other, other_source, conflicts_with=[n["id"]])


def test_member_can_propose_but_only_admin_approves_and_viewer_cannot_write():
    with identity(role="org:member"), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        link(s, o, c)
        n = note(s, c, o, source)
        with pytest.raises(HTTPException) as error:
            review(s, n)
        assert error.value.status_code == 403
    with identity(role="org:viewer"), Session.begin() as s:
        assert customers.customer_detail(s, c["id"])["notes"]
        for action in (
            lambda: customer(s, "new"),
            lambda: link(s, o, c, 1),
            lambda: note(s, c, o, source, key="another"),
            lambda: customers.refresh_customer_candidates(s, c["id"]),
        ):
            with pytest.raises(HTTPException) as error:
                action()
            assert error.value.status_code == 403


@pytest.mark.parametrize(
    "change",
    ["source_text", "source_location", "document_hash", "source_customer", "expire"],
)
def test_changed_evidence_identity_or_expiry_excludes_approved_memory(change):
    with identity(), Session.begin() as s:
        c = customer(s)
        o, doc, source = order(s)
        target, _, _ = order(s)
        link(s, o, c)
        link(s, target, c)
        n = note(s, c, o, source)
        review(s, n)
        before = query(s, target)["context_hash"]
        if change == "source_text":
            source.text = "A correction to the source content."
        elif change == "source_location":
            source.location = {"page": 10}
        elif change == "document_hash":
            doc.sha256 = "b" * 64
        elif change == "source_customer":
            link(s, o, customer(s, "another"), version=1)
        else:
            s.get(CustomerMemory, n["id"]).expires_at = now() - timedelta(seconds=1)
        s.flush()
        assert not memory(s, target)["notes"]
        assert query(s, target)["context_hash"] != before
        assert not customers.customer_detail(s, c["id"])["notes"][0]["evidence_current"]


def test_later_revision_does_not_turn_verified_history_into_current_requirement():
    with identity(), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        link(s, o, c)
        n = note(s, c, o, source)
        review(s, n)
        s.add(
            Document(
                project_id=o.project_id,
                name="RevD.pdf",
                kind="markup",
                blob="D.pdf",
                sha256="d" * 64,
                state="ready",
                meta={"revision_label": "D"},
            )
        )
        s.flush()
        result = memory(s, o)["notes"][0]
        assert result["id"] == n["id"]
        assert result["provenance"]["evidence"][0]["revision"] == "C"
        assert result["applicability"] == "historical_reference"
        assert "current order" in result["use"]


def test_category_and_relevance_restrict_retrieval():
    with identity(), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        other, _, _ = order(s, category="Transformers")
        link(s, o, c)
        link(s, other, c)
        review(s, note(s, c, o, source))
        assert memory(s, o)["notes"]
        assert not memory(s, other)["notes"]
        assert not company.assemble_context(
            s, o, "Schedule a project meeting", ["scheduling"]
        )["customer_memory"]["notes"]


def test_declared_conflicts_show_both_sides_without_choosing_a_specification():
    with identity(), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        link(s, o, c)
        first = note(s, c, o, source)
        second = note(
            s,
            c,
            o,
            source,
            key="service-access",
            title="Alternative arrangement",
            text="This earlier request had a different arrangement.",
            topics=["service-access"],
            conflicts_with=[first["id"]],
        )
        review(s, first)
        review(s, second)
        result = memory(s, o)
        assert {n["id"] for n in result["notes"]} == {first["id"], second["id"]}
        assert result["conflicts"][0]["status"] == "requires_human_resolution"
        assert result["conflicts"][0]["other_available"]


def test_customer_notes_and_approval_history_obey_holdout_order_exclusions():
    with identity(), Session.begin() as s:
        c = customer(s)
        old, _, source = order(s)
        target, _, _ = order(s)
        for o in (old, target):
            link(s, o, c)
            s.add(
                OrderRecord(
                    order_id=o.id,
                    data={
                        "comments": [],
                        "approvals": [
                            {
                                "id": "approval-" + o.id,
                                "label": "Historical approval",
                                "actor": "PM",
                                "at": "2026-01-01",
                                "version": 3,
                                "snapshot": {"private": "not copied"},
                            }
                        ],
                    },
                )
            )
        n = note(s, c, old, source)
        review(s, n)
        result = memory(s, target)
        assert result["notes"][0]["source_approvals"][0]["id"] == "approval-" + old.id
        assert "snapshot" not in result["notes"][0]["source_approvals"][0]
        held_out = memory(s, target, exclude_order_ids=[old.id, target.id])
        assert not held_out["notes"] and not held_out["current_order_approvals"]


def test_source_order_must_match_customer_and_sources_must_match_order():
    with identity(), Session.begin() as s:
        c, other_c = customer(s), customer(s, "another")
        o, _, source = order(s)
        other, _, other_source = order(s)
        with pytest.raises(HTTPException):
            note(s, c, o, source)
        link(s, o, c)
        link(s, other, other_c)
        with pytest.raises(HTTPException):
            note(s, c, other, other_source)
        with pytest.raises(HTTPException):
            note(s, c, o, other_source)


def test_optimistic_versions_supersede_only_after_human_approval():
    with identity(), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        link(s, o, c)
        first = note(s, c, o, source)
        review(s, first)
        second = note(
            s,
            c,
            o,
            source,
            expected_version=1,
            text="Clarify the cable entry for each site.",
        )
        assert memory(s, o)["notes"][0]["id"] == first["id"]
        for action in (
            lambda: note(s, c, o, source),
            lambda: link(s, o, c),
            lambda: review(s, first),
        ):
            with pytest.raises(HTTPException) as error:
                action()
            assert error.value.status_code == 409
        review(s, second)
        assert memory(s, o)["notes"][0]["id"] == second["id"]
        review(s, second, approve=False)
        assert not memory(s, o)["notes"]  # No implicit rollback to an earlier approval.
        assert s.scalar(
            select(CompanyContextEvent).where(
                CompanyContextEvent.action == "superseded"
            )
        )


def test_background_candidates_quote_reviewed_work_and_replay_without_duplicates():
    with identity(), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        record, comment = reviewed_comment(s, o, source)
        link(s, o, c)  # Explicit linking prepares the historical candidate immediately.
        notes = customers.customer_detail(s, c["id"])["notes"]
        assert len(notes) == 1
        assert (
            comment["text"] in notes[0]["text"]
            and comment["response"] in notes[0]["text"]
        )
        assert notes[0]["provenance"]["comment_id"] == comment["id"]
        assert notes[0]["status"] == "candidate" and not memory(s, o)["notes"]
        replay = customers.refresh_order_customer_candidates(s, o)
        assert replay["created"] == 0 and replay["unchanged"] == 1
        review(s, notes[0])
        assert memory(s, o)["notes"]
        data = deepcopy(record.data)
        data["comments"][0]["response"] = (
            "PM corrected the response: top cable entry is required here."
        )
        record.data = data
        s.flush()
        assert not memory(s, o)["notes"]
        changed = customers.refresh_order_customer_candidates(s, o)
        assert changed["created"] == 1
        assert changed["notes"][0]["version"] == 2
        assert changed["notes"][0]["status"] == "candidate"
        assert not memory(s, o)["notes"]


def test_revoked_background_candidate_is_not_resurrected():
    with identity(), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        record, _ = reviewed_comment(s, o, source)
        link(s, o, c)
        n = customers.customer_detail(s, c["id"])["notes"][0]
        review(s, n, approve=False)
        data = deepcopy(record.data)
        data["comments"][0]["response"] = (
            "Another human edit to the cable entry response."
        )
        record.data = data
        s.flush()
        assert customers.refresh_order_customer_candidates(s, o)["created"] == 0
        assert len(customers.customer_detail(s, c["id"])["notes"]) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewed", False),
        ("response", ""),
        ("responder", ""),
        ("source_ids", []),
        ("source_ids", ["not-a-source"]),
    ],
)
def test_unreviewed_or_unproven_comments_never_become_memory_candidates(field, value):
    with identity(), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        reviewed_comment(s, o, source, **{field: value})
        link(s, o, c)
        assert not customers.customer_detail(s, c["id"])["notes"]


def test_customer_routes_expose_real_status_and_require_versioned_reviews(client):
    created = client.post(
        "/api/company-context/customers",
        json={"key": "test-account", "name": "Test Account"},
    )
    assert created.status_code == 201, created.text
    id = created.json()["id"]
    assert (
        client.get("/api/company-context/customers").json()["customers"][0]["id"] == id
    )
    assert client.get(f"/api/company-context/customers/{id}").json()["notes"] == []
    assert (
        client.post(f"/api/company-context/customers/{id}/notes/refresh").json()[
            "created"
        ]
        == 0
    )
    assert client.get(f"/api/company-context/customers/{uuid4()}").status_code == 404
    assert (
        client.post(
            "/api/company-context/customers",
            json={"key": "test-account", "name": "Same code"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/company-context/customer-notes/{uuid4()}/approve",
            json={"reason": "Missing version is rejected."},
        ).status_code
        == 422
    )


def test_refresh_is_bounded_and_reports_partial_work_without_dropping_history():
    with identity(), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        link(s, o, c)
        record, first = reviewed_comment(s, o, source)
        data = deepcopy(record.data)
        second = {**first, "id": str(uuid4()), "number": "8"}
        data["comments"].append(second)
        record.data = data
        s.flush()
        result = customers.refresh_order_customer_candidates(s, o, limit=1)
        assert result["created"] == 1 and result["partial"]
        result = customers.refresh_order_customer_candidates(s, o, limit=1)
        assert result["created"] == 1 and result["unchanged"] == 1
        assert len(customers.customer_detail(s, c["id"])["notes"]) == 2


def test_holdout_excludes_notes_derived_from_conflicts_with_held_out_history():
    with identity(), Session.begin() as s:
        c = customer(s)
        old, _, source = order(s)
        target, _, target_source = order(s)
        link(s, old, c)
        link(s, target, c)
        historical = note(s, c, old, source)
        comparison = note(
            s,
            c,
            target,
            target_source,
            key="comparison",
            conflicts_with=[historical["id"]],
        )
        review(s, historical)
        review(s, comparison)
        assert len(memory(s, target)["notes"]) == 2
        result = memory(s, target, exclude_order_ids=[old.id])
        assert not result["notes"] and not result["conflicts"]
