from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.company_context import service as context
from backend.company_context.api import router
from backend.company_context.models import CompanyContextEvent, CompanyFeedback
from backend.company_context.schemas import ReuseReview, RuleWrite
from backend.identity import Identity, identity_scope
from backend.storage.db import Session
from backend.storage.models import Document, Order, Project, Span, now

ORG_A = "a" * 36
ORG_B = "b" * 36


def identity(org=ORG_A, role="org:admin"):
    return identity_scope(Identity(org, "reviewer-" + org[0], role))


def order(s, category="Switchgear"):
    project = Project(
        title="Fictional switchgear review",
        customer="Fixture customer",
        category=category,
    )
    s.add(project)
    s.flush()
    order = Order(project_id=project.id, number="FIX-" + str(uuid4())[:8])
    s.add(order)
    s.flush()
    doc = Document(
        project_id=project.id,
        name="returned.pdf",
        kind="markup",
        blob="fixture.pdf",
        sha256="a" * 64,
        state="ready",
        meta={"revision_label": "C"},
    )
    s.add(doc)
    s.flush()
    span = Span(
        document_id=doc.id,
        text="Please confirm the cable entry position on sheet E-10.",
        location={"page": 2},
    )
    s.add(span)
    s.flush()
    return order, doc, span


def feedback(s, o, source, **kw):
    values = {
        "request_key": str(uuid4()),
        "generation": {
            "kind": "reply",
            "id": "saved-generation",
            "model": "fixture-model",
            "context_hash": "context-v1",
        },
        "original_text": "Please provide the referenced sheet E-10 and revision.",
        "input_text": "Please confirm the cable entry position on sheet E-10.",
        "evidence_ids": [source.id],
        "outcome": "edited",
        "human_response": "Please provide sheet E-10 and its revision so engineering can confirm the cable entry position.",
        "reason": "PM checked the missing drawing reference.",
        "topics": ["drawing-reference", "cable-entry"],
    }
    values.update(kw)
    return context.capture_generation_feedback(s, o, **values)


def promote(s, f, **kw):
    return context.approve_example(
        s,
        f["id"],
        ReuseReview(reason="Admin reviewed the source and wording for reuse.", **kw),
    )


def query(s, o, **kw):
    return context.assemble_context(
        s,
        o,
        "Prepare a cable entry clarification for E-10",
        topics=["cable-entry"],
        **kw,
    )


def test_feedback_is_immutable_idempotent_and_requires_explicit_promotion():
    with identity(), Session.begin() as s:
        o, _d, source = order(s)
        f = feedback(s, o, source, request_key="one-review")
        repeated = feedback(s, o, source, request_key="one-review")
        assert repeated == f
        assert f["original_text"] != f["human_response"]
        assert f["generation_hash"] and f["evidence_hash"]
        assert not query(s, o)["examples"]
        with pytest.raises(HTTPException) as error:
            feedback(
                s,
                o,
                source,
                request_key="one-review",
                reason="Different reason from a second review.",
            )
        assert error.value.status_code == 409
        promoted = promote(s, f)
        assert promoted["reuse_status"] == "approved"
        result = query(s, o)
        assert result["examples"][0]["reviewed_response"] == f["human_response"]
        assert "not current evidence" in result["guardrails"]
        assert len(list(s.scalars(select(CompanyFeedback)))) == 1


def test_tenant_and_order_isolation_including_cross_tenant_orm_objects():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        f = feedback(s, o, source)
        promote(s, f)
        other, _, _ = order(s)
        assert not query(s, other)["examples"]
    with identity(ORG_B), Session.begin() as s:
        their_order, _, _their_source = order(s)
        assert not query(s, their_order)["examples"]
        assert not context.evaluation_snapshot(s, include_content=True)["cases"]
        for fn in (
            lambda: context.approve_example(
                s,
                f["id"],
                ReuseReview(reason="This cannot authorize another tenant's row."),
            ),
            lambda: query(s, o),
            lambda: feedback(s, their_order, source),
        ):
            with pytest.raises(HTTPException) as error:
                fn()
            assert error.value.status_code == 404


