"""Synthetic software controls; these do not count as manufacturer accuracy."""

from copy import deepcopy

import ezdxf
import pymupdf
import pytest

from backend.engineering.cad_snapshot import snapshot_dxf
from backend.engineering.comment_redesign import build_package, file_hash
from backend.engineering.drawing_calculations import (
    propose_current_corrections,
    verify_saved_calculations,
)


@pytest.fixture(autouse=True)
def database():
    yield


def fixture(tmp_path, *, upper="10", phase="1"):
    cad = ezdxf.new("R2010")
    model = cad.modelspace()

    def label(value, x, y):
        return model.add_text(value, dxfattribs={"insert": (x, y), "height": 2})

    def box(x0, y0, x1, y1):
        model.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True)

    box(0, 0, 30, 20)
    label("VOLTS(V)", 5, 15)
    box(30, 10, 90, 20)
    label("AMPS(A)", 45, 15)
    box(30, 0, 60, 10)
    label("5 MVA", 35, 5)
    box(60, 0, 90, 10)
    label(upper + " MVA", 65, 5)
    for i, (voltage, low, high) in enumerate(
        [(10000, 499, 999), (12500, 399, 799), (15000, 332, 666)]
    ):
        y = -10 * i
        for x in (0, 30, 60):
            box(x, y - 10, x + 30, y)
        label(str(voltage), 5, y - 5)
        label(str(low), 35, y - 5)
        label(str(high), 65, y - 5)
    label("Number of Phases", -150, -60)
    box(-60, -64, -10, -56)
    label(phase, -59, -60)
    label("Tertiary Capacity", -150, -80)
    box(-60, -84, -10, -76)
    label("5/7.5/10MVA@55°C", -59, -80)
    path = tmp_path / "source.dxf"
    cad.saveas(path)
    pdf_path = tmp_path / "comments.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.add_freetext_annot(
            pymupdf.Rect(20, 20, 300, 120), "10000 V\n12500 V\n15000 V"
        )
        page.add_freetext_annot(
            pymupdf.Rect(20, 150, 400, 250),
            "Update amp values to reflect correct voltages",
        )
        pdf.save(pdf_path)
    sources = [
        {
            "id": "markup",
            "role": "markup",
            "name": pdf_path.name,
            "path": str(pdf_path),
            "sha256": file_hash(pdf_path),
        }
    ]
    source = {
        "id": "native",
        "role": "drawing",
        "name": path.name,
        "path": str(path),
        "sha256": file_hash(path),
    }
    from backend.engineering.comment_redesign import comments_from_pdfs

    return sources, source, snapshot_dxf(path), comments_from_pdfs(sources)


def test_supported_current_cells_saved_and_independently_calculated(tmp_path):
    sources, source, before, _ = fixture(tmp_path)
    result = build_package(sources, source, tmp_path / "out")
    assert len(result["changes"]) == 6
    assert {c["kind"] for c in result["changes"]} == {"cad_calculation"}
    assert result["saved_calculation_check"]["cells_checked"] == 6
    assert result["saved_calculation_check"]["passed"]
    assert not result["saved_calculation_check"]["engineering_verified"]
    saved = ezdxf.readfile(tmp_path / "out/Proposed-drawing.dxf")
    values = {e.dxf.text for e in saved.modelspace().query("TEXT")}
    assert {"500", "400", "333", "1000", "800", "667"} <= values
    assert not ({"499", "399", "332", "999", "799", "666"} & values)
    assert result["verification"]["preservation_verified"]
    assert result["saved_drawing_check"]["supported_geometry_unchanged"]
    assert file_hash(source["path"]) == before["source_sha256"]
    assert result["totals"]["already_present"] == 1
    assert not result["approval_eligible"]
    assert not result["equipment_evidence"]["production_bom_verified"]


def test_conflicting_capacity_blocks_only_affected_column(tmp_path):
    sources, source, snapshot, comments = fixture(tmp_path, upper="10.01")
    edits, audits = propose_current_corrections(comments, snapshot)
    assert len(edits) == 3
    assert audits[0]["blocked_columns"][0]["code"] == "capacity_conflict"
    result = build_package(sources, source, tmp_path / "out")
    saved = ezdxf.readfile(tmp_path / "out/Proposed-drawing.dxf")
    values = {e.dxf.text for e in saved.modelspace().query("TEXT")}
    assert {"999", "799", "666", "500", "400", "333"} <= values
    assert result["saved_calculation_check"]["cells_checked"] == 3


