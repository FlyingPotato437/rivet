"""Pure synthetic source-role controls; no real-data accuracy claim."""

from copy import deepcopy

import pytest

from backend.engineering.requirements import (
    extract_requirements,
    merge_drawing_readings,
    to_config_inputs,
)
from backend.engineering.source_roles import propose_source_role_decision


@pytest.fixture(autouse=True)
def database():
    yield  # No database for these pure controls.


SHA = "a" * 64


def prop(feature, value, text, unit=None):
    return {
        "feature": feature,
        "value": value,
        "raw_text": text,
        "unit": unit,
        "bbox": [0.1, 0.1, 0.2, 0.2],
        "confidence": "high",
    }


def obj(key, *, label=None, circuit=None, section=None, properties=None):
    return {
        "key": key,
        "kind": "device_symbol",
        "raw_label": label,
        "raw_tag": None,
        "circuit_label": circuit,
        "section_label": section,
        "position_state": "populated",
        "properties": properties
        if properties is not None
        else [prop("rated_current_a", 225, "225A", "A"), prop("pole_count", 3, "3P")],
    }


def report(objects, *, page=2, sha=SHA, source="customer-drawing"):
    return {
        "source_id": source,
        "sha256": sha,
        "version": "synthetic-control",
        "prompt_sha256": "b" * 64,
        "pages": [
            {
                "page": page,
                "reading": {
                    "objects": objects,
                    "conductors": [],
                    "labels": [],
                    "unknown_regions": [],
                },
            }
        ],
    }


def projected(objects=None):
    return merge_drawing_readings(
        extract_requirements([]), [report(objects or [obj("observed-1")])]
    )


def decide(requirements=None, *, key="observed-1", role="feeder", **kwargs):
    return propose_source_role_decision(
        requirements or projected(),
        source_id=kwargs.get("source_id", "customer-drawing"),
        source_sha=kwargs.get("source_sha", SHA),
        page=kwargs.get("page", 2),
        source_object_key=key,
        role=role,
        reason=kwargs.get("reason", "Engineer reviewed this exact observed object"),
    )


def test_bare_ampere_and_pole_observations_are_preserved_without_protective_role_or_invented_ratings():
    empty = extract_requirements([])
    reports = [report([obj(f"device-{i}", circuit=str(i)) for i in range(15)])]
    before = deepcopy((empty, reports))
    result = merge_drawing_readings(empty, reports)
    assert len(result["items"]) == 30
    assert len(result["reference_observations"]) == 15
    assert all(
        row["unresolved_role"] and row["configuration_eligible"] is False
        for row in result["items"]
    )
    assert all(
        row["observation_scope"] == "source_equipment_observation"
        for row in result["items"]
    )
    assert {row["key"] for row in result["items"]} == {"rated_current_a", "poles"}
    assert len({row["subject_id"] for row in result["items"]}) == 15
    assert not to_config_inputs(result)["feeders"] and "main" not in to_config_inputs(
        result
    )
    assert (empty, reports) == before


def test_downstream_panel_mcb_and_bare_main_label_are_not_new_common_main_or_feeders():
    result = projected(
        [obj("panel-mcb", label="PANEL LP-2 MCB"), obj("main-label", label="MAIN")]
    )
    inputs = to_config_inputs(result)
    assert "main" not in inputs and not inputs["feeders"]
    assert len(result["items"]) == 4
    assert all(row["subject_id"].startswith("drawing:") for row in result["items"])


def test_existing_explicit_frame_trip_protective_observation_is_not_reclassified_by_retention_fix():
    result = projected(
        [
            obj(
                "breaker",
                properties=[
                    prop("frame_a", 250, "250AF", "A"),
                    prop("trip_a", 225, "225AT", "A"),
                ],
            )
        ]
    )
    assert len(to_config_inputs(result)["feeders"]) == 1
    assert all(
        row["configuration_eligible"] is True and not row["unresolved_role"]
        for row in result["items"]
    )


def test_explicit_feeder_decision_confirms_only_one_object_and_retains_immutable_observations():
    requirements = projected([obj("observed-1"), obj("observed-2")])
    before = deepcopy(requirements)
    result = decide(requirements)
    updated = result["requirements_updated"]
    feeder = to_config_inputs(updated)["feeders"][0]
    assert requirements == before
    assert len(to_config_inputs(updated)["feeders"]) == 1
    assert feeder["rated_current_a"] == 225 and feeder["poles"] == 3
    assert (
        "frame_a" not in feeder and "trip_a" not in feeder and "supplied" not in feeder
    )
    assert updated["evidence_claims"][:4] == before["evidence_claims"]
    assert updated["reference_observations"] == before["reference_observations"]
    assert all(
        row["confirmed"] and row["role_engineering_verified"]
        for row in updated["items"]
        if row["source_object_key"] == "observed-1"
    )
    assert all(
        row["unresolved_role"] and not row["confirmed"]
        for row in updated["items"]
        if row["source_object_key"] == "observed-2"
    )
    assert result["requires_reconfiguration"] is True
    assert result["decision"]["engineering_approved"] is False


def test_explicit_main_decision_does_not_convert_bare_current_to_frame_or_trip():
    result = decide(role="main")
    main = to_config_inputs(result["requirements_updated"])["main"]
    assert main["rated_current_a"]["value"] == 225
    assert main["poles"]["value"] == 3
    assert "frame_a" not in main and "trip_a" not in main and "kind" not in main
    assert not to_config_inputs(result["requirements_updated"])["feeders"]
    assert {row["key"] for row in result["requirements_updated"]["items"]} == {
        "main.rated_current_a",
        "main.poles",
    }


