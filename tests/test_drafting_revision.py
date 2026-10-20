"""Pure file adapter tests; deliberately override the repository DB fixture."""

import csv
import json
from pathlib import Path
from uuid import uuid4

import ezdxf
import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from backend.drafting import (
    DraftingError,
    DraftRevisionRequest,
    load_bundle,
    prepare_revision,
    read_bundle_artifact,
    sha256_file,
)
from scripts.build_drafting_demo import make_request, make_sources


@pytest.fixture(autouse=True)
def database():
    """No database is accessed or reset by these tests."""
    yield


@pytest.fixture
def sources(tmp_path):
    drawing, bom, handle = make_sources(tmp_path / "originals")
    request = make_request(drawing, bom, handle)
    return drawing, bom, handle, request, tmp_path / "prepared"


def revised(request, **updates):
    data = request.model_dump(mode="json")
    data.update(updates)
    return DraftRevisionRequest.model_validate(data)


def prepare(sources, request=None):
    drawing, bom, _, default, output = sources
    return prepare_revision(
        request or default, drawing_path=drawing, bom_path=bom, destination=output
    )


def geometry(document):
    return [
        (entity.dxftype(), str(entity.dxf.all_existing_dxf_attribs()))
        for entity in document.modelspace()
        if entity.dxftype() != "INSERT"
    ], [
        (
            entity.dxf.handle,
            tuple(entity.dxf.insert),
            entity.dxf.rotation,
            entity.dxf.xscale,
            entity.dxf.yscale,
        )
        for entity in document.modelspace().query("INSERT")
    ]


def test_native_dxf_and_excel_copies_have_three_exact_changes_originals_unchanged(
    sources,
):
    drawing, bom, handle, request, output = sources
    original_cad, original_bom = drawing.read_bytes(), bom.read_bytes()
    original_geometry = geometry(ezdxf.readfile(drawing))
    result = prepare(sources)
    assert drawing.read_bytes() == original_cad
    assert bom.read_bytes() == original_bom
    draft = ezdxf.readfile(output / "E-10-draft-Rev-D.dxf")
    assert geometry(draft) == original_geometry
    breaker = draft.entitydb[handle]
    assert breaker.get_attrib_text("TRIP_UNIT") == "TU-3000-DEMO"
    assert breaker.get_attrib_text("NAMEPLATE") == "CB-12 / DATA HALL A"
    assert tuple(breaker.dxf.insert) == (95, 106, 0)
    workbook = load_workbook(output / "BOM-draft-Rev-D.xlsx")
    assert workbook["BOM"]["C2"].value == "TU-3000-DEMO"
    assert workbook["BOM"]["C3"].value == "CB-12-DEMO"
    assert workbook["BOM"]["A1"].fill.fgColor.rgb == "00252A35"
    assert workbook["BOM"].freeze_panes == "A2"
    assert result.status == "pending_engineering_approval"
    assert len(result.changes) == 3
    assert result.layout_tasks[0]["geometry_changed"] is False
    assert result.layout_tasks[0]["anchor_xyz"] == [95, 106, 0]
    assert result.layout_tasks[0]["bbox"]["min"][0] <= 95
    assert result.layout_tasks[0]["status"] == "requires_drafter"
    report = json.loads((output / "report.json").read_text())
    assert report["engineering_approved"] is False
    assert report["production_released"] is False
    assert report["external_systems_modified"] is False
    assert request.drawing_sha256 == sha256_file(drawing)
    assert "Rivet draft D" in draft.layout_names()
    assert "TU-2500-DEMO" in (output / "before.svg").read_text()
    assert "TU-3000-DEMO" in (output / "after.svg").read_text()


def test_a_released_source_only_generates_a_new_pending_copy(sources):
    request = revised(sources[3], source_lifecycle="released")
    result = prepare(sources, request)
    assert result.status == "pending_engineering_approval"
    report = json.loads(Path(result.report_path).read_text())
    assert report["source_lifecycle"] == "released"
    assert report["production_released"] is False
    assert sha256_file(sources[0]) == request.drawing_sha256


@pytest.mark.parametrize("target", ["drawing_sha256", "bom_sha256"])
def test_wrong_hash_refuses_all_output(sources, target):
    with pytest.raises(DraftingError, match="hash changed"):
        prepare(sources, revised(sources[3], **{target: "0" * 64}))
    assert not sources[4].exists()


