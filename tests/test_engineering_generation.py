"""File/export integrity controls. Synthetic fixtures are not real accuracy."""

import copy
import hashlib
import json
from pathlib import Path

import ezdxf
import pymupdf
import pytest
from openpyxl import load_workbook
from reportlab.pdfgen import canvas

from backend.engineering.generation import GenerationError, generate_package
from backend.equipment import corrections
from backend.equipment.native_model import inspect_native
from backend.equipment.readers import read_source
from backend.equipment.schemas import SourceInput


@pytest.fixture(autouse=True)
def database():
    """This suite exercises pure file generation, not the shared test database."""
    yield


@pytest.fixture
def design():
    return {
        "id": "cfg-1",
        "model_id": "SYNTHETIC-EXPORT-CONTROL",
        "model_version": "1",
        "order_name": "Software export integrity fixture",
        "state": "proposed",
        "manufacturer": "Square D",
        "family": "PowerPacT",
        "rule_pack_id": "control-only",
        "rule_pack_version": "1",
        "sections": [
            {
                "id": "S1",
                "kind": "distribution",
                "width_in": None,
                "height_in": None,
                "depth_in": None,
                "bus_a": 600,
                "positions": [
                    {
                        "id": "P1",
                        "device_id": "D1",
                        "start_in": None,
                        "mounting_space_in": None,
                    }
                ],
            }
        ],
        "devices": [
            {
                "id": "D1",
                "tag": "BKR-12",
                "role": "feeder",
                "manufacturer": "Square D",
                "family": "PowerPacT",
                "frame": "L",
                "frame_a": 600,
                "trip_a": 400,
                "pole_count": 3,
                "trip_type": "LI",
                "interrupting_ka": None,
                "catalog_sccr_ka": 65,
                "part_number": None,
                "supplied": True,
                "section_id": "S1",
                "position_id": "P1",
                "nameplate": "PUMP ROOM",
            }
        ],
        "rule_results": [
            {
                "rule_id": "example",
                "status": "unknown",
                "reason": "Synthetic test has no manufacturer engineering validation.",
            }
        ],
        "unresolved_questions": [
            {"question": "Confirm production dimensions and exact component selection."}
        ],
        "engineering_approved": True,
    }


