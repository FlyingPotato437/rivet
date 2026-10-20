"""Manufacturer rules need explicit review, identity and cited actual evidence."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.equipment.profiles import (
    ProfileError,
    evaluate,
    resolve_alias,
    seal_profile,
)


def source(document_id="guide", *, role="specification", location=None):
    return {
        "document_id": document_id,
        "name": f"{document_id}.pdf",
        "revision": "C",
        "sha256": ("a" if document_id == "guide" else "b") * 64,
        "role": role,
        "location": location
        or {
            "page": 3,
            "text": "Company-reviewed breaker compatibility and layout limits.",
        },
    }


def rule(constraint=None, **kwargs):
    return {
        "rule_id": "trip",
        "label": "Approved trip unit",
        "status": "approved",
        "device_tag": "CB-12",
        "source_ids": ["guide"],
        "constraint": constraint
        or {
            "op": "allowed_set",
            "field": "trip_unit",
            "allowed": ["TU-2500", "TU-3000"],
        },
        **kwargs,
    }


def profile(**kwargs):
    return seal_profile(
        {
            "profile_id": "panel-a",
            "tenant_id": "manufacturer-1",
            "manufacturer_name": "Example Switchgear",
            "name": "Panel A company rules",
            "status": "approved",
            "review": {
                "reviewer_id": "company-reviewer-1",
                "reviewed_at": "2026-10-18T12:00:00Z",
            },
            "sources": [source()],
            "aliases": [
                {
                    "alias_id": "breaker-12",
                    "physical_device_id": "breaker-12",
                    "tags": ["CB-12", "BKR12", "FDR-12"],
                    "status": "approved",
                    "source_ids": ["guide"],
                }
            ],
            "rules": [rule()],
            **kwargs,
        }
    )


def observation(
    value, *, document_id="drawing", role="drawing", unit=None, location=None
):
    return {
        "observation_id": document_id + "-trip",
        "value": value,
        "unit": unit,
        "raw_value": str(value),
        "source": source(
            document_id,
            role=role,
            location=location
            or {"sheet": "E-10", "cad_handle": "AB12", "text": str(value)},
        ),
    }


def device(tag="CB-12", value="TU-3000", **kwargs):
    return {
        "tag": tag,
        "observed_in_current_package": True,
        "fields": {
            "trip_unit": {"build": [observation(value)], "agreement": "consistent"}
        },
        **kwargs,
    }


def model(devices=None, **kwargs):
    return {
        "version": "d" * 64,
        "devices": devices or [device()],
        "coverage": {},
        **kwargs,
    }


def native():
    return {
        "source": source(
            "native",
            role="drawing",
            location={"coordinate_system": "DXF modelspace / mm"},
        ),
        "status": "verified",
        "units": "mm",
        "checks": [{"id": "native-incidence", "status": "pass"}],
        "devices": [
            {
                "tag": "CB-12",
                "handle": "A1",
                "bounds": [10, 20, 110, 220],
                "terminals": [{"id": "CB-12:T1"}],
            }
        ],
        "wires": [
            {
                "id": "W1",
                "handle": "D1",
                "from": "CB-12:T1",
                "to": "K86:12",
                "actual_from": "CB-12:T1",
                "actual_to": "K86:12",
            }
        ],
    }


def only(result):
    assert len(result.results) == 1
    return result.results[0]


def test_sealed_version_binds_rules_review_source_aliases_and_tenant():
    initial = profile()
    assert len(initial.version) == 64
    assert seal_profile(initial.model_dump(mode="json")).version == initial.version
    for key, value in (("tenant_id", "other-company"), ("name", "Different rule pack")):
        changed = initial.model_dump(mode="json")
        changed[key] = value
        with pytest.raises(ProfileError, match="immutable version"):
            seal_profile(changed)
        changed.pop("version")
        assert seal_profile(changed).version != initial.version
    changed = initial.model_dump(mode="json")
    changed["sources"][0]["location"]["text"] = "Different quoted rule"
    with pytest.raises(ProfileError):
        seal_profile(changed)
    with pytest.raises(ValidationError):
        initial.status = "revoked"


def test_nested_source_mutation_cannot_reuse_approved_version():
    p = profile()
    p.sources[0].location["text"] = "Changed after review"
    with pytest.raises(ProfileError, match="immutable version"):
        evaluate(p, model())


@pytest.mark.parametrize("status", ["proposed", "revoked"])
def test_inactive_profiles_never_activate_rules_or_aliases(status):
    result = evaluate(profile(status=status, review=None), model())
    assert result.active is False and result.status == "inactive"
    assert result.results == () and result.aliases == ()


def test_revocation_ledger_disables_exact_version():
    p = profile()
    result = evaluate(p, model(), revoked_versions=[p.version])
    assert result.active is False and result.results == ()
    assert (
        resolve_alias(p, "BKR12", ["CB-12"], revoked_versions=[p.version]).status
        == "blocked"
    )


def test_approval_needs_identified_timezone_bound_company_review():
    with pytest.raises(ValidationError, match="company review"):
        profile(review=None)
    with pytest.raises(ValidationError, match="timezone"):
        profile(
            review={"reviewer_id": "engineer", "reviewed_at": "2026-10-18T12:00:00"}
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"sha256": "bad"},
        {"location": {}},
        {"location": {"page": 0, "text": "Rule"}},
        {"location": {"page": 3}},
        {"location": {"text": "Unlocated rule"}},
    ],
)
def test_profile_rules_require_valid_quoted_precise_source(changes):
    with pytest.raises(ValidationError):
        profile(sources=[{**source(), **changes}])


def test_unknown_source_reference_and_arbitrary_code_are_rejected():
    with pytest.raises(ValidationError, match="unavailable"):
        profile(rules=[rule(source_ids=["not-owned"])])
    with pytest.raises(ValidationError):
        profile(rules=[rule({"op": "eval", "expression": "open('/secret')"})])
    with pytest.raises(ValidationError):
        profile(
            rules=[
                rule(
                    {
                        "op": "range",
                        "field": "rating",
                        "minimum": 200,
                        "maximum": 100,
                        "unit": "A",
                    }
                )
            ]
        )


@pytest.mark.parametrize(
    "constraint,value,unit,expected",
    [
        (
            {"op": "equals", "field": "trip_unit", "expected": "TU-3000"},
            "TU-3000",
            None,
            "pass",
        ),
        (
            {"op": "equals", "field": "trip_unit", "expected": "TU-3000"},
            "TU-2500",
            None,
            "fail",
        ),
        (
            {
                "op": "allowed_set",
                "field": "trip_unit",
                "allowed": ["TU-3000", "TU-2500"],
            },
            "TU-2500",
            None,
            "pass",
        ),
        (
            {"op": "allowed_set", "field": "trip_unit", "allowed": ["TU-3000"]},
            "UNKNOWN-PART",
            None,
            "fail",
        ),
        (
            {
                "op": "range",
                "field": "trip_unit",
                "minimum": 100,
                "maximum": 3000,
                "unit": "A",
            },
            2500,
            "A",
            "pass",
        ),
        (
            {"op": "range", "field": "trip_unit", "maximum": 2500, "unit": "A"},
            3000,
            "A",
            "fail",
        ),
        (
            {"op": "range", "field": "trip_unit", "maximum": 3000, "unit": "A"},
            "2500",
            "A",
            "unknown",
        ),
        (
            {"op": "range", "field": "trip_unit", "maximum": 3000, "unit": "A"},
            2.5,
            "kA",
            "unknown",
        ),
    ],
)
def test_finite_operators_use_actual_values_and_exact_units(
    constraint, value, unit, expected
):
    actual = device()
    actual["fields"]["trip_unit"]["build"] = [observation(value, unit=unit)]
    result = evaluate(profile(rules=[rule(constraint)]), model([actual]))
    assert only(result).status == expected
    assert only(result).evidence[0].source.location["cad_handle"] == "AB12"
    assert result.engineering_approved is False and result.production_released is False


def test_conflicting_current_sources_are_unknown_even_when_one_passes():
    actual = device()
    actual["fields"]["trip_unit"]["build"].append(
        observation(
            "TU-2500",
            document_id="bom",
            role="bom",
            location={"sheet": "BOM", "row": 8},
        )
    )
    result = evaluate(
        profile(
            rules=[rule({"op": "equals", "field": "trip_unit", "expected": "TU-3000"})]
        ),
        model([actual]),
    )
    assert only(result).status == "unknown"
    assert {item.value for item in only(result).evidence} == {"TU-2500", "TU-3000"}


def test_missing_build_observations_never_fall_back_to_requested_or_specified():
    actual = device()
    actual["fields"]["trip_unit"] = {
        "build": [],
        "specified": [observation("TU-3000")],
        "requested": [observation("TU-3000")],
    }
    assert only(evaluate(profile(), model([actual]))).status == "unknown"


def test_bad_or_stale_observation_anchor_cannot_pass():
    actual = device()
    actual["fields"]["trip_unit"]["build"][0]["source"]["location"] = {}
    assert only(evaluate(profile(), model([actual]))).status == "unknown"
    result = evaluate(
        profile(), model(), source_catalog={"guide": "a" * 64, "drawing": "c" * 64}
    )
    assert only(result).status == "unknown" and "stale" in only(result).reason


def test_alias_reconciles_drawing_bom_different_names_without_losing_evidence():
    bom = device("BKR12")
    bom["fields"]["trip_unit"]["build"] = [
        observation(
            "TU-3000",
            document_id="bom",
            role="bom",
            location={"sheet": "BOM", "row": 8, "cell": "C8"},
        )
    ]
    result = evaluate(profile(), model([device(), bom]))
    assert only(result).status == "pass"
    assert only(result).original_tags == ("CB-12", "BKR12")
    assert {item.original_tag for item in only(result).evidence} == {"CB-12", "BKR12"}
    assert {item.physical_device_id for item in only(result).evidence} == {"breaker-12"}
    assert {item.original_tag for item in result.aliases} == {"CB-12", "BKR12"}
    bom["fields"]["trip_unit"]["build"][0]["value"] = "TU-2500"
    assert only(evaluate(profile(), model([device(), bom]))).status == "unknown"


def test_alias_collision_in_same_source_sheet_distinct_devices_blocks_map():
    second = device("BKR12")
    second["fields"]["trip_unit"]["build"][0]["source"]["location"]["cad_handle"] = (
        "AB13"
    )
    result = evaluate(profile(), model([device(), second]))
    assert only(result).status == "unknown" and result.blockers
    assert result.aliases == ()


def test_alias_collision_between_bom_rows_blocks_but_different_sheets_do_not():
    first, second = device(), device("BKR12")
    for item, row in ((first, 8), (second, 9)):
        item["fields"]["trip_unit"]["build"] = [
            observation(
                "TU-3000",
                document_id="bom",
                role="bom",
                location={"sheet": "BOM", "row": row},
            )
        ]
    assert evaluate(profile(), model([first, second])).blockers
    second["fields"]["trip_unit"]["build"][0]["source"]["location"]["sheet"] = (
        "Alternate BOM"
    )
    assert only(evaluate(profile(), model([first, second]))).status == "pass"


def test_alias_overlap_and_physical_identity_collision_are_blocked():
    original = profile().model_dump(mode="json")
    original.pop("version")
    original["aliases"].append(
        {**original["aliases"][0], "alias_id": "other", "tags": ["CB-12", "X12"]}
    )
    result = evaluate(seal_profile(original), model())
    assert only(result).status == "unknown" and result.blockers


def test_no_ansi_alias_or_punctuation_identity_is_inferred():
    p = profile(aliases=[])
    assert only(evaluate(p, model([device("52-12")]))).status == "unknown"
    assert resolve_alias(p, "CB-12", ["BKR12"]).status == "unknown"
    assert resolve_alias(p, "CB-12", ["CB12"]).status == "unknown"
    exact = resolve_alias(p, "CB-12", ["CB-12"])
    assert exact.status == "resolved" and exact.alias_id is None and exact.sources == ()


def test_native_alias_target_is_unique_original_and_source_cited():
    resolved = resolve_alias(profile(), "FDR-12", ["CB-12", "KA-1"])
    assert resolved.status == "resolved" and resolved.actual_tag == "CB-12"
    assert resolved.physical_device_id == "breaker-12"
    assert resolved.sources[0].location["page"] == 3
    assert resolve_alias(profile(), "FDR-12", ["CB-12", "BKR12"]).status == "blocked"
    assert resolve_alias(profile(), "CB-12", ["CB-12", "CB-12"]).status == "blocked"


def test_proposed_aliases_or_stale_rule_sources_do_not_activate():
    p = profile().model_dump(mode="json")
    p.pop("version")
    p["aliases"][0]["status"] = "proposed"
    assert resolve_alias(seal_profile(p), "FDR-12", ["CB-12"]).status == "unknown"
    result = evaluate(
        profile(), model(), source_catalog={"guide": "c" * 64, "drawing": "b" * 64}
    )
    assert result.status == "unknown" and result.aliases == ()


def test_proposed_rules_are_not_evaluated_and_failed_conditions_are_not_passes():
    p = profile(rules=[rule(status="proposed")])
    assert evaluate(p, model()).results == ()
    p = profile(rules=[rule(when=[{"field": "trip_unit", "equals": "OTHER"}])])
    result = only(evaluate(p, model()))
    assert result.status == "unknown" and result.applicable is False
    p = profile(rules=[rule(when=[{"field": "part_number", "equals": "CB2500"}])])
    result = only(evaluate(p, model()))
    assert result.status == "unknown" and result.applicable is None


def test_requires_does_not_treat_incomplete_inventory_as_proven_absence():
    p = profile(rules=[rule({"op": "requires", "required_device_tag": "KA-1"})])
    assert only(evaluate(p, model())).status == "unknown"
    assert (
        only(evaluate(p, model(coverage={"complete_inventory_verified": True}))).status
        == "unknown"
    )
    complete = {
        "complete_inventory_verified": True,
        "inventory_source": source(
            "bom", role="bom", location={"sheet": "BOM", "row": 1}
        ),
    }
    assert only(evaluate(p, model(coverage=complete))).status == "fail"
    relay = device("KA-1")
    relay["fields"]["trip_unit"]["build"][0]["source"]["location"]["cad_handle"] = "KA1"
    assert only(evaluate(p, model([device(), relay]))).status == "pass"
    relay["fields"] = {}
    assert only(evaluate(p, model([device(), relay]))).status == "unknown"


def test_geometry_checks_actual_bounding_body_not_advisory_or_raster():
    p = profile(
        rules=[
            rule(
                {
                    "op": "dimension_bounds",
                    "x_min": 0,
                    "x_max": 200,
                    "width_max": 150,
                    "height_max": 250,
                }
            )
        ]
    )
    graph = native()
    result = only(evaluate(p, model(), graph))
    assert result.status == "pass" and result.evidence[0].value == (10, 20, 110, 220)
    assert result.evidence[0].source.location["cad_handle"] == "A1"
    graph["devices"][0]["bounds"][2] = 250
    assert only(evaluate(p, model(), graph)).status == "fail"
    graph["status"] = "illustrative"
    assert only(evaluate(p, model(), graph)).status == "unknown"
    assert only(evaluate(p, model())).status == "unknown"


@pytest.mark.parametrize("bounds", [[0, 0, 0, 1], [0, 0, float("nan"), 2], [0, 0, 3]])
def test_bad_native_bounds_never_pass(bounds):
    graph = native()
    graph["devices"][0]["bounds"] = bounds
    p = profile(rules=[rule({"op": "dimension_bounds", "width_max": 300})])
    assert only(evaluate(p, model(), graph)).status == "unknown"


def test_terminal_pair_uses_actual_incidence_and_exact_alias_no_declared_fallback():
    p = profile(
        rules=[
            rule(
                {
                    "op": "terminal_pair",
                    "from_terminal": "BKR12:T1",
                    "to_terminal": "K86:12",
                    "wire_id": "W1",
                }
            )
        ]
    )
    graph = native()
    assert only(evaluate(p, model(), graph)).status == "pass"
    graph["wires"][0]["actual_to"] = "K86:22"
    assert only(evaluate(p, model(), graph)).status == "fail"
    graph["wires"][0]["actual_to"] = None
    result = only(evaluate(p, model(), graph))
    assert result.status == "unknown" and "declared" in result.reason
    graph["wires"][0]["actual_to"] = "K86:12"
    graph["checks"][0]["status"] = "unknown"
    assert only(evaluate(p, model(), graph)).status == "unknown"
    graph["status"] = "pdf-extracted"
    assert only(evaluate(p, model(), graph)).status == "unknown"


def test_manifest_json_round_trip_keeps_evidence_and_never_promotes_engineering():
    p = profile()
    result = evaluate(p, model())
    raw = result.model_dump(mode="json")
    assert raw["profile_version"] == p.version
    assert raw["results"][0]["rule_sources"][0] == source()
    assert raw["results"][0]["evidence"][0]["original_tag"] == "CB-12"
    assert raw["engineering_approved"] is False and raw["production_released"] is False


def test_rules_and_aliases_do_not_mutate_original_model():
    current = model()
    original = deepcopy(current)
    evaluate(profile(), current)
    assert current == original


def test_requested_or_specification_role_disguised_as_build_never_passes():
    actual = device()
    actual["fields"]["trip_unit"]["build"][0]["source"]["role"] = "specification"
    assert only(evaluate(profile(), model([actual]))).status == "unknown"


def test_duplicate_projection_entries_and_malformed_evidence_never_pass():
    assert only(evaluate(profile(), model([device(), device()]))).status == "unknown"
    current = model()
    current["devices"][0]["fields"]["trip_unit"]["build"][0]["source"] = None
    assert only(evaluate(profile(), current)).status == "unknown"


def test_native_resolver_accepts_generator_and_no_identity_namespace_collision():
    resolved = resolve_alias(profile(), "FDR-12", (tag for tag in ["CB-12", "KA-1"]))
    assert resolved.actual_tag == "CB-12"
    raw = profile().model_dump(mode="json")
    raw.pop("version")
    raw["aliases"][0]["physical_device_id"] = "tag:KA-1"
    with pytest.raises(ValidationError):
        seal_profile(raw)


def test_multiple_exact_quotes_in_same_guide_are_retained():
    second = source(
        location={"page": 4, "text": "Alternative tags are the same physical breaker."}
    )
    result = evaluate(profile(sources=[source(), second]), model())
    assert {citation.location["page"] for citation in only(result).rule_sources} == {
        3,
        4,
    }
