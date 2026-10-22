from __future__ import annotations

import pytest

from backend.engineering.cad_units import audit_cad_units


@pytest.fixture(autouse=True)
def database():
    """Keep this audit test suite independent of the repository DB fixture."""
    yield


def dim(handle, measured, points, *, scope="MODEL", frame="V1", override=None, display=None):
    return {
        "handle": handle,
        "actual_measurement": measured,
        "display_text": display,
        "text_override": override,
        "point_distance": points,
        "scope_id": scope,
        "frame_id": frame,
    }


@pytest.mark.parametrize(
    ("unit_text", "measured", "points", "expected_unit", "ratio"),
    [
        ("Dimensions in inches", 25.4, 1, "inch", 25.4),
        ("All dimensions mm", 1000, 1000, "millimetre", 1),
    ],
)
def test_multiple_independent_dimensions_verify_scale_with_explicit_unit(
    unit_text, measured, points, expected_unit, ratio
):
    result = audit_cad_units(
        [dim("D1", measured, points), dim("D2", measured * 2, points * 2)],
        [{"handle": "T1", "text": unit_text, "scope_id": "MODEL", "frame_id": "V1"}],
        header_insunits=1,
    )

    assert result["status"] == "insufficient"
    assert result["measurement_point_evidence"]["status"] == "verified"
    frame = result["measurement_point_evidence"]["frames"][0]
    assert frame["measurement_to_point_ratio"] == pytest.approx(ratio)
    assert frame["dimension_handles"] == ["D1", "D2"]
    assert frame["unit_evidence_handles"] == ["T1"]
    assert result["physical_unit_calibration"]["status"] == "insufficient"
    assert result["physical_unit_calibration"]["explicit_unit_labels"] == [expected_unit]
    assert result["scale_evidence"]["verified_physical_scale"] is None
    assert result["header_evidence"]["authoritative"] is False


def test_header_units_alone_cannot_verify_scale():
    result = audit_cad_units(
        [dim("D1", 25.4, 1), dim("D2", 50.8, 2)], [], header_insunits=1
    )

    assert result["status"] == "insufficient"
    assert result["scale_evidence"]["verified_physical_scale"] is None


def test_numeric_dimension_display_override_is_preserved_but_never_used():
    result = audit_cad_units(
        [
            dim("D1", 25.4, 1, override="1.0"),
            dim("D2", 50.8, 2, override="2.0"),
        ],
        [{"handle": "T1", "text": "inches", "scope_id": "MODEL", "frame_id": "V1"}],
    )

    assert result["status"] == "insufficient"
    assert all(not item["eligible_for_measurement_point_consistency"] for item in result["dimension_evidence"])
    assert "override" in result["dimension_evidence"][0]["exclusion_reason"]
    assert result["dimension_evidence"][0]["text_override"] == "1.0"


def test_mixed_frames_are_not_combined_into_a_verified_scale():
    result = audit_cad_units(
        [
            dim("D1", 25.4, 1, frame="V1"),
            dim("D2", 50.8, 2, frame="V1"),
            dim("D3", 10, 1, frame="V2"),
            dim("D4", 20, 2, frame="V2"),
        ],
        [{"handle": "T1", "text": "inches", "scope_id": "MODEL", "frame_id": "V1"}],
    )

    assert result["status"] == "insufficient"
    assert result["measurement_point_evidence"]["status"] == "insufficient"
    assert result["scale_evidence"]["verified_physical_scale"] is None
    assert "multiple scopes or frames" in result["measurement_point_evidence"]["reason"]


def test_disagreeing_dimensions_in_one_frame_are_conflicting():
    result = audit_cad_units(
        [dim("D1", 25.4, 1), dim("D2", 30, 1)],
        [{"handle": "T1", "text": "inches", "scope_id": "MODEL", "frame_id": "V1"}],
    )

    assert result["status"] == "conflicting"
    assert result["measurement_point_evidence"]["frames"][0]["status"] == "conflicting"
    assert result["scale_evidence"]["verified_physical_scale"] is None


