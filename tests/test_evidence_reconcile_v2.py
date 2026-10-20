"""Version-two synthetic controls; no heldout sources, keys, or predictions."""

import ast
import inspect
import json
from copy import deepcopy
from dataclasses import replace
from typing import get_args

import pytest

from backend.equipment.evidence.adapters import adapt_sources
from backend.equipment.evidence.ontology import (
    FEATURES,
    normalize_feature_value,
)
from backend.equipment.evidence.reconcile import POLICY, reconcile_graph
from backend.equipment.evidence.schema import make_claim, new_graph
from backend.equipment.visual_reader import (
    Confidence,
    FieldName,
    _observations,
    _scalar,
)


@pytest.fixture(autouse=True)
def database():
    """These mathematical/producer contract controls never reset a database."""
    yield


def add(graph, feature, value, score, *, side="target", state="observed", subject="d"):
    if subject not in {n["id"] for n in graph["nodes"]}:
        graph["nodes"].append(
            {
                "id": subject,
                "kind": "device",
                "scope_key": graph["scope_id"],
                "metadata": {},
            }
        )
    # A literal source family weighs four; an independent visual family one.
    names = ["native_literal"] * (score // 4) + ["vision_control"] * (score % 4)
    for name in names:
        graph["claims"].append(
            make_claim(
                subject,
                feature,
                value,
                source={
                    "id": "synthetic",
                    "sha256": "a" * 64,
                    "page": len(graph["claims"]) + 1,
                },
                extractor={"name": name},
                unit=FEATURES[feature].unit if feature in FEATURES else None,
                conditions={"side": side},
                state=state,
            )
        )


def domain(trips, frames):
    graph = new_graph("joint-control")
    for feature, values in (("trip_a", trips), ("frame_a", frames)):
        for value, score in values:
            add(graph, feature, value, score)
    return graph


def values(result, *, subject="d", side="target", state="observed"):
    return {
        r["feature"]: r
        for r in result["resolutions"]
        if r["subject_id"] == subject and r["side"] == side and r["state"] == state
    }


def test_joint_selection_never_combines_separately_supported_infeasible_maxima():
    graph = domain([(600, 8), (100, 1)], [(400, 4), (800, 1)])
    original = deepcopy(graph)
    result = reconcile_graph(graph)
    selected = values(result)
    assert (selected["trip_a"]["value"], selected["frame_a"]["value"]) == (600, 800)
    joint = result["joint_constraints"][0]
    assert joint["evaluated_pair_count"] == 4
    assert joint["feasible_pair_count"] == 3
    assert joint["maximum_support_sum"] == 9
    assert joint["maximum_pair_count"] == 1
    assert all(
        r["needs_review"] and r["conflicting_claim_ids"] for r in selected.values()
    )
    assert graph == original and result["claims"] == original["claims"]
    assert result["reconciliation"]["source_claims_preserved"]


def test_joint_selection_can_choose_lower_supported_trip_for_stronger_feasible_frame():
    result = reconcile_graph(domain([(600, 8), (100, 4)], [(400, 8), (800, 1)]))
    selected = values(result)
    assert (selected["trip_a"]["value"], selected["frame_a"]["value"]) == (100, 400)
    assert result["joint_constraints"][0]["maximum_support_sum"] == 12


def test_joint_maximum_tie_leaves_both_attributes_unknown():
    # 600+800 support 8+1 == 100+400 support 5+4.
    result = reconcile_graph(domain([(600, 8), (100, 5)], [(400, 4), (800, 1)]))
    assert all(
        r["value"] is None and r["needs_review"] for r in values(result).values()
    )
    assert result["joint_constraints"][0]["status"] == "tied_feasible_pairs"
    assert result["joint_constraints"][0]["maximum_pair_count"] == 2
    assert all(
        not r["supporting_claim_ids"] and r["conflicting_claim_ids"]
        for r in values(result).values()
    )


def test_pair_tie_does_not_claim_a_complete_selection_even_with_one_shared_component():
    result = reconcile_graph(domain([(100, 4)], [(400, 4), (800, 4)]))
    assert all(r["value"] is None for r in values(result).values())
    assert result["joint_constraints"][0]["maximum_pair_count"] == 2


def test_no_feasible_pair_preserves_observations_and_returns_unknown():
    graph = domain([(600, 4), (700, 1)], [(100, 4), (200, 1)])
    result = reconcile_graph(graph)
    assert all(
        r["value"] is None and r["needs_review"] for r in values(result).values()
    )
    assert result["joint_constraints"][0]["status"] == "no_feasible_pair"
    assert result["joint_constraints"][0]["feasible_pair_count"] == 0
    assert all(
        "trip_exceeds_observed_frame" in r["constraint_failures"]
        for r in values(result).values()
    )
    assert result["claims"] == graph["claims"]


def test_joint_selection_never_borrows_another_device_snapshot_or_lifecycle():
    graph = domain([(600, 8)], [(400, 4)])
    add(graph, "frame_a", 800, 8, subject="other-device")
    add(graph, "frame_a", 800, 8, side="baseline")
    add(graph, "frame_a", 800, 8, side="requirements")
    add(graph, "frame_a", 800, 8, state="proposed")
    result = reconcile_graph(graph)
    assert values(result)["trip_a"]["value"] is None
    assert values(result)["frame_a"]["value"] is None
    assert len(result["joint_constraints"]) == 1


def test_an_absent_attribute_is_not_manufactured_by_joint_constraint():
    result = reconcile_graph(domain([(100, 4)], []))
    assert values(result)["trip_a"]["value"] == 100
    assert "frame_a" not in values(result)
    assert not result["joint_constraints"]


def test_invalid_numeric_opposite_is_not_used_to_prune_literal_trip():
    graph = domain([(600, 4)], [])
    add(graph, "frame_a", -5, 4)
    result = reconcile_graph(graph)
    assert values(result)["trip_a"]["value"] == 600
    assert values(result)["frame_a"]["value"] is None
    assert not result["joint_constraints"]


def test_all_32_by_32_pairs_are_considered_without_expanding_the_domain():
    result = reconcile_graph(
        domain([(v, 1) for v in range(1, 33)], [(v, 1) for v in range(1, 33)])
    )
    joint = result["joint_constraints"][0]
    assert joint["evaluated_pair_count"] == POLICY["maximum_trip_frame_pairs"] == 1024
    assert joint["feasible_pair_count"] == 528
    assert joint["maximum_pair_count"] == 528
    assert all(r["value"] is None for r in values(result).values())
    json.dumps(result, allow_nan=False)
    with pytest.raises(ValueError, match="32"):
        reconcile_graph(domain([(v, 1) for v in range(1, 34)], [(100, 1)]))


def test_joint_selection_is_independent_of_claim_order():
    graph = domain([(600, 8), (100, 1)], [(400, 4), (800, 1)])
    forward = values(reconcile_graph(graph))
    graph["claims"].reverse()
    backward = values(reconcile_graph(graph))
    assert {k: r["value"] for k, r in forward.items()} == {
        k: r["value"] for k, r in backward.items()
    }


ENUM_CASES = [
    (field, value) for field, spec in FEATURES.items() for value in spec.allowed_values
]


@pytest.mark.parametrize(("field", "literal"), ENUM_CASES)
def test_every_application_schema_enum_has_explicit_normalization(field, literal):
    variants = {
        literal,
        literal.swapcase(),
        literal.upper(),
        literal.replace("_", "-"),
        literal.replace("_", " "),
        literal.upper().replace("_", " / "),
    }
    for variant in variants:
        normalized = normalize_feature_value(field, variant)
        if literal == "unknown":
            assert normalized["value"] is None and not normalized["valid"]
        else:
            assert normalized["valid"] and normalized["value"] == literal


def test_enum_separator_matching_refuses_ambiguous_canonical_spellings(monkeypatch):
    monkeypatch.setitem(
        FEATURES,
        "position_state",
        replace(
            FEATURES["position_state"],
            allowed_values=("prepared_space", "prepared-space"),
        ),
    )
    result = normalize_feature_value("position_state", "PREPARED SPACE")
    assert result["value"] is None and not result["valid"]
    assert result["reason"] == "ambiguous_enum_spelling"


def test_canonical_enum_spelling_retains_original_raw_text_in_direct_graph():
    graph = new_graph("enum-control")
    add(graph, "position_state", "POPULATED-BREAKER", 4)
    result = reconcile_graph(graph)
    assert values(result)["position_state"]["value"] == "populated_breaker"
    assert (
        result["claims"][0]["value"]
        == result["claims"][0]["raw_value"]
        == "POPULATED-BREAKER"
    )


def test_every_hardcoded_visual_position_state_conforms_without_reading_predictions():
    # Derive the vocabulary from producer code, never from evaluation output.
    tree = ast.parse(inspect.getsource(_observations))
    emitted = {
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "fields"
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "position_state"
            for target in node.targets
        )
    }
    assert emitted == {"prepared_space", "incoming_connection", "populated_breaker"}
    for literal in emitted:
        normalized = normalize_feature_value("position_state", literal)
        assert normalized["valid"]
        assert normalized["value"] in FEATURES["position_state"].allowed_values


