from copy import deepcopy

import pytest
from test_company_context import ORG_B, feedback, identity, order, promote

from backend.improvement.replay import RUBRIC, prepare_cases, run_cases, score_review
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session


def evaluation_case(s):
    o, _doc, source = order(s)
    frozen = {
        "instruction": "Please confirm cable entry on E-10.",
        "current_order": {
            "sources": [{"id": source.id, "text": source.text}],
            "comments": [],
        },
        "company": {"context_hash": "original", "examples": []},
    }
    draft = ReplyDraft(
        order_id=o.id,
        request_key=o.id,
        request_hash="r" * 64,
        evidence_hash="e" * 64,
        context=frozen,
    )
    s.add(draft)
    s.flush()
    review = feedback(
        s,
        o,
        source,
        generation={"kind": "reply", "reply_id": draft.id, "model": "fixture-model"},
    )
    promote(s, review, purpose="evaluation", scope="company")
    return o, source, draft, review


def test_only_approved_evaluation_frozen_inputs_and_all_heldout_orders_are_excluded():
    with identity(), Session.begin() as s:
        o, source, draft, _review = evaluation_case(s)
        second, _, _, _ = evaluation_case(s)
        promote(s, feedback(s, o, source), scope="company")
        training_order, _, training_source = order(s)
        promote(s, feedback(s, training_order, training_source), scope="company")
        plan = prepare_cases(s, limit=1)
        assert len(plan["cases"]) == 1
        assert set(plan["withheld_order_ids"]) == {o.id, second.id}
        case = plan["cases"][0]
        assert (
            case["baseline"]["current_order"] == draft.context["current_order"]
            or case["order_id"] == second.id
        )
        assert not case["baseline"]["company"]["examples"]
        assert all(
            x["source_order_id"] == training_order.id
            for x in case["candidate"]["company"]["examples"]
        )
        assert case["reviewed_response"] not in str(case["baseline"])
        # Human review may contain a identical example from another order; only
        # held-out order provenance, not common wording, determines leakage.
    with identity(ORG_B), Session.begin() as s:
        assert not prepare_cases(s)["cases"]


def test_changed_source_blocks_replay_and_pending_feedback_cannot_be_evaluated():
    with identity(), Session.begin() as s:
        _o, source, _draft, _review = evaluation_case(s)
        other, _, sp = order(s)
        feedback(s, other, sp)
        source.text = "A changed drawing reference invalidates the evaluation snapshot."
        s.flush()
        result = prepare_cases(s)
        assert not result["cases"]
        assert len(result["skipped"]) == 1


def sample_plan():
    current = {"sources": [{"id": "source-a", "text": "Please confirm sheet E-10."}]}
    snapshot = {
        "instruction": "Confirm cable entry",
        "current_order": current,
        "company": {"context_hash": "ctx", "examples": []},
    }
    return {
        "schema": "rivet-memory-replay-v1",
        "organization_id": "fixture-org",
        "withheld_order_ids": ["order-one"],
        "cases": [
            {
                "case_id": "case-one",
                "order_id": "order-one",
                "baseline": snapshot,
                "candidate": deepcopy(snapshot),
                "frozen_input_hash": "hash",
                "reviewed_response": "HELD OUT ANSWER",
                "review_reason": "Human confirmed reply",
                "review_outcome": "edited",
            }
        ],
    }


def make_run(generate=None):
    def fake(snapshot):
        assert "HELD OUT ANSWER" not in str(snapshot)
        return {"body": "Please confirm sheet E-10.", "source_ids": ["source-a"]}

    return run_cases(
        sample_plan(), generate or fake, model="fixture-model", prompt_hash="prompt-v1"
    )


def grades_for(run):
    return [
        {
            "case_id": "case-one",
            "variant": label,
            "reviewer": "Engineer",
            "reason": "Checked the source excerpt and context.",
            **{key: True for key in RUBRIC},
        }
        for label in ("A", "B")
    ]


def test_generation_is_blinded_and_not_its_own_accuracy_label():
    run = make_run()
    assert run["review"]["status"] == "awaiting_human_evaluation"
    assert "private_answer_key" not in run["review"]
    assert score_review(run, [])["status"] == "needs_review"
    assert not score_review(run, grades_for(run))["release_eligible"]


def test_wrong_citations_and_provider_failure_block_release_even_with_positive_grades():
    for generate in [
        lambda _: {"body": "Looks fine", "source_ids": ["other-order"]},
        lambda _: 1 / 0,
    ]:
        run = make_run(generate)
        score = score_review(run, grades_for(run), min_cases=1, min_orders=1)
        assert score["generation_failures"] == 2
        assert not score["release_eligible"]


def test_gate_needs_measured_improvement_and_refuses_regression():
    run = make_run()
    grades = grades_for(run)
    baseline = next(
        g
        for g in grades
        if run["private_answer_key"]["case-one"][g["variant"]] == "baseline"
    )
    candidate = next(g for g in grades if g is not baseline)
    baseline["respects_customer_context"] = False
    assert score_review(run, grades, min_cases=1, min_orders=1)["release_eligible"]
    assert not score_review(run, grades)["release_eligible"]  # insufficient sample
    candidate["supported_by_evidence"] = False
    score = score_review(run, grades, min_cases=1, min_orders=1)
    assert score["regressions"] == ["case-one"]
    assert not score["release_eligible"]


def test_missing_unknown_duplicate_or_nonboolean_grades_fail_closed():
    run = make_run()
    grades = grades_for(run)
    assert not score_review(run, grades[:1])["release_eligible"]
    for bad in [
        grades + grades[:1],
        [{**grades[0], "variant": "unknown"}],
        [{**grades[0], "answers_question": "true"}],
    ]:
        with pytest.raises(ValueError):
            score_review(run, bad)
