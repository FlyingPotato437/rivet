"""Printed procurement rows retain provenance without inventing engineering facts."""

import json
import re
from hashlib import sha256
from pathlib import Path

import pymupdf
import pytest
from openpyxl import Workbook

from backend.equipment.bom_rows import pdf_rows
from backend.equipment.comparison import analyze_package
from backend.equipment.readers import read_source
from backend.equipment.schemas import SourceInput

ROOT = Path(__file__).resolve().parents[1]
ALASKA = (
    ROOT
    / ".data/real-document-corpus/alaska-adjacent-bom/Passenger_Car_Wireless_Bill_of_Materials.pdf"
)
KANSAS = (
    ROOT
    / ".data/real-document-corpus/new-official/kansas-docking-switchboard/H3-circuit-schedule-page-246.pdf"
)


def source(path, identifier="original-bom", **changes):
    return {
        "id": identifier,
        "name": path.name,
        "path": str(path),
        "role": "bom",
        "revision": "original",
        "sha256": sha256(path.read_bytes()).hexdigest(),
        **changes,
    }


def normalized(text):
    return re.sub(
        r"\s+", " ", str(text).translate(str.maketrans("‐‑‒–—−", "------"))
    ).strip()


@pytest.mark.skipif(
    not ALASKA.exists(), reason="Unchanged official original is not in this checkout"
)
def test_actual_alaska_original_recovers_ten_rows_without_tag_or_quantity():
    # Independent visual answer key; production extraction never reads it.
    key = json.loads(
        (
            ROOT / "benchmarks/real-packages/keys/alaska-passenger-wireless.json"
        ).read_text()
    )
    expected = key["labels"]["bom_rows"]["items"]
    inputs = source(ALASKA, "alaska-wireless-bom")
    assert inputs["sha256"] == key["source_hashes"]["alaska-wireless-bom"]
    original = ALASKA.read_bytes()
    report, facts = read_source(inputs)
    rows = report["observed_bom_rows"]
    assert len(rows) == len(expected) == 10
    assert facts == [] and report["observed_device_tags"] == []
    assert (
        report["status"] == "partial"
    )  # Reading a table is not a verified equipment inventory.
    assert [row["row"] for row in rows] == list(range(1, 11))
    for row, wanted in zip(rows, expected, strict=True):
        assert row["fields"]["item_number"] == wanted["fields"]["item_number"]
        for field in ("unit", "description", "supplied_by"):
            assert normalized(row["fields"][field]) == normalized(
                wanted["fields"][field]
            )
        assert row["raw_headers"] == [
            "Item Number",
            "Unit",
            "Description",
            "Supplied by",
        ]
        assert row["page"] == 1 and "sheet" not in row
        assert row["sha256"] == inputs["sha256"] and row["source_id"] == inputs["id"]
        assert row["device_tag"] is None and row["quantity"] is None
        assert "quantity" not in row["fields"]
        assert len(row["cells"]) == 4
        assert all(0 <= value <= 1 for cell in row["cells"] for value in cell["bbox"])
        assert row["coordinate_system"] == "normalized_top_left"
        assert not any("path" in field for field in row)
    # Original PDF is /Rotate=90: anchors follow the actual visible table.
    assert 0.16 < rows[0]["bbox"][1] < 0.20
    assert 0.37 < rows[-1]["bbox"][1] < 0.39
    assert rows[-1]["fields"]["description"].endswith(
        "Hardware, Misc. Installation Materials"
    )
    assert ALASKA.read_bytes() == original
    model = analyze_package([inputs])
    assert model["facts"] == [] and not model["devices"]
    assert len(model["sources"][0]["observed_bom_rows"]) == 10


@pytest.mark.skipif(
    not KANSAS.exists(), reason="Unchanged official original is not in this checkout"
)
def test_actual_raster_kansas_schedule_is_not_fabricated():
    report, facts = read_source(source(KANSAS, "kansas-h3-schedule"))
    assert not report["observed_bom_rows"] and not facts
    assert report["status"] == "partial"
    assert any(w["code"] == "unreadable_page" for w in report["warnings"])


def workbook(path):
    book = Workbook()
    cover = book.active
    cover.title = "Cover"
    cover.append(["Bill of materials", "Quantity varies by installation"])
    sheet = book.create_sheet("Procurement")
    sheet.merge_cells("A1:D1")
    sheet["A1"] = "Original procurement schedule"
    sheet.append([])
    sheet.append(["Item number", "Unit", "Description", "Supplied by"])
    sheet.append([1, "EA", "Actual breaker SU202M-5A", "Owner"])
    sheet.append([2, "FT", "Power cable", "Contractor"])
    sheet.append([None, None, "SECTION 2 - NOTES", None])
    sheet.append([3, None, "Mounting hardware", "Contractor"])
    book.save(path)
    book.close()


