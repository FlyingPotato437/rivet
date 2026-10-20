"""Train and use a small example ranker from observed, approved reply outcomes.

The label is sent unchanged versus edited, NOT engineering accuracy. Only numeric
overlap features enter training. Model scores can reorder already eligible
examples; they cannot waive evidence, tenant, scope or sending checks.
"""

import math
from collections import Counter

from sqlalchemy import select, text

from backend.company_context import service as memory
from backend.company_context.models import CompanyFeedback, OrderCustomerBinding
from backend.domain.service import digest, get, scoped
from backend.identity import Identity, current_actor, current_org, identity_scope
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session

from .learning import reusable_correction
from .models import ExampleRanker, LearningPolicy

SHARED_ORG = "a9e8140c-1084-42a3-890e-080e00d05e09"
FEATURES = [
    "bias",
    "query_overlap",
    "topic_overlap",
    "same_order",
    "corrected",
    "length",
]
VERSION = "rivet-example-ranker-v1"


def policy(s):
    row = s.scalar(scoped(LearningPolicy))
    return {
        "private_training": row.private_training if row else True,
        "share_features": row.share_features if row else False,
        "version": row.version if row else 0,
    }


def update_policy(s, body):
    memory.require_admin()
    s.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int(digest([current_org(), "learning-policy"])[:15], 16)},
    )
    row = s.scalar(scoped(LearningPolicy).with_for_update())
    if row is None:
        row = LearningPolicy()
        s.add(row)
        s.flush()
    row.private_training = body.private_training
    row.share_features = body.share_features
    row.version += 1
    row.actor, row.reason = current_actor(), body.reason
    # A permission change invalidates previous models immediately, including
    # numeric shared artifacts contributed under the old consent version.
    s.flush()
    return policy(s)


def features(query, topics, example, *, same_order=False):
    q = memory._tokens(query)
    e = memory._tokens(example.input_text + " " + " ".join(example.topics))
    return [
        1.0,
        len(q & e) / max(1, len(q)),
        min(1.0, len(set(topics) & set(example.topics))),
        float(same_order),
        float(example.outcome == "edited"),
        min(1.0, len(example.human_response.split()) / 300),
    ]


def _probability(weights, x):
    z = max(-30, min(30, sum(a * b for a, b in zip(weights, x))))
    return 1 / (1 + math.exp(-z))


def fit(rows):
    """Whole customer/order groups are held out; no text is accepted by the fitter."""
    episodes = {r["episode"] for r in rows}
    groups = {r["group"] for r in rows}
    report = {
        "episodes": len(episodes),
        "groups": len(groups),
        "feature_rows": len(rows),
        "label": "sent_unchanged_vs_edited",
        "engineering_accuracy": "not_measured",
        "features": FEATURES,
        "minimum_episodes": 40,
    }
    train = [r for r in rows if int(digest(r["group"])[:8], 16) % 5 != 0]
    test = [r for r in rows if int(digest(r["group"])[:8], 16) % 5 == 0]
    report.update(
        training_episodes=len({r["episode"] for r in train}),
        heldout_episodes=len({r["episode"] for r in test}),
        heldout_groups=len({r["group"] for r in test}),
    )
    if (
        len(episodes) < 40
        or len(groups) < 6
        or report["training_episodes"] < 25
        or report["heldout_episodes"] < 10
        or report["heldout_groups"] < 2
        or {r["y"] for r in train} != {0, 1}
        or {r["y"] for r in test} != {0, 1}
    ):
        return [], {**report, "status": "collecting_examples"}
    # Equal episode weights prevent a reply with four examples counting as four
    # independent approvals. Duplicate feature/episode rows are removed upstream.
    counts = Counter(r["episode"] for r in train)
    weights = [0.0] * len(FEATURES)
    total = sum(1 / counts[r["episode"]] for r in train)
    for _ in range(350):
        gradient = [0.0] * len(weights)
        for row in train:
            err = (_probability(weights, row["x"]) - row["y"]) / counts[row["episode"]]
            for j, value in enumerate(row["x"]):
                gradient[j] += err * value
        weights = [
            w - 0.5 * (g / total + (0.015 * w if j else 0))
            for j, (w, g) in enumerate(zip(weights, gradient))
        ]
    mean = sum(r["y"] / counts[r["episode"]] for r in train) / total
    test_counts = Counter(r["episode"] for r in test)

    def brier(predict):
        return sum(
            (predict(r) - r["y"]) ** 2 / test_counts[r["episode"]] for r in test
        ) / len(test_counts)

    baseline = brier(lambda r: mean)
    candidate = brier(lambda r: _probability(weights, r["x"]))
    passes = baseline - candidate >= 0.01
    return weights, {
        **report,
        "baseline_brier": baseline,
        "candidate_brier": candidate,
        "status": "active" if passes else "not_improved",
    }


def _eligible(s, row):
    return (
        row.reuse_status == "approved"
        and row.reuse_purpose == "retrieval"
        and row.outcome in {"accepted", "edited"}
        and memory._fresh(s, row)
        and reusable_correction(row)
    )


