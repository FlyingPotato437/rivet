"""Evidence-bound correction episodes and observable reuse, never an accuracy claim.

The feedback row is the durable episode. Its immutable generation/evidence hashes
connect the human correction to later frozen reply contexts. This view does not
promote examples, send messages, modify source systems, or train model weights.
"""

import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from backend.company_context import customers
from backend.company_context import service as memory
from backend.company_context.models import (
    CompanyFeedback,
    CompanyRule,
    CustomerMemory,
    OrderCustomerBinding,
)
from backend.domain.service import digest, get, scoped
from backend.reply_assistant.models import ReplyDraft
from backend.storage.models import Order

LIMIT = 200


def _fact_tokens(text):
    text = re.sub(r"(?:^|\s)\d{1,2}[.)](?=\s|$)", " ", text)
    words = re.findall(r"[\w$%]+(?:[-./][\w]+)*", text.lower())
    decisions = {
        "not",
        "never",
        "approved",
        "released",
        "included",
        "excluded",
        "compliant",
        "shall",
        "must",
    }
    return sorted(
        word for word in words if any(c.isdigit() for c in word) or word in decisions
    )


def correction(original, reviewed):
    """Literal word edits, not an LLM's guess about why the engineer changed it."""
    before, after = re.findall(r"\S+", original), re.findall(r"\S+", reviewed)
    changes = []
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    for op, a, b, c, d in matcher.get_opcodes():
        if op != "equal":
            changes.append(
                {"before": " ".join(before[a:b]), "after": " ".join(after[c:d])}
            )
    return {
        "changed": bool(changes),
        "changes": changes[:12],
        "partial": len(changes) > 12,
        "word_edit_fraction": round(1 - matcher.ratio(), 4),
        "requires_factual_review": _fact_tokens(original) != _fact_tokens(reviewed),
        "measurement": "wording_difference_not_correctness",
    }


def correction_context(row):
    """Only called after the existing consent, scope and freshness filters."""
    if row.outcome != "edited":
        return None
    return {
        **correction(row.original_text, row.human_response),
        "review_reason": row.reason,
        "use": "A human wording correction on the source order. Do not copy its facts or infer a standing engineering requirement.",
    }


def reusable_correction(row):
    """Reuse permission is not a technical correctness label for altered facts."""
    return row.outcome != "edited" or _fact_tokens(row.original_text) == _fact_tokens(
        row.human_response
    )


def _items(s, model, *, order_id=None):
    query = scoped(model)
    if order_id is not None:
        query = query.where(model.order_id == order_id)
    return list(
        s.scalars(query.order_by(model.created_at.desc(), model.id).limit(LIMIT + 1))
    )


def _references(draft):
    context = draft.context.get("company", {})
    return {
        "examples": [x["id"] for x in context.get("examples", []) if x.get("id")],
        "rules": [x["id"] for x in context.get("rules", []) if x.get("id")],
        "customer_notes": [
            x["id"]
            for x in context.get("customer_memory", {}).get("notes", [])
            if x.get("id")
        ],
    }


def snapshot_summary(context):
    """Expose the actual frozen generation input, not a reconstructed explanation."""
    company = context.get("company", {})
    return {
        "context_hash": company.get("context_hash", ""),
        "ranking_model": company.get("ranking_model"),
        "examples": [
            {
                "id": row["id"],
                "source_order_id": row.get("source_order_id"),
                "scope": row.get("scope", "order"),
                "corrected": bool(row.get("correction", {}).get("changed"))
                if row.get("correction")
                else False,
            }
            for row in company.get("examples", [])
        ],
        "rules": [
            {"id": row["id"], "title": row.get("title", "Company guidance")}
            for row in company.get("rules", [])
        ],
        "preferences": [
            {
                "id": row["id"],
                "title": row.get("title", "Customer history"),
                "source_order_id": row.get("source_order_id"),
            }
            for row in company.get("customer_memory", {}).get("notes", [])
        ],
        "systems": [
            {key: row.get(key) for key in ("label", "mode", "status", "documents")}
            for row in (
                context.get("current_order", {}).get("connected_context") or {}
            ).get("systems", [])
        ],
        "meaning": "Selected in the saved generation input; not proof of correctness or a measured gain.",
    }