def test_package_has_one_proposed_version_and_native_readback(tmp_path, design):
    result = generate_package(design, output_dir=tmp_path / "package")
    assert result["engineering_approved"] is False
    assert result["production_released"] is False
    assert len(result["artifacts"]) == 7
    assert result["unresolved_geometry"]
    for artifact in result["artifacts"]:
        path = Path(artifact["path"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
        assert artifact["model_version"] == "1"
    snapshot = json.loads((tmp_path / "package/model-snapshot.json").read_text())
    assert snapshot["devices"][0]["trip_a"] == 400
    assert snapshot["engineering_approved"] is False
    for name in ("one-line.dxf", "front-elevation.dxf"):
        drawing = ezdxf.readfile(tmp_path / "package" / name)
        devices = [
            entity
            for entity in drawing.modelspace()
            if entity.dxftype() == "INSERT" and entity.has_attrib("EQUIPMENT_TAG")
        ]
        assert len(devices) == 1
        assert devices[0].get_attrib_text("TRIP_A") == "400 A"
        assert devices[0].get_attrib_text("MODEL_VERSION") == "1"
        native = inspect_native(tmp_path / "package" / name)
        assert len(native["devices"]) == 1
    workbook = load_workbook(tmp_path / "package/bom.xlsx")
    sheet = workbook["Equipment BOM"]
    row = dict(
        zip(
            [cell.value for cell in sheet[1]],
            [cell.value for cell in sheet[2]],
            strict=True,
        )
    )
    assert row["frame_a"] == 600 and row["trip_a"] == 400
    assert row["part_number"] is None and row["interrupting_ka"] is None
    assert row["catalog_sccr_ka"] == 65
    assert row["model_version"] == "1" and row["proposal_status"] == "PROPOSED"
    with pymupdf.open(tmp_path / "package/submittal.pdf") as pdf:
        assert pdf.page_count >= 5
        texts = [page.get_text() for page in pdf]
        assert all("1" in text and "PROPOSED" in text for text in texts)
        assert "UNKNOWN" in "\n".join(texts)
    assert generate_package(design, output_dir=tmp_path / "package") == result


def test_existing_equipment_reader_reads_generated_values(tmp_path, design):
    result = generate_package(design, output_dir=tmp_path / "package")
    for artifact in result["artifacts"]:
        if artifact["kind"] not in ("one_line_dxf", "bom_xlsx"):
            continue
        report, facts = read_source(
            SourceInput(
                id=artifact["kind"],
                name=artifact["name"],
                role="drawing" if artifact["kind"] == "one_line_dxf" else "bom",
                revision="GEN-1",
                path=artifact["path"],
                sha256=artifact["sha256"],
            )
        )
        assert report["status"] in ("parsed", "partial")
        assert any(
            fact["device_tag"] == "BKR-12"
            and fact["field"] == "trip_a"
            and fact["value"] == 400
            for fact in facts
        )


def test_version_is_immutable_and_new_version_changes_all_outputs(tmp_path, design):
    generate_package(design, output_dir=tmp_path / "v1")
    changed = copy.deepcopy(design)
    changed["devices"][0]["trip_a"] = 500
    with pytest.raises(GenerationError, match="different design"):
        generate_package(changed, output_dir=tmp_path / "v1")
    changed["model_version"] = "2"
    second = generate_package(changed, output_dir=tmp_path / "v2")
    assert second["model_version"] == "2"
    assert (
        ezdxf.readfile(tmp_path / "v2/one-line.dxf")
        .modelspace()
        .query("INSERT")[1]
        .get_attrib_text("TRIP_A")
        == "500 A"
    )
    workbook = load_workbook(tmp_path / "v2/bom.xlsx")
    headers = [cell.value for cell in workbook["Equipment BOM"][1]]
    assert workbook["Equipment BOM"].cell(2, headers.index("trip_a") + 1).value == 500


def test_unknown_dimensions_never_become_production_geometry(tmp_path, design):
    with pytest.raises(GenerationError, match="Production elevation"):
        generate_package(
            design,
            output_dir=tmp_path / "production",
            template={"elevation_mode": "production"},
        )
    generate_package(design, output_dir=tmp_path / "schematic")
    drawing = ezdxf.readfile(tmp_path / "schematic/front-elevation.dxf")
    text = " ".join(entity.dxf.text for entity in drawing.modelspace().query("TEXT"))
    assert "W UNKNOWN" in text and "H UNKNOWN" in text
    assert "NOT TO SCALE" in text
    assert drawing.modelspace().query('LINE[layer=="MISSING_DIMENSIONS"]')


def test_named_block_library_can_be_swapped_and_hash_is_verified(tmp_path, design):
    library = ezdxf.new("R2010")
    library.units = 4
    block = library.blocks.new("SHOP_BREAKER")
    block.add_circle((0, 0), 5)
    block = library.blocks.new("SHOP_TITLE")
    block.add_line((0, 0), (400, 0))
    library_path = tmp_path / "shop.dxf"
    library.saveas(library_path)
    template = {
        "id": "shop",
        "version": "2026-1",
        "library_path": str(library_path),
        "library_sha256": hashlib.sha256(library_path.read_bytes()).hexdigest(),
        "blocks": {"breaker": "SHOP_BREAKER", "title_block": "SHOP_TITLE"},
    }
    generate_package(design, output_dir=tmp_path / "shop-package", template=template)
    drawing = ezdxf.readfile(tmp_path / "shop-package/one-line.dxf")
    assert len(drawing.modelspace().query('INSERT[name=="SHOP_BREAKER"]')) == 1
    assert len(drawing.blocks["SHOP_BREAKER"].query("CIRCLE")) == 1
    template["library_sha256"] = "0" * 64
    with pytest.raises(GenerationError, match="hash-pinned"):
        generate_package(design, output_dir=tmp_path / "bad-library", template=template)


def test_only_verified_mapped_supplied_cut_sheet_pages_are_included(tmp_path, design):
    cut_path = tmp_path / "cut.pdf"
    c = canvas.Canvas(str(cut_path))
    c.drawString(40, 700, "SYNTHETIC CUT SHEET L FRAME")
    c.showPage()
    c.drawString(40, 700, "UNSELECTED OTHER PRODUCT")
    c.save()
    item = {
        "path": str(cut_path),
        "sha256": hashlib.sha256(cut_path.read_bytes()).hexdigest(),
        "pages": [1],
        "component_ids": ["D1"],
        "verified": True,
        "identity": {"manufacturer": "Square D", "family": "PowerPacT", "frame": "L"},
        "source_ref": {"source_id": "synthetic-control"},
    }
    bad = copy.deepcopy(item)
    bad["component_ids"] = ["unused"]
    result = generate_package(
        design, output_dir=tmp_path / "with-cuts", cut_sheets=[item, bad]
    )
    assert len(result["cut_sheets"]) == 1 and len(result["cut_sheet_exclusions"]) == 1
    with pymupdf.open(tmp_path / "with-cuts/submittal.pdf") as pdf:
        text = "\n".join(page.get_text() for page in pdf)
        assert "SYNTHETIC CUT SHEET" in text
        assert "UNSELECTED OTHER PRODUCT" not in text
        assert "MODEL VERSION 1" in pdf[-1].get_text()
    design["devices"][0]["supplied"] = None
    result = generate_package(
        design, output_dir=tmp_path / "unknown-supply", cut_sheets=[item]
    )
    assert not result["cut_sheets"]
    assert "supply is unknown" in result["cut_sheet_exclusions"][0]["reason"]


@pytest.mark.parametrize(
    "mutation", ["no-version", "duplicate-tag", "invalid-number", "select-array"]
)
def test_does_not_guess_invalid_or_ambiguous_design(tmp_path, design, mutation):
    if mutation == "no-version":
        design.pop("model_version")
    elif mutation == "duplicate-tag":
        design["devices"].append({**design["devices"][0], "id": "D2"})
    elif mutation == "invalid-number":
        design["devices"][0]["trip_a"] = float("nan")
    else:
        design = {
            "model_id": "x",
            "model_version": "1",
            "configurations": [design, design],
        }
    with pytest.raises(GenerationError):
        generate_package(design, output_dir=tmp_path / "invalid")


def test_literal_customer_text_cannot_execute_excel_formula(tmp_path, design):
    design["devices"][0]["nameplate"] = '=HYPERLINK("https://example.invalid","text")'
    generate_package(design, output_dir=tmp_path / "literal")
    workbook = load_workbook(tmp_path / "literal/bom.xlsx")
    sheet = workbook["Equipment BOM"]
    column = [cell.value for cell in sheet[1]].index("nameplate") + 1
    assert sheet.cell(2, column).data_type == "s"
    assert sheet.cell(2, column).value.startswith("=HYPERLINK")
    assert "'=HYPERLINK" in (tmp_path / "literal/nameplates.csv").read_text()


def test_explicit_proposed_bus_is_drawn_without_inventing_terminal_wiring(
    tmp_path, design
):
    feeder = design["devices"][0]
    main = {**feeder, "id": "MAIN", "tag": "MAIN-1", "role": "main"}
    design["devices"].insert(0, main)
    design["topology"] = {
        "state": "proposed",
        "bus_id": "B1",
        "bus_a": 600,
        "connections": [
            {"from_device_id": "MAIN", "from_port": "load", "to_bus_id": "B1"},
            {"from_bus_id": "B1", "to_device_id": "D1", "to_port": "line"},
        ],
        "engineering_approved": False,
    }
    generate_package(design, output_dir=tmp_path / "connected")
    drawing = ezdxf.readfile(tmp_path / "connected/one-line.dxf")
    connections = [
        entity
        for entity in drawing.modelspace().query("LINE")
        if entity.has_xdata("RIVET_GENERATION")
        and entity.get_xdata("RIVET_GENERATION")[0].value
        == "proposed_logical_connection"
    ]
    assert len(connections) == 2
    assert all(not entity.has_xdata("RIVET") for entity in connections)
    assert (
        len(
            [
                entity
                for entity in drawing.modelspace().query("INSERT")
                if entity.has_attrib("EQUIPMENT_TAG")
            ]
        )
        == 2
    )
    design["topology"]["connections"].pop()
    with pytest.raises(GenerationError, match="does not account"):
        generate_package(design, output_dir=tmp_path / "incomplete-topology")


def test_dense_front_assignments_keep_every_device_block(tmp_path, design):
    design["devices"] = [
        {**design["devices"][0], "id": f"D-{index}", "tag": f"BKR-{index}"}
        for index in range(1, 15)
    ]
    generate_package(design, output_dir=tmp_path / "dense-assignment")
    drawing = ezdxf.readfile(tmp_path / "dense-assignment/front-elevation.dxf")
    inserts = [
        entity
        for entity in drawing.modelspace().query("INSERT")
        if entity.has_attrib("EQUIPMENT_TAG")
    ]
    assert len(inserts) == 14
    assert len({entity.get_attrib_text("EQUIPMENT_TAG") for entity in inserts}) == 14


def test_generated_native_and_bom_support_existing_literal_correction_and_recheck(
    tmp_path, design
):
    package = generate_package(design, output_dir=tmp_path / "package")
    sources = [
        {
            "id": item["kind"],
            "name": item["name"],
            "path": item["path"],
            "sha256": item["sha256"],
            "revision": "GEN-1",
            "role": "drawing" if item["kind"] == "one_line_dxf" else "bom",
        }
        for item in package["artifacts"]
        if item["kind"] in {"one_line_dxf", "bom_xlsx"}
    ]
    required = tmp_path / "synthetic-literal-required-values.csv"
    required.write_text("equipment_tag,trip_unit,nameplate\nBKR-12,LSI,PUMP ROOM B\n")
    requirement = {
        "id": "synthetic-requirement",
        "name": required.name,
        "path": str(required),
        "sha256": hashlib.sha256(required.read_bytes()).hexdigest(),
        "role": "specification",
        "revision": "REQ-1",
    }
    plan = corrections.plan_corrections(sources, sources, [requirement])
    assert plan["status"] == "ready", plan["blockers"]
    assert {item["field"] for item in plan["edits"]} == {"trip_unit", "nameplate"}
    result = corrections.prepare_corrections(
        sources,
        sources,
        [requirement],
        destination=tmp_path / "corrected",
        expected_plan_id=plan["plan_id"],
        target_revision="GEN-1-R1",
    )
    assert result["attachable"] is True
    assert result["engineering_approved"] is False
    assert result["verification"]["summary"]["remaining"] == 0
    assert result["verification"]["summary"]["source_issues"] == 0