@pytest.mark.parametrize("literal", ["populated_breaker", "populated"])
def test_legacy_producer_state_keeps_its_canonical_spelling_and_source_literal(literal):
    graph = new_graph("enum-control")
    add(graph, "position_state", literal, 4)
    result = reconcile_graph(graph)
    decision = values(result)["position_state"]
    assert decision["value"] == literal and not decision["conflicting_claim_ids"]
    assert result["claims"] == graph["claims"]
    assert result["claims"][0]["raw_value"] == literal


def test_visual_schema_fields_and_confidences_remain_compatible():
    assert set(get_args(FieldName)) <= FEATURES.keys()
    for confidence in get_args(Confidence):
        value = _scalar("position_state", "populated_breaker", confidence)
        assert (
            (value == "populated_breaker")
            if confidence in {"high", "medium"}
            else value is None
        )


def test_adapter_retains_producer_state_text_while_emitting_canonical_value():
    prediction = {
        "producer": {"name": "visual_equipment", "version": "control"},
        "observed_bom_rows": [
            {
                "id": "row",
                "source_id": "scan",
                "page": 1,
                "table": 1,
                "row": 1,
                "bbox": [0.1, 0.1, 0.9, 0.2],
                "fields": {"position_state": "populated_breaker"},
                "cells": [],
                "field_confidence": {"position_state": "medium"},
            }
        ],
    }
    graph = adapt_sources(
        sources=[{"id": "scan", "sha256": "a" * 64}],
        visual_predictions=prediction,
        scope_id="enum-control",
    )
    claim = graph["claims"][0]
    assert (
        claim["value"] == "populated_breaker"
        and claim["raw_value"] == "populated_breaker"
    )
    result = reconcile_graph(graph)
    assert result["resolutions"][0]["value"] == "populated_breaker"
    assert result["resolutions"][0]["needs_review"]


