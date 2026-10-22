"""Synthetic software controls, not manufacturer-document accuracy results."""

import hashlib
import math
from copy import deepcopy

import pymupdf
import pytest
from pydantic import ValidationError

from backend.engineering.phase_change import (
    PhaseScenario,
    analyze,
    current_calculation,
    read_claims,
)
from backend.equipment.schemas import SourceInput


@pytest.fixture(autouse=True)
def database():
    # Pure controls must not reset any database.
    yield


def source(tmp_path, text, annotation=None):
    path = tmp_path / "control.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((30, 50), text)
        if annotation:
            page.add_freetext_annot(pymupdf.Rect(30, 450, 450, 490), annotation)
        pdf.save(path)
    return SourceInput(
        id="control",
        name=path.name,
        path=str(path),
        role="specification",
        revision="control-only",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def test_published_single_and_three_phase_calculation_examples():
    single = current_calculation(50, 240, 1)
    three = current_calculation(75, 208, 3)
    assert single["current_a"] == pytest.approx(208.333333)
    assert three["current_a"] == pytest.approx(208.1791836)
    for row in (single, three):
        assert row["arithmetic_recheck"]["passed"] and not row["engineering_verified"]
        # Test uses the inverse apparent-power relationship independently.
        factor = 1 if row["phases"] == 1 else math.sqrt(3)
        assert row["terminal_voltage_v"] * row["current_a"] * factor == pytest.approx(
            row["total_kva"] * 1000
        )


@pytest.mark.parametrize(
    "kva,volts,phases",
    [
        (True, 240, 1),
        (0, 240, 1),
        (-1, 240, 3),
        (50, 0, 1),
        (float("nan"), 240, 1),
        (50, float("inf"), 3),
        (50, 240, 2),
        (50, 240, True),
    ],
)
def test_invalid_calculation_inputs_are_not_guessed(kva, volts, phases):
    with pytest.raises(ValueError):
        current_calculation(kva, volts, phases)


@pytest.mark.parametrize("value", [True, "50", -1, 0, float("inf"), float("nan")])
def test_http_scenario_rejects_ambiguous_numeric_inputs(value):
    with pytest.raises(ValidationError):
        PhaseScenario(total_kva=value)


def test_unit_bank_and_three_windings_remain_separate(tmp_path):
    original = source(
        tmp_path,
        "Phase: Single Phase, Subtractive Polarity\nTransformer Type: 3 Separate Windings\nThree single-phase transformer units connected as a three-phase bank.\nPrimary Voltage: 480Y/277\nPrimary Line Continuous Current Ratings (at 0.075 MVA): 271 A",
    )
    snapshot = read_claims([original])
    fields = {c["field"]: c["value"] for c in snapshot["claims"]}
    assert fields["unit_phase_count"] == 1 and fields["bank_phase_count"] == 3
    assert fields["bank_unit_count"] == fields["winding_count"] == 3
    before = deepcopy(snapshot)
    result = analyze(snapshot)
    assert (
        snapshot == before
        and result["findings"][0]["code"] == "unit_and_bank_are_different"
    )
    comparison = result["rating_comparisons"][0]
    assert comparison["if_rating_is_total_bank"]["current_a"] == pytest.approx(
        90.21097956
    )
    assert comparison["if_rating_is_each_single_phase_unit"][
        "current_a"
    ] == pytest.approx(270.75812274)
    assert comparison["classification"] == "rating_basis_needs_confirmation"
    assert not comparison["engineering_error_proven"]
    assert all(
        len(c["evidence"]["bbox"]) == 4 and c["evidence"]["sha256"] == original.sha256
        for c in snapshot["claims"]
    )
    assert "path" not in str(snapshot)
    assert not result["native_execution_supported"] and not result["files_changed"]


def test_three_windings_do_not_imply_three_phases(tmp_path):
    snap = read_claims([source(tmp_path, "Transformer Type: 3 Separate Windings")])
    assert [c["field"] for c in snap["claims"]] == ["winding_count"]


def test_reviewer_overlay_is_not_a_printed_installed_phase_fact(tmp_path):
    snap = read_claims(
        [source(tmp_path, "Phase: Single Phase", annotation="Phase: Three Phase")]
    )
    assert [c["value"] for c in snap["claims"] if c["field"] == "unit_phase_count"] == [
        1
    ]


def test_changed_original_is_rejected(tmp_path):
    original = source(tmp_path, "Phase: Single Phase")
    with open(original.path, "ab") as file:
        file.write(b"\nchanged")
    with pytest.raises(ValueError, match="source changed"):
        read_claims([original])


def test_separate_text_objects_need_a_single_aligned_phase_value(tmp_path):
    original = source(tmp_path, "Number of Phases")
    path = tmp_path / "spatial.pdf"
    with pymupdf.open(original.path) as pdf:
        pdf[0].insert_text((200, 50), "1")
        pdf[0].insert_text((210, 120), "3")
        pdf.save(path)
    original = original.model_copy(
        update={
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )
    claims = read_claims([original])["claims"]
    assert any(
        c["field"] == "unit_phase_count"
        and c["value"] == 1
        and c.get("reading_method") == "spatially_aligned_explicit_row"
        for c in claims
    )


def test_rotated_cad_page_matches_the_visible_row(tmp_path):
    path = tmp_path / "rotated.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((200, 450), "Number of Phases", rotate=90)
        page.insert_text((200, 300), "1", rotate=90)
        page.set_rotation(90)
        pdf.save(path)
    original = SourceInput(
        id="rotated-control",
        name=path.name,
        path=str(path),
        role="drawing",
        revision="control",
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    found = read_claims([original])["claims"]
    assert any(c["field"] == "unit_phase_count" and c["value"] == 1 for c in found)
    assert all(
        c["evidence"]["coordinate_space"] == "visible_page_points" for c in found
    )


def test_two_phase_and_split_phase_are_not_silently_converted():
    empty = {"claims": [], "coverage": [], "sources": []}
    two = analyze(
        empty, PhaseScenario(target="two_phase", total_kva=75, terminal_voltage_v=240)
    )
    split = analyze(
        empty, PhaseScenario(target="split_phase", total_kva=75, terminal_voltage_v=240)
    )
    assert not two["scenario_calculations"] and not split["scenario_calculations"]
    assert "two_phase_ambiguous" in {b["code"] for b in two["blockers"]}
    assert "split_phase_loads_unknown" in {b["code"] for b in split["blockers"]}


def test_balanced_three_phase_what_if_never_authorizes_a_supply_conversion():
    empty = {"claims": [], "coverage": [], "sources": []}
    scenario = PhaseScenario(
        target="three_phase",
        supply="single_phase",
        total_kva=75,
        terminal_voltage_v=208,
        balanced_load=True,
    )
    out = analyze(empty, scenario)
    assert out["scenario_calculations"][0]["current_a"] == pytest.approx(208.1791836)
    assert "passive_transformer_cannot_create_three_phases" in {
        b["code"] for b in out["blockers"]
    }
    assert not out["native_execution_supported"] and not out["engineering_approved"]
    assert (
        not out["production_released"] and not out["revised_drawing_required_to_start"]
    )
    assert not out["files_changed"]


def test_three_phase_needs_balanced_assumption_and_fingerprint_tracks_it():
    empty = {"claims": [], "coverage": [], "sources": []}
    a = analyze(
        empty, PhaseScenario(target="three_phase", total_kva=75, terminal_voltage_v=208)
    )
    b = analyze(
        empty,
        PhaseScenario(
            target="three_phase",
            total_kva=75,
            terminal_voltage_v=208,
            balanced_load=True,
        ),
    )
    assert not a["scenario_calculations"] and b["scenario_calculations"]
    assert a["fingerprint"] != b["fingerprint"]


def test_existing_cad_and_bom_do_not_claim_ready_or_request_duplicate_files():
    data = {
        "claims": [],
        "coverage": [],
        "sources": [
            {"role": "drawing", "name": "existing.dxf"},
            {"role": "bom", "name": "existing.xlsx"},
        ],
    }
    report = analyze(data)
    assert "native_inputs_needed" not in {b["code"] for b in report["blockers"]}
    assert "native_mapping_unverified" in {b["code"] for b in report["blockers"]}
    assert "transformer_conversion_writer_not_implemented" in {
        b["code"] for b in report["blockers"]
    }
    assert not report["native_execution_supported"]