@pytest.mark.parametrize(
    "fault",
    [
        "phase",
        "units",
        "duplicate",
        "rotation",
        "wrong_order",
        "source",
        "capacity",
        "missing_geometry",
        "extra_cell_text",
        "missing_voltage",
    ],
)
def test_ambiguity_never_produces_a_numeric_edit(tmp_path, fault):
    _, _, s, comments = fixture(tmp_path)

    def item(value):
        return next(t for t in s["placed_text"] if t["text"] == value)

    if fault == "phase":
        item("1")["text"] = "3"
    elif fault == "units":
        item("VOLTS(V)")["text"] = "VOLTS(kV)"
    elif fault == "duplicate":
        duplicate = deepcopy(item("10000"))
        duplicate["locator"] += "copy"
        s["placed_text"].append(duplicate)
    elif fault == "rotation":
        item("499")["text_rotation"] = 90
    elif fault == "wrong_order":
        comments[0]["text"] = "15000 V\n12500 V\n10000 V"
    elif fault == "source":
        comments[0]["source_document_id"] = "unrelated"
    elif fault == "capacity":
        item("Tertiary Capacity")["text"] = "Unidentified capacity"
    elif fault == "missing_geometry":
        s["placed_paths"] = []
    elif fault == "extra_cell_text":
        duplicate = deepcopy(item("499"))
        duplicate.update(locator="extra", handle="EXTRA", text="500")
        s["placed_text"].append(duplicate)
    elif fault == "missing_voltage":
        item("10000")["text"] = "unknown"
    edits, audits = propose_current_corrections(comments, s)
    assert not edits
    assert audits[0]["status"] == "blocked"


def test_independent_math_does_not_trust_proposed_values(tmp_path, monkeypatch):
    sources, source, _, _ = fixture(tmp_path)
    import backend.engineering.comment_redesign as module

    original = module.propose_current_corrections

    def corrupt(comments, snapshot):
        edits, audits = original(comments, snapshot)
        for edit in edits:
            edit["replacement_text"] = "42"
        return edits, audits

    monkeypatch.setattr(module, "propose_current_corrections", corrupt)
    with pytest.raises(ValueError, match="saved arithmetic"):
        build_package(sources, source, tmp_path / "out")
    assert not (tmp_path / "out/Drawing-review-package.zip").exists()


def test_changed_calculation_input_fails_even_if_output_is_expected(tmp_path):
    _, _, before, comments = fixture(tmp_path)
    edits, audits = propose_current_corrections(comments, before)
    after = deepcopy(before)
    by_handle = {e["handle"]: e["replacement_text"] for e in edits}
    for t in after["placed_text"]:
        if t["handle"] in by_handle:
            t["text"] = by_handle[t["handle"]]
        elif t["text"] == "5 MVA":
            t["text"] = "6 MVA"
    assert not verify_saved_calculations(after, audits)["passed"]


def test_second_run_does_not_reedit_correct_current_cells(tmp_path):
    sources, source, _, _ = fixture(tmp_path)
    build_package(sources, source, tmp_path / "one")
    path = tmp_path / "one/Proposed-drawing.dxf"
    second_source = {**source, "path": str(path), "sha256": file_hash(path)}
    second = build_package(sources, second_source, tmp_path / "two")
    assert not second["changes"]
    assert second["saved_calculation_check"]["cells_checked"] == 6
    assert second["saved_calculation_check"]["passed"]
    assert not second["engineering_approved"]


@pytest.mark.parametrize("capacity", ["5MVA", "5/10/7.5MVA", "5/5/10MVA"])
def test_ambiguous_capacity_stages_are_not_inferred(tmp_path, capacity):
    _, _, snapshot, comments = fixture(tmp_path)
    next(t for t in snapshot["placed_text"] if t["text"] == "5/7.5/10MVA@55°C")[
        "text"
    ] = capacity
    edits, audits = propose_current_corrections(comments, snapshot)
    assert not edits
    assert audits[0]["status"] == "blocked"


def test_unit_case_does_not_change_saved_arithmetic(tmp_path):
    _, _, snapshot, comments = fixture(tmp_path)
    for t in snapshot["placed_text"]:
        t["text"] = t["text"].replace("MVA", "mva")
    edits, audits = propose_current_corrections(comments, snapshot)
    assert len(edits) == 6
    replacements = {e["handle"]: e["replacement_text"] for e in edits}
    for t in snapshot["placed_text"]:
        t["text"] = replacements.get(t["handle"], t["text"])
    assert verify_saved_calculations(snapshot, audits)["cells_checked"] == 6