def test_unknown_unit_text_and_malformed_dimension_values_are_insufficient():
    result = audit_cad_units(
        [
            dim("D1", 0, 1),
            dim("D2", "nan", 1),
            dim("D3", "not-a-number", 1),
            dim("D4", float("inf"), 1),
        ],
        [{"handle": "T1", "text": "Units to be confirmed", "scope_id": "MODEL", "frame_id": "V1"}],
    )

    assert result["status"] == "insufficient"
    assert result["unit_evidence"][0]["explicit"] is False
    assert result["dimension_evidence"][0]["actual_measurement"] == 0
    assert all(item["actual_measurement"] is None for item in result["dimension_evidence"][1:])
    assert all(not item["eligible_for_measurement_point_consistency"] for item in result["dimension_evidence"])


def test_scope_and_frame_unit_evidence_must_match_dimension_evidence():
    result = audit_cad_units(
        [dim("D1", 25.4, 1), dim("D2", 50.8, 2)],
        [{"handle": "T1", "text": "inches", "scope_id": "MODEL", "frame_id": "OTHER"}],
    )

    assert result["status"] == "insufficient"
    assert result["measurement_point_evidence"]["frames"][0]["units"] == []


@pytest.mark.parametrize("label", ["made in China", "put in cabinet"])
def test_generic_english_in_does_not_mean_inches(label):
    result = audit_cad_units([], [{"handle": "T1", "text": label, "scope_id": "MODEL", "frame_id": "V1"}])

    assert result["unit_evidence"][0]["units"] == []
    assert result["physical_unit_calibration"]["explicit_unit_labels"] == []


def test_in_abbreviation_requires_explicit_unit_notation():
    result = audit_cad_units([], [{"handle": "T1", "text": "Units: in", "scope_id": "MODEL", "frame_id": "V1"}])

    assert result["unit_evidence"][0]["units"] == ["inch"]


@pytest.mark.parametrize(
    "changes",
    [
        {"handle": None},
        {"scope_id": None},
        {"frame_id": None},
    ],
)
def test_missing_identity_or_frame_context_cannot_verify(changes):
    first = dim("D1", 25.4, 1)
    first.update(changes)
    result = audit_cad_units(
        [first, dim("D2", 50.8, 2)],
        [{"handle": "T1", "text": "inches", "scope_id": "MODEL", "frame_id": "V1"}],
    )

    assert result["measurement_point_evidence"]["status"] == "insufficient"


def test_anonymous_list_records_are_not_given_manufactured_independent_ids():
    result = audit_cad_units(
        [
            {"actual_measurement": 25.4, "point_distance": 1, "scope_id": "MODEL", "frame_id": "V1"},
            {"actual_measurement": 50.8, "point_distance": 2, "scope_id": "MODEL", "frame_id": "V1"},
        ],
        [],
    )

    assert result["measurement_point_evidence"]["status"] == "insufficient"
    assert all(item["handle"] is None for item in result["dimension_evidence"])


def test_mapping_input_truncation_is_bounded_reported_and_disqualifies_verification():
    dimensions = {
        f"D{index}": {
            "actual_measurement": 10 * index,
            "point_distance": index,
            "scope_id": "MODEL",
            "frame_id": "V1",
        }
        for index in range(1, 5)
    }
    result = audit_cad_units(
        dimensions,
        {"T1": {"text": "mm", "scope_id": "MODEL", "frame_id": "V1"}},
        max_items=2,
    )

    assert result["limits"]["dimensions_seen"] == 2
    assert result["limits"]["truncation"] == {"dimensions": True, "unit_texts": False, "any": True}
    assert result["measurement_point_evidence"]["status"] == "insufficient"
    assert result["status"] == "insufficient"


def test_explicit_unit_label_and_dimension_point_ratio_do_not_claim_physical_scale():
    result = audit_cad_units(
        [dim("D1", 750, 75), dim("D2", 1500, 150)],
        [{"handle": "T1", "text": "inches", "scope_id": "MODEL", "frame_id": "V1"}],
    )

    assert result["measurement_point_evidence"]["status"] == "verified"
    assert result["measurement_point_evidence"]["frames"][0]["measurement_to_point_ratio"] == pytest.approx(10)
    assert result["physical_unit_calibration"]["status"] == "insufficient"
    assert result["scale_evidence"]["verified_physical_scale"] is None
