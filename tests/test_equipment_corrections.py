"""Pure native corrections checked again from serialized actual output files."""

import csv
import hashlib
import json
from pathlib import Path

import ezdxf
import pytest
from openpyxl import load_workbook
from reportlab.pdfgen import canvas

from backend.equipment import corrections
from backend.equipment.demo_documents import build_demo_corpus


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "sources"
    manifest = build_demo_corpus(root)
    return {
        item["id"]: {**item, "path": str(root / item["path"])}
        for item in manifest["sources"]
    }


def selected(corpus, bom="xlsx", target="04-titleblock-only"):
    return (
        [corpus["01-baseline-C.drawing.dxf"], corpus[f"01-baseline-C.bom.{bom}"]],
        [corpus[f"{target}.drawing.dxf"], corpus[f"{target}.bom.{bom}"]],
        [corpus["requirements.pdf"]],
    )


def artifact(output, role):
    return next(item for item in output["artifacts"] if item["role"] == role)


def attrs(path):
    return {
        entity.get_attrib_text("EQUIPMENT_TAG"): {
            attribute.dxf.tag: attribute.dxf.text for attribute in entity.attribs
        }
        for entity in ezdxf.readfile(path).modelspace().query("INSERT")
    }


def source(path, role, revision="R1", source_id=None):
    return {
        "id": source_id or path.name,
        "name": path.name,
        "path": str(path),
        "role": role,
        "revision": revision,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "lifecycle": "draft",
    }


@pytest.mark.parametrize("bom", ["xlsx", "csv"])
def test_actual_dxf_and_bom_are_corrected_then_independently_rechecked(
    corpus, tmp_path, bom
):
    baseline, target, required = selected(corpus, bom)
    original_bytes = {
        item["path"]: Path(item["path"]).read_bytes()
        for item in [*baseline, *target, *required]
    }
    plan = corrections.plan_corrections(baseline, target, required)
    assert plan["status"] == "ready"
    assert {(edit["device_tag"], edit["field"]) for edit in plan["edits"]} == {
        ("CB-12", "trip_unit"),
        ("CB-12", "nameplate"),
    }
    assert all(
        {t["kind"] for t in edit["targets"]} == {"cad_attribute", "bom_cell"}
        for edit in plan["edits"]
    )
    destination = tmp_path / "corrected"
    result = corrections.prepare_corrections(
        baseline,
        target,
        required,
        destination=destination,
        expected_plan_id=plan["plan_id"],
        target_revision="D-R1",
    )
    assert result["status"] == "prepared_draft" and result["attachable"]
    assert result["verification"]["summary"]["outcome"] == "supported_fields_match"
    assert result["verification"]["summary"]["remaining"] == 0
    assert result["verification"]["summary"]["source_issues"] == 0
    assert result["verification"]["data_rules"]["failed"] == 0
    assert result["geometry_changed"] is False
    assert (
        result["engineering_approved"] is False
        and result["production_released"] is False
    )
    saved_drawing = destination / artifact(result, "drawing")["name"]
    saved_native = ezdxf.readfile(saved_drawing)
    source_native = ezdxf.readfile(target[0]["path"])
    assert "Rivet draft D-R1" in saved_native.layouts.names()
    assert any(
        "ENGINEERING APPROVAL REQUIRED" in entity.dxf.text
        for entity in saved_native.layouts.get("Rivet draft D-R1").query("TEXT")
    )
    for old_entity, new_entity in zip(
        source_native.modelspace(), saved_native.modelspace(), strict=True
    ):
        assert old_entity.dxftype() == new_entity.dxftype()
        assert old_entity.dxfattribs() == new_entity.dxfattribs()
    actual = attrs(saved_drawing)
    assert actual["CB-12"]["TRIP_UNIT"] == "TU-3000"
    assert actual["CB-12"]["NAMEPLATE"] == "DATA HALL A"
    assert set(actual) == {"CB-12", "CB-14", "CB-16"}
    assert actual["CB-12"]["FRAME_A"] == "2500 A"
    saved_bom = destination / artifact(result, "bom")["name"]
    if bom == "xlsx":
        workbook = load_workbook(saved_bom, data_only=False)
        rows = list(workbook["Equipment BOM"].values)
        assert workbook["Rivet draft"]["B1"].value == "D-R1"
        assert "Engineering approval required" in workbook["Rivet draft"]["B2"].value
        assert "DRAFT D-R1" in workbook["Equipment BOM"].oddHeader.right.text
        workbook.close()
    else:
        with saved_bom.open() as f:
            rows = list(csv.reader(f))
    values = dict(zip(rows[0], rows[1]))
    assert values["trip_unit"] == "TU-3000"
    assert values["nameplate"] == "DATA HALL A"
    for original, before in original_bytes.items():
        assert Path(original).read_bytes() == before
    # The independently parsed preview must show values read from the saved
    # DXF, not a canned picture or a completion claim.
    preview_facts = [
        fact
        for fact in result["verification"]["target"]["facts"]
        if fact["source_name"].endswith(".pdf")
    ]
    assert any(
        fact["device_tag"] == "CB-12"
        and fact["field"] == "trip_unit"
        and fact["value"] == "TU-3000"
        for fact in preview_facts
    )
    for item in result["artifacts"]:
        assert (
            hashlib.sha256((destination / item["name"]).read_bytes()).hexdigest()
            == item["sha256"]
        )
    assert "path" not in json.dumps(result).lower()


