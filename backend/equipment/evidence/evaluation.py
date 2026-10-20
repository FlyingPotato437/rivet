"""Key-independent projection and separate accuracy/review diagnostics.

Answer keys enter only the scoring functions. They never create claims, fill a
cell, match an entity, select a competing reader or change a resolution.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from ..real_evaluation import normalized_text, score_predictions
from .ontology import FEATURES

LEGACY_PROJECTION_VERSION = "rivet.canonical-row-projection.v1"
SOURCE_SUPPORTED_PROJECTION_VERSION = "rivet.canonical-row-projection.v2"


def merge_frozen_predictions(predictions, *, package_id, source_hashes):
    result = {
        "schema_version": "rivet.real-predictions.v1",
        "package_id": package_id,
        "source_hashes": dict(source_hashes),
        "producer": "saved_frozen_reader_observations",
    }
    for category in ("comments", "devices", "bom_rows", "issues", "revision_checks"):
        rows = {}
        for prediction in predictions:
            for sid, digest in prediction.get("source_hashes", {}).items():
                if source_hashes.get(sid) != digest:
                    raise ValueError(
                        "Frozen prediction does not bind the package source hashes."
                    )
            values = (
                prediction.get("observed_bom_rows", prediction.get("bom_rows", []))
                if category == "bom_rows"
                else prediction.get(category, [])
            )
            for index, value in enumerate(values):
                original_id = value.get("id") or json.dumps(
                    [value.get("source_id"), value.get("page"), category, index],
                    sort_keys=True,
                )
                identity = [value.get("source_id"), value.get("page"), original_id]
                key = hashlib.sha256(
                    json.dumps(identity, sort_keys=True).encode()
                ).hexdigest()[:32]
                projected = {
                    **deepcopy(value),
                    "id": key,
                    "source_observation_id": original_id,
                }
                if key in rows and rows[key] != projected:
                    raise ValueError(
                        "Conflicting frozen prediction IDs must be evaluated as separate runs."
                    )
                rows[key] = projected
        result[category] = list(rows.values())
    return result


def _display(value, feature):
    # Scoring representation only: typed identifiers retain their original
    # spelling in the graph. Do not turn 01, 52-12 or BKR-12 into a different ID.
    if (
        feature in {"section_no", "circuit_no"}
        and isinstance(value, str)
        and re.fullmatch(r"0|[1-9][0-9]*", value)
    ):
        return int(value)
    return value


def _subject_ids(graph, item):
    sid = item.get("source_id")
    identities = {
        str(item.get(k))
        for k in ("id", "source_local_id", "source_observation_id")
        if item.get(k) is not None
    }
    found = {
        n["id"]
        for n in graph.get("nodes", [])
        if n.get("metadata", {}).get("source_id") == sid
        and n.get("metadata", {}).get("source_local_id") in identities
    }
    found |= {
        c["subject_id"]
        for c in graph.get("claims", [])
        if c.get("source", {}).get("source_id") == sid
        and str(c.get("source", {}).get("entity", "")) in identities
    }
    return found


def _decision(graph, subjects, feature, sid):
    claims = [
        c
        for c in graph.get("claims", [])
        if c.get("subject_id") in subjects
        and c.get("feature") == feature
        and c.get("source", {}).get("source_id") == sid
        and c.get("state", "observed") == "observed"
        and c.get("conditions", {}).get("side", "target") != "requirements"
    ]
    sides = {c.get("conditions", {}).get("side", "target") for c in claims}
    values = [
        r
        for r in graph.get("resolutions", [])
        if r.get("subject_id", r.get("entity_id")) in subjects
        and r.get("feature") == feature
        and r.get("state", "observed") == "observed"
        and r.get("side", "target") in sides
    ]
    strata = {
        json.dumps(
            [
                r.get("value"),
                r.get("unit"),
                r.get("side", "target"),
                r.get("conditions", {}),
            ],
            sort_keys=True,
        )
        for r in values
    }
    if len(strata) == 1:
        result = deepcopy(values[0])
        result["needs_review"] = any(v.get("needs_review", True) for v in values)
        result["projection_resolution_ids"] = [v.get("id") for v in values]
        return result
    return {
        "feature": feature,
        "value": None,
        "needs_review": True,
        "status": "ambiguous" if values else "unknown",
        "projection_resolution_ids": [v.get("id") for v in values],
        "explanation": [
            "No unique source-bound observed resolution; separate identities and condition strata are not merged by the scorer."
        ],
    }


def _projection_version(graph):
    metadata = graph.get("metadata", {})
    enabled = metadata.get("correspondence_policy") == "source-local-v2" or (
        metadata.get("source_local_correspondence", {}).get("version")
        == "rivet.source-local-correspondence.v2"
    )
    return SOURCE_SUPPORTED_PROJECTION_VERSION if enabled else LEGACY_PROJECTION_VERSION


def _source_supported_features(graph, subjects, sid, row_features):
    """Features already observed on this exact row, never keys or other rows.

    Source-only OCR may add a missing schema field only after a proven local
    correspondence projected its claim to this subject. The original frozen
    reader transcript is never changed, and candidate-only remains unaccepted.
    """
    def actual(claim):
        conditions = claim.get("conditions", {})
        return (
            claim.get("subject_id") in subjects
            and claim.get("source", {}).get("source_id") == sid
            and claim.get("state", "observed") == "observed"
            and conditions.get("side", "target") != "requirements"
            and conditions.get("role", conditions.get("source_role")) != "specification"
        )
    original = [c for c in graph.get("claims", []) if actual(c) and c.get("feature") in row_features
                and c.get("extractor", {}).get("name") != "source_local_correspondence_projection"]
    sides = {c.get("conditions", {}).get("side", "target") for c in original}
    links = {link["id"]: link for link in graph.get("identity_correspondence", [])}
    features = set()
    for claim in graph.get("claims", []):
        if not actual(claim) or claim.get("feature") not in FEATURES:
            continue
        if claim.get("conditions", {}).get("side", "target") not in sides:
            continue
        if claim.get("extractor", {}).get("name") == "source_local_correspondence_projection":
            link = links.get(claim.get("conditions", {}).get("source_local_correspondence_id"), {})
            if not (link.get("verified") is True and link.get("status") == "proven_source_local"
                    and link.get("scope_id") == graph.get("scope_id")
                    and link.get("source_id") == sid
                    and link.get("sha256") == claim.get("source", {}).get("sha256")
                    and link.get("page") == claim.get("source", {}).get("page")
                    and link.get("unresolved") == []):
                continue
        features.add(claim["feature"])
    return features


def canonical_predictions(graph, frozen, *, accepted_only=False):
    """Project stable reader row/device identities through reconciled decisions."""
    projected = deepcopy(frozen)
    projected["projection_version"] = _projection_version(graph)
    projected["producer"] = (
        "accepted_reconciled_values"
        if accepted_only
        else "reconciled_candidates_with_review_flags"
    )
    for row in projected.get("bom_rows", []):
        subjects = _subject_ids(graph, row)
        features = set(row.get("fields", {})) | {
            c.get("field") for c in row.get("cells", []) if c.get("field")
        }
        if projected["projection_version"] == SOURCE_SUPPORTED_PROJECTION_VERSION:
            features |= _source_supported_features(graph, subjects, row["source_id"], features)
        decisions, fields = {}, {}
        for feature in sorted(features):
            decision = _decision(graph, subjects, feature, row["source_id"])
            decisions[feature] = decision
            fields[feature] = (
                None
                if accepted_only and decision.get("needs_review", True)
                else _display(decision.get("value"), feature)
            )
        row["fields"], row["field_decisions"] = fields, decisions
        row["graph_subject_ids"] = sorted(subjects)
    devices = []
    for item in projected.get("devices", []):
        subjects = _subject_ids(graph, item)
        decision = _decision(graph, subjects, "device_tag", item["source_id"])
        value = decision.get("value")
        if value is not None and (
            not accepted_only or not decision.get("needs_review", True)
        ):
            item["tag"] = value
            item["tag_decision"] = decision
            devices.append(item)
    projected["devices"] = devices
    return projected


def table_report_prediction(report, *, package_id, source_hashes):
    """A source-only table body ordinal is separate from its measured grid row."""
    result = {
        "schema_version": "rivet.real-predictions.v1",
        "package_id": package_id,
        "source_hashes": dict(source_hashes),
        "producer": "source_only_ruled_table_ocr",
        "bom_rows": [],
        "comments": [],
        "devices": [],
        "issues": [],
        "revision_checks": [],
    }
    for row in report.get("rows", []):
        if not row.get("body_row"):
            raise ValueError(
                "Table evidence needs source-derived body_row; do not reuse a vision row ID."
            )
        result["bom_rows"].append(
            {
                **deepcopy(row),
                "id": row.get("source_local_id", row.get("subject_id")),
                "row": row["body_row"],
                "source_grid_row": row.get("grid_row"),
                "fields": {k: _display(v, k) for k, v in row.get("fields", {}).items()},
            }
        )
    return result


def _equal(actual, expected):
    return (
        actual == expected
        or isinstance(actual, str)
        and isinstance(expected, str)
        and normalized_text(actual) == normalized_text(expected)
    )


def _confidence(row, field):
    value = row.get("field_confidence", {}).get(field)
    if value is not None:
        return value
    return next(
        (
            c.get("confidence", c.get("value_confidence"))
            for c in row.get("cells", [])
            if c.get("field") == field
        ),
        None,
    )


def score_reconciliation(key, frozen, graph):
    candidates = canonical_predictions(graph, frozen)
    accepted = canonical_predictions(graph, frozen, accepted_only=True)
    before_score = score_predictions(key, frozen)
    candidate_score = score_predictions(key, candidates)
    accepted_score = score_predictions(key, accepted)
    labelled = (key.get("labels", {}).get("bom_rows") or {}).get("items", [])
    matched = candidate_score["extraction"]["bom_rows"]
    matched = (
        {m["reference_id"]: m["prediction_id"] for m in matched.get("matched", [])}
        if matched
        else {}
    )
    baseline_match = before_score["extraction"]["bom_rows"]
    baseline_match = (
        {
            m["reference_id"]: m["prediction_id"]
            for m in baseline_match.get("matched", [])
        }
        if baseline_match
        else {}
    )
    rows = {r.get("id"): r for r in candidates.get("bom_rows", [])}
    raw_rows = {r.get("id"): r for r in frozen.get("bom_rows", [])}
    cells = []
    for reference in labelled:
        row = rows.get(matched.get(reference["id"]), {})
        raw = raw_rows.get(baseline_match.get(reference["id"]), {})
        for field, expected in reference.get("fields", {}).items():
            decision = row.get("field_decisions", {}).get(field, {})
            actual = row.get("fields", {}).get(field)
            before = raw.get("fields", {}).get(field)
            review = actual is None or decision.get("needs_review", True)
            cells.append(
                {
                    "reference_id": reference["id"],
                    "field": field,
                    "expected": expected,
                    "before": before,
                    "candidate": actual,
                    "candidate_correct": actual is not None
                    and _equal(actual, expected),
                    "needs_review": review,
                    "accepted": actual is not None and not review,
                    "wrong_value_unflagged": actual is not None
                    and not _equal(actual, expected)
                    and not review,
                    "baseline_raw_confidence": _confidence(raw, field),
                    "baseline_high_confidence_wrong": before is not None
                    and not _equal(before, expected)
                    and _confidence(raw, field) == "high",
                    "resolution": decision,
                }
            )
    accepted_cells = [c for c in cells if c["accepted"]]
    review_count = sum(c["needs_review"] for c in cells)
    return {
        "schema_version": "rivet.reconciled-equipment-score.v1",
        "projection_version": candidates["projection_version"],
        "package_id": frozen["package_id"],
        "before": before_score,
        "candidate": candidate_score,
        "accepted_only": accepted_score,
        "labelled_cell_policy": {
            "labelled_values": len(cells),
            "needs_review": review_count,
            "review_fraction": review_count / len(cells) if cells else None,
            "accepted_values": len(accepted_cells),
            "accepted_value_accuracy": sum(
                c["candidate_correct"] for c in accepted_cells
            )
            / len(accepted_cells)
            if accepted_cells
            else None,
            "wrong_values_unflagged": sum(c["wrong_value_unflagged"] for c in cells),
            "baseline_raw_high_confidence_wrong": sum(
                c["baseline_high_confidence_wrong"] for c in cells
            ),
            "note": "Zero unflagged errors with no accepted values means review protection, not solved extraction.",
        },
        "all_resolution_policy": {
            "resolutions": len(graph.get("resolutions", [])),
            "needs_review": sum(
                r.get("needs_review", True) for r in graph.get("resolutions", [])
            ),
            "review_fraction": sum(
                r.get("needs_review", True) for r in graph.get("resolutions", [])
            )
            / len(graph.get("resolutions", []))
            if graph.get("resolutions")
            else None,
        },
        "cells": cells,
        "projection_limits": [
            "Saved row identity is retained; no key-derived fill, reader cherry-picking, or unproven row merge.",
            "Candidate accuracy includes flagged alternatives; accepted accuracy includes only values released from attribute review.",
            "Digit-only section/circuit identifiers use consistent numeric scoring representation; original graph spelling is preserved.",
        ],
    }


def calibration_examples(score, *, split, package_ancestry):
    if split != "development":
        raise ValueError("Only development labels can generate calibration examples.")
    if not package_ancestry:
        raise ValueError(
            "Original-package ancestry is required for reliability calibration."
        )
    return [
        {
            "resolution": c["resolution"],
            "correct": c["candidate_correct"],
            "package_ancestry": package_ancestry,
            "split": "development",
        }
        for c in score.get("cells", [])
        if c.get("candidate") is not None and c.get("resolution", {}).get("id")
    ]
