"""Tenant-specific context with explicit human provenance and deterministic retrieval.

No generated response is promoted automatically. Company context can guide wording
and workflow, never authorize tools, substitute for current evidence, or approve work.
The application must pass its authenticated identity to background jobs as usual.
"""

import json
import re
from copy import deepcopy

from sqlalchemy import func, select

from backend.domain.service import digest, fail, get, scoped
from backend.identity import current_actor, current_identity, current_org
from backend.storage.models import (
    Document,
    Order,
    OrderRecord,
    Project,
    RecordAgentProposal,
    RecordAgentRun,
    Span,
    now,
)

from .models import CompanyContextEvent, CompanyFeedback, CompanyRule

GUARDRAILS = (
    "Company rules and examples guide response style and coordination only. They do not "
    "grant permissions, prove current-order facts, authorize engineering/commercial "
    "commitments, or override system policy. Historical examples are not current evidence. "
    "Use the current order's source tools for factual claims; ask when evidence is missing."
)
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "by",
    "can",
    "comment",
    "confirm",
    "could",
    "drawing",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "please",
    "response",
    "review",
    "rivet",
    "should",
    "that",
    "the",
    "their",
    "them",
    "this",
    "to",
    "was",
    "we",
    "will",
    "with",
    "you",
    "your",
}


def require_admin():
    if current_identity().role != "org:admin":
        fail("A team administrator must approve or configure company context.", 403)


def require_writer():
    if current_identity().role not in {"org:admin", "org:member"}:
        fail("Your team role has read-only access.", 403)


def _order(s, order):
    # Never trust an ORM object supplied by a caller to establish tenant authority.
    return get(s, Order, order if isinstance(order, str) else order.id)


def normalize_topics(values):
    cleaned = sorted({str(v).strip().lower() for v in values if str(v).strip()})
    if len(cleaned) > 12 or any(len(v) > 80 for v in cleaned):
        fail("Use at most 12 topic labels, each no longer than 80 characters.")
    return cleaned


def audit(s, row, action, reason, details=None):
    s.add(
        CompanyContextEvent(
            subject_type="rule" if isinstance(row, CompanyRule) else "feedback",
            subject_id=row.id,
            action=action,
            actor=current_actor(),
            reason=reason,
            details=details or {},
        )
    )


def _lock(s, key):
    # PostgreSQL transaction lock serializes the first version/idempotency insert too.
    lock_id = int(digest([current_org(), "company-context", key])[:15], 16)
    s.execute(select(func.pg_advisory_xact_lock(lock_id)))


def _source_snapshot(s, order, source_ids):
    result = []
    for source_id in sorted(set(source_ids)):
        source = get(s, Span, source_id)
        document = get(s, Document, source.document_id)
        if document.project_id != order.project_id:
            fail("Company feedback evidence belongs to another order.", 403)
        result.append(
            {
                "source_id": source.id,
                "document_id": document.id,
                "document_sha256": document.sha256,
                "source_hash": digest([source.text, source.location]),
            }
        )
    return result


def _order_evidence_version(s, order):
    docs = list(
        s.scalars(
            scoped(Document)
            .where(Document.project_id == order.project_id)
            .order_by(Document.id)
        )
    )
    return digest(
        [
            [
                d.id,
                d.sha256,
                d.state,
                d.meta.get("revision_label", ""),
                d.meta.get("active", True),
            ]
            for d in docs
        ]
    )


def _fresh(s, row):
    if isinstance(row, CompanyFeedback):
        if row.expires_at and row.expires_at <= now():
            return False
        order = s.scalar(scoped(Order).where(Order.id == row.order_id))
        evidence = row.evidence
        expected_version = row.generation.get("order_evidence_version")
    else:
        evidence = row.provenance.get("evidence", [])
        if not evidence:
            return True
        order = s.scalar(
            scoped(Order).where(
                Order.id == row.provenance.get("source_order_id", row.order_id)
            )
        )
        expected_version = row.provenance.get("order_evidence_version")
    if order is None or expected_version != _order_evidence_version(s, order):
        return False
    for item in evidence:
        source = s.scalar(scoped(Span).where(Span.id == item["source_id"]))
        doc = s.scalar(scoped(Document).where(Document.id == item["document_id"]))
        if (
            source is None
            or doc is None
            or source.document_id != doc.id
            or doc.project_id != order.project_id
        ):
            return False
        if (
            doc.sha256 != item["document_sha256"]
            or digest([source.text, source.location]) != item["source_hash"]
        ):
            return False
    return True


