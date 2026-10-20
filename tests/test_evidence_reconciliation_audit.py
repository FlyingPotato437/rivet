"""Synthetic safety audit: no heldout files, database, mail, or network access."""

from copy import deepcopy

import pytest

from backend.equipment.evidence.adapters import adapt_sources
from backend.equipment.evidence.integration import (
    decision_for,
    ensure_reconciled,
    reconcile_comparison,
)
from backend.equipment.evidence.reconcile import VERSION, reconcile_graph
from backend.equipment.evidence.schema import make_claim, new_graph


@pytest.fixture(autouse=True)
def database():
    """Override global schema resets: every audit here is a pure control."""
    yield


def _graph(*items):
    graph = new_graph("audit-order")
    graph["nodes"] = [
        {"id": "device", "kind": "device", "scope_key": "audit-order", "metadata": {}}
    ]
    for index, (feature, value, options) in enumerate(items, 1):
        options = deepcopy(options)
        source = options.pop(
            "source", {"id": "synthetic-source", "sha256": "a" * 64, "page": index}
        )
        extractor = options.pop("extractor", "native_literal")
        graph["claims"].append(
            make_claim(
                "device",
                feature,
                value,
                source=source,
                extractor={"name": extractor},
                **options,
            )
        )
    return graph


def _resolution(graph, feature):
    return next(r for r in graph["resolutions"] if r["feature"] == feature)


def _must_reject_or_review(graph, feature):
    try:
        result = reconcile_graph(graph)
    except ValueError:
        return
    assert _resolution(result, feature)["needs_review"]


def _raw(*, role="drawing", fact_hash="a" * 64, source_hash="a" * 64):
    fact = {
        "id": "synthetic-trip",
        "source_id": "source",
        "sha256": fact_hash,
        "role": role,
        "device_tag": "CB-12",
        "field": "trip_a",
        "value": 300,
        "unit": "A",
        "raw_value": "300A",
        "evidence": {"page": 1, "bbox": [0.1, 0.1, 0.2, 0.2]},
    }
    return {
        "target": {
            "facts": [fact],
            "sources": [
                {
                    "id": "source",
                    "sha256": source_hash,
                    "role": role,
                    "status": "parsed",
                }
            ],
        },
        "baseline": {},
        "requirements": {},
        "checks": [],
        "summary": {},
    }


def test_catalog_cannot_accept_50_60hz_rating_for_explicit_400hz():
    graph = _graph(
        ("part_number", "HDL36060", {}),
        ("voltage_v", 480, {}),
        ("current_type", "AC", {}),
        ("rating_standard", "UL489", {}),
        ("frequency_hz", 400, {}),
    )
    result = reconcile_graph(graph)
    ratings = [r for r in result["resolutions"] if r["feature"] == "interrupting_ka"]
    assert not ratings or all(r["needs_review"] or r["value"] is None for r in ratings)


def test_specification_role_cannot_fill_actual_when_placed_in_target_analysis():
    result = reconcile_comparison(_raw(role="specification"), scope_id="audit-order")
    actual = decision_for(result, "CB-12", "trip_a")
    assert actual is None or actual["value"] is None or actual["needs_review"]
    specification_claims = {
        c["id"]
        for c in result["evidence_graph"]["claims"]
        if c.get("conditions", {}).get("role") == "specification"
    }
    # Preserve the original declared-side text, but never let it vote as actual.
    assert not [
        r
        for r in result["evidence_graph"]["resolutions"]
        if r["side"] == "target"
        and r["state"] == "observed"
        and specification_claims.intersection(r["supporting_claim_ids"])
    ]


def test_fact_and_metadata_source_hash_disagreement_is_not_silently_rebound():
    with pytest.raises(ValueError, match="hash|source|provenance"):
        reconcile_comparison(_raw(fact_hash="b" * 64), scope_id="audit-order")


def test_foreign_node_scope_cannot_yield_an_accepted_value():
    graph = _graph(("trip_a", 100, {"unit": "A"}))
    graph["nodes"][0]["scope_key"] = "another-tenant-order"
    _must_reject_or_review(graph, "trip_a")


def test_missing_derivation_parent_is_never_literal_support():
    graph = _graph(
        ("trip_a", 100, {"unit": "A", "derived_from": ["missing-source-claim"]})
    )
    _must_reject_or_review(graph, "trip_a")


def test_derived_parent_from_another_equipment_cannot_establish_literal_support():
    graph = _graph(("trip_a", 100, {"unit": "A"}))
    graph["nodes"].append(
        {
            "id": "different-device",
            "kind": "device",
            "scope_key": "audit-order",
            "metadata": {},
        }
    )
    parent = graph["claims"][0]
    graph["claims"] = [
        make_claim(
            "different-device",
            "trip_a",
            100,
            source={"id": "source-2", "sha256": "b" * 64, "page": 1},
            extractor={"name": "catalog_decoder"},
            unit="A",
            derived_from=[parent["id"]],
        ),
        parent,
    ]
    try:
        result = reconcile_graph(graph)
    except ValueError:
        return
    candidate = next(
        r for r in result["resolutions"] if r["subject_id"] == "different-device"
    )
    assert candidate["needs_review"]


def test_requirement_part_number_cannot_establish_actual_catalog_product():
    graph = _graph(
        (
            "part_number",
            "HDL36060",
            {"conditions": {"side": "requirements", "role": "specification"}},
        )
    )
    parent = graph["claims"][0]
    graph["claims"].append(
        make_claim(
            "device",
            "rated_current_a",
            60,
            source={"id": "catalog", "sha256": "b" * 64, "page": 1},
            extractor={"name": "catalog_decoder"},
            unit="A",
            conditions={"side": "target"},
            derived_from=[parent["id"]],
        )
    )
    _must_reject_or_review(graph, "rated_current_a")