def test_missing_feeder_geometry_is_not_invented_or_silently_restored(corpus, tmp_path):
    baseline, target, required = selected(corpus, target="02-partial-D")
    plan = corrections.plan_corrections(baseline, target, required)
    assert plan["status"] == "blocked"
    assert any(item.get("device_tag") == "CB-14" for item in plan["blockers"])
    destination = tmp_path / "blocked"
    with pytest.raises(corrections.CorrectionError):
        corrections.prepare_corrections(
            baseline, target, required, destination=destination
        )
    assert not destination.exists()
    assert "CB-14" not in attrs(Path(target[0]["path"]))


def test_pdf_only_drawing_requires_actual_editable_native_file(corpus):
    baseline, target, required = selected(corpus)
    plan = corrections.plan_corrections(
        baseline, [corpus["04-titleblock-only.drawing.pdf"], target[1]], required
    )
    assert plan["status"] == "blocked"
    assert any(item["code"] == "editable_sources_required" for item in plan["blockers"])


def test_stale_input_hash_or_forged_plan_id_never_produces_outputs(corpus, tmp_path):
    baseline, target, required = selected(corpus)
    plan = corrections.plan_corrections(baseline, target, required)
    with pytest.raises(corrections.CorrectionError, match="changed"):
        corrections.prepare_corrections(
            baseline,
            target,
            required,
            destination=tmp_path / "forged",
            expected_plan_id="forged",
        )
    Path(target[1]["path"]).write_bytes(b"not the verified workbook")
    with pytest.raises(corrections.CorrectionError):
        corrections.prepare_corrections(
            baseline,
            target,
            required,
            destination=tmp_path / "stale",
            expected_plan_id=plan["plan_id"],
        )
    assert not (tmp_path / "forged").exists()
    assert not (tmp_path / "stale").exists()


def test_conflicting_requirements_require_engineer_instead_of_choosing_value(
    corpus, tmp_path
):
    baseline, target, required = selected(corpus)
    path = tmp_path / "other-requirement.csv"
    path.write_text("tag,trip_unit\nCB-12,TU-2500\n")
    plan = corrections.plan_corrections(
        baseline, target, [*required, source(path, "specification")]
    )
    assert plan["status"] == "blocked"
    assert any(item.get("field") == "trip_unit" for item in plan["blockers"])


def test_formula_cell_is_not_replaced_with_literal(corpus, tmp_path):
    baseline, target, required = selected(corpus)
    workbook = load_workbook(target[1]["path"])
    workbook["Equipment BOM"]["D2"] = '=IF(1,"TU-2500","TU-3000")'
    path = tmp_path / "formula.xlsx"
    workbook.save(path)
    workbook.close()
    plan = corrections.plan_corrections(
        baseline, [target[0], source(path, "bom", "D")], required
    )
    assert plan["status"] == "blocked"
    assert any(item.get("field") == "trip_unit" for item in plan["blockers"])