def rule_view(row):
    return {
        "id": row.id,
        "key": row.key,
        "version": row.version,
        "title": row.title,
        "text": row.text,
        "topics": row.topics,
        "order_id": row.order_id,
        "category": row.category,
        "status": row.status,
        "provenance": row.provenance,
        "author": row.author,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_reason": row.review_reason,
    }


def create_rule(s, body):
    require_admin()
    _lock(s, "rule:" + body.key)
    previous = s.scalar(
        scoped(CompanyRule)
        .where(CompanyRule.key == body.key)
        .order_by(CompanyRule.version.desc())
        .limit(1)
    )
    if body.expected_version != (previous.version if previous else 0):
        fail("This rule has a newer version. Refresh before editing.", 409)
    order = _order(s, body.order_id) if body.order_id else None
    source_order = _order(s, body.source_order_id) if body.source_order_id else order
    if body.source_ids and source_order is None:
        fail("Source-backed rules must identify the source order.")
    evidence = (
        _source_snapshot(s, source_order, body.source_ids) if source_order else []
    )
    row = CompanyRule(
        key=body.key,
        version=body.expected_version + 1,
        title=body.title,
        text=body.text,
        topics=normalize_topics(body.topics),
        order_id=order.id if order else None,
        category=body.category.strip(),
        author=current_actor(),
        provenance={
            "note": body.provenance_note,
            "kind": "human_authored",
            "evidence": evidence,
            "source_order_id": source_order.id if source_order else None,
            "order_evidence_version": _order_evidence_version(s, source_order)
            if source_order
            else None,
        },
    )
    s.add(row)
    s.flush()
    audit(s, row, "draft_created", body.provenance_note)
    return rule_view(row)


def approve_rule(s, rule_id, reason):
    require_admin()
    row = get(s, CompanyRule, rule_id)
    _lock(s, "rule:" + row.key)
    s.refresh(row)
    if row.status != "draft":
        fail(
            "Only a draft rule can be approved. Create a new version for changes.", 409
        )
    if not _fresh(s, row):
        fail("The rule's source evidence changed. Create a fresh rule version.", 409)
    for previous in s.scalars(
        scoped(CompanyRule).where(
            CompanyRule.key == row.key, CompanyRule.status == "approved"
        )
    ):
        previous.status = "superseded"
        audit(s, previous, "superseded", reason, {"replacement_id": row.id})
    row.status, row.reviewed_by, row.reviewed_at, row.review_reason = (
        "approved",
        current_actor(),
        now(),
        reason,
    )
    audit(s, row, "approved", reason)
    s.flush()
    return rule_view(row)


def revoke_rule(s, rule_id, reason):
    require_admin()
    row = get(s, CompanyRule, rule_id, True)
    row.status = "revoked"
    audit(s, row, "revoked", reason)
    s.flush()
    return rule_view(row)