def test_company_reuse_requires_matching_category_and_relevant_topic_or_terms():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        promote(s, feedback(s, o, source), scope="company")
        other, _, _ = order(s)
        different, _, _ = order(s, category="Transformers")
        assert query(s, other)["examples"]
        assert not query(s, different)["examples"]
        assert not context.assemble_context(
            s, other, "What is the paint finish?", ["paint-finish"]
        )["examples"]
        assert not query(s, other, exclude_order_ids=[o.id])["examples"]
        # No positive example for generic words shared by every engineering comment.
        assert not context.assemble_context(
            s, other, "Please review this drawing comment"
        )["examples"]


def test_revoke_and_expiration_remove_examples_immediately_without_erasing_review():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        f = feedback(s, o, source)
        promote(s, f, expires_at=now() + timedelta(days=10))
        before = query(s, o)
        context.revoke_example(
            s, f["id"], "The company has changed its clarification wording."
        )
        after = query(s, o)
        assert before["context_hash"] != after["context_hash"]
        assert not after["examples"]
        assert not context.evaluation_snapshot(s)["cases"]
        stored = s.get(CompanyFeedback, f["id"])
        assert stored.human_response == f["human_response"]
        fresh = feedback(s, o, source)
        promote(s, fresh, expires_at=now() + timedelta(days=1))
        s.get(CompanyFeedback, fresh["id"]).expires_at = now() - timedelta(seconds=1)
        s.flush()
        assert not query(s, o)["examples"]


@pytest.mark.parametrize("change", ["source_text", "file_hash", "new_revision"])
def test_stale_evidence_never_reused_or_exported(change):
    with identity(), Session.begin() as s:
        o, doc, source = order(s)
        f = feedback(s, o, source)
        promote(s, f)
        assert query(s, o)["examples"]
        if change == "source_text":
            source.text = "The customer now specifies another cable entry."
        elif change == "file_hash":
            doc.sha256 = "b" * 64
        else:
            s.add(
                Document(
                    project_id=o.project_id,
                    name="RevD.pdf",
                    kind="markup",
                    blob="D.pdf",
                    sha256="c" * 64,
                    state="ready",
                    meta={"revision_label": "D"},
                )
            )
        s.flush()
        assert not query(s, o)["examples"]
        assert not context.evaluation_snapshot(s, include_content=True)["cases"]


def test_rule_versions_need_admin_approval_and_old_rules_do_not_silently_return():
    with identity(), Session.begin() as s:
        o, _, _ = order(s)
        body = {
            "key": "clarification-tone",
            "title": "Clarification wording",
            "text": "Request the exact drawing number and revision, without promising engineering approval.",
            "topics": ["cable-entry"],
            "provenance_note": "PM lead supplied the approved communication practice.",
        }
        first = context.create_rule(s, RuleWrite(**body))
        assert not query(s, o)["rules"]
        context.approve_rule(
            s, first["id"], "PM lead approved this wording for the pilot."
        )
        assert query(s, o)["rules"][0]["version"] == 1
        second = context.create_rule(
            s,
            RuleWrite(
                **{
                    **body,
                    "text": "Ask for the sheet, revision and author. Do not make a commitment.",
                },
                expected_version=1,
            ),
        )
        assert query(s, o)["rules"][0]["version"] == 1
        context.approve_rule(
            s, second["id"], "Admin verified the updated coordination instructions."
        )
        assert [r["version"] for r in query(s, o)["rules"]] == [2]
        context.revoke_rule(
            s, second["id"], "Instructions withdrawn pending company review."
        )
        assert not query(s, o)["rules"]
        with pytest.raises(HTTPException) as error:
            context.create_rule(s, RuleWrite(**body, expected_version=1))
        assert error.value.status_code == 409
    with identity(role="org:member"), Session.begin() as s:
        with pytest.raises(HTTPException) as error:
            context.create_rule(s, RuleWrite(**body))
        assert error.value.status_code == 403


def test_readonly_member_cannot_record_feedback_or_promote_rules():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        f = feedback(s, o, source)
    with identity(role="org:member"), Session.begin() as s:
        assert feedback(s, o, source)["reuse_status"] == "unapproved"
        with pytest.raises(HTTPException) as error:
            promote(s, f)
        assert error.value.status_code == 403
        with pytest.raises(HTTPException) as error:
            context.evaluation_snapshot(s)
        assert error.value.status_code == 403
    with identity(role="org:viewer"), Session.begin() as s:
        with pytest.raises(HTTPException) as error:
            feedback(s, o, source)
        assert error.value.status_code == 403


