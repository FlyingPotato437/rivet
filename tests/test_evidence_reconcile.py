"""Decision integrity controls, not real-document accuracy measurements."""
from copy import deepcopy

import pytest

from backend.equipment.evidence.calibration import fit_development
from backend.equipment.evidence.reconcile import reconcile_graph
from backend.equipment.evidence.schema import make_claim, new_graph


def graph(*items):
    g = new_graph("control-order")
    g["nodes"] = [{"id": "d", "kind": "device", "scope_key": "control-order", "metadata": {}}]
    for feature, value, kwargs in items:
        from backend.equipment.evidence.ontology import FEATURES
        kwargs.setdefault("unit", FEATURES[feature].unit if feature in FEATURES else None)
        g["claims"].append(make_claim("d", feature, value,
            source={"source_id": kwargs.pop("source_id", "source"), "sha256": "a" * 64, "page": kwargs.pop("page", 1)},
            extractor={"name": kwargs.pop("extractor", "native_literal")},
            **kwargs))
    return g


def res(g, feature):
    return [r for r in g["resolutions"] if r["feature"] == feature]


def test_claims_never_overwritten_and_model_confidence_cannot_accept():
    g = graph(("trip_a", 60, {"unit": "A", "extractor": "vision", "raw_confidence": .999999}))
    before = deepcopy(g)
    r = reconcile_graph(g)
    assert g == before and r["claims"][:1] == g["claims"]
    assert res(r, "trip_a")[0]["needs_review"]
    assert res(r, "trip_a")[0]["calibration"]["empirical_probability"] is None


def test_repeat_readers_same_page_never_add_agreement():
    g = graph(("trip_a", 600, {"extractor": "vision1"}), ("trip_a", 600, {"extractor": "vision2"}))
    assert res(reconcile_graph(g), "trip_a")[0]["support"]["independent_group_count"] == 1


def test_joint_catalog_derivation_is_one_proof_not_three_pages():
    g = graph(("part_number", "HDL36060", {"page": 1}), ("voltage_v", 480, {"page": 2}),
              ("current_type", "AC", {"page": 3}), ("rating_standard", "UL489", {"page": 4}))
    r = reconcile_graph(g)
    ka = res(r, "interrupting_ka")[0]
    assert ka["value"] == 18 and ka["support"]["independent_group_count"] == 1
    assert len(ka["support"]["proof_ancestry"][0]) >= 4
    assert res(r, "product_family") and not res(r, "family")


def test_no_implicit_ac_or_standard():
    for extra in ([], [("current_type", "AC", {})], [("rating_standard", "UL489", {})]):
        r = reconcile_graph(graph(("part_number", "HDL36060", {}), ("voltage_v", 480, {}), *extra))
        assert not res(r, "interrupting_ka")


def test_compatible_catalog_frequency_and_observed_rating_conflict():
    g = graph(("part_number", "HDL36060", {}), ("voltage_v", 480, {}),
              ("current_type", "AC", {}), ("rating_standard", "UL489", {}),
              ("interrupting_ka", 65, {"page": 2, "unit": "kA", "conditions": {"voltage_v": 480, "current_type": "AC", "rating_standard": "UL489", "frequency_hz": 60}}))
    ka = res(reconcile_graph(g), "interrupting_ka")
    assert len(ka) == 1
    assert ka[0]["needs_review"] and ka[0]["conflicting_claim_ids"]


def test_alternative_l_ranges_are_not_intersected():
    r = reconcile_graph(graph(("manufacturer", "Square D", {}), ("product_family", "PowerPacT", {}),
                              ("device_frame", "L", {}), ("current_type", "AC", {}), ("rated_current_a", 600, {"unit": "A"})))
    current = res(r, "rated_current_a")[0]
    assert current["value"] == 600 and not current["constraint_failures"]


def test_catalog_wont_fix_illegible_value_by_inventing_digit():
    r = reconcile_graph(graph(("manufacturer", "Square D", {}), ("product_family", "PowerPacT", {}),
                              ("device_frame", "J", {}), ("current_type", "AC", {}), ("rated_current_a", 60, {"unit": "A"})))
    current = res(r, "rated_current_a")[0]
    assert current["value"] is None and current["needs_review"]
    assert r["claims"][4]["value"] == 60