def feedback_view(row):
    from backend.improvement.learning import reusable_correction

    return {
        "id": row.id,
        "order_id": row.order_id,
        "proposal_id": row.proposal_id,
        "generation": row.generation,
        "generation_hash": row.generation_hash,
        "evidence_hash": row.evidence_hash,
        "evidence": row.evidence,
        "input_text": row.input_text,
        "original_text": row.original_text,
        "human_response": row.human_response,
        "outcome": row.outcome,
        "reason": row.reason,
        "reviewer": row.reviewer,
        "topics": row.topics,
        "category": row.category,
        "reuse_status": row.reuse_status,
        "reuse_scope": row.reuse_scope,
        "reuse_purpose": row.reuse_purpose,
        "factual_edit_withheld": not reusable_correction(row),
        "approved_by": row.approved_by,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def capture_generation_feedback(
    s,
    order,
    *,
    request_key,
    generation,
    original_text,
    input_text,
    evidence_ids,
    outcome,
    human_response,
    reason,
    topics,
    proposal_id=None,
    run_id=None,
):
    """Trusted hook: callers obtain generation/original/evidence from their saved draft.

    This function is not an API that accepts client-provided model provenance.
    Capturing feedback never authorizes reuse, sends a response, or approves an order.
    """
    require_writer()
    order = _order(s, order)
    if not request_key or len(request_key) > 180 or len(reason.strip()) < 8:
        fail("A request key and specific review reason are required.")
    if outcome not in {"accepted", "edited", "rejected"}:
        fail("Unknown feedback outcome.")
    if (
        not original_text.strip()
        or not input_text.strip()
        or max(len(original_text), len(human_response), len(input_text)) > 12000
    ):
        fail(
            "Feedback needs the saved input and original draft, within 12,000 characters."
        )
    if outcome == "accepted":
        if human_response and human_response != original_text:
            fail(
                "Use edited feedback when the accepted response differs from the draft."
            )
        human_response = original_text
    if outcome == "edited" and (
        not human_response.strip() or human_response == original_text
    ):
        fail("Edited feedback requires the revised response.")
    if outcome == "rejected" and human_response:
        fail(
            "Rejected feedback cannot supply an accepted response. Use edited instead."
        )
    if not evidence_ids:
        fail("Response feedback requires at least one source from this order.")
    for cls, value in ((RecordAgentProposal, proposal_id), (RecordAgentRun, run_id)):
        if value and get(s, cls, value).order_id != order.id:
            fail("Feedback generation belongs to another order.", 403)
    topic_labels = normalize_topics(topics)
    request_hash = digest(
        [
            generation,
            original_text,
            input_text,
            sorted(set(evidence_ids)),
            outcome,
            human_response,
            reason,
            topic_labels,
            proposal_id,
            run_id,
        ]
    )
    _lock(s, "feedback:" + order.id + ":" + request_key)
    old = s.scalar(
        scoped(CompanyFeedback).where(
            CompanyFeedback.order_id == order.id,
            CompanyFeedback.request_key == request_key,
        )
    )
    if old:
        if old.request_hash != request_hash:
            fail(
                "This feedback request key has already been used for another review.",
                409,
            )
        return feedback_view(old)
    evidence = _source_snapshot(s, order, evidence_ids)
    generation = deepcopy(generation)
    current_version = _order_evidence_version(s, order)
    if generation.get("order_evidence_version") not in {None, current_version}:
        fail(
            "The saved generation used older evidence. Review a current draft before recording feedback.",
            409,
        )
    generation["order_evidence_version"] = current_version
    row = CompanyFeedback(
        order_id=order.id,
        proposal_id=proposal_id,
        run_id=run_id,
        request_key=request_key,
        request_hash=request_hash,
        generation=generation,
        generation_hash=digest([generation, original_text]),
        evidence=evidence,
        evidence_hash=digest(evidence),
        input_text=input_text,
        original_text=original_text,
        human_response=human_response,
        outcome=outcome,
        reason=reason,
        topics=topic_labels,
        reviewer=current_actor(),
        category=get(s, Project, order.project_id).category,
    )
    s.add(row)
    s.flush()
    audit(s, row, "feedback_recorded", reason, {"outcome": outcome})
    return feedback_view(row)


def capture_feedback(s, order, body, request_key):
    order = _order(s, order)
    proposal = get(s, RecordAgentProposal, body.proposal_id)
    if proposal.order_id != order.id:
        fail("Feedback proposal belongs to another order.", 403)
    if proposal.status == "stale":
        fail("Review the current proposal before recording feedback.", 409)
    if body.operation_index >= len(proposal.operations):
        fail("This proposal operation does not exist.")
    operation = proposal.operations[body.operation_index]
    if operation.get("type") not in {"draft_clarification", "draft_summary"}:
        fail("Response feedback applies to a generated communication draft.")
    run = get(s, RecordAgentRun, proposal.run_id)
    record = s.scalar(scoped(OrderRecord).where(OrderRecord.order_id == order.id))
    comment = next(
        (
            c
            for c in (record.data.get("comments", []) if record else [])
            if c.get("id") == operation.get("comment_id")
        ),
        {},
    )
    return capture_generation_feedback(
        s,
        order,
        request_key=request_key,
        generation={
            "kind": "record_agent",
            "run_id": run.id,
            "proposal_id": proposal.id,
            "operation_index": body.operation_index,
            "model": run.model,
            "input_hash": proposal.input_hash,
            "record_version": proposal.base_version,
        },
        original_text=operation.get("text", ""),
        input_text=comment.get("original_text") or run.goal,
        evidence_ids=operation.get("evidence_ids") or proposal.evidence_ids,
        outcome=body.outcome,
        human_response=body.human_response,
        reason=body.reason,
        topics=body.topics,
        proposal_id=proposal.id,
        run_id=run.id,
    )


def approve_example(s, feedback_id, body):
    require_admin()
    row = get(s, CompanyFeedback, feedback_id, True)
    if row.reuse_status != "unapproved":
        fail(
            "Only unapproved feedback can be promoted. Record a new review after revocation.",
            409,
        )
    if body.purpose == "retrieval" and (
        row.outcome == "rejected" or not row.human_response
    ):
        fail(
            "Rejected responses can be evaluation cases, never positive response examples."
        )
    if not _fresh(s, row):
        fail(
            "The source evidence changed. Review a current draft before approving reuse.",
            409,
        )
    if body.expires_at and body.expires_at <= now():
        fail("Choose a future expiration date.")
    if body.scope == "company" and not row.topics:
        fail("Company-wide reuse requires explicit topic labels.")
    row.reuse_status, row.reuse_scope, row.reuse_purpose = (
        "approved",
        body.scope,
        body.purpose,
    )
    row.approved_by, row.approved_at, row.expires_at = (
        current_actor(),
        now(),
        body.expires_at,
    )
    audit(
        s,
        row,
        "reuse_approved",
        body.reason,
        {"scope": body.scope, "purpose": body.purpose},
    )
    s.flush()
    return feedback_view(row)


def revoke_example(s, feedback_id, reason):
    require_admin()
    row = get(s, CompanyFeedback, feedback_id, True)
    row.reuse_status = "revoked"
    audit(s, row, "reuse_revoked", reason)
    s.flush()
    return feedback_view(row)


def _tokens(text):
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if len(word) >= 3 and word not in STOP_WORDS
    }