def test_xlsx_offset_headers_actual_cells_units_are_not_quantities(tmp_path):
    path = tmp_path / "procurement.xlsx"
    workbook(path)
    report, facts = read_source(source(path))
    rows = report["observed_bom_rows"]
    assert len(rows) == 3 and not facts
    assert [r["row"] for r in rows] == [4, 5, 7]
    assert all(r["sheet"] == "Procurement" for r in rows)
    assert rows[0]["cells"][2] == {
        "column": 3,
        "header": "Description",
        "text": "Actual breaker SU202M-5A",
        "cell": "C4",
    }
    assert rows[0]["fields"]["item_number"] == 1
    assert all(r["quantity"] is None and r["device_tag"] is None for r in rows)
    assert all("quantity" not in r["fields"] for r in rows)
    assert rows[2]["cells"][1]["text"] == ""  # No forward fill of EA/FT.
    assert read_source(source(path))[0]["observed_bom_rows"] == rows


def test_formulas_and_merged_cells_are_preserved_without_filling(tmp_path):
    path = tmp_path / "uncertain.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.append(["Item", "Description", "Qty", "Equipment tag"])
    sheet.append([1, "Relay", "=1+1", "KA-1"])
    sheet.append([2, "Contactor", None, None])
    sheet.merge_cells("D2:D3")
    sheet.append([3, "Fuse", 2, None])
    book.save(path)
    book.close()
    report, facts = read_source(source(path))
    rows = report["observed_bom_rows"]
    assert len(rows) == 3 and not facts
    assert rows[0]["quantity"] is None and rows[0]["fields"]["quantity"] is None
    assert rows[0]["cells"][2]["text"] == "=1+1"
    assert rows[0]["cells"][3]["merged_range"] == "D2:D3"
    assert rows[0]["device_tag"] is None
    assert rows[1]["device_tag"] is None
    assert rows[2]["quantity"] == 2
    assert any(w["code"] == "formula_not_evaluated" for w in rows[0]["warnings"])


def draw_table(path, mode="visible", pages=1):
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=500, height=300)
        if _ != pages - 1:
            page.insert_text((30, 30), "Original package - no table on this page")
    x, y = [30, 80, 120, 390, 470], [60, 85, 115]
    for xx in x:
        page.draw_line((xx, y[0]), (xx, y[-1]))
    for yy in y:
        page.draw_line((x[0], yy), (x[-1], yy))
    headers = ["Item", "Unit", "Description", "Supplied by"]
    values = ["1", "EA", "ABB SU202M-5A breaker", "Owner"]
    for row, texts in enumerate([headers, values]):
        for column, text in enumerate(texts):
            page.insert_text(
                (x[column] + 4, y[row] + 16),
                text,
                fontsize=9,
                render_mode=3 if mode == "hidden" else 0,
            )
    if mode == "covered":
        page.draw_rect((125, 87, 385, 114), color=None, fill=(1, 1, 1))
    doc.save(path)
    doc.close()


@pytest.mark.parametrize("mode", ["hidden", "covered"])
def test_hidden_or_covered_table_text_does_not_become_raw_rows(tmp_path, mode):
    path = tmp_path / f"{mode}.pdf"
    draw_table(path, mode)
    report, facts = read_source(source(path))
    assert report["observed_bom_rows"] == [] and facts == []


def test_original_page_provenance_survives_automatic_batch_split(tmp_path):
    path = tmp_path / "26-pages.pdf"
    draw_table(path, pages=26)
    inputs = source(path)
    report, facts = read_source(inputs)
    assert not facts and len(report["observed_bom_rows"]) == 1
    row = report["observed_bom_rows"][0]
    assert row["page"] == 26 and row["sha256"] == inputs["sha256"]
    assert report["page_coverage"][-1]["observed_bom_row_count"] == 1
    assert len(report["processing_batches"]) == 2


def test_stale_source_has_no_rows_even_when_parseable(tmp_path):
    path = tmp_path / "stale.xlsx"
    workbook(path)
    inputs = source(path)
    path.write_bytes(path.read_bytes() + b"changed")
    report, facts = read_source(inputs)
    assert (
        report["status"] == "unreadable"
        and not facts
        and not report["observed_bom_rows"]
    )


def test_table_work_is_bounded(tmp_path, monkeypatch):
    import backend.equipment.bom_rows as rows
    from backend.equipment.pdf_model import visible_equipment_words

    path = tmp_path / "bounded.pdf"
    draw_table(path)
    inputs = SourceInput.model_validate(source(path))
    with pymupdf.open(path) as doc:
        page = doc[0]
        page._rivet_original_page = 1
        visible, _ = visible_equipment_words(page, inputs.sha256)
        monkeypatch.setattr(rows, "MAX_ASSOCIATION_WORK", 1)
        with pytest.raises(ValueError, match="bom_table_association_limit"):
            pdf_rows(inputs, page, visible)
