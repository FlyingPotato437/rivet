"""Conformance controls from visual_reader._observations; no real-file labels."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.equipment.visual_reader import _observations
from backend.equipment.evidence.adapters import from_visual_predictions
from backend.equipment.evidence.reconcile import reconcile_graph


@pytest.fixture(autouse=True)
def database():
    yield


def _producer(frame="HL", trip="30A", np="No", *, frame_confidence="high"):
    source = SimpleNamespace(id="schedule", name="control.pdf", role="bom", revision="B", sha256="a" * 64)
    transcript = {"tables": [{
        "headers": [{"text": "DEVICE/FRAME RATING", "field": "device_frame"},
                    {"text": "TRIP AMP", "field": "trip_a"}, {"text": "N/P", "field": "position_state"}],
        "rows": [{"bbox": [.1, .2, .8, .3], "cells": [
            {"column": 1, "text": frame, "confidence": frame_confidence, "bbox": [.1, .2, .3, .3]},
            {"column": 2, "text": trip, "confidence": "high", "bbox": [.3, .2, .5, .3]},
            {"column": 3, "text": np, "confidence": "high", "bbox": [.5, .2, .8, .3]},
        ]}],
    }]}
    rows, devices = _observations(source, 1, transcript)
    return {"schema_version": "rivet.visual-equipment.v4", "producer": {"name": "visual_reader", "version": "v4"},
            "source_hashes": {source.id: source.sha256}, "observed_bom_rows": rows, "devices": devices}


def _graph(prediction, **kwargs):
    return from_visual_predictions(prediction, scope_id="order:synthetic", **kwargs)


def _interpretations(graph):
    return [c for c in graph["claims"] if c["extractor"]["name"] == "visual_position_state_interpretation"]


@pytest.mark.parametrize("frame,trip,state,parent_features", [
    ("HL", "30A", "populated_breaker", {"device_frame", "trip_a"}),
    ("HL (PS)", "(100A)", "prepared_space", {"device_frame"}),
    ("Incoming Connection", "-", "incoming_connection", {"device_frame"}),
])
def test_raw_np_no_survives_and_actual_producer_classification_gets_sourced_inference(frame, trip, state, parent_features):
    prediction = _producer(frame, trip)
    assert prediction["observed_bom_rows"][0]["fields"]["position_state"] == state
    before = deepcopy(prediction)
    graph = _graph(prediction)
    raw = next(c for c in graph["claims"] if c["feature"] == "position_state" and c["raw_value"] == "No")
    assert raw["value"] is None and not raw["normalization"]["valid"]
    derived, = _interpretations(graph)
    assert derived["value"] == state
    parents = {c["id"]: c for c in graph["claims"]}
    assert {parents[p]["feature"] for p in derived["derived_from"]} == parent_features
    assert all(parents[p]["source"]["sha256"] == "a" * 64 and parents[p]["source"]["page"] == 1 for p in derived["derived_from"])
    assert {parents[p]["independence_group"] for p in derived["derived_from"]} == {derived["independence_group"]}
    assert derived["conditions"]["candidate_only"] and derived["conditions"]["inferred"]
    assert derived["conditions"]["model_confidence_is_authority"] is False
    assert derived["conditions"]["physical_population_verified"] is False
    assert {p["raw_text"] for p in derived["raw_value"]["source_cells"]} == ({frame, trip} if state == "populated_breaker" else {frame})
    assert prediction == before
    result = reconcile_graph(graph)
    decision = next(r for r in result["resolutions"] if r["feature"] == "position_state")
    assert decision["value"] == state and decision["needs_review"]
    assert decision["status"] == "provisional"
    assert decision["support"]["independent_group_count"] == 1
    assert decision["support"]["literal_source"] is False
    assert not decision["engineering_approved"] and not decision["production_released"]


def test_no_alone_is_not_population_evidence():
    prediction = _producer("", "-")
    assert prediction["observed_bom_rows"][0]["fields"]["position_state"] == "No"
    graph = _graph(prediction)
    assert all(c["value"] is None for c in graph["claims"] if c["feature"] == "position_state")
    assert not _interpretations(graph)
    assert next(r for r in reconcile_graph(graph)["resolutions"] if r["feature"] == "position_state")["value"] is None


@pytest.mark.parametrize("kind", ["missing", "low_confidence", "wrong_derived_value", "inconsistent_row_field"])
def test_unsupported_producer_classification_remains_unknown_with_raw_assertion(kind):
    prediction = _producer("", "-") if kind == "missing" else _producer()
    row = prediction["observed_bom_rows"][0]
    row["fields"]["position_state"] = "populated_breaker"
    if kind == "low_confidence":
        row["cells"][0].update(value=None, confidence="low", value_confidence="unknown")
    elif kind == "wrong_derived_value": row["fields"]["position_state"] = "prepared_space"
    elif kind == "inconsistent_row_field": row["fields"]["trip_a"] = 300
    graph = _graph(prediction)
    claim, = _interpretations(graph)
    assert claim["value"] is None
    assert claim["raw_value"]["producer_value"] == row["fields"]["position_state"]
    assert claim["conditions"]["candidate_only"] and claim["conditions"]["inferred"]
    assert next(r for r in reconcile_graph(graph)["resolutions"] if r["feature"] == "position_state")["value"] is None


def test_direct_conflicting_state_is_retained_alongside_inferred_state():
    prediction = _producer(np="prepared_space")
    assert prediction["observed_bom_rows"][0]["fields"]["position_state"] == "populated_breaker"
    graph = _graph(prediction)
    assert {c["value"] for c in graph["claims"] if c["feature"] == "position_state"} == {"prepared_space", "populated_breaker"}
    decision = next(r for r in reconcile_graph(graph)["resolutions"] if r["feature"] == "position_state")
    assert decision["value"] is None and decision["status"] == "conflict" and decision["needs_review"]


def test_original_page_derivation_and_all_parents_share_one_ancestry():
    graph = _graph(_producer(), source_context={"schedule": {"side": "target", "role": "bom",
                  "original_sha256": "b" * 64, "page_map": {"1": 246}}})
    claim, = _interpretations(graph)
    parents = {c["id"]: c for c in graph["claims"]}
    assert {parents[p]["independence_group"] for p in claim["derived_from"]} == {claim["independence_group"]}
    assert len({c["independence_group"] for c in graph["claims"]}) == 1