def test_wrong_or_missing_entity_refuses_all_output(sources):
    plan = sources[3].model_dump(mode="json")
    plan["edits"][0]["entity_handle"] = "DEADBEEF"
    with pytest.raises(DraftingError, match="not found"):
        prepare(sources, DraftRevisionRequest.model_validate(plan))
    assert not sources[4].exists()


def test_duplicate_equipment_identity_is_not_guessed(sources):
    drawing, _, _, request, _ = sources
    document = ezdxf.readfile(drawing)
    other = document.modelspace().add_blockref("RIVET_BREAKER", (220, 106))
    other.add_attrib("EQUIPMENT_TAG", "CB-12", insert=(240, 100))
    document.saveas(drawing)
    with pytest.raises(DraftingError, match="ambiguous"):
        prepare(sources, revised(request, drawing_sha256=sha256_file(drawing)))
    assert not sources[4].exists()


def test_duplicate_attribute_and_missing_attribute_are_refused(sources):
    drawing, _, handle, request, _ = sources
    document = ezdxf.readfile(drawing)
    document.entitydb[handle].add_attrib("TRIP_UNIT", "TU-2500-DEMO", insert=(145, 108))
    document.saveas(drawing)
    with pytest.raises(DraftingError, match="Expected one TRIP_UNIT"):
        prepare(sources, revised(request, drawing_sha256=sha256_file(drawing)))


def test_outdated_expected_value_refuses_batch_even_after_prior_in_memory_edit(sources):
    plan = sources[3].model_dump(mode="json")
    plan["edits"][1]["expected_value"] = "Outdated title"
    with pytest.raises(DraftingError, match="value changed"):
        prepare(sources, DraftRevisionRequest.model_validate(plan))
    assert not sources[4].exists()
    assert (
        ezdxf.readfile(sources[0]).entitydb[sources[2]].get_attrib_text("TRIP_UNIT")
        == "TU-2500-DEMO"
    )


def test_duplicate_bom_row_is_not_guessed(sources):
    _, bom, _, request, _ = sources
    workbook = load_workbook(bom)
    workbook["BOM"].append(["10", "CB-12", "TU-2500-DEMO", "Duplicate identity", 1])
    workbook.save(bom)
    with pytest.raises(DraftingError, match="Expected one BOM row"):
        prepare(sources, revised(request, bom_sha256=sha256_file(bom)))
    assert not sources[4].exists()


def test_formula_cell_is_not_overwritten(sources):
    _, bom, _, request, _ = sources
    workbook = load_workbook(bom)
    workbook["BOM"]["C2"] = '=CONCAT("TU-", "2500-DEMO")'
    workbook.save(bom)
    with pytest.raises(DraftingError, match="formula cell"):
        prepare(sources, revised(request, bom_sha256=sha256_file(bom)))


def test_policy_rejects_geometry_or_unknown_operations_and_unresolved_decisions(
    sources,
):
    plan = sources[3].model_dump(mode="json")
    plan["edits"][0]["kind"] = "move_breaker"
    with pytest.raises(ValidationError):
        DraftRevisionRequest.model_validate(plan)
    plan = sources[3].model_dump(mode="json")
    plan["edits"][0]["decision_status"] = "needs_clarification"
    with pytest.raises(ValidationError):
        DraftRevisionRequest.model_validate(plan)
    plan = sources[3].model_dump(mode="json")
    plan["edits"][0]["attribute_tag"] = "ENGINEERING_RELEASE"
    with pytest.raises(ValidationError, match="outside the explicit edit policy"):
        DraftRevisionRequest.model_validate(plan)


def test_csv_bom_is_supported_without_excel(sources, tmp_path):
    csv_path = tmp_path / "BOM.csv"
    with csv_path.open("w", newline="") as out:
        csv.writer(out).writerows(
            [
                ["line_id", "part_number", "quantity"],
                ["10", "TU-2500-DEMO", "1"],
            ]
        )
    request = revised(sources[3], bom_sha256=sha256_file(csv_path))
    result = prepare_revision(
        request, drawing_path=sources[0], bom_path=csv_path, destination=sources[4]
    )
    with (sources[4] / "BOM-draft-Rev-D.csv").open() as source:
        rows = list(csv.DictReader(source))
    assert rows[0]["part_number"] == "TU-3000-DEMO"
    assert result.status == "pending_engineering_approval"


