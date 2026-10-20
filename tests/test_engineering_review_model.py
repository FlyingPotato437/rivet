"""Pure synthetic controls for explicit decisions, never real accuracy evidence."""

from copy import deepcopy

import pytest

from backend.engineering.configurator import _digest, _system
from backend.engineering.requirements import to_config_inputs
from backend.engineering.review_model import propose_comment_model_edit
from backend.equipment.evidence.schema import make_claim


@pytest.fixture(autouse=True)
def database():
    """Do not connect to or reset a database for these pure controls."""
    yield


def control():
    source = {
        "source_id": "original-spec",
        "sha256": "a" * 64,
        "page": 3,
        "text": "Original scalar source",
    }
    requirements = {
        "items": [],
        "evidence_claims": [],
        "conflicts": [],
        "warnings": [],
        "unreadable_pages": [],
    }
    for identifier, subject, key, value, unit in [
        ("req-trip", "F1", "trip_a", 225, "A"),
        ("req-poles", "F1", "poles", 3, None),
        ("req-main", "order", "main.trip_a", 1200, "A"),
        ("req-voltage", "order", "voltage_v", 480, "V"),
    ]:
        claim = make_claim(
            subject,
            key,
            value,
            source=source,
            unit=unit,
            extractor={"name": "synthetic-source-control"},
            state="proposed",
        )
        requirements["evidence_claims"].append(claim)
        requirements["items"].append(
            {
                "id": identifier,
                "subject_id": subject,
                "key": key,
                "value": value,
                "unit": unit,
                "source_claim_ids": [claim["id"]],
                "source_refs": [deepcopy(source)],
                "origin_role": "specification",
                "confirmed": True,
                "state": "proposed",
            }
        )
    inputs = to_config_inputs(requirements)
    design = {
        "model_id": "synthetic-model",
        "model_version": 7,
        "requirements_digest": _digest(inputs),
        "system": _system(inputs),
        "state": "proposed",
        "fully_configured": True,
        "engineering_approved": True,
        "production_released": True,
        "rule_results": [{"status": "pass"}],
        "unresolved_questions": [],
        "devices": [
            {"id": "main", "role": "main", "tag": "MAIN", "trip_a": 1200},
            {
                "id": "F1",
                "role": "feeder",
                "tag": "FDR-1",
                "trip_a": 225,
                "rated_current_a": 225,
                "frame_a": 250,
                "pole_count": 3,
                "trip_type": "LI",
                "part_number": "TEST-225",
                "nameplate": "ORIGINAL",
                "catalog_variant_id": "unchanged-variant",
                "section_id": "section-1",
                "position_id": "position-1",
            },
            {"id": "F2", "role": "feeder", "tag": "FDR-2", "trip_a": 400},
        ],
        "sections": [
            {
                "id": "section-1",
                "positions": [
                    {
                        "id": "position-1",
                        "device_id": "F1",
                        "start_in": 0,
                        "mounting_space_in": 4.5,
                    }
                ],
            }
        ],
        "topology": {"bus_id": "bus", "bus_a": 2000, "connections": []},
    }
    comment = {
        "source_id": "returned-markup",
        "sha256": "b" * 64,
        "page": 2,
        "comment_id": "comment-1",
        "text": "FDR-1: change trip to 250 A.",
        "evidence_ids": ["comment-span-1"],
        "same_order_checked": True,
    }
    return requirements, design, comment


def apply(
    values=None,
    *,
    requirement_id="req-trip",
    value=250,
    reason="Engineer explicitly accepted this comment",
):
    requirements, design, comment = values or control()
    return propose_comment_model_edit(
        requirements,
        design,
        requirement_id=requirement_id,
        value=value,
        comment_ref=comment,
        reason=reason,
    )


def test_one_explicit_trip_decision_preserves_original_inputs_claims_and_unrelated_model_structure():
    values = control()
    before = deepcopy(values)
    result = apply(values)
    requirement = result["requirements_updated"]["items"][0]
    device = result["design_updated"]["devices"][1]
    assert values == before
    assert requirement["value"] == device["trip_a"] == 250
    assert requirement["confirmed"] is True
    assert (
        device["rated_current_a"] == 225
    )  # Revalidation, never an implicit sibling repair.
    assert (
        device["frame_a"] == 250 and device["catalog_variant_id"] == "unchanged-variant"
    )
    assert result["design_updated"]["sections"] == before[1]["sections"]
    assert result["design_updated"]["topology"] == before[1]["topology"]
    assert result["design_updated"]["devices"][0] == before[1]["devices"][0]
    assert result["design_updated"]["devices"][2] == before[1]["devices"][2]
    assert (
        result["requirements_updated"]["evidence_claims"][:4]
        == before[0]["evidence_claims"]
    )


