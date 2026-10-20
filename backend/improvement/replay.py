"""Replay frozen reply inputs with/without approved memory on withheld orders.

Model output is not its own correctness label. The operator reviews blinded pairs
against evidence, then records explicit rubric grades before a release decision.
"""

from copy import deepcopy

from backend.company_context import service as memory
from backend.company_context.models import CompanyFeedback, OrderCustomerBinding
from backend.domain.service import digest, get, scoped
from backend.identity import current_org
from backend.reply_assistant.models import ReplyDraft
from backend.storage.models import Order

RUBRIC = (
    "supported_by_evidence",
    "answers_question",
    "respects_customer_context",
    "no_unsupported_commitment",
)
CRITICAL = ("supported_by_evidence", "no_unsupported_commitment")


def prepare_cases(s, *, limit=20):
    memory.require_admin()
    approved = list(
        s.scalars(
            scoped(CompanyFeedback)
            .where(
                CompanyFeedback.reuse_status == "approved",
                CompanyFeedback.reuse_purpose == "evaluation",
            )
            .order_by(CompanyFeedback.id)
        )
    )
    # Withhold the entire evaluation pool, including cases beyond this batch.
    excluded = {row.order_id for row in approved}
    bindings = list(s.scalars(scoped(OrderCustomerBinding)))
    withheld_customers = {
        row.customer_id
        for row in bindings
        if row.order_id in excluded and row.customer_id
    }
    excluded.update(
        row.order_id for row in bindings if row.customer_id in withheld_customers
    )
    cases, skipped = [], []
    for row in approved:
        if len(cases) >= max(1, min(limit, 100)):
            break
        reply_id = row.generation.get("reply_id")
        draft = (
            s.scalar(scoped(ReplyDraft).where(ReplyDraft.id == reply_id))
            if reply_id
            else None
        )
        reason = ""
        if not memory._fresh(s, row):
            reason = "Evidence changed or evaluation permission expired."
        elif not draft or draft.order_id != row.order_id:
            reason = "No frozen reply input is available for this review."
        elif not draft.context.get("current_order", {}).get("sources"):
            reason = "Frozen input has no source excerpts."
        if reason:
            skipped.append({"case_id": row.id, "reason": reason})
            continue
        snapshot = deepcopy(draft.context)
        # Never add the corrected answer or today's order state to the model input.
        snapshot.pop("attachment_candidates", None)
        baseline = deepcopy(snapshot)
        empty = {"rules": [], "examples": [], "guardrails": memory.GUARDRAILS}
        baseline["company"] = {
            **empty,
            "context_hash": digest(empty),
            "learning_mode": "baseline_no_company_memory",
        }
        candidate = deepcopy(snapshot)
        candidate["company"] = memory.assemble_context(
            s,
            get(s, Order, row.order_id),
            snapshot.get("instruction", ""),
            topics=row.topics,
            exclude_order_ids=excluded,
        )
        cases.append(
            {
                "case_id": row.id,
                "order_id": row.order_id,
                "frozen_input_hash": digest(snapshot["current_order"]),
                "evidence_hash": row.evidence_hash,
                "baseline": baseline,
                "candidate": candidate,
                "reviewed_response": row.human_response,
                "review_reason": row.reason,
                "review_outcome": row.outcome,
            }
        )
    return {
        "schema": "rivet-memory-replay-v1",
        "organization_id": current_org(),
        "withheld_order_ids": sorted(excluded),
        "withheld_customer_ids": sorted(withheld_customers),
        "cases": cases,
        "skipped": skipped,
        "status": "ready_for_replay" if cases else "needs_approved_evaluation_cases",
    }


def validate_output(result, snapshot):
    allowed = {x["id"] for x in snapshot["current_order"]["sources"]}
    refs = result.get("source_ids", [])
    valid = (
        isinstance(result.get("body"), str)
        and bool(result["body"].strip())
        and isinstance(refs, list)
        and bool(refs)
        and all(isinstance(x, str) and x in allowed for x in refs)
    )
    return {
        "valid_citations_and_shape": bool(valid),
        "semantic_correctness": "requires_human_review",
    }