def test_output_directory_is_never_overwritten(sources):
    sources[4].mkdir()
    (sources[4] / "owned-by-someone-else.txt").write_text("Keep")
    with pytest.raises(DraftingError, match="must be new"):
        prepare(sources)
    assert (sources[4] / "owned-by-someone-else.txt").read_text() == "Keep"


def test_dwg_is_rejected_without_claiming_native_support(sources, tmp_path):
    dwg = tmp_path / "source.dwg"
    dwg.write_bytes(b"Not an editable DXF")
    with pytest.raises(DraftingError, match="Only editable DXF"):
        prepare_revision(
            sources[3], drawing_path=dwg, bom_path=sources[1], destination=sources[4]
        )


def test_bundle_loading_checks_tenant_and_all_native_artifact_hashes(sources):
    result = prepare(sources)
    loaded = load_bundle(
        Path(result.report_path),
        organization_id=result.organization_id,
        order_id=result.order_id,
        expected_report_sha256=result.report_sha256,
    )
    assert loaded == result
    contents, artifact = read_bundle_artifact(
        Path(result.report_path),
        artifact_name="BOM-draft-Rev-D.xlsx",
        organization_id=result.organization_id,
        order_id=result.order_id,
        expected_report_sha256=result.report_sha256,
    )
    assert contents == (sources[4] / "BOM-draft-Rev-D.xlsx").read_bytes()
    assert artifact.role == "draft_bom"
    with pytest.raises(DraftingError, match="not a unique member"):
        read_bundle_artifact(
            Path(result.report_path),
            artifact_name="../originals/E-10-Rev-C.dxf",
            organization_id=result.organization_id,
            order_id=result.order_id,
        )
    with pytest.raises(DraftingError, match="does not belong"):
        load_bundle(
            Path(result.report_path), organization_id=uuid4(), order_id=result.order_id
        )
    with (sources[4] / "E-10-draft-Rev-D.dxf").open("ab") as output:
        output.write(b"tampered")
    with pytest.raises(DraftingError, match="artifact changed"):
        load_bundle(
            Path(result.report_path),
            organization_id=result.organization_id,
            order_id=result.order_id,
        )


def test_bundle_cannot_claim_approval_and_path_traversal_is_rejected(sources):
    result = prepare(sources)
    path = Path(result.report_path)
    report = json.loads(path.read_text())
    report["engineering_approved"] = True
    path.write_text(json.dumps(report))
    with pytest.raises(DraftingError, match="approval or release"):
        load_bundle(
            path, organization_id=result.organization_id, order_id=result.order_id
        )
    report["engineering_approved"] = False
    report["artifacts"][0]["name"] = "../originals/E-10-Rev-C.dxf"
    path.write_text(json.dumps(report))
    with pytest.raises(DraftingError, match="path is invalid"):
        load_bundle(
            path, organization_id=result.organization_id, order_id=result.order_id
        )


def test_spreadsheet_formula_injection_and_multiline_cad_values_are_refused(sources):
    plan = sources[3].model_dump(mode="json")
    plan["edits"][2]["replacement_value"] = '=HYPERLINK("https://example.com")'
    with pytest.raises(DraftingError, match="spreadsheet formula"):
        prepare(sources, DraftRevisionRequest.model_validate(plan))
    plan = sources[3].model_dump(mode="json")
    plan["edits"][0]["replacement_value"] = "TU-3000\nNEW INSTRUCTION"
    with pytest.raises(DraftingError, match="single printable line"):
        prepare(sources, DraftRevisionRequest.model_validate(plan))


def test_source_changed_during_preparation_prevents_publication(sources, monkeypatch):
    original_save = ezdxf.document.Drawing.saveas
    source = sources[0]

    def save_and_change_original(document, filename, *args, **kwargs):
        original_save(document, filename, *args, **kwargs)
        source.write_bytes(source.read_bytes() + b"\n")

    monkeypatch.setattr(ezdxf.document.Drawing, "saveas", save_and_change_original)
    with pytest.raises(DraftingError, match="Source hash changed"):
        prepare(sources)
    assert not sources[4].exists()
    assert not list(sources[4].parent.glob(".rivet-draft-*"))
