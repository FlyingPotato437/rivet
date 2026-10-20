"""Saved-file system metadata controls, never real drawing accuracy."""

import json

import ezdxf
import pytest
from openpyxl import load_workbook

import test_engineering_generation as controls
from backend.engineering.artifact_inventory import read_generated_inventory
from backend.engineering.generation import generate_package


@pytest.fixture(autouse=True)
def database():
    yield


def package(tmp_path):
    design = controls.design.__wrapped__()
    design["system"] = {
        "voltage_v": 480,
        "phases": 3,
        "wires": 4,
        "frequency_hz": 60,
        "service_rating_a": 600,
        "current_type": "AC",
    }
    generate_package(design, output_dir=tmp_path / "package")
    return design, tmp_path / "package"


def read(design, root):
    return read_generated_inventory(
        drawing_path=root / "one-line.dxf",
        bom_path=root / "bom.xlsx",
        model_id=design["model_id"],
        model_version=design["model_version"],
    )


def test_saved_system_values_are_read_from_both_actual_files(tmp_path):
    design, root = package(tmp_path)
    inventory = read(design, root)
    for role in ("drawing", "bom"):
        facts = inventory[role]["facts"]
        voltage = [
            f for f in facts if f.get("scope") == "system" and f["field"] == "voltage_v"
        ]
        assert voltage and all(
            f["value"] == 480 and f["role"] == role and f["evidence"] for f in voltage
        )
        assert all(f["sha256"] == inventory[role]["report"]["sha256"] for f in voltage)
        assert any(
            f.get("scope") == "system"
            and f["field"] == "grounding"
            and f["value"] is None
            for f in facts
        )


def test_tampered_bom_value_is_not_replaced_by_design_value(tmp_path):
    design, root = package(tmp_path)
    wb = load_workbook(root / "bom.xlsx")
    sheet = wb["System requirements"]
    for row in sheet:
        if row[0].value == "voltage_v":
            row[1].value = json.dumps(600)
    wb.save(root / "bom.xlsx")
    wb.close()
    inventory = read(design, root)
    assert any(
        f.get("scope") == "system" and f["field"] == "voltage_v" and f["value"] == 600
        for f in inventory["bom"]["facts"]
    )
    assert design["system"]["voltage_v"] == 480


@pytest.mark.parametrize("mutation", ["model", "duplicate", "json"])
def test_ambiguous_or_wrongly_bound_dxf_system_metadata_rejected(tmp_path, mutation):
    design, root = package(tmp_path)
    drawing = ezdxf.readfile(root / "one-line.dxf")
    insert = next(
        e for e in drawing.modelspace().query("INSERT") if e.has_attrib("SYS_SCHEMA")
    )
    if mutation == "model":
        insert.get_attrib("SYS_MODEL_ID").dxf.text = "OTHER"
    elif mutation == "duplicate":
        insert.add_attrib("SYS_VOLTAGE_V", "600")
    else:
        insert.get_attrib("SYS_VOLTAGE_V").dxf.text = "not-json"
    drawing.saveas(root / "one-line.dxf")
    with pytest.raises(ValueError):
        read(design, root)