def dataset(s, *, shared=False):
    """Approved snapshots to numeric features, without copying private source text."""
    feedback = list(
        s.scalars(
            scoped(CompanyFeedback)
            .order_by(CompanyFeedback.created_at.desc())
            .limit(400)
        )
    )
    eligible = {f.id: f for f in feedback if _eligible(s, f)}
    bindings = {
        b.order_id: b.customer_id
        for b in s.scalars(scoped(OrderCustomerBinding))
        if b.customer_id
    }
    rows, dependencies, seen = [], {}, set()
    for target in eligible.values():
        draft = s.scalar(
            scoped(ReplyDraft).where(ReplyDraft.id == target.generation.get("reply_id"))
        )
        if not draft or draft.order_id != target.order_id:
            continue
        query = draft.context.get("instruction", "")
        for item in draft.context.get("company", {}).get("examples", []):
            example = eligible.get(item.get("id"))
            if not example or example.created_at >= target.created_at:
                continue
            if example.reuse_scope == "order" and example.order_id != target.order_id:
                continue
            x = features(
                query,
                target.topics,
                example,
                same_order=example.order_id == target.order_id,
            )
            key = (target.id, tuple(x))
            if key in seen:
                continue
            seen.add(key)
            group = (
                current_org()
                if shared
                else bindings.get(target.order_id, target.order_id)
            )
            rows.append(
                {
                    "x": x,
                    "y": int(target.outcome == "accepted"),
                    "episode": digest([current_org(), target.id]),
                    "group": digest(group),
                }
            )
            for f in (target, example):
                dependencies[f.id] = f.evidence_hash
    return rows, dependencies


def train_private(s):
    if not policy(s)["private_training"]:
        return {"status": "paused"}
    rows, deps = dataset(s)
    return _save(
        s,
        rows,
        {current_org(): {"feedback": deps, "policy": policy(s)["version"]}},
        "private",
    )


def _save(s, rows, deps, scope, *, owner=None):
    owner = owner or current_org()
    s.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int(digest([owner, scope, VERSION])[:15], 16)},
    )
    fingerprint = digest([VERSION, rows, deps])
    existing = s.scalar(
        select(ExampleRanker).where(
            ExampleRanker.organization_id == owner,
            ExampleRanker.scope == scope,
            ExampleRanker.dataset_hash == fingerprint,
        )
    )
    if existing:
        return view(existing)
    weights, report = fit(rows)
    if scope == "shared" and len(deps) < 3:
        report["status"] = "collecting_examples"
    row = ExampleRanker(
        organization_id=owner,
        scope=scope,
        dataset_hash=fingerprint,
        status=report["status"],
        weights=weights,
        report=report,
        dependencies=deps,
    )
    s.add(row)
    s.flush()
    return view(row)


def view(row):
    return {
        "id": row.id,
        "scope": row.scope,
        "status": row.status,
        "report": row.report,
        "trained_at": row.created_at.isoformat(),
        "version": VERSION,
    }


def _valid(s, row):
    for org, dep in row.dependencies.items():
        p = s.scalar(
            select(LearningPolicy).where(LearningPolicy.organization_id == org)
        )
        if (p.version if p else 0) != dep["policy"]:
            return False
        if row.scope == "shared" and (not p or not p.share_features):
            return False
        if row.scope in {"private", "shared"}:
            with identity_scope(Identity(org, "Rivet learning validation")):
                for f_id, evidence_hash in dep["feedback"].items():
                    f = s.scalar(
                        scoped(CompanyFeedback).where(CompanyFeedback.id == f_id)
                    )
                    if not f or f.evidence_hash != evidence_hash or not _eligible(s, f):
                        return False
    return True


def active(s):
    if not policy(s)["private_training"]:
        return None
    for owner, scope in [(current_org(), "private"), (SHARED_ORG, "shared")]:
        row = s.scalar(
            select(ExampleRanker)
            .where(ExampleRanker.organization_id == owner, ExampleRanker.scope == scope)
            .order_by(ExampleRanker.created_at.desc(), ExampleRanker.id.desc())
        )
        # A rollback or failed replacement is not silently undone by selecting
        # an older artifact. The deterministic baseline is always available.
        if row and row.status == "active" and _valid(s, row):
            return row
    return None


def adjusted_score(model, base, query, topics, example, order_id):
    if not model:
        return base
    p = _probability(
        model.weights,
        features(query, topics, example, same_order=example.order_id == order_id),
    )
    return base * (
        0.85 + 0.30 * p
    )  # Bounded ranking change; never changes eligibility.


def status(s):
    memory.require_admin()
    latest = s.scalar(
        scoped(ExampleRanker)
        .where(ExampleRanker.scope == "private")
        .order_by(ExampleRanker.created_at.desc(), ExampleRanker.id.desc())
    )
    selected = active(s)
    return {
        "policy": policy(s),
        "latest": view(latest) if latest else None,
        "active": view(selected) if selected else None,
        "shared_training": "numeric_features_only_with_explicit_opt_in",
        "model_kind": "example_ranker",
        "foundation_model_weights_updated": False,
    }


def rollback(s, model_id):
    memory.require_admin()
    row = get(s, ExampleRanker, model_id, True)
    if row.scope != "private":
        memory.fail("Shared model release is not owned by this workspace.", 403)
    row.status = "rolled_back"
    s.flush()
    return view(row)


def scheduled_pass():
    """Run in the existing worker; all provider text stays in its own tenant."""
    with Session() as s:
        orgs = list(s.scalars(select(CompanyFeedback.organization_id).distinct()))
    shared_rows, shared_deps = [], {}
    for org in orgs:
        with identity_scope(Identity(org, "Rivet learning")), Session.begin() as s:
            train_private(s)
            p = policy(s)
            if p["share_features"]:
                rows, deps = dataset(s, shared=True)
                if rows:
                    shared_rows.extend(rows)
                    shared_deps[org] = {"policy": p["version"], "feedback": deps}
    if shared_deps:
        with Session.begin() as s:
            _save(s, shared_rows, shared_deps, "shared", owner=SHARED_ORG)
