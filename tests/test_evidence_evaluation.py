"""Synthetic scoring controls; none contribute to real-file accuracy."""

from copy import deepcopy

import pytest

from backend.equipment.evidence.adapters import from_visual_predictions
from backend.equipment.evidence.calibration import fit_development
from backend.equipment.evidence.evaluation import (
    calibration_examples,
    canonical_predictions,
    merge_frozen_predictions,
    score_reconciliation,
    table_report_prediction,
)
from backend.equipment.evidence.reconcile import reconcile_graph


@pytest.fixture(autouse=True)
def database():
    """Pure graph/scoring controls never rebuild a shared test database."""
    yield


def scenario():
    digest = "1" * 64
    prediction = {
        "schema_version": "rivet.visual-equipment.v4",
        "source_hashes": {"s": digest},
        "observed_bom_rows": [
            {
                "id": "row1",
                "source_id": "s",
                "sha256": digest,
                "page": 1,
                "row": 1,
                "table": 1,
                "bbox": [0.1, 0.1, 0.4, 0.2],
                "fields": {"trip_a": 60},
                "field_confidence": {"trip_a": "high"},
                "cells": [
                    {
                        "field": "trip_a",
                        "value": 60,
                        "raw_text": "60A",
                        "confidence": "high",
                        "column": 1,
                    }
                ],
            }
        ],
    }
    frozen = merge_frozen_predictions(
        [prediction], package_id="control", source_hashes={"s": digest}
    )
    graph = reconcile_graph(
        from_visual_predictions(prediction, scope_id="test-control")
    )
    key = {
        "schema_version": "rivet.real-answer-key.v1",
        "package_id": "control",
        "source_hashes": {"s": digest},
        "review": {
            "reviewer": "Synthetic control author",
            "method": "assistant_visual_review",
            "reviewed_at": "2026-10-18T00:00:00+00:00",
            "engineering_reviewed": False,
        },
        "labels": {
            "bom_rows": {
                "scope": "complete",
                "source_ids": ["s"],
                "items": [
                    {
                        "id": "gold1",
                        "source_id": "s",
                        "page": 1,
                        "row": 1,
                        "fields": {"trip_a": 600},
                    }
                ],
            }
        },
    }
    return prediction, frozen, graph, key


def test_flagging_every_value_is_not_reported_as_solved_extraction():
    _, frozen, graph, key = scenario()
    result = score_reconciliation(key, frozen, graph)
    policy = result["labelled_cell_policy"]
    assert result["candidate"]["extraction"]["bom_rows"]["field_values"]["wrong"] == 1
    assert policy["wrong_values_unflagged"] == 0
    assert policy["review_fraction"] == 1
    assert policy["accepted_values"] == 0
    assert policy["accepted_value_accuracy"] is None
    assert policy["baseline_raw_high_confidence_wrong"] == 1


def test_scoring_detects_a_wrong_value_that_bypasses_review():
    _, frozen, graph, key = scenario()
    graph["resolutions"][0]["needs_review"] = False
    result = score_reconciliation(key, frozen, graph)
    assert result["labelled_cell_policy"]["wrong_values_unflagged"] == 1
    assert result["labelled_cell_policy"]["accepted_value_accuracy"] == 0


def test_projection_never_fills_a_missing_field_from_an_unrelated_entity():
    _, frozen, graph, _ = scenario()
    untouched = deepcopy(frozen)
    graph["resolutions"][0]["subject_id"] = "different-node"
    projected = canonical_predictions(graph, frozen)
    assert projected["bom_rows"][0]["fields"]["trip_a"] is None
    assert frozen == untouched


def test_original_package_calibration_and_no_heldout_training():
    _, frozen, graph, key = scenario()
    score = score_reconciliation(key, frozen, graph)
    with pytest.raises(ValueError, match="Only development"):
        calibration_examples(score, split="unseen_test", package_ancestry="parent-pdf")
    examples = calibration_examples(
        score, split="development", package_ancestry="parent-pdf"
    )
    fitted = fit_development(examples + examples)
    assert fitted["buckets"]
    assert all(b["independent_package_count"] == 1 for b in fitted["buckets"].values())
    assert all(
        b["package_success_lower_bound"] < 0.99 for b in fitted["buckets"].values()
    )


def test_conflicting_frozen_runs_cannot_be_cherry_picked_or_merged():
    prediction, _, _, _ = scenario()
    conflict = deepcopy(prediction)
    conflict["observed_bom_rows"][0]["fields"]["trip_a"] = 600
    with pytest.raises(ValueError, match="separate runs"):
        merge_frozen_predictions(
            [prediction, conflict],
            package_id="control",
            source_hashes=prediction["source_hashes"],
        )


def test_source_table_body_ordinal_keeps_measured_grid_provenance():
    report = {
        "rows": [
            {
                "source_id": "s",
                "source_local_id": "grid-row-3",
                "subject_id": "node1",
                "page": 1,
                "grid_row": 3,
                "body_row": 1,
                "fields": {"circuit_no": "01", "section_no": "2"},
            }
        ]
    }
    output = table_report_prediction(
        report, package_id="control", source_hashes={"s": "1" * 64}
    )
    row = output["bom_rows"][0]
    assert row["row"] == 1 and row["source_grid_row"] == 3
    assert row["fields"] == {"circuit_no": "01", "section_no": 2}