@pytest.mark.parametrize(
    ("feature", "value", "unit"),
    [
        ("trip_a", 600, "kA"),
        ("trip_a", -5, "A"),
        ("trip_a", True, "A"),
        ("not_a_profile_feature", 100, "A"),
    ],
)
def test_untyped_or_noncanonical_graph_values_are_not_accepted(feature, value, unit):
    _must_reject_or_review(_graph((feature, value, {"unit": unit})), feature)


def test_valid_adapter_unit_conversion_does_not_create_raw_digit_conflict():
    source = {"id": "source", "sha256": "a" * 64, "role": "drawing"}
    fact = {
        "id": "synthetic-conversion",
        "source_id": "source",
        "sha256": "a" * 64,
        "device_tag": "CB-1",
        "field": "trip_a",
        "value": 0.6,
        "unit": "kA",
        "raw_value": 0.6,
        "evidence": {"page": 1, "bbox": [0.1, 0.1, 0.2, 0.2]},
    }
    graph = adapt_sources(
        sources=[source],
        scope_id="audit-order",
        rawcomparison={"target": {"facts": [fact]}},
    )
    claim = graph["claims"][0]
    assert (claim["value"], claim["unit"], claim["normalization"]["input_unit"]) == (
        600,
        "A",
        "kA",
    )
    result = _resolution(reconcile_graph(graph), "trip_a")
    assert "raw_numeric_normalization_conflict" not in result["constraint_failures"]


def test_same_source_page_cannot_gain_independence_from_supplied_reader_labels():
    source = {"id": "same-page", "sha256": "a" * 64, "page": 1}
    graph = _graph(
        (
            "trip_a",
            600,
            {
                "source": source,
                "unit": "A",
                "extractor": "vision-a",
                "independence_group": "reader-a",
            },
        ),
        (
            "trip_a",
            600,
            {
                "source": source,
                "unit": "A",
                "extractor": "vision-b",
                "independence_group": "reader-b",
            },
        ),
    )
    calibration = {
        "buckets": {
            "groups:2|literal:0|conflict:0": {
                "independent_package_count": 5,
                "package_success_lower_bound": 1,
            }
        },
        "minimum_packages": 5,
        "acceptance_lower_bound": 0.99,
    }
    result = _resolution(reconcile_graph(graph, calibration=calibration), "trip_a")
    assert result["support"]["independent_group_count"] == 1
    assert result["needs_review"]


def test_requirement_with_missing_citation_cannot_be_reported_as_satisfied():
    graph = _graph(("trip_a", 100, {"unit": "A"}))
    graph["rules"] = [
        {
            "id": "uncited-rule",
            "kind": "attribute_constraint",
            "subject_id": "device",
            "feature": "trip_a",
            "operator": "eq",
            "target": {"value": 100, "unit": "A"},
            "source_claim_ids": ["missing-requirement-citation"],
        }
    ]
    try:
        result = reconcile_graph(graph)
    except ValueError:
        return
    assert result["rule_checks"][0]["status"] != "satisfied"
    assert result["rule_checks"][0]["needs_review"]


def test_unknown_rule_cannot_leave_its_equipment_accepted_without_review():
    graph = _graph(("trip_a", 100, {"unit": "A"}))
    graph["rules"] = [
        {
            "id": "unsupported-rule",
            "kind": "unsupported-engineering-rule",
            "subject_id": "device",
            "feature": "trip_a",
            "source_claim_ids": [graph["claims"][0]["id"]],
        }
    ]
    _must_reject_or_review(graph, "trip_a")


def test_attached_decision_cannot_override_unchanged_raw_sources():
    raw = reconcile_comparison(_raw(), scope_id="audit-order")
    assert decision_for(raw, "CB-12", "trip_a")["value"] == 300
    raw["decisions"][0]["value"] = 900
    current = decision_for(ensure_reconciled(raw), "CB-12", "trip_a")
    assert current["value"] == 300 or current["needs_review"]


def test_obsolete_cached_policy_is_recomputed_with_unchanged_sources():
    raw = reconcile_comparison(_raw(), scope_id="audit-order")
    raw["evidence_graph"]["policy"]["version"] = "obsolete-permissive-policy"
    current = ensure_reconciled(raw)
    assert current["evidence_graph"]["policy"]["version"] == VERSION


def test_violated_graph_requirement_outside_legacy_checks_blocks_package_success():
    raw = _raw()
    template = raw["target"]["facts"][0]
    maker = {
        **deepcopy(template),
        "id": "actual-maker",
        "field": "manufacturer",
        "value": "Maker-A",
        "raw_value": "Maker-A",
        "unit": None,
    }
    raw["target"]["facts"].append(maker)
    specified = {
        **deepcopy(maker),
        "id": "specified-maker",
        "source_id": "specification",
        "sha256": "b" * 64,
        "role": "specification",
        "value": "Maker-B",
        "raw_value": "Maker-B",
    }
    raw["requirements"] = {
        "facts": [specified],
        "sources": [
            {
                "id": "specification",
                "sha256": "b" * 64,
                "role": "specification",
                "status": "parsed",
            }
        ],
    }
    raw["checks"] = [
        {
            "id": "legacy-trip-check",
            "device_tag": "CB-12",
            "field": "trip_a",
            "status": "verified",
        }
    ]
    raw["summary"] = {"outcome": "supported_fields_match", "source_issues": 0}
    result = reconcile_comparison(raw, scope_id="audit-order")
    assert any(
        check["status"] == "violated"
        for check in result["evidence_graph"]["rule_checks"]
    )
    assert result["summary"]["outcome"] != "supported_fields_match"
    assert not result["summary"]["revision_changes_verified"]
