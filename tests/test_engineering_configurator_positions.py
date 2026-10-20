"""Synthetic source-position controls, not real project accuracy measurements."""

import copy

import pytest

from backend.engineering.configurator import configure


@pytest.fixture(autouse=True)
def database():
    """These catalog/configuration controls do not use a database."""
    yield


@pytest.fixture
def synthetic_requirements():
    return {
        "service_rating_a": 1200,
        "voltage_v": 480,
        "voltage_to_ground_v": 277,
        "current_type": "AC",
        "frequency_hz": 60,
        "phases": 3,
        "wires": 4,
        "grounding": "solidly_grounded_wye",
        "minimum_interrupting_ka": 65,
        "service_disconnect": True,
        "code_edition": "2020",
        "ground_fault": True,
        "metering": False,
        "spd": False,
        "confirmed": True,
        "evidence_claims": [{"id": "synthetic-position-control"}],
        "main": {
            "kind": "circuit_breaker",
            "trip_a": 1200,
            "poles": 3,
            "trip_type": "LSIG",
            "maximum_trip_a": 1200,
        },
        "feeders": [
            {
                "id": f"drawing:{section}:7",
                "tag": f"drawing:{section}:7",
                "display_tag": "7",
                "section_label": section,
                "source_local_identity": True,
                "trip_a": 100,
                "poles": 3,
                "trip_type": "LI",
                "maximum_trip_a": 100,
                "confirmed": True,
                "source_claim_ids": [f"synthetic-{section}-claim"],
            }
            for section in ("A", "B")
        ],
        "constraints": {"enclosure": "NEMA1", "line_load_connection": "opposite_side"},
    }


def _first(requirements):
    result = configure(requirements, max_options=1)
    assert len(result["configurations"]) == 1
    return result["configurations"][0]


def test_same_printed_circuit_in_two_sections_retains_unique_internal_identity(
    synthetic_requirements,
):
    before = copy.deepcopy(synthetic_requirements)

    design = _first(synthetic_requirements)

    feeders = [device for device in design["devices"] if device["role"] == "feeder"]
    assert len(feeders) == 2
    assert {device["tag"] for device in feeders} == {"drawing:A:7", "drawing:B:7"}
    assert {device["display_tag"] for device in feeders} == {"7"}
    assert {device["section_label"] for device in feeders} == {"A", "B"}
    assert all(device["source_local_identity"] is True for device in feeders)
    unsupported_metadata = {
        f"drawing:{section}:7.{field}"
        for section in ("A", "B")
        for field in ("display_tag", "section_label", "source_local_identity")
    }
    assert not unsupported_metadata.intersection(
        question["key"] for question in design["unresolved_questions"]
    )
    assert synthetic_requirements == before


@pytest.mark.parametrize(
    "position_state", ["prepared", "future", "populated", "unknown"]
)
def test_explicit_source_position_state_survives_configuration(
    synthetic_requirements, position_state
):
    synthetic_requirements["feeders"][0]["position_state"] = position_state

    design = _first(synthetic_requirements)

    device = next(
        device for device in design["devices"] if device["id"] == "drawing:A:7"
    )
    assert device["position_state"] == position_state
    assert device["display_tag"] == "7" and device["section_label"] == "A"
    assert device["source_local_identity"] is True


def test_missing_position_state_stays_unknown_without_populated_default(
    synthetic_requirements,
):
    design = _first(synthetic_requirements)

    assert all(device["position_state"] == "unknown" for device in design["devices"])
    assert all(device["supplied"] is not True for device in design["devices"])


@pytest.mark.parametrize("position_state", ["prepared", "future"])
def test_prepared_or_future_supply_conflict_is_withheld_as_an_engineer_question(
    synthetic_requirements, position_state
):
    synthetic_requirements["feeders"][0].update(
        position_state=position_state, supplied=True
    )

    design = _first(synthetic_requirements)

    device = next(
        device for device in design["devices"] if device["id"] == "drawing:A:7"
    )
    assert device["position_state"] == position_state
    assert device["supplied"] is None
    assert any(
        question["key"] == "drawing:A:7.supplied"
        for question in design["unresolved_questions"]
    )
    assert design["engineering_approved"] is False