def assemble_context(s, order, query, topics=(), *, limit=4, exclude_order_ids=()):
    """Read-only deterministic selection. No embeddings, provider calls, or auto-learning."""
    from backend.improvement.learning import correction_context, reusable_correction

    order = _order(s, order)
    category = get(s, Project, order.project_id).category
    topic_set = set(normalize_topics(topics))
    query_tokens = _tokens(query)
    rules = []
    for row in s.scalars(
        scoped(CompanyRule)
        .where(CompanyRule.status == "approved")
        .order_by(CompanyRule.key, CompanyRule.version)
    ):
        if row.order_id and (
            row.order_id != order.id or row.order_id in exclude_order_ids
        ):
            continue
        if row.provenance.get("source_order_id") in exclude_order_ids:
            continue
        if row.category and row.category != category:
            continue
        if row.topics and not (
            set(row.topics) & topic_set
            or len(_tokens(" ".join(row.topics)) & query_tokens) >= 1
        ):
            continue
        if _fresh(s, row):
            rules.append(
                {
                    "id": row.id,
                    "key": row.key,
                    "version": row.version,
                    "title": row.title,
                    "text": row.text,
                    "provenance": row.provenance,
                    "approved_by": row.reviewed_by,
                }
            )
    from backend.improvement.adaptive import active, adjusted_score

    # Holdout replay deliberately uses the fixed baseline: a fitted model may
    # have observed the withheld jobs, even if its features contain no text.
    ranking_model = None if exclude_order_ids else active(s)
    candidates = []
    for row in s.scalars(
        scoped(CompanyFeedback)
        .where(
            CompanyFeedback.reuse_status == "approved",
            CompanyFeedback.reuse_purpose == "retrieval",
            CompanyFeedback.outcome.in_(["accepted", "edited"]),
        )
        .order_by(CompanyFeedback.id)
    ):
        if row.order_id in exclude_order_ids or row.category != category:
            continue
        if row.reuse_scope == "order" and row.order_id != order.id:
            continue
        overlap = query_tokens & _tokens(row.input_text + " " + " ".join(row.topics))
        topic_overlap = set(row.topics) & topic_set
        if not topic_overlap and len(overlap) < 2:
            continue
        if not _fresh(s, row):
            continue
        if not reusable_correction(row):
            continue
        score = (
            4 * len(topic_overlap)
            + len(overlap)
            + (1 if row.order_id == order.id else 0)
        )
        score = adjusted_score(ranking_model, score, query, topic_set, row, order.id)
        candidates.append(
            (
                score,
                row.id,
                {
                    "id": row.id,
                    "source_order_id": row.order_id,
                    "input": row.input_text,
                    "reviewed_response": row.human_response,
                    "topics": row.topics,
                    "generation_hash": row.generation_hash,
                    "evidence_hash": row.evidence_hash,
                    "approved_by": row.approved_by,
                    "scope": row.reuse_scope,
                    "correction": correction_context(row),
                    "use": "Historical response example; verify every fact against current order evidence.",
                },
            )
        )
    examples = [
        item[2]
        for item in sorted(candidates, key=lambda item: (-item[0], item[1]))[
            : max(0, min(limit, 8))
        ]
    ]
    from .customers import retrieve as customer_context

    payload = {
        "rules": rules,
        "examples": examples,
        "guardrails": GUARDRAILS,
        "customer_memory": customer_context(
            s, order, query, topics, exclude_order_ids=exclude_order_ids
        ),
        "ranking_model": {"id": ranking_model.id, "scope": ranking_model.scope}
        if ranking_model else None,
    }
    while examples and len(json.dumps(payload)) > 32000:
        examples.pop()
    if len(json.dumps(payload)) > 32000:
        fail(
            "The approved company/customer guidance exceeds the context budget. Narrow its topics or applicability before generating a reply.",
            409,
        )
    return {
        **payload,
        "context_hash": digest(payload),
        "learning_mode": "approved_context_retrieval",
    }