def test_source_comment_observation_and_engineer_decision_have_distinct_scopes_and_ancestry():
    requirements, _design, comment = control()
    result = apply()
    observation, derived = result["derived_claims"]
    assert observation["state"] == "observed"
    assert observation["value"] == comment["text"]
    assert observation["conditions"]["scope"] == "verbatim_source_comment_observation"
    assert derived["state"] == "proposed"
    assert derived["conditions"]["scope"] == "explicit_engineer_requirement_decision"
    assert derived["derived_from"] == [
        *requirements["items"][0]["source_claim_ids"],
        observation["id"],
    ]
    assert derived["source"]["source_id"] == comment["source_id"]
    assert derived["source"]["sha256"] == comment["sha256"]
    assert derived["source"]["page"] == 2
    row = result["requirements_updated"]["items"][0]
    assert row["source_refs"][0] == requirements["items"][0]["source_refs"][0]
    assert row["source_refs"][-1]["comment_id"] == comment["comment_id"]
    assert row["value_origin"] == "explicit_engineer_comment_decision"


def test_no_model_version_advance_catalog_approval_or_cached_pass_flags_are_produced():
    result = apply()
    model = result["design_updated"]
    assert model["model_version"] == 7
    assert result["requires_revalidation"] is True
    assert model["validation_pending"] is True and model["fully_configured"] is False
    assert model["engineering_approved"] is model["production_released"] is False
    assert "rule_results" not in model and "unresolved_questions" not in model
    assert model["requirements_digest"] == _digest(
        to_config_inputs(result["requirements_updated"])
    )


@pytest.mark.parametrize(
    "key,value,field",
    [
        ("frame_a", 400, "frame_a"),
        ("trip_a", "250", "trip_a"),
        ("poles", "4", "pole_count"),
        ("trip_type", "LSI", "trip_type"),
        ("nameplate", "NEW LABEL", "nameplate"),
        ("part_number", "EXPLICIT-NEW-PART", "part_number"),
    ],
)
def test_supported_device_fields_use_exact_mapping_without_related_defaults(
    key, value, field
):
    values = control()
    values[0]["items"][0].update(key=key, unit=None)
    result = apply(values, value=value)
    expected = int(value) if key in {"frame_a", "trip_a", "poles"} else value
    assert result["design_updated"]["devices"][1][field] == expected
    assert result["decision"]["model_device_id"] == "F1"


def test_main_prefix_targets_the_single_main_and_not_a_feeder():
    result = apply(requirement_id="req-main", value=1000)
    assert result["design_updated"]["devices"][0]["trip_a"] == 1000
    assert result["design_updated"]["devices"][1]["trip_a"] == 225


def test_system_decision_reprojects_explicit_system_with_original_and_comment_provenance():
    values = control()
    result = apply(values, requirement_id="req-voltage", value="208")
    system = result["design_updated"]["system"]
    assert system["voltage_v"] == 208
    assert system["frequency_hz"] is None  # No electrical default.
    assert (
        system["provenance"]["voltage_v"]["source_refs"][0]
        == values[0]["items"][3]["source_refs"][0]
    )
    assert (
        system["provenance"]["voltage_v"]["source_refs"][-1]["comment_id"]
        == "comment-1"
    )
    assert result["design_updated"]["devices"] == values[1]["devices"]
    assert result["design_updated"]["topology"] == values[1]["topology"]


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("available_fault_ka", 30, 30),
        ("short_time_withstand_ka", "25", 25),
        ("service_disconnect", "no", False),
        ("code_edition", 2020, "2020"),
    ],
)
def test_system_types_remain_explicit_and_typed(key, value, expected):
    values = control()
    values[0]["items"][3].update(key=key, unit=None)
    assert (
        apply(values, requirement_id="req-voltage", value=value)["design_updated"][
            "system"
        ][key]
        == expected
    )


def test_other_conflicts_unknowns_and_requirement_confirmations_are_not_silently_resolved():
    values = control()
    other = {
        "id": "other-conflict",
        "item_ids": ["unrelated-1", "unrelated-2"],
        "reason": "Unresolved manufacturer scope",
    }
    values[0]["conflicts"] = [other]
    values[0]["items"][1]["confirmed"] = False
    values[0]["warnings"] = [
        {"verification_required": True, "reason": "Unreadable source"}
    ]
    result = apply(values)
    assert other in result["requirements_updated"]["conflicts"]
    assert result["requirements_updated"]["items"][1]["confirmed"] is False
    assert result["requirements_updated"]["warnings"] == values[0]["warnings"]