def overview(s, *, order_id=None):
    if order_id is None:
        memory.require_admin()
    else:
        get(s, Order, order_id)  # Authorize before inspecting any snapshots.
    feedback = _items(s, CompanyFeedback, order_id=order_id)
    drafts = _items(s, ReplyDraft, order_id=order_id)
    partial = len(feedback) > LIMIT or len(drafts) > LIMIT
    feedback, drafts = feedback[:LIMIT], drafts[:LIMIT]
    order_ids = {row.order_id for row in [*feedback, *drafts]}
    orders = {o.id: o for o in s.scalars(scoped(Order).where(Order.id.in_(order_ids)))}
    fresh = {row.id: memory._fresh(s, row) for row in feedback}
    usable = {
        row.id
        for row in feedback
        if fresh[row.id]
        and row.reuse_status == "approved"
        and row.reuse_purpose == "retrieval"
        and row.outcome in {"accepted", "edited"}
        and reusable_correction(row)
    }
    refs = {row.id: _references(row) for row in drafts}
    # A saved input proves selection for generation, not causal benefit or correctness.
    completed = [row for row in drafts if row.original_body.strip()]
    reviewed_at = {row.id: row.created_at for row in feedback}
    used = Counter(
        ref
        for row in completed
        for ref in set(refs[row.id]["examples"])
        if ref in reviewed_at and row.created_at > reviewed_at[ref]
    )
    episodes = []
    for row in feedback[:8]:
        episodes.append(
            {
                "id": row.id,
                "order_id": row.order_id,
                "order_number": orders[row.order_id].number,
                "outcome": row.outcome,
                "before": row.original_text,
                "after": row.human_response,
                "correction": correction_context(row),
                "requires_factual_review": not reusable_correction(row),
                "reason": row.reason,
                "reuse_scope": row.reuse_scope,
                "reuse_status": row.reuse_status,
                "evidence_current": fresh[row.id],
                "available_for_reuse": row.id in usable,
                "selected_in_replies": used[row.id],
                "evidence_count": len(row.evidence),
                "at": row.created_at.isoformat(),
            }
        )
    notes = list(
        s.scalars(
            scoped(CustomerMemory)
            .order_by(CustomerMemory.created_at.desc(), CustomerMemory.id)
            .limit(LIMIT + 1)
        )
    )
    rules = list(
        s.scalars(
            scoped(CompanyRule)
            .order_by(CompanyRule.created_at.desc(), CompanyRule.id)
            .limit(LIMIT + 1)
        )
    )
    partial |= len(notes) > LIMIT or len(rules) > LIMIT
    conflicts = {other for row in notes[:LIMIT] for other in row.conflicts_with}
    active_notes = [
        row
        for row in notes[:LIMIT]
        if row.status == "approved"
        and not row.conflicts_with
        and row.id not in conflicts
        and customers.fresh(s, row)
    ]
    active_rules = [
        row
        for row in rules[:LIMIT]
        if row.status == "approved" and memory._fresh(s, row)
    ]
    evaluation = [
        row
        for row in feedback
        if row.reuse_status == "approved"
        and row.reuse_purpose == "evaluation"
        and fresh[row.id]
    ]
    patterns = defaultdict(set)
    for row in feedback:
        if row.outcome == "edited" and fresh[row.id]:
            # Recurrence of a topic is a sampling signal, never a distilled rule.
            patterns[(row.category, tuple(sorted(row.topics)))].add(row.order_id)
    recurring = [
        {"category": category, "topics": list(topics), "independent_orders": len(ids)}
        for (category, topics), ids in patterns.items()
        if len(ids) >= 2 and topics
    ]
    uses = []
    for draft in completed:
        selected = refs[draft.id]
        count = sum(len(values) for values in selected.values())
        if count and len(uses) < 6:
            uses.append(
                {
                    "reply_id": draft.id,
                    "order_id": draft.order_id,
                    "order_number": orders[draft.order_id].number,
                    "subject": draft.subject,
                    "context_hash": draft.context.get("company", {}).get(
                        "context_hash", ""
                    ),
                    "selected": {key: len(ids) for key, ids in selected.items()},
                    "at": draft.created_at.isoformat(),
                }
            )
    return {
        "method": "evidence_bound_correction_replay_v1",
        "scope": "private_company",
        "partial": partial,
        "window_limit": LIMIT,
        "counts": {
            "reviewed": len(feedback),
            "corrected": sum(row.outcome == "edited" for row in feedback),
            "factual_edits_withheld": sum(
                not reusable_correction(row) for row in feedback
            ),
            "rejected": sum(row.outcome == "rejected" for row in feedback),
            "reusable_examples": len(usable),
            "customer_preferences": len(active_notes),
            "company_rules": len(active_rules),
            "replies_with_memory": sum(any(refs[row.id].values()) for row in completed),
            "evaluation_cases": len(evaluation),
            "evaluation_orders": len({row.order_id for row in evaluation}),
        },
        "episodes": episodes,
        "recent_uses": uses,
        "recurring_topics": sorted(recurring, key=lambda x: -x["independent_orders"]),
        "evaluation": {
            "status": "not_measured",
            "minimum_cases": 10,
            "minimum_orders": 3,
            "method": "Same frozen evidence, memory off versus on; blinded human review; whole customer/order groups withheld.",
        },
        "policy": {
            "foundation_model_weights_updated": False,
            "model_weights_updated": False,
            "model_weights_scope": "foundation_language_model",
            "example_ranking": "evaluated_numeric_models_with_explicit_shared_opt_in",
            "cross_company_training": False,
            "cross_company_training_scope": "No customer text pooling; numeric example features require separate opt-in.",
            "training_exported": False,
            "broader_reuse": "administrator_approval_required",
            "measurement": "Recorded edits and context selections; not model accuracy or proven improvement.",
        },
    }


