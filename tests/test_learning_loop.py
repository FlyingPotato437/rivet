from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_company_context import ORG_B, feedback, identity, order, promote, query

from backend.company_context.models import CustomerProfile, OrderCustomerBinding
from backend.improvement.learning import (
    correction,
    overview,
    snapshot_summary,
    training_plan,
)
from backend.improvement.replay import prepare_cases
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session
from backend.storage.models import now


def saved_reply(s, o, source, company=None):
    draft = ReplyDraft(
        order_id=o.id,
        request_key=str(uuid4()),
        request_hash="r" * 64,
        evidence_hash="e" * 64,
        original_body="Please provide sheet E-10 and its revision.",
        context={
            "instruction": "Confirm the cable entry on E-10.",
            "current_order": {"sources": [{"id": source.id, "text": source.text}]},
            "company": company or {},
        },
    )
    s.add(draft)
    s.flush()
    return draft


def link(s, customer, o):
    s.add(
        OrderCustomerBinding(
            order_id=o.id,
            customer_id=customer.id,
            confirmed_by="PM",
            confirmed_at=now(),
            reason="Confirmed fictional customer for this order.",
        )
    )
    s.flush()


def test_literal_correction_preserves_insertions_and_removals_without_semantic_claims():
    result = correction("The drawing is released.", "The drawing is not released.")
    assert result["changes"] == [{"before": "", "after": "not"}]
    assert result["changed"]
    assert result["measurement"] == "wording_difference_not_correctness"
    assert not correction("Please confirm E-10.", "Please  confirm\nE-10.")["changed"]


def test_only_approved_fresh_correction_enters_live_generation_context():
    with identity(), Session.begin() as s:
        o, document, source = order(s)
        f = feedback(s, o, source)
        assert not query(s, o)["examples"]
        promote(s, f)
        chosen = query(s, o)["examples"][0]
        assert chosen["correction"]["changed"]
        assert chosen["correction"]["review_reason"] == f["reason"]
        document.sha256 = "b" * 64
        s.flush()
        assert not query(s, o)["examples"]
        report = overview(s)
        assert report["counts"]["reusable_examples"] == 0
        assert not report["episodes"][0]["evidence_current"]


def test_ledger_counts_actual_later_generation_inputs_not_just_approval():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        f = feedback(s, o, source)
        promote(s, f)
        assert overview(s)["counts"]["replies_with_memory"] == 0
        reply = saved_reply(s, o, source, query(s, o))
        reply.created_at = now() + timedelta(seconds=1)
        s.flush()
        report = overview(s)
        assert report["counts"]["replies_with_memory"] == 1
        assert report["episodes"][0]["selected_in_replies"] == 1
        assert report["recent_uses"][0]["selected"]["examples"] == 1
        assert report["policy"]["model_weights_updated"] is False
        assert report["evaluation"]["status"] == "not_measured"
        assert snapshot_summary(reply.context)["examples"][0]["corrected"]
    with identity(ORG_B), Session.begin() as s:
        assert not overview(s)["episodes"]
        assert not overview(s)["recent_uses"]
        with pytest.raises(HTTPException):
            overview(s, order_id=o.id)


def test_org_ledger_requires_admin_but_member_can_read_accessible_order():
    with identity(), Session.begin() as s:
        o, _, _ = order(s)
    with identity(role="org:member"), Session.begin() as s:
        with pytest.raises(HTTPException) as error:
            overview(s)
        assert error.value.status_code == 403
        assert overview(s, order_id=o.id)["counts"]["reviewed"] == 0


def test_null_connected_snapshot_and_empty_memory_are_supported():
    result = snapshot_summary({"current_order": {"connected_context": None}})
    assert not result["systems"]
    assert not result["examples"]


def test_fact_changes_are_recorded_but_cannot_become_reusable_examples():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        f = feedback(
            s,
            o,
            source,
            original_text="The specification identifies TU-3000-DEMO.",
            human_response="The specification identifies TU-2500-DEMO.",
        )
        promote(s, f)
        assert not query(s, o)["examples"]
        report = overview(s)
        assert report["episodes"][0]["requires_factual_review"]
        assert not report["episodes"][0]["available_for_reuse"]
        assert report["counts"]["corrected"] == 1
        assert report["counts"]["factual_edits_withheld"] == 1
    assert correction("Release is approved.", "Release is not approved.")[
        "requires_factual_review"
    ]
    assert not correction("1. Please confirm E-10.", "Please confirm E-10.")[
        "requires_factual_review"
    ]


def test_training_plan_is_metadata_only_and_not_training_consent():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        draft = saved_reply(s, o, source)
        f = feedback(s, o, source, generation={"reply_id": draft.id})
        promote(s, f)
        plan = training_plan(s, [f["id"], f["id"]])
        assert len(plan["cases"]) == 1
        assert not plan["contains_training_text"]
        assert not plan["weights_trained"]
        assert plan["status"] == "consent_and_expert_labels_required"
        assert f["human_response"] not in str(plan)
        assert draft.original_body not in str(plan)
        assert plan["plan_hash"] == training_plan(s, [f["id"]])["plan_hash"]
    with identity(ORG_B), Session.begin() as s, pytest.raises(HTTPException):
        training_plan(s, [f["id"]])


def test_training_plan_rejects_stale_unapproved_and_missing_frozen_inputs():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        f = feedback(s, o, source)
        assert not training_plan(s, [f["id"]])["cases"]
        promote(s, f)
        assert "No frozen" in training_plan(s, [f["id"]])["excluded"][0]["reason"]


def test_customer_group_split_and_duplicate_inputs_fail_closed():
    with identity(), Session.begin() as s:
        customer = CustomerProfile(
            key="customer-one", name="Fictional customer", created_by="PM"
        )
        s.add(customer)
        s.flush()
        ids = []
        for _ in range(2):
            o, _, source = order(s)
            link(s, customer, o)
            draft = saved_reply(s, o, source)
            f = feedback(s, o, source, generation={"reply_id": draft.id})
            promote(s, f)
            ids.append(f["id"])
        plan = training_plan(s, ids)
        assert len(plan["cases"]) == 2
        assert len({c["group"] for c in plan["cases"]}) == 1
        assert len({c["split"] for c in plan["cases"]}) == 1
        third, _, source = order(s)
        draft = saved_reply(s, third, source)
        f = feedback(s, third, source, generation={"reply_id": draft.id})
        promote(s, f)
        assert not training_plan(s, ids + [f["id"]])["cases"]


def test_replay_excludes_other_jobs_of_evaluation_customer():
    from test_improvement_replay import evaluation_case

    with identity(), Session.begin() as s:
        customer = CustomerProfile(
            key="evaluation-customer", name="Evaluation customer", created_by="PM"
        )
        s.add(customer)
        s.flush()
        heldout, _, _, _ = evaluation_case(s)
        link(s, customer, heldout)
        same_customer, _, source = order(s)
        link(s, customer, same_customer)
        promote(s, feedback(s, same_customer, source), scope="company")
        plan = prepare_cases(s)
        assert same_customer.id in plan["withheld_order_ids"]
        assert customer.id in plan["withheld_customer_ids"]
        assert not plan["cases"][0]["candidate"]["company"]["examples"]


def test_learning_api_does_not_export_private_training_text(client):
    response = client.get("/api/company-context/learning")
    assert response.status_code == 200
    assert response.json()["policy"]["cross_company_training"] is False
    assert (
        client.post(
            "/api/company-context/learning/training-plan", json={"feedback_ids": []}
        ).status_code
        == 422
    )
