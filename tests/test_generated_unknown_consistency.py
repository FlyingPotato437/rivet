"""Unknown representation is file consistency, never a known engineering fact."""

import csv
import hashlib
from pathlib import Path

import ezdxf
import pytest
from openpyxl import load_workbook

from backend.engineering.generation import generate_package
from backend.equipment.native_bom import verify_inventory
from backend.equipment.readers import normalize_value, read_source
from backend.equipment.schemas import SourceInput


@pytest.fixture(autouse=True)
def database():
    yield


def generated(tmp_path, identity="model_local"):
    device = {
        "id": "main",
        "tag": None,
        "role": "main",
        "frame_a": None,
        "trip_a": 600,
        "pole_count": None,
        "trip_type": None,
        "interrupting_ka": None,
        "part_number": None,
        "nameplate": None,
        "frame": None,
        "catalog_sccr_ka": None,
        "display_tag": None,
        "section_label": None,
    }
    if identity == "declared_model":
        device.update(id="CB-1", tag="CB-1")
    elif identity == "source_local":
        device.update(
            id="drawing:" + "a" * 32,
            tag="drawing:" + "a" * 32,
            source_local_identity=True,
        )
    model = {
        "model_id": "unknown-control",
        "model_version": "1",
        "devices": [device],
        "sections": [],
    }
    package = generate_package(model, output_dir=tmp_path / identity)
    files = {entry["name"]: Path(entry["path"]) for entry in package["artifacts"]}
    return files["one-line.dxf"], files["bom.xlsx"]


def read(path):
    return read_source(
        SourceInput(
            id="synthetic-unknown",
            name=path.name,
            path=str(path),
            role="drawing" if path.suffix == ".dxf" else "bom",
            revision="TEST",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    )


def edit_bom(path, changes):
    workbook = load_workbook(path)
    sheet = next(
        sheet for sheet in workbook if sheet.cell(1, 1).value == "equipment_tag"
    )
    headers = {cell.value: cell.column for cell in sheet[1]}
    for field, value in changes.items():
        sheet.cell(2, headers[field], value)
        if value is None:
            sheet.cell(2, headers[field]).value = None
    workbook.save(path)
    workbook.close()


@pytest.mark.parametrize("scope", ["model_local", "declared_model", "source_local"])
def test_actual_generated_unknowns_are_consistent_representations_and_remain_unknown_facts(
    tmp_path, scope
):
    native, bom = generated(tmp_path, scope)
    consistency = verify_inventory(native, bom)
    assert consistency["status"] == "verified"
    unknowns = [
        check
        for check in consistency["checks"]
        if check.get("knowledge_status") == "unknown"
    ]
    assert unknowns and consistency["unknown_fields"]
    assert all(
        check["status"] == "pass"
        and check["engineering_value_known"] is False
        and check["scope"] == "saved_file_representation_consistency"
        for check in unknowns
    )
    assert consistency["engineering_approved"] is False
    assert any(":interrupting_ka" in check["id"] for check in unknowns)
    assert any(":part_number" in check["id"] for check in unknowns)
    for path in (native, bom):
        report, facts = read(path)
        assert not any(
            fact["field"]
            in {"frame_a", "interrupting_ka", "part_number", "trip_unit", "nameplate"}
            for fact in facts
        )
        assert all(fact["value"] is not None for fact in facts)
        assert report["uncertain_fields"]


@pytest.mark.parametrize(
    "changes",
    [
        {"interrupting_ka": 65},
        {"part_number": "KNOWN-PART"},
        {"nameplate": "KNOWN LABEL"},
        {"frame_a": 1600},
    ],
)
def test_unknown_on_one_side_cannot_match_a_known_value(tmp_path, changes):
    native, bom = generated(tmp_path)
    edit_bom(bom, changes)
    result = verify_inventory(native, bom)
    assert result["status"] == "blocked"
    assert any(check["status"] == "fail" for check in result["checks"])


@pytest.mark.parametrize(
    "changes", [{"model_version": "2"}, {"proposal_status": "APPROVED"}]
)
def test_unknown_equivalence_requires_matching_proposed_model_binding(
    tmp_path, changes
):
    native, bom = generated(tmp_path)
    edit_bom(bom, changes)
    assert verify_inventory(native, bom)["status"] == "blocked"


def test_declared_tag_foreign_model_metadata_does_not_enable_unknown_equivalence(
    tmp_path,
):
    native, bom = generated(tmp_path, "declared_model")
    edit_bom(bom, {"model_id": "foreign-model"})
    assert verify_inventory(native, bom)["status"] == "blocked"


def test_generic_enterprise_unknowns_keep_existing_strict_semantics(tmp_path):
    document = ezdxf.new("R2010")
    document.blocks.new("GENERIC_DEVICE")
    insert = document.modelspace().add_blockref("GENERIC_DEVICE", (0, 0))
    for key, value in [
        ("EQUIPMENT_TAG", "CB-1"),
        ("PART_NUMBER", "UNKNOWN"),
        ("TRIP_A", "225"),
    ]:
        insert.add_attrib(key, value)
    native = tmp_path / "generic.dxf"
    document.saveas(native)
    bom = tmp_path / "generic.csv"
    with bom.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["equipment_tag", "part_number", "trip_a"])
        writer.writerow(["CB-1", "", 225])
    assert verify_inventory(native, bom)["status"] == "blocked"
    assert normalize_value("part_number", "UNKNOWN")[2] == "empty_or_unspecified_value"
    assert normalize_value("interrupting_ka", None)[2] == "empty_or_unspecified_value"


def test_arbitrary_unreadable_literals_are_not_the_exporter_unknown_contract(tmp_path):
    native, bom = generated(tmp_path)
    edit_bom(bom, {"part_number": "TBD"})
    assert verify_inventory(native, bom)["status"] == "blocked"