def test_another_document_value_disagreeing_with_the_decision_remains_a_conflict():
    values = control()
    extra = deepcopy(values[0]["items"][0])
    extra["id"] = "other-source-same-requirement"
    values[0]["items"].append(extra)
    result = apply(values)
    assert any(
        set(conflict["item_ids"]) == {"req-trip", "other-source-same-requirement"}
        for conflict in result["requirements_updated"]["conflicts"]
    )


@pytest.mark.parametrize(
    "change", [{"dismissed": True}, {"excluded": True}, {"unresolved_role": True}]
)
def test_scope_exclusions_dismissals_and_ambiguous_owners_require_separate_decision(
    change,
):
    values = control()
    values[0]["items"][0].update(change)
    with pytest.raises(ValueError, match="scope decision"):
        apply(values)


@pytest.mark.parametrize(
    "key",
    [
        "main.mounting",
        "main.reuse_existing",
        "main.kind",
        "service_side",
        "position_state",
        "unknown_field",
    ],
)
def test_unsupported_geometry_reuse_scope_or_main_fields_are_rejected(key):
    values = control()
    values[0]["items"][0].update(subject_id="order", key=key)
    with pytest.raises(ValueError, match="supported"):
        apply(values, value="explicit human value")


@pytest.mark.parametrize(
    "value", [None, True, 0, -10, float("nan"), float("inf"), "225/250"]
)
def test_invalid_numeric_decisions_are_rejected(value):
    with pytest.raises(ValueError):
        apply(value=value)


def test_fractional_poles_or_boolean_text_properties_are_rejected():
    with pytest.raises(ValueError, match="whole"):
        apply(requirement_id="req-poles", value=2.5)
    values = control()
    values[0]["items"][0].update(key="trip_type", unit=None)
    with pytest.raises(ValueError, match="text value"):
        apply(values, value=True)


@pytest.mark.parametrize(
    "change",
    [
        {"same_order_checked": False},
        {"source_id": ""},
        {"sha256": "wrong"},
        {"page": True},
        {"page": 0},
        {"comment_id": ""},
        {"text": ""},
        {"evidence_ids": []},
        {"evidence_ids": [None]},
    ],
)
def test_comment_requires_explicit_checked_order_hash_page_identity_and_evidence(
    change,
):
    values = control()
    values[2].update(change)
    with pytest.raises(ValueError):
        apply(values)


def test_source_local_id_is_exact_and_display_label_cannot_pick_a_device():
    values = control()
    values[0]["items"][0]["subject_id"] = "7"
    values[1]["devices"][1].update(
        id="drawing:opaque-1", tag="drawing:opaque-1", display_tag="7"
    )
    values[1]["devices"][2].update(
        id="drawing:opaque-2", tag="drawing:opaque-2", display_tag="7"
    )
    with pytest.raises(ValueError, match="exactly one"):
        apply(values)
    values[0]["items"][0]["subject_id"] = "drawing:opaque-1"
    assert apply(values)["design_updated"]["devices"][1]["trip_a"] == 250


def test_duplicate_main_and_missing_original_claims_are_rejected():
    values = control()
    values[1]["devices"].append({"id": "second-main", "role": "main"})
    with pytest.raises(ValueError, match="exactly one"):
        apply(values, requirement_id="req-main")
    values = control()
    values[0]["evidence_claims"] = []
    with pytest.raises(ValueError, match="immutable evidence"):
        apply(values)


def test_no_text_parser_overrides_the_explicit_engineer_decision_and_output_is_deterministic():
    values = control()
    values[2]["text"] = (
        "Ambiguous customer note; requires an engineer decision, no inferred value."
    )
    first = apply(values, value=250)
    assert first == apply(values, value=250)
    assert first["design_updated"]["devices"][1]["trip_a"] == 250
    assert first["derived_claims"][0]["value"] == values[2]["text"]


def test_absent_or_duplicate_requirement_and_missing_reason_are_rejected():
    with pytest.raises(ValueError, match="exactly one"):
        apply(requirement_id="not-current")
    values = control()
    values[0]["items"].append(deepcopy(values[0]["items"][0]))
    with pytest.raises(ValueError, match="exactly one"):
        apply(values)
    with pytest.raises(ValueError, match="reason"):
        apply(reason=" ")