def run_cases(plan, generate, *, model, prompt_hash):
    """`generate` receives model input only, never the held-out answer label."""
    pairs, key, outcomes = [], {}, []
    for case in plan["cases"]:
        order = ["baseline", "candidate"]
        if int(digest(case["case_id"])[:2], 16) % 2:
            order.reverse()
        outputs = {}
        key[case["case_id"]] = {}
        for label, kind in zip(("A", "B"), order):
            snapshot = case[kind]
            try:
                value = generate(deepcopy(snapshot))
                validation = validate_output(value, snapshot)
                error = (
                    ""
                    if validation["valid_citations_and_shape"]
                    else "Output failed citation or shape validation."
                )
            except Exception:  # noqa: BLE001 - keep one provider failure in the paired report
                value, error = (
                    {},
                    "Generation failed; this case is not a successful evaluation.",
                )
                validation = {
                    "valid_citations_and_shape": False,
                    "semantic_correctness": "not_evaluated",
                }
            outputs[label] = {"output": value, "checks": validation, "error": error}
            key[case["case_id"]][label] = kind
        pairs.append(
            {
                "case_id": case["case_id"],
                "order_id": case["order_id"],
                "question": case["baseline"].get("instruction", ""),
                "evidence": case["baseline"]["current_order"],
                "approved_reference_context": case["candidate"]["company"],
                "outputs": outputs,
            }
        )
        outcomes.append(
            {
                "case_id": case["case_id"],
                "frozen_input_hash": case["frozen_input_hash"],
                "baseline_context_hash": case["baseline"]["company"]["context_hash"],
                "candidate_context_hash": case["candidate"]["company"]["context_hash"],
            }
        )
    manifest = {
        "schema": plan["schema"],
        "organization_id": plan["organization_id"],
        "withheld_order_ids": plan["withheld_order_ids"],
        "withheld_customer_ids": plan.get("withheld_customer_ids", []),
        "model": model,
        "prompt_hash": prompt_hash,
        "inputs": outcomes,
    }
    return {
        "manifest": {**manifest, "run_hash": digest(manifest)},
        "review": {
            "status": "awaiting_human_evaluation",
            "rubric": RUBRIC,
            "pairs": pairs,
        },
        "private_answer_key": key,
        "reference_responses": [
            {
                k: c[k]
                for k in (
                    "case_id",
                    "reviewed_response",
                    "review_reason",
                    "review_outcome",
                )
            }
            for c in plan["cases"]
        ],
    }


def score_review(run, grades, *, min_cases=10, min_orders=3):
    """A conservative review gate, not an automatic model deployment operation."""
    pairs = run["review"]["pairs"]
    expected = {(p["case_id"], label) for p in pairs for label in ("A", "B")}
    keyed = {}
    for grade in grades:
        identity = (grade.get("case_id"), grade.get("variant"))
        if identity not in expected or identity in keyed:
            raise ValueError("Grades contain an unknown or duplicate case/variant.")
        if (
            not str(grade.get("reviewer", "")).strip()
            or len(str(grade.get("reason", "")).strip()) < 8
        ):
            raise ValueError(
                "Each grade needs a reviewer and a specific review reason."
            )
        if any(type(grade.get(k)) is not bool for k in RUBRIC):
            raise ValueError("Every rubric field must be explicitly true or false.")
        keyed[identity] = grade
    if set(keyed) != expected or not pairs:
        return {
            "status": "needs_review",
            "release_eligible": False,
            "graded_variants": len(keyed),
        }
    failures, regressions, improvements = 0, [], []
    totals = {kind: {k: 0 for k in RUBRIC} for kind in ("baseline", "candidate")}
    for pair in pairs:
        labels = run["private_answer_key"][pair["case_id"]]
        per_kind = {
            labels[label]: keyed[(pair["case_id"], label)] for label in ("A", "B")
        }
        failures += sum(
            not pair["outputs"][label]["checks"]["valid_citations_and_shape"]
            for label in ("A", "B")
        )
        for kind, grade in per_kind.items():
            for criterion in RUBRIC:
                totals[kind][criterion] += int(grade[criterion])
        before, after = per_kind["baseline"], per_kind["candidate"]
        if any(before[k] and not after[k] for k in RUBRIC):
            regressions.append(pair["case_id"])
        if sum(after[k] for k in RUBRIC) > sum(before[k] for k in RUBRIC):
            improvements.append(pair["case_id"])
    count = len(pairs)
    orders = len({p["order_id"] for p in pairs})
    eligible = (
        count >= min_cases
        and orders >= min_orders
        and not failures
        and not regressions
        and bool(improvements)
        and all(totals["candidate"][k] == count for k in CRITICAL)
    )
    return {
        "status": "eligible_for_release_review"
        if eligible
        else "not_ready_for_release",
        "release_eligible": eligible,
        "case_count": count,
        "order_count": orders,
        "rubric_pass_counts": totals,
        "generation_failures": failures,
        "regressions": regressions,
        "improvements": improvements,
        "model": run["manifest"]["model"],
        "run_hash": run["manifest"]["run_hash"],
        "scope": "This private evaluated dataset only; no model weights were trained and no release was deployed.",
    }
