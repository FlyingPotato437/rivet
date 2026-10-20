"""Exercise training, live selection, revocation, and tenant/holdout boundaries."""

from datetime import timedelta

import pytest
from fastapi import HTTPException
from test_company_context import ORG_A, ORG_B, feedback, identity, order, promote, query
from test_learning_loop import saved_reply

from backend.company_context import service as memory
from backend.company_context.api import LearningPolicyWrite
from backend.company_context.models import CompanyFeedback
from backend.domain.service import digest
from backend.improvement import adaptive
from backend.improvement.models import ExampleRanker
from backend.storage.db import Session
from backend.storage.models import now


def numeric_rows():
    rows = []
    for group in range(30):
        for episode in range(6):
            y = episode % 2
            rows.append(
                {
                    "group": str(group),
                    "episode": f"{group}-{episode}",
                    "x": [1.0, float(y), 0.0, 0.0, 0.0, 0.1],
                    "y": y,
                }
            )
    return rows


def artifact(s, dependencies, *, scope="private", owner=ORG_A):
    row = ExampleRanker(
        organization_id=owner,
        scope=scope,
        dataset_hash=digest([dependencies, str(now())]),
        status="active",
        weights=[0.0, 0.0, 0.0, 0.0, 8.0, 0.0],
        report={"episodes": 100, "heldout_episodes": 20},
        dependencies=dependencies,
    )
    s.add(row)
    s.flush()
    return row


def test_trained_numeric_ranker_improves_on_unseen_whole_groups():
    rows = numeric_rows()
    weights, report = adaptive.fit(rows)
    assert report["status"] == "active"
    assert report["heldout_groups"] >= 2
    assert report["candidate_brier"] < report["baseline_brier"] - 0.01
    assert report["engineering_accuracy"] == "not_measured"
    assert all(isinstance(w, float) for w in weights)
    # Duplicating every row from one episode cannot change its weight.
    duplicate = rows + [rows[0]] * 20
    duplicated_weights, _ = adaptive.fit(duplicate)
    assert duplicated_weights == pytest.approx(weights)


def test_insufficient_or_uninformative_feedback_never_activates():
    weights, report = adaptive.fit(numeric_rows()[:10])
    assert not weights and report["status"] == "collecting_examples"
    rows = [{**r, "x": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]} for r in numeric_rows()]
    _, report = adaptive.fit(rows)
    assert report["status"] == "not_improved"


def test_background_learning_failure_isolated_from_the_job_lane(monkeypatch, caplog):
    from backend.worker import learning_loop

    class OnePass:
        stopped = False

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            assert seconds == 300
            self.stopped = True

    def fail():
        raise RuntimeError("Fixture training failure")

    monkeypatch.setattr(adaptive, "scheduled_pass", fail)
    learning_loop(OnePass())
    assert "Learning refresh needs attention" in caplog.text


def test_dataset_uses_actual_frozen_examples_once_and_preserves_text_boundary():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        first = feedback(s, o, source)
        promote(s, first)
        example = s.get(CompanyFeedback, first["id"])
        example.created_at = now() - timedelta(minutes=1)
        draft = saved_reply(
            s, o, source, {"examples": [{"id": example.id}, {"id": example.id}]}
        )
        target = feedback(
            s,
            o,
            source,
            outcome="accepted",
            original_text="Please provide sheet E-10.",
            human_response="Please provide sheet E-10.",
            generation={"reply_id": draft.id},
        )
        promote(s, target)
        rows, deps = adaptive.dataset(s)
        assert len(rows) == 1 and rows[0]["y"] == 1
        assert set(deps) == {first["id"], target["id"]}
        assert "Please" not in str(rows)
        result = adaptive.train_private(s)
        assert result["status"] == "collecting_examples"
        assert adaptive.train_private(s)["id"] == result["id"]
        memory.revoke_example(
            s, first["id"], "The example is no longer authorized for reuse."
        )
        assert not adaptive.dataset(s)[0]


def test_live_ranker_has_bounded_influence_and_never_bypasses_holdouts_or_scope():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        edited = feedback(s, o, source)
        accepted = feedback(
            s,
            o,
            source,
            outcome="accepted",
            original_text="Please provide sheet E-10.",
            human_response="Please provide sheet E-10.",
        )
        promote(s, edited)
        promote(s, accepted)
        model = artifact(
            s,
            {ORG_A: {"policy": 0, "feedback": {edited["id"]: edited["evidence_hash"]}}},
        )
        selected = query(s, o)
        assert selected["ranking_model"]["id"] == model.id
        assert selected["examples"][0]["id"] == edited["id"]
        f = s.get(CompanyFeedback, edited["id"])
        assert (
            8.5
            <= adaptive.adjusted_score(model, 10, "cable entry", [], f, o.id)
            <= 11.5
        )
        assert not query(s, o, exclude_order_ids=["another-held-out-order"])[
            "ranking_model"
        ]
        other, _, _ = order(s)
        assert not query(s, other)["examples"]
        memory.revoke_example(
            s, f.id, "Remove this correction from all future selection."
        )
        assert adaptive.active(s) is None
    with identity(ORG_B), Session.begin() as s:
        assert adaptive.active(s) is None
        with pytest.raises(HTTPException) as error:
            adaptive.rollback(s, model.id)
        assert error.value.status_code == 404


def test_rollback_and_policy_changes_immediately_restore_baseline():
    with identity(), Session.begin() as s:
        row = artifact(s, {ORG_A: {"policy": 0, "feedback": {}}})
        assert adaptive.active(s).id == row.id
        assert adaptive.rollback(s, row.id)["status"] == "rolled_back"
        assert adaptive.active(s) is None
        adaptive.update_policy(
            s,
            LearningPolicyWrite(
                private_training=False,
                reason="Admin disabled automatic example model training.",
            ),
        )
        assert adaptive.train_private(s)["status"] == "paused"
        assert not adaptive.status(s)["foundation_model_weights_updated"]
    with identity(role="org:member"), Session.begin() as s:
        with pytest.raises(HTTPException) as error:
            adaptive.update_policy(
                s,
                LearningPolicyWrite(
                    reason="This member cannot authorize shared model training."
                ),
            )
        assert error.value.status_code == 403


def test_shared_model_withdrawn_when_contributor_opts_out_or_revokes_source():
    with identity(), Session.begin() as s:
        o, _, source = order(s)
        f = feedback(s, o, source)
        promote(s, f)
        p = adaptive.update_policy(
            s,
            LearningPolicyWrite(
                share_features=True,
                reason="Administrator explicitly opted into numeric learning.",
            ),
        )
        artifact(
            s,
            {
                ORG_A: {
                    "policy": p["version"],
                    "feedback": {f["id"]: f["evidence_hash"]},
                }
            },
            scope="shared",
            owner=adaptive.SHARED_ORG,
        )
    with identity(ORG_B), Session.begin() as s:
        assert adaptive.active(s).scope == "shared"
    with identity(), Session.begin() as s:
        adaptive.update_policy(
            s,
            LearningPolicyWrite(
                share_features=False,
                reason="Administrator withdrew numeric learning consent.",
            ),
        )
    with identity(ORG_B), Session.begin() as s:
        assert adaptive.active(s) is None