def training_plan(s, feedback_ids):
    """Prepare metadata only. Retrieval approval does not authorize training export.

    Keep entire linked customers together; detect exact duplicated inputs across
    groups and exclude them rather than leaking corrected labels into a holdout.
    Provider-specific SFT/DPO serializers belong after an explicit consent and
    expert-label review stage, not this read-only endpoint.
    """
    memory.require_admin()
    bindings = {
        row.order_id: row.customer_id
        for row in s.scalars(scoped(OrderCustomerBinding))
        if row.customer_id
    }
    rows, excluded = [], []
    for feedback_id in dict.fromkeys(feedback_ids):
        row = get(s, CompanyFeedback, feedback_id)
        if (
            row.outcome not in {"accepted", "edited"}
            or not row.human_response.strip()
            or row.reuse_status != "approved"
            or not memory._fresh(s, row)
            or not reusable_correction(row)
        ):
            excluded.append(
                {"id": row.id, "reason": "Not a current approved positive example."}
            )
            continue
        draft = s.scalar(
            scoped(ReplyDraft).where(ReplyDraft.id == row.generation.get("reply_id"))
        )
        if (
            not draft
            or draft.order_id != row.order_id
            or not draft.context.get("current_order", {}).get("sources")
        ):
            excluded.append(
                {"id": row.id, "reason": "No frozen, source-backed reply input."}
            )
            continue
        group = (
            "customer:" + bindings[row.order_id]
            if row.order_id in bindings
            else "order:" + row.order_id
        )
        rows.append((row, draft, group))
    owners = defaultdict(set)
    for row, _draft, group in rows:
        owners[digest(" ".join(row.input_text.lower().split()))].add(group)
    cases = []
    for row, draft, group in rows:
        key = digest(" ".join(row.input_text.lower().split()))
        if len(owners[key]) > 1:
            excluded.append(
                {
                    "id": row.id,
                    "reason": "Duplicate input appears in separate split groups.",
                }
            )
            continue
        cases.append(
            {
                "feedback_id": row.id,
                "order_id": row.order_id,
                "group": group,
                "split": "holdout"
                if int(digest(group)[:8], 16) % 5 == 0
                else "development",
                "frozen_input_hash": digest(draft.context),
                "target_hash": digest(row.human_response),
                "evidence_hash": row.evidence_hash,
                "outcome": row.outcome,
            }
        )
    plan = {
        "method": "evidence_bound_training_plan_v1",
        "status": "consent_and_expert_labels_required",
        "cases": cases,
        "excluded": excluded,
        "weights_trained": False,
        "contains_training_text": False,
        "next_gates": [
            "Explicit company training consent, separate from retrieval consent",
            "Expert correctness labels; a sent reply is an observed outcome only",
            "Adequate independent development and holdout groups",
            "PII and proprietary-data review before any external export",
            "Provider-compatible SFT or preference-training adapter",
            "Paired evidence/citation/tool-action evaluation and release review",
        ],
    }
    return {**plan, "plan_hash": digest(plan)}