def evaluation_snapshot(s, *, include_content=False):
    """Admin-only tenant export. No external upload, no training job, no accuracy claim."""
    require_admin()
    cases = []
    for row in s.scalars(
        scoped(CompanyFeedback)
        .where(CompanyFeedback.reuse_status == "approved")
        .order_by(CompanyFeedback.id)
    ):
        # Keep stale/revoked/private-but-unapproved records out of evaluation exports.
        if not _fresh(s, row):
            continue
        case = {
            "id": row.id,
            "order_id": row.order_id,
            "outcome": row.outcome,
            "generation_hash": row.generation_hash,
            "evidence_hash": row.evidence_hash,
            "topics": row.topics,
            "model": row.generation.get("model", ""),
            "context_hash": row.generation.get("context_hash", ""),
            "split": "holdout"
            if int(digest([current_org(), row.order_id])[:8], 16) % 5 == 0
            else "development",
            "approved_by": row.approved_by,
            "label_type": "rejected_output"
            if row.outcome == "rejected"
            else "human_reviewed_response",
        }
        if include_content:
            case.update(
                input=row.input_text,
                generated_response=row.original_text,
                reviewed_response=row.human_response,
                review_reason=row.reason,
            )
        cases.append(case)
    rules = [
        rule_view(row)
        for row in s.scalars(
            scoped(CompanyRule)
            .where(CompanyRule.status == "approved")
            .order_by(CompanyRule.key)
        )
        if _fresh(s, row)
    ]
    if not include_content:
        rules = [
            {
                "id": r["id"],
                "key": r["key"],
                "version": r["version"],
                "content_hash": digest(r["text"]),
            }
            for r in rules
        ]
    payload = {
        "schema": "rivet-company-eval-v1",
        "organization_id": current_org(),
        "contains_private_content": include_content,
        "cases": cases,
        "rules": rules,
        "evaluation_status": "dataset_only_not_evaluated",
        "holdout_policy": "Split by order. Exclude every held-out order from context retrieval and rule derivation during replay. Never score with the answer in the prompt.",
    }
    return {
        **payload,
        "snapshot_hash": digest(payload),
        "exported_at": now().isoformat(),
    }


def feedback_metrics(s):
    """Observed human review counts, deliberately not a model accuracy estimate."""
    from backend.improvement.learning import reusable_correction

    require_admin()
    outcomes = dict(
        s.execute(
            select(CompanyFeedback.outcome, func.count(CompanyFeedback.id))
            .where(CompanyFeedback.organization_id == current_org())
            .group_by(CompanyFeedback.outcome)
        ).all()
    )
    approved = list(
        s.scalars(
            scoped(CompanyFeedback).where(CompanyFeedback.reuse_status == "approved")
        )
    )
    current = [row for row in approved if _fresh(s, row)]
    by_order = {}
    for order_id, outcome, count in s.execute(
        select(
            CompanyFeedback.order_id,
            CompanyFeedback.outcome,
            func.count(CompanyFeedback.id),
        )
        .where(CompanyFeedback.organization_id == current_org())
        .group_by(CompanyFeedback.order_id, CompanyFeedback.outcome)
        .order_by(CompanyFeedback.order_id)
    ):
        by_order.setdefault(order_id, {"accepted": 0, "edited": 0, "rejected": 0})[
            outcome
        ] = count
    return {
        "measurement": "observed_review_outcomes_not_model_accuracy",
        "reviewed_responses": sum(outcomes.values()),
        "accepted": outcomes.get("accepted", 0),
        "edited": outcomes.get("edited", 0),
        "rejected": outcomes.get("rejected", 0),
        "approved_current_examples": sum(
            row.reuse_purpose == "retrieval" and reusable_correction(row)
            for row in current
        ),
        "approved_current_evaluation_cases": len(current),
        "approved_but_stale_or_expired": len(approved) - len(current),
        "orders_with_feedback": len(by_order),
        "coverage_by_order": [
            {"order_id": order_id, **counts, "reviewed_responses": sum(counts.values())}
            for order_id, counts in by_order.items()
        ],
        "caveat": "These are recorded human decisions, not validated correctness or a measured improvement. Compare held-out cases with a baseline before making an accuracy claim.",
    }
