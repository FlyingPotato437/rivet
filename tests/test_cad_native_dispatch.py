"""Synthetic native/CAD dispatch and explicit mixed-coverage controls."""

import csv
import hashlib

import pytest
from openpyxl import Workbook

from backend.equipment.cad_reports import read_cad_report
from backend.equipment.readers import read_source
from backend.equipment.schemas import SourceInput


@pytest.fixture(autouse=True)
def database():
    yield


NATIVE_HEADERS = [
    "equipment_tag", "part_number", "quantity", "manufacturer",
    "frame_a", "trip_a", "nameplate",
]
NATIVE_ROW = ["CB-1", "SYNTHETIC", 1, "SYNTHETIC", 250, 225, "TEST FEEDER"]
CAD_HEADERS = ["TAGNAME", "CAT", "MFG", "CNT"]
CAD_ROW = ["52-12", "SYNTHETIC", "SYNTHETIC", 1]


def _source(path):
    return SourceInput(
        id="synthetic-dispatch", name=path.name, path=str(path), role="bom",
        revision="SYNTHETIC", sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


@pytest.mark.parametrize("suffix", [".csv", ".xlsx"])
@pytest.mark.parametrize("tag_header", ["equipment_tag", "device_tag"])
def test_native_semantic_table_retains_ratings_names_and_source_cells(tmp_path, suffix, tag_header):
    headers = [tag_header, *NATIVE_HEADERS[1:]]
    path = tmp_path / ("native" + suffix)
    if suffix == ".csv":
        with path.open("w", newline="") as stream:
            csv.writer(stream).writerows([headers, NATIVE_ROW])
    else:
        book = Workbook()
        book.active.append(headers)
        book.active.append(NATIVE_ROW)
        book.save(path)
        book.close()
    source = _source(path)
    assert read_cad_report(source) is None
    report, facts = read_source(source)
    assert report["status"] == "parsed"
    assert report["observed_device_tags"] == ["CB-1"]
    actual = {f["field"]: f["value"] for f in facts}
    assert actual["frame_a"] == 250 and actual["trip_a"] == 225
    assert actual["nameplate"] == "TEST FEEDER"
    assert all(f["sha256"] == source.sha256 and f["evidence"]["row"] == 2 for f in facts)


@pytest.mark.parametrize("order", ["native-first", "cad-first", "same-sheet"])
def test_mixed_native_and_genuine_cad_tables_are_explicitly_unsupported(tmp_path, order):
    book = Workbook()
    book.remove(book.active)
    if order == "same-sheet":
        sheet = book.create_sheet("Mixed")
        for row in (NATIVE_HEADERS, NATIVE_ROW, [], CAD_HEADERS, CAD_ROW):
            sheet.append(row)
    else:
        tables = [("Native", NATIVE_HEADERS, NATIVE_ROW), ("Vendor", CAD_HEADERS, CAD_ROW)]
        if order == "cad-first":
            tables.reverse()
        for name, headers, row in tables:
            sheet = book.create_sheet(name)
            sheet.append(headers)
            sheet.append(row)
    path = tmp_path / "mixed.xlsx"
    book.save(path)
    book.close()
    source = _source(path)
    report, facts = read_cad_report(source)
    assert report["status"] == "partial" and not facts
    assert report["deferred_native_tables"] and report["deferred_cad_tables"]
    assert report["warnings"][0]["code"] == "mixed_native_cad_tables_unsupported"
    imported, accepted = read_source(source)
    assert imported["status"] == "partial" and not accepted
    assert imported["warnings"][0]["code"] == "mixed_native_cad_tables_unsupported"


def test_tallied_cad_bom_signature_remains_observed_without_device_invention(tmp_path):
    path = tmp_path / "tally.csv"
    path.write_text("MFG,CAT,QTY\nSYNTHETIC,SYNTHETIC-PART,4\n")
    report, facts = read_cad_report(_source(path))
    assert report["format"] == "electrical_report_table"
    assert report["source_row_counts"]["bom_rows"] == 1
    assert not report["components"] and not facts