def test_duplicate_native_instances_are_not_edited_by_guessing(corpus, tmp_path):
    baseline, target, required = selected(corpus)
    drawing = ezdxf.readfile(target[0]["path"])
    original = next(iter(drawing.modelspace().query("INSERT")))
    duplicate = drawing.modelspace().add_blockref(original.dxf.name, (600, 200))
    for attribute in original.attribs:
        duplicate.add_attrib(attribute.dxf.tag, attribute.dxf.text, (600, 200))
    path = tmp_path / "duplicate.dxf"
    drawing.saveas(path)
    plan = corrections.plan_corrections(
        baseline, [source(path, "drawing", "D"), target[1]], required
    )
    assert plan["status"] == "blocked"
    assert any(item["code"] == "native_edit_unavailable" for item in plan["blockers"])


def test_layout_request_is_a_real_blocker_not_a_fake_geometry_fix(corpus, tmp_path):
    baseline, target, required = selected(corpus)
    path = tmp_path / "layout-request.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(50, 750, "CB-12")
    pdf.drawString(50, 720, "Target trip unit: TU-3000")
    pdf.drawString(50, 690, "Please move CB-12 lower in the compartment.")
    pdf.save()
    plan = corrections.plan_corrections(
        baseline, [*target, source(path, "markup", "D")], required
    )
    assert plan["status"] == "blocked"
    assert any(
        item["code"] == "unsupported_geometry_request" for item in plan["blockers"]
    )


def test_actual_preview_corruption_fails_independent_recheck(
    corpus, tmp_path, monkeypatch
):
    baseline, target, required = selected(corpus)
    old_pdf = Path(corpus["01-baseline-C.drawing.pdf"]["path"]).read_bytes()
    monkeypatch.setattr(
        corrections, "_native_preview", lambda path, *_: path.write_bytes(old_pdf)
    )
    destination = tmp_path / "corrupted-preview"
    with pytest.raises(corrections.CorrectionError, match="independent"):
        corrections.prepare_corrections(
            baseline, target, required, destination=destination
        )
    assert not destination.exists()
    assert not list(tmp_path.glob(".rivet-correction-*"))


def test_impossible_trip_requirement_is_blocked_by_data_rule(corpus, tmp_path):
    baseline, target, required = selected(corpus)
    path = tmp_path / "impossible.csv"
    path.write_text("tag,trip_a\nCB-12,3000 A\n")
    plan = corrections.plan_corrections(
        baseline, target, [*required, source(path, "specification")]
    )
    assert plan["status"] == "blocked"
    assert plan["predicted_rules"]["failed"] == 2
    assert any(item["code"] == "equipment_data_constraint" for item in plan["blockers"])


def test_empty_inputs_are_blocked_during_ingestion():
    plan = corrections.plan_corrections([], [])
    assert plan["status"] == "blocked"
    assert plan["edits"] == []


def test_scanned_returned_changes_cannot_be_silently_dropped(corpus):
    baseline, target, required = selected(corpus)
    scanned = {**corpus["unreadable.drawing"], "role": "markup"}
    plan = corrections.plan_corrections(baseline, [*target, scanned], required)
    assert plan["status"] == "blocked"
    assert any(
        item["code"] == "unreadable_requested_changes" for item in plan["blockers"]
    )


def test_output_destination_cannot_replace_sources_or_existing_files(corpus, tmp_path):
    baseline, target, required = selected(corpus)
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "keep.txt").write_text("keep")
    with pytest.raises(corrections.CorrectionError):
        corrections.prepare_corrections(
            baseline, target, required, destination=destination
        )
    assert (destination / "keep.txt").read_text() == "keep"
    with pytest.raises(corrections.CorrectionError):
        corrections.prepare_corrections(
            baseline, target, required, destination=Path(target[0]["path"]).parent
        )
