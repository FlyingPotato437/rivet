"""Software failure controls; these synthetic drawings are not accuracy data."""

from copy import deepcopy
from pathlib import Path

import ezdxf
import pytest
from test_comment_redesign import inputs

from backend.engineering.cad_evidence import (
    equipment_evidence,
    explicit_tags,
    verify_saved_drawing,
)
from backend.engineering.cad_snapshot import snapshot_dxf
from backend.engineering.comment_redesign import build_package, file_hash
from backend.equipment.readers import read_source
from backend.equipment.schemas import SourceInput


@pytest.fixture(autouse=True)
def database():
    yield


def schedule(tmp_path, *, ambiguous=False, rotate=False):
    path = tmp_path / "schedule.dxf"
    doc = ezdxf.new("R2010")
    m = doc.modelspace()
    labels = [
        ["NO.", "Symbol", "Name", "Type", ""],
        ["1", "QM1-QM3", "Breaker", "", "3"],
        ["2", "KM1-KMn", "Contactor", "", "2"],
        ["3", "", "", "", ""],
    ]
    for r, row in enumerate(labels):
        for c, label in enumerate(row):
            x, y = c * 40, -r * 10
            m.add_lwpolyline(
                [(x, y), (x + 40, y), (x + 40, y - 10), (x, y - 10)], close=True
            )
            if label:
                m.add_text(
                    label,
                    dxfattribs={
                        "insert": (x + 4, y - 4),
                        "height": 2,
                        "rotation": 90 if rotate and r == 1 and c == 1 else 0,
                    },
                )
    if ambiguous:
        m.add_text("Other", dxfattribs={"insert": (84, -17), "height": 2})
    m.add_text("QM1", dxfattribs={"insert": (400, 400), "height": 2})
    doc.saveas(path)
    return path


def test_schedule_retains_blank_cells_and_unlabelled_quantity(tmp_path):
    evidence = equipment_evidence(snapshot_dxf(schedule(tmp_path)))
    assert evidence["counts"] == {
        "schedules": 1,
        "schedule_rows": 3,
        "explicit_tag_candidates": 3,
        "cross_reference_candidates": 1,
    }
    table = evidence["schedules"][0]
    assert table["columns"][-1]["field"] == "unlabelled_5"
    assert table["rows"][0]["cells"]["type"]["value"] is None
    assert table["rows"][0]["cells"]["unlabelled_5"]["value"] == "3"
    assert {d["tag"] for d in evidence["device_candidates"]} == {"QM1", "QM2", "QM3"}
    assert not evidence["production_bom_verified"]
    assert not evidence["electrical_connectivity_verified"]
    assert all(
        not d["physical_identity_verified"] for d in evidence["device_candidates"]
    )


def test_ambiguous_and_rotated_cells_are_unknown(tmp_path):
    evidence = equipment_evidence(
        snapshot_dxf(schedule(tmp_path, ambiguous=True, rotate=True))
    )
    row = evidence["schedules"][0]["rows"][0]
    for field in ("symbol", "description"):
        assert row["cells"][field]["status"] == "ambiguous"
        assert row["cells"][field]["value"] is None
        assert row["cells"][field]["evidence"]


@pytest.mark.parametrize(
    "value", ["KM1-KMn", "1ZK-nZK", "K1-K999", "K04-K06", "QM1-QL3", "52-12", None]
)
def test_no_guessed_tag_ranges(value):
    assert explicit_tags(value) == []


def test_stream_reader_is_used_by_existing_equipment_path(tmp_path, monkeypatch):
    from backend.equipment import readers

    path = schedule(tmp_path)
    monkeypatch.setattr(readers, "MAX_BYTES", 100)
    source = SourceInput(
        id="native",
        name=path.name,
        path=str(path),
        role="drawing",
        revision="A",
        sha256=file_hash(path),
    )
    report, facts = read_source(source)
    assert report["status"] == "partial" and facts == []
    assert report["observed_device_tags"] == ["QM1", "QM2", "QM3"]
    assert len(report["observed_bom_rows"]) == 3
    row = report["observed_bom_rows"][0]
    assert row["quantity"] is None and row["device_tag"] is None
    assert row["candidate_tags"] == ["QM1", "QM2", "QM3"]
    bad, facts = read_source(source.model_copy(update={"sha256": "f" * 64}))
    assert bad["status"] == "unreadable" and not facts
    assert bad["observed_device_tags"] == []


def test_saved_recheck_sees_shared_block_fanout(tmp_path):
    sources, cad = inputs(tmp_path)
    doc = ezdxf.readfile(cad["path"])
    doc.blocks.new("TAP").add_text("NLTC", dxfattribs={"height": 3})
    doc.modelspace().add_blockref("TAP", (100, 100))
    doc.modelspace().add_blockref("TAP", (900, 900))
    sheet = doc.layouts.new("Customer Sheet")
    sheet.add_text("ORIGINAL SHEET TITLE", dxfattribs={"insert": (10, 10), "height": 2})
    doc.saveas(cad["path"])
    cad["sha256"] = file_hash(cad["path"])
    original_bytes = Path(cad["path"]).read_bytes()
    result = build_package(sources, cad, tmp_path / "out")
    check = result["saved_drawing_check"]
    assert check["passed"] and check["expected_edits"] == 2
    assert check["placements_checked"] == 3
    assert not check["engineering_correctness_verified"]
    assert sorted(len(c["affected_placements"]) for c in result["changes"]) == [1, 2]
    assert Path(cad["path"]).read_bytes() == original_bytes
    after = snapshot_dxf(tmp_path / "out/Proposed-drawing.dxf")
    assert any(
        t["text"] == "ORIGINAL SHEET TITLE"
        for t in after["paper_space_placed_text"]
    )
    assert result["saved_drawing_check"]["paper_space_entities_preserved"]
    assert all(
        (tmp_path / "out" / p["after_name"]).exists()
        for c in result["changes"]
        for p in c["affected_placements"]
    )