def test_rejected_output_only_enters_evaluation_when_explicitly_approved():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        f = feedback(s, o, source, outcome="rejected", human_response="")
        with pytest.raises(HTTPException):
            promote(s, f)
        assert not context.evaluation_snapshot(s)["cases"]
        promote(s, f, purpose="evaluation")
        assert not query(s, o)["examples"]
        public_fields = context.evaluation_snapshot(s)
        assert "generated_response" not in public_fields["cases"][0]
        detailed = context.evaluation_snapshot(s, include_content=True)
        assert detailed["cases"][0]["label_type"] == "rejected_output"
        assert detailed["evaluation_status"] == "dataset_only_not_evaluated"
        assert detailed["cases"][0]["generated_response"] == f["original_text"]
        assert (
            detailed["snapshot_hash"]
            == context.evaluation_snapshot(s, include_content=True)["snapshot_hash"]
        )


def test_eval_splits_group_every_response_from_an_order_and_audit_is_recorded():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        for _ in range(3):
            promote(s, feedback(s, o, source))
        snapshot = context.evaluation_snapshot(s)
        assert len({c["split"] for c in snapshot["cases"]}) == 1
        actions = [row.action for row in s.scalars(select(CompanyContextEvent))]
        assert actions.count("feedback_recorded") == 3
        assert actions.count("reuse_approved") == 3


def test_api_routes_validate_shape_and_use_current_identity_without_tenant_parameter():
    # Small router test avoids coupling this unit to main.py registration by another task.
    app = FastAPI()
    app.include_router(router)
    with identity(), TestClient(app) as client:
        res = client.post(
            "/api/company-context/rules",
            json={
                "key": "style",
                "title": "Response tone",
                "text": "Use brief paragraphs and specific questions.",
                "provenance_note": "Admin supplied the company tone guide.",
            },
        )
        assert res.status_code == 201, res.text
        row = res.json()
        assert (
            client.post(
                f"/api/company-context/rules/{row['id']}/approve",
                json={"reason": "Admin checked this company rule."},
            ).status_code
            == 200
        )
        assert (
            client.get("/api/company-context").json()["rules"][0]["status"]
            == "approved"
        )
        assert (
            client.post(
                "/api/company-context/retrieve",
                json={
                    "order_id": "invented",
                    "query": "paint finish",
                    "organization_id": ORG_B,
                },
            ).status_code
            == 422
        )
    with identity(ORG_B), TestClient(app) as client:
        assert client.get("/api/company-context").json()["rules"] == []


def test_source_backed_company_rule_has_provenance_and_can_be_withheld_from_eval():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        target, _, _ = order(s)
        row = context.create_rule(
            s,
            RuleWrite(
                key="drawing-request",
                title="Drawing request wording",
                text="Ask for the drawing number and explicit revision before confirming a reference.",
                source_order_id=o.id,
                source_ids=[source.id],
                topics=["cable-entry"],
                provenance_note="Admin verified this coordination instruction against the original source.",
            ),
        )
        context.approve_rule(
            s, row["id"], "Admin approves this source-backed template company-wide."
        )
        assert query(s, target)["rules"][0]["provenance"]["source_order_id"] == o.id
        assert not query(s, target, exclude_order_ids=[o.id])["rules"]
        source.text = "A corrected source replaces the earlier reference."
        s.flush()
        assert not query(s, target)["rules"]


def test_review_metrics_are_tenant_scoped_observations_not_accuracy():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        approved = feedback(s, o, source, outcome="accepted", human_response="")
        promote(s, approved)
        feedback(s, o, source)
        feedback(s, o, source, outcome="rejected", human_response="")
        metrics = context.feedback_metrics(s)
        assert [metrics[k] for k in ("accepted", "edited", "rejected")] == [1, 1, 1]
        assert metrics["reviewed_responses"] == 3
        assert metrics["approved_current_examples"] == 1
        assert metrics["orders_with_feedback"] == 1
        assert metrics["measurement"] == "observed_review_outcomes_not_model_accuracy"
    with identity(ORG_B), Session.begin() as s:
        assert context.feedback_metrics(s)["reviewed_responses"] == 0


def test_declared_old_generation_evidence_is_not_silently_recertified():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        with pytest.raises(HTTPException) as error:
            feedback(
                s, o, source, generation={"order_evidence_version": "old-evidence"}
            )
        assert error.value.status_code == 409
