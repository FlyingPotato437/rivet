"""Manufacturer BOM layouts: actual units, sparse/merged cells, exact origins."""

from hashlib import sha256

import pytest
from openpyxl import Workbook

from backend.equipment.readers import read_source


def read(path):
    return read_source(
        {
            "id": "bom-original",
            "name": path.name,
            "path": str(path),
            "role": "bom",
            "revision": "C",
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
    )


def test_header_declared_units_preserve_original_cell_and_header_evidence(tmp_path):
    path = tmp_path / "manufacturer-bom.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Switchboard BOM"
    sheet.merge_cells("A1:F1")
    sheet["A1"] = "Manufacturer equipment schedule"
    sheet.append([])
    sheet.append(
        [
            "Equipment tag",
            "Frame [kA]",
            "Trip rating (A)",
            "Control voltage [VDC]",
            "Qty",
            "Part number",
        ]
    )
    sheet.append(["52-12", 2.5, 2000, 125, 1, "ACB-2500"])
    workbook.save(path)
    workbook.close()
    report, facts = read(path)
    assert report["status"] == "parsed"
    values = {f["field"]: f for f in facts}
    assert values["frame_a"]["value"] == 2500 and values["frame_a"]["unit"] == "A"
    assert values["frame_a"]["raw_value"] == "2.5"
    assert values["frame_a"]["evidence"] == {
        "sheet": "Switchboard BOM",
        "row": 4,
        "cell": "B4",
        "column": 2,
        "header": "Frame [kA]",
        "declared_unit": "kA",
    }
    assert (
        values["control_voltage_v"]["value"] == 125
        and values["control_voltage_v"]["unit"] == "V DC"
    )
    assert values["trip_a"]["value"] == 2000


def test_merged_device_rows_are_ambiguous_not_forward_filled(tmp_path):
    path = tmp_path / "merged-equipment.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Equipment tag", "Frame [A]", "Trip [A]", "Part number"])
    sheet.append(["BKR-12", 800, 600, "BRK-800"])
    sheet.append([None, 1000, 800, "DIFFERENT-PART"])
    sheet.merge_cells("A2:A3")
    sheet.append(["FDR 12", 400, 250, "BRK-400"])
    workbook.save(path)
    workbook.close()
    report, facts = read(path)
    assert report["status"] == "partial"
    assert {f["device_tag"] for f in facts} == {"FDR 12"}
    assert {
        w["row"]
        for w in report["warnings"]
        if w["code"] == "merged_equipment_row_ambiguous"
    } == {2, 3}
    assert all(f["evidence"]["row"] == 4 for f in facts)


@pytest.mark.parametrize(
    "header,value,reason",
    [
        ("Frame [kA]", "800 A", "header_cell_unit_conflict"),
        ("Frame [MVA]", 800, "unsupported_or_wrong_unit"),
        ("Frame [A]", "=800+0", "formula_not_evaluated"),
    ],
)
def test_conflicting_or_unsupported_units_and_formulas_remain_unknown(
    tmp_path, header, value, reason
):
    path = tmp_path / "unit-or-formula.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Equipment tag", header, "Part number"])
    sheet.append(["1086M", value, "REL-86"])
    workbook.save(path)
    workbook.close()
    report, facts = read(path)
    assert not any(f["field"] == "frame_a" for f in facts)
    assert report["uncertain_fields"][0]["reason"] == reason
    assert report["uncertain_fields"][0]["evidence"]["cell"] == "B2"


def test_duplicate_recognized_columns_are_not_voted_into_one_value(tmp_path):
    path = tmp_path / "duplicated-current.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Equipment tag", "Frame [A]", "Frame (A)", "Part number"])
    sheet.append(["86T", 800, 1000, "REL-86"])
    workbook.save(path)
    workbook.close()
    report, facts = read(path)
    assert report["status"] == "partial"
    assert {f["value"] for f in facts if f["field"] == "frame_a"} == {800, 1000}
    assert any(w["code"] == "duplicate_columns" for w in report["warnings"])