def test_independent_check_rejects_wrong_value_geometry_and_missing_placement(tmp_path):
    before = snapshot_dxf(schedule(tmp_path))
    target = before["placed_text"][0]
    edits = [
        {
            "id": "e1",
            "handle": target["handle"],
            "expected_text": target["text"],
            "replacement_text": "WRONG",
        }
    ]
    assert not verify_saved_drawing(before, before, edits)["passed"]
    moved = deepcopy(before)
    moved["placed_text"][0]["x"] += 100
    assert not verify_saved_drawing(before, moved, [])["passed"]
    missing = deepcopy(before)
    missing["placed_text"].pop()
    assert not verify_saved_drawing(before, missing, [])["passed"]
    changed = deepcopy(before)
    changed["placed_paths"][0]["points"][0][0] += 1
    assert not verify_saved_drawing(before, changed, [])["passed"]


def test_saved_check_rejects_unplanned_paper_space_layout_change(tmp_path):
    # A deterministic second-sheet control: this remains outside model-space
    # correction targets, while the independent saved-file check still covers it.
    schedule(tmp_path)
    doc = ezdxf.readfile(tmp_path / "schedule.dxf")
    layout = doc.layouts.new("Sheet 2")
    layout.add_text("TITLE BLOCK", dxfattribs={"insert": (10, 10), "height": 2})
    doc.saveas(tmp_path / "schedule.dxf")
    before = snapshot_dxf(tmp_path / "schedule.dxf")
    assert before["coverage_complete"]
    assert before["layout_coverage_complete"]
    assert before["paper_space_placed_text"][0]["layout"].startswith(
        "Paper space ["
    )
    changed = deepcopy(before)
    changed["paper_space_placed_text"][0]["text"] = "ALTERED TITLE BLOCK"
    check = verify_saved_drawing(before, changed, [])
    assert not check["passed"]
    assert any("paper-space" in error for error in check["errors"])


def test_a_lying_writer_cannot_publish_an_after_preview(tmp_path, monkeypatch):
    import backend.engineering.comment_redesign as redesign

    sources, cad = inputs(tmp_path)

    def lying_writer(source, output, _edits, expected):
        output.write_bytes(Path(source).read_bytes())
        return {
            "preservation_verified": True,
            "source_sha256": expected,
            "output_sha256": file_hash(output),
        }

    monkeypatch.setattr(redesign, "patch_dxf", lying_writer)
    with pytest.raises(ValueError, match="Independent saved drawing"):
        build_package(sources, cad, tmp_path / "out")
    assert (tmp_path / "out/failed-independent-check.json").exists()
    assert not (tmp_path / "out/Drawing-review-package.zip").exists()
    assert not list((tmp_path / "out").glob("*-after.svg"))


def test_unseen_shared_block_placements_withhold_a_pass(tmp_path):
    before = snapshot_dxf(schedule(tmp_path))
    target = before["placed_text"][0]
    before["omissions"]["insert_array"] = 1
    after = deepcopy(before)
    after["placed_text"][0]["text"] = "Updated"
    edit = {
        "id": "e",
        "handle": target["handle"],
        "expected_text": target["text"],
        "replacement_text": "Updated",
        "allow_shared_definition": True,
    }
    result = verify_saved_drawing(before, after, [edit])
    assert not result["passed"]
    assert not result["shared_definition_placement_coverage_verified"]


def test_graphical_markups_are_retained_and_do_not_become_resolved(tmp_path):
    import pymupdf

    sources, cad = inputs(tmp_path)
    path = Path(sources[0]["path"])
    with pymupdf.open(path) as doc:
        doc[0].add_rect_annot(pymupdf.Rect(30, 300, 130, 400))
        doc.saveIncr()
    sources[0]["sha256"] = file_hash(path)
    result = build_package(sources, cad, tmp_path / "out")
    assert result["totals"]["changed"] == 1
    assert len(result["unread_graphical_annotations"]) == 1
    assert not result["approval_eligible"]
    assert result["status"] == "needs_information"


def test_key_evaluation_is_bound_to_original_and_separates_blanks(tmp_path):
    import json

    from scripts.evaluate_native_change_loop import evaluate

    sources, cad = inputs(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"sources": sources + [cad], "native_source_id": cad["id"]})
    )
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"source_sha256": "a" * 64, "cells": []}))
    with pytest.raises(ValueError, match="different native original"):
        evaluate(manifest, tmp_path / "wrong-key-output", key)
    assert not (tmp_path / "wrong-key-output").exists()