def test_trip_frame_rules_never_use_request_as_actual():
    r = reconcile_graph(graph(("trip_a", 600, {"unit": "A"}), ("frame_a", 400, {"unit": "A"}),
                              ("frame_a", 800, {"unit": "A", "conditions": {"side": "requirements"}})))
    trip = res(r, "trip_a")[0]
    assert trip["value"] is None and trip["needs_review"]


def test_graph_requirement_flags_without_replacing_actual():
    g = graph(("trip_a", 250, {"unit": "A"}), ("trip_a", 300, {"unit": "A", "conditions": {"side": "requirements"}}))
    g["rules"] = [{"id": "r", "kind": "attribute_constraint", "subject_id": "d", "feature": "trip_a",
                   "operator": "eq", "target": {"value": 300, "unit": "A"}, "source_claim_ids": [g["claims"][1]["id"]]}]
    r = reconcile_graph(g)
    actual = next(v for v in res(r, "trip_a") if v["side"] == "target")
    assert actual["value"] == 250
    assert actual["needs_review"]
    assert r["rule_checks"][0]["status"] == "violated"


def test_same_numbers_normalize_to_one_candidate():
    r = reconcile_graph(graph(("trip_a", 600, {}), ("trip_a", 600.0, {"page": 2})))
    assert not res(r, "trip_a")[0]["conflicting_claim_ids"]


def test_unknown_operating_context_cannot_accept_ka():
    r = reconcile_graph(graph(("interrupting_ka", 65, {"unit": "kA"})))
    assert res(r, "interrupting_ka")[0]["needs_review"]


def test_calibration_rejects_holdout_and_counts_packages_not_cells():
    r = res(reconcile_graph(graph(("trip_a", 600, {"extractor": "vision"}))), "trip_a")[0]
    ex = {"split": "development", "package_ancestry": "one-original-binder", "correct": True, "resolution": r}
    fitted = fit_development([ex] * 1000)
    bucket = next(iter(fitted["buckets"].values()))
    assert bucket["independent_package_count"] == 1
    assert bucket["package_success_lower_bound"] < .3
    assert res(reconcile_graph(graph(("trip_a", 600, {"extractor": "vision"})), calibration=fitted), "trip_a")[0]["needs_review"]
    with pytest.raises(ValueError, match="development"):
        fit_development([{**ex, "split": "unseen_test"}])


def test_raw_600_cannot_be_accepted_as_normalized_60():
    r = reconcile_graph(graph(("trip_a", 60, {"unit": "A", "raw_value": "600A"})))
    v = res(r, "trip_a")[0]
    assert v["value"] == 60 and v["needs_review"]
    assert "raw_numeric_normalization_conflict" in v["constraint_failures"]


def test_explicit_symmetry_is_a_rule_not_a_source_of_missing_values():
    g = graph(("trip_a", 250, {"unit": "A"}))
    g["nodes"].append({"id": "sibling", "kind": "device", "scope_key": "control-order", "metadata": {}})
    g["rules"] = [{"id": "sym", "kind": "symmetry", "subject_id": "sibling", "feature": "trip_a", "operator": "eq", "target": {"other_subject_id": "d", "feature": "trip_a"}, "source_claim_ids": [g["claims"][0]["id"]]}]
    r = reconcile_graph(g)
    assert not [x for x in r["resolutions"] if x["subject_id"] == "sibling"]
    assert r["rule_checks"][0]["status"] == "unknown"


@pytest.fixture(autouse=True)
def database():
    """These pure source/decision controls do not use a database."""
    yield


def test_known_brand_alias_keeps_raw_claim_and_one_canonical_value():
    g = graph(("manufacturer", "Square D", {}), ("product_family", "PowerPacT", {}), ("part_number", "HDL36060", {}))
    r = reconcile_graph(g)
    maker = res(r, "manufacturer")[0]
    assert maker["value"] == "Schneider Electric" and not maker["conflicting_claim_ids"]
    assert r["claims"][0]["value"] == "Square D"


def test_satisfied_unverified_observation_is_not_a_verified_requirement():
    g = graph(("trip_a", 250, {"unit": "A", "extractor": "vision"}))
    g["rules"] = [{"id": "r", "kind": "attribute_constraint", "subject_id": "d", "feature": "trip_a", "operator": "eq", "target": {"value": 250, "unit": "A"}, "source_claim_ids": [g["claims"][0]["id"]]}]
    r = reconcile_graph(g)
    assert r["rule_checks"][0]["status"] == "candidate_only"
    assert r["rule_checks"][0]["needs_review"]