def test_reference_is_visible_with_values_and_claims_but_excluded_from_configuration():
    result = decide(role="reference")
    updated = result["requirements_updated"]
    assert len(updated["items"]) == 2 and len(updated["reference_observations"]) == 1
    assert {row["value"] for row in updated["items"]} == {225, 3}
    assert all(
        row["equipment_role"] == "reference"
        and not row["configuration_eligible"]
        and not row.get("dismissed")
        for row in updated["items"]
    )
    assert not to_config_inputs(updated)["feeders"] and "main" not in to_config_inputs(
        updated
    )


def test_repeated_circuit_labels_across_sections_do_not_merge_or_cross_apply_role_decision():
    requirements = projected(
        [
            obj("left-7", circuit="7", section="LEFT"),
            obj("right-7", circuit="7", section="RIGHT"),
        ]
    )
    assert len({row["subject_id"] for row in requirements["items"]}) == 2
    result = decide(requirements, key="left-7")
    updated = result["requirements_updated"]
    assert len(to_config_inputs(updated)["feeders"]) == 1
    assert to_config_inputs(updated)["feeders"][0]["section_label"] == "LEFT"
    assert all(
        row["unresolved_role"]
        for row in updated["items"]
        if row["source_object_key"] == "right-7"
    )
    with pytest.raises(ValueError, match="matches"):
        decide(requirements, key="7")


def test_same_object_key_on_other_page_remains_a_different_group():
    requirements = merge_drawing_readings(
        extract_requirements([]),
        [report([obj("same-key")], page=2), report([obj("same-key")], page=3)],
    )
    result = decide(requirements, key="same-key")
    assert len(to_config_inputs(result["requirements_updated"])["feeders"]) == 1
    assert all(
        row["unresolved_role"]
        for row in result["requirements_updated"]["items"]
        if row["source_refs"][0]["page"] == 3
    )


def test_second_main_requires_a_separate_explicit_decision_for_the_first_main():
    requirements = projected([obj("observed-1"), obj("observed-2")])
    first = decide(requirements, role="main")["requirements_updated"]
    with pytest.raises(ValueError, match="different source object"):
        decide(first, key="observed-2", role="main")
    reference = decide(first, role="reference")["requirements_updated"]
    second = decide(reference, key="observed-2", role="main")["requirements_updated"]
    assert {
        row["source_object_key"]
        for row in second["items"]
        if row["key"].startswith("main.")
    } == {"observed-2"}
    assert all(
        row["equipment_role"] == "reference"
        for row in second["items"]
        if row["source_object_key"] == "observed-1"
    )


def test_each_new_property_claim_is_derived_from_original_and_role_claim_with_explicit_decision_scope():
    requirements = projected()
    result = decide(requirements)
    role_claim, *properties = result["derived_claims"]
    assert (
        role_claim["feature"] == "source_equipment_role"
        and role_claim["value"] == "feeder"
    )
    assert set(role_claim["derived_from"]) == {
        claim["id"] for claim in requirements["evidence_claims"]
    }
    for old, new in zip(requirements["items"], properties, strict=True):
        assert new["derived_from"] == [*old["source_claim_ids"], role_claim["id"]]
        assert (
            new["conditions"]["scope"]
            == "explicit_engineer_source_equipment_role_decision"
        )
        assert new["source"]["sha256"] == SHA and new["source"]["page"] == 2
        assert new["state"] == "proposed" and new["value"] == old["value"]


def test_role_decision_does_not_confirm_unknown_values_or_clear_other_conflicts_and_warnings():
    requirements = projected(
        [obj("observed-1", properties=[prop("rated_current_a", None, "?A", "A")])]
    )
    requirements["conflicts"] = [
        {"id": "unrelated", "item_ids": ["other-1", "other-2"]}
    ]
    requirements["warnings"] = [
        {"reason": "Unknown drawing area", "verification_required": True}
    ]
    result = decide(requirements)["requirements_updated"]
    assert result["items"][0]["value"] is None and not result["items"][0]["confirmed"]
    assert result["items"][0]["role_engineering_verified"] is True
    assert requirements["conflicts"][0] in result["conflicts"]
    assert result["warnings"] == requirements["warnings"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_id": "other-source"},
        {"source_sha": "c" * 64},
        {"page": 3},
        {"source_sha": "bad"},
        {"page": True},
        {"page": 0},
        {"role": "load"},
        {"key": ""},
        {"reason": " "},
    ],
)
def test_role_requires_exact_current_source_hash_page_object_and_explicit_reason(
    kwargs,
):
    with pytest.raises(ValueError):
        decide(**kwargs)


def test_dismissed_excluded_non_customer_and_missing_original_claims_are_not_promoted():
    for change in (
        {"dismissed": True},
        {"excluded": True},
        {"origin_role": "manufacturer_reference"},
    ):
        requirements = projected()
        requirements["items"][0].update(change)
        with pytest.raises(ValueError):
            decide(requirements)
    requirements = projected()
    requirements["evidence_claims"] = []
    with pytest.raises(ValueError, match="immutable"):
        decide(requirements)


def test_bus_observation_cannot_receive_a_device_role_and_repeated_calls_are_deterministic():
    bus = obj("bus", properties=[prop("bus_current_a", 2000, "2000A BUS", "A")])
    bus["kind"] = "busbar"
    requirements = projected([bus])
    with pytest.raises(ValueError, match="device observations"):
        decide(requirements, key="bus", role="main")
    assert decide() == decide()
