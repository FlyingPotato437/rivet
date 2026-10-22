"""Source-bound native-edit controls; synthetic controls are not design accuracy."""

from pathlib import Path

import ezdxf
import pymupdf
import pytest

from backend.engineering.cad_snapshot import snapshot_dxf
from backend.engineering.comment_redesign import build_package, file_hash, propose


@pytest.fixture(autouse=True)
def database():
    yield


def inputs(tmp_path, request='Mark tap changers as "DETC"'):
    drawing = tmp_path / "source.dxf"
    doc = ezdxf.new("R2010")
    doc.units = 1
    doc.modelspace().add_text("NLTC", dxfattribs={"height": 3, "insert": (15, 15)})
    doc.modelspace().add_line((0, 0), (100, 0))
    doc.saveas(drawing)
    pdf_path = tmp_path / "comments.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.add_freetext_annot(pymupdf.Rect(30, 30, 400, 200), request)
        pdf.save(pdf_path)
    source = {
        "id": "comment-source",
        "name": pdf_path.name,
        "path": str(pdf_path),
        "sha256": file_hash(pdf_path),
        "role": "markup",
    }
    cad = {
        "id": "cad-source",
        "name": drawing.name,
        "path": str(drawing),
        "sha256": file_hash(drawing),
        "role": "drawing",
    }
    return [source], cad


def test_comment_actually_changes_native_file_then_independently_rereads(tmp_path):
    sources, cad = inputs(tmp_path)
    before = Path(cad["path"]).read_bytes()
    result = build_package(sources, cad, tmp_path / "out")
    assert result["status"] == "ready_for_review"
    assert result["approval_eligible"] and not result["engineering_approved"]
    assert result["totals"] == {
        "comments": 1,
        "changed": 1,
        "already_present": 0,
        "needs_information": 0,
    }
    saved = ezdxf.readfile(tmp_path / "out/Proposed-drawing.dxf")
    assert [e.dxf.text for e in saved.modelspace().query("TEXT")] == ["DETC"]
    assert Path(cad["path"]).read_bytes() == before
    assert result["verification"]["preservation_verified"]
    assert result["changes"][0]["before"] == "NLTC"
    assert result["changes"][0]["after"] == "DETC"
    assert all((tmp_path / "out" / a["name"]).is_file() for a in result["artifacts"])


def test_composite_comment_does_not_close_when_only_naming_clause_is_done(tmp_path):
    sources, cad = inputs(
        tmp_path,
        'Add symbols for HV and TV tap locations. Mark tap changers as "DETC". Add CT data.',
    )
    result = build_package(sources, cad, tmp_path / "out")
    assert len(result["changes"]) == 1
    assert result["comments"][0]["status"] == "needs_information"
    assert result["approval_eligible"] is False
    assert result["status"] == "needs_information"
    assert result["totals"]["comments"] == 1


def test_geometry_request_never_turns_into_a_text_fix(tmp_path):
    sources, cad = inputs(
        tmp_path, "Move the fans to the side of the tank and remove ladder."
    )
    result = build_package(sources, cad, tmp_path / "out")
    assert not result["changes"]
    assert not (tmp_path / "out/Proposed-drawing.dxf").exists()
    assert result["comments"][0]["status"] == "needs_information"
    assert not result["approval_eligible"]


def test_cad_or_comment_hash_drift_prevents_execution(tmp_path):
    sources, cad = inputs(tmp_path)
    sources[0]["sha256"] = "a" * 64
    with pytest.raises(ValueError, match="original changed"):
        build_package(sources, cad, tmp_path / "out")
    assert not (tmp_path / "out/Proposed-drawing.dxf").exists()


def test_oltc_is_not_relabelled_as_a_deenergized_device(tmp_path):
    _, cad = inputs(tmp_path)
    snapshot = snapshot_dxf(cad["path"])
    snapshot["placed_text"][0]["text"] = "OLTC"
    comment = {
        "id": "c1",
        "text": 'Mark tap changers as "DETC"',
        "status": "needs_information",
        "change_ids": [],
        "evidence_handles": [],
    }
    assert propose([comment], snapshot)["edits"] == []


def test_exported_request_cannot_execute_as_a_spreadsheet_formula(tmp_path):
    import csv
    import json

    request = '=HYPERLINK("https://example.invalid", "review")'
    sources, cad = inputs(tmp_path, request)
    build_package(sources, cad, tmp_path / "out")
    with (tmp_path / "out/Drawing-change-register.csv").open() as stream:
        row = next(csv.DictReader(stream))
    assert row["request"] == "'" + request
    original = json.loads((tmp_path / "out/verification.json").read_text())
    assert original["comments"][0]["text"] == request


def test_after_preview_includes_neighboring_saved_changes(tmp_path):
    sources, cad = inputs(tmp_path)
    drawing = ezdxf.readfile(cad["path"])
    drawing.modelspace().add_text("NLTC", dxfattribs={"height": 3, "insert": (35, 15)})
    drawing.saveas(cad["path"])
    cad["sha256"] = file_hash(cad["path"])
    result = build_package(sources, cad, tmp_path / "out")
    assert len(result["changes"]) == 2
    for change in result["changes"]:
        after = (tmp_path / "out" / change["comparison"]["after_name"]).read_text()
        before = (tmp_path / "out" / change["comparison"]["before_name"]).read_text()
        assert after.count(">DETC</text>") == 2
        assert "NLTC" not in after
        assert before.count(">NLTC</text>") == 2
