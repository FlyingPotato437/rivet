"""Authenticated company context management. No public or model-writable routes."""

from fastapi import APIRouter, Header
from pydantic import Field

from backend.domain.service import scoped
from backend.improvement import models as improvement_models  # noqa: F401
from backend.orders.schemas import StrictModel
from backend.orders.service import lock_order
from backend.storage.db import Session

from . import brief, customers
from . import service as svc
from .models import CompanyContextEvent, CompanyFeedback, CompanyRule
from .schemas import (
    ContextQuery,
    CustomerBindingWrite,
    CustomerDocumentCheck,
    CustomerHistorySearch,
    CustomerNoteReview,
    CustomerNoteWrite,
    CustomerWrite,
    FeedbackWrite,
    ReuseReview,
    Review,
    RuleWrite,
)

router = APIRouter(prefix="/api/company-context", tags=["Company context"])


class TrainingPlanWrite(StrictModel):
    feedback_ids: list[str] = Field(min_length=1, max_length=200)


class LearningPolicyWrite(StrictModel):
    private_training: bool = True
    share_features: bool = False
    reason: str = Field(min_length=8, max_length=1000)


@router.get("/learning/model")
def learning_model():
    from backend.improvement.adaptive import status

    with Session() as s:
        return status(s)


@router.put("/learning/policy")
def learning_policy(body: LearningPolicyWrite):
    from backend.improvement.adaptive import update_policy

    with Session.begin() as s:
        return update_policy(s, body)


@router.post("/learning/train")
def train_learning_model():
    from backend.improvement.adaptive import train_private

    svc.require_admin()
    with Session.begin() as s:
        return train_private(s)


@router.post("/learning/models/{model_id}/rollback")
def rollback_learning_model(model_id: str):
    from backend.improvement.adaptive import rollback

    with Session.begin() as s:
        return rollback(s, model_id)


@router.get("/learning")
def learning_overview():
    from backend.improvement.learning import overview

    with Session() as s:
        return overview(s)


@router.get("/orders/{order_id}/learning")
def order_learning(order_id: str):
    from backend.improvement.learning import overview

    with Session() as s:
        return overview(s, order_id=order_id)


@router.post("/learning/training-plan")
def learning_training_plan(body: TrainingPlanWrite):
    from backend.improvement.learning import training_plan

    with Session() as s:
        return training_plan(s, body.feedback_ids)


@router.get("/orders/{order_id}/customer-brief")
def customer_brief(order_id: str):
    with Session.begin() as s:
        return brief.order_brief(s, order_id)


@router.post("/orders/{order_id}/customer-check")
def customer_check(order_id: str, body: CustomerDocumentCheck):
    with Session.begin() as s:
        return brief.check_documents(s, order_id, body)


@router.post("/customers/{customer_id}/search")
def customer_search(customer_id: str, body: CustomerHistorySearch):
    with Session.begin() as s:
        return brief.search_history(s, customer_id, body.query)


@router.get("/customers")
def customer_list():
    with Session.begin() as s:
        return customers.list_customers(s)


@router.post("/customers", status_code=201)
def customer_create(body: CustomerWrite):
    with Session.begin() as s:
        return customers.create_customer(s, body)


@router.get("/customers/{customer_id}")
def customer_detail(customer_id: str):
    with Session.begin() as s:
        return customers.customer_detail(s, customer_id)


@router.get("/orders/{order_id}/customer")
def customer_binding(order_id: str):
    with Session.begin() as s:
        return customers.binding_view(s, order_id)


@router.put("/orders/{order_id}/customer")
def customer_link(order_id: str, body: CustomerBindingWrite):
    with Session.begin() as s:
        return customers.bind_order(s, order_id, body)


@router.post("/customers/{customer_id}/notes", status_code=201)
def customer_note(customer_id: str, body: CustomerNoteWrite):
    with Session.begin() as s:
        return customers.create_note(s, customer_id, body)


@router.post("/customers/{customer_id}/notes/refresh")
def customer_refresh(customer_id: str):
    with Session.begin() as s:
        return customers.refresh_customer_candidates(s, customer_id)


@router.post("/customer-notes/{note_id}/approve")
def customer_note_approve(note_id: str, body: CustomerNoteReview):
    with Session.begin() as s:
        return customers.review_note(s, note_id, body, approve=True)


@router.post("/customer-notes/{note_id}/revoke")
def customer_note_revoke(note_id: str, body: CustomerNoteReview):
    with Session.begin() as s:
        return customers.review_note(s, note_id, body, approve=False)


@router.get("")
def overview():
    svc.require_admin()
    with Session.begin() as s:
        rules = list(
            s.scalars(
                scoped(CompanyRule).order_by(CompanyRule.created_at.desc()).limit(200)
            )
        )
        feedback = list(
            s.scalars(
                scoped(CompanyFeedback)
                .order_by(CompanyFeedback.created_at.desc())
                .limit(200)
            )
        )
        return {
            "learning_mode": "approved_context_retrieval",
            "metrics": svc.feedback_metrics(s),
            "rules": [
                {**svc.rule_view(row), "evidence_current": svc._fresh(s, row)}
                for row in rules
            ],
            "feedback": [
                {**svc.feedback_view(row), "evidence_current": svc._fresh(s, row)}
                for row in feedback
            ],
            "guardrails": svc.GUARDRAILS,
        }


@router.post("/rules", status_code=201)
def new_rule(body: RuleWrite):
    with Session.begin() as s:
        return svc.create_rule(s, body)


@router.post("/rules/{rule_id}/approve")
def approve_rule(rule_id: str, body: Review):
    with Session.begin() as s:
        return svc.approve_rule(s, rule_id, body.reason)


@router.post("/rules/{rule_id}/revoke")
def revoke_rule(rule_id: str, body: Review):
    with Session.begin() as s:
        return svc.revoke_rule(s, rule_id, body.reason)


@router.post("/orders/{order_id}/feedback", status_code=201)
def feedback(order_id: str, body: FeedbackWrite, idempotency_key: str = Header()):
    with Session.begin() as s:
        return svc.capture_feedback(s, lock_order(s, order_id), body, idempotency_key)


@router.post("/feedback/{feedback_id}/approve")
def approve_feedback(feedback_id: str, body: ReuseReview):
    with Session.begin() as s:
        return svc.approve_example(s, feedback_id, body)


@router.post("/feedback/{feedback_id}/revoke")
def revoke_feedback(feedback_id: str, body: Review):
    with Session.begin() as s:
        return svc.revoke_example(s, feedback_id, body.reason)


@router.post("/retrieve")
def retrieve(body: ContextQuery):
    with Session.begin() as s:
        return svc.assemble_context(
            s, lock_order(s, body.order_id), body.query, body.topics
        )


@router.get("/evaluation-snapshot")
def evaluation_snapshot(include_content: bool = False):
    with Session.begin() as s:
        return svc.evaluation_snapshot(s, include_content=include_content)


@router.get("/history")
def history():
    svc.require_admin()
    with Session.begin() as s:
        return {
            "events": [
                {
                    "id": e.id,
                    "subject_type": e.subject_type,
                    "subject_id": e.subject_id,
                    "action": e.action,
                    "actor": e.actor,
                    "reason": e.reason,
                    "details": e.details,
                    "at": e.created_at.isoformat(),
                }
                for e in s.scalars(
                    scoped(CompanyContextEvent)
                    .order_by(
                        CompanyContextEvent.created_at.desc(),
                        CompanyContextEvent.id.desc(),
                    )
                    .limit(200)
                )
            ]
        }