@pytest.mark.parametrize("standard", ["UL 489", "ul-489", "Ul_489", "UL489"])
def test_known_standard_context_spelling_cannot_hide_catalog_rating_conflict(standard):
    graph = new_graph("context-control")
    for feature, value in [
        ("part_number", "HDL36060"),
        ("voltage_v", 480),
        ("current_type", "AC"),
        ("rating_standard", standard),
        ("frequency_hz", 60),
    ]:
        add(graph, feature, value, 4)
    graph["claims"].append(
        make_claim(
            "d",
            "interrupting_ka",
            65,
            source={"id": "printed-rating", "sha256": "b" * 64, "page": 1},
            extractor={"name": "native_literal"},
            unit="kA",
            raw_value="65 kA",
            conditions={
                "side": "target",
                "voltage_v": 480.0,
                "current_type": "AC",
                "rating_standard": standard,
                "frequency_hz": 60,
            },
        )
    )
    original = deepcopy(graph)
    result = reconcile_graph(graph)
    ratings = [r for r in result["resolutions"] if r["feature"] == "interrupting_ka"]
    assert len(ratings) == 1
    assert ratings[0]["conditions"]["rating_standard"] == "UL489"
    assert ratings[0]["needs_review"] and ratings[0]["conflicting_claim_ids"]
    assert {a["value"] for a in ratings[0]["alternatives"]} == {18, 65}
    assert (
        graph == original
        and result["claims"][: len(graph["claims"])] == original["claims"]
    )


@pytest.mark.parametrize(
    "standard", ["unknown", "UNLISTED_UL489", "private-standard", ""]
)
def test_unknown_or_unlisted_standard_is_never_implicitly_ul489(standard):
    graph = new_graph("context-control")
    for feature, value in [
        ("part_number", "HDL36060"),
        ("voltage_v", 480),
        ("current_type", "AC"),
        ("rating_standard", standard),
        ("frequency_hz", 60),
    ]:
        add(graph, feature, value, 4)
    result = reconcile_graph(graph)
    assert not [
        c
        for c in result["claims"]
        if c["feature"] == "interrupting_ka"
        and c["extractor"]["name"] == "catalog_decoder"
    ]


def _catalog_operating_control(frequency=None):
    graph = new_graph("frequency-control")
    for feature, value in [
        ("part_number", "HDL36060"),
        ("voltage_v", 480),
        ("current_type", "AC"),
        ("rating_standard", "UL489"),
    ]:
        add(graph, feature, value, 4)
    if frequency is not None:
        add(graph, "frequency_hz", frequency, 4)
    return graph


def test_missing_actual_frequency_retains_cited_conditional_catalog_candidate_for_review():
    graph = _catalog_operating_control()
    original = deepcopy(graph)
    result = reconcile_graph(graph)
    rating = values(result)["interrupting_ka"]
    assert rating["value"] == 18 and rating["needs_review"]
    assert rating["conditions"]["frequency_hz"] == [50, 60]
    catalog = next(c for c in result["claims"] if c["feature"] == "interrupting_ka")
    assert catalog["conditions"]["frequency_hz"] == [50, 60]
    assert catalog["catalog_citation"] and catalog["source"]["sha256"]
    assert not result["catalog_lookups"][0]["applicability"]["operating_rating_bound"]
    assert graph == original


@pytest.mark.parametrize("frequency", [50, 60])
def test_explicit_compatible_actual_frequency_can_bind_catalog_rating(frequency):
    result = reconcile_graph(_catalog_operating_control(frequency))
    rating = values(result)["interrupting_ka"]
    assert rating["value"] == 18 and not rating["needs_review"]
    assert rating["conditions"]["frequency_hz"] == frequency
    catalog = next(c for c in result["claims"] if c["feature"] == "interrupting_ka")
    assert catalog["conditions"]["frequency_hz"] == [50, 60]


def test_explicit_incompatible_frequency_never_uses_50_60hz_catalog_rating():
    result = reconcile_graph(_catalog_operating_control(400))
    assert not [c for c in result["claims"] if c["feature"] == "interrupting_ka"]
    assert not result["catalog_lookups"][0]["applicability"]["operating_rating_bound"]


def test_even_single_catalog_frequency_does_not_observe_actual_equipment_frequency():
    def catalog(**_):
        return {
            "claims": [
                {
                    "feature": "interrupting_ka",
                    "value": 18,
                    "unit": "kA",
                    "conditions": {
                        "voltage_v": 480,
                        "current_type": "AC",
                        "rating_standard": "UL489",
                        "frequency_hz": 60,
                    },
                    "source_citation": {
                        "source_id": "synthetic-catalog",
                        "sha256": "b" * 64,
                        "page": 1,
                    },
                }
            ],
            "constraints": [],
            "applicability": {"operating_rating_bound": True},
        }

    result = reconcile_graph(_catalog_operating_control(), catalog_lookup=catalog)
    rating = values(result)["interrupting_ka"]
    assert rating["value"] == 18 and rating["needs_review"]


def test_frequency_from_source_rating_conditions_remains_a_catalog_parent():
    graph = _catalog_operating_control()
    observed = make_claim(
        "d",
        "interrupting_ka",
        18,
        source={"id": "actual-rating", "sha256": "b" * 64, "page": 1},
        extractor={"name": "native_literal"},
        unit="kA",
        raw_value="18 kA at 480V AC 60Hz UL489",
        conditions={
            "side": "target",
            "voltage_v": 480,
            "current_type": "AC",
            "rating_standard": "UL489",
            "frequency_hz": 60,
        },
    )
    graph["claims"].append(observed)
    result = reconcile_graph(graph)
    catalog = next(
        c
        for c in result["claims"]
        if c["feature"] == "interrupting_ka"
        and c["extractor"]["name"] == "catalog_decoder"
    )
    assert observed["id"] in catalog["derived_from"]
    rating = values(result)["interrupting_ka"]
    assert rating["value"] == 18 and not rating["needs_review"]
    assert len(rating["alternatives"][0]["independent_groups"]) == 1


def test_noncanonical_frequency_unit_cannot_establish_catalog_operating_context():
    graph = _catalog_operating_control(60)
    frequency = next(c for c in graph["claims"] if c["feature"] == "frequency_hz")
    frequency["unit"] = "kHz"
    result = reconcile_graph(graph)
    rating = values(result)["interrupting_ka"]
    assert rating["value"] == 18 and rating["needs_review"]
    assert not result["catalog_lookups"][0]["applicability"]["operating_rating_bound"]
