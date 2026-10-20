"""Actual saved BOM schedules are checked against inspected DXF geometry."""

import copy

import pytest
from openpyxl import Workbook, load_workbook

from backend.equipment.native_demo import (
    baseline_graph,
    scenario_definitions,
    write_native,
)
from backend.equipment.native_model import inspect_native
from backend.equipment.native_schedules import (
    DATA_ROW,
    LAYOUT_SHEET,
    WIRE_SHEET,
    NativeScheduleError,
    add_native_schedules,
    verify_native_schedules,
)


@pytest.fixture
def native_graph(tmp_path):
    source = tmp_path / "source.dxf"
    write_native(source, baseline_graph(), "D-R1")
    graph = inspect_native(source)
    assert graph["status"] == "verified", graph["checks"]
    return graph


def _workbook(path, graph):
    workbook = Workbook()
    workbook.active.title = "Original BOM"
    workbook.active["A1"] = "Original values preserved"
    metadata = add_native_schedules(workbook, graph, "D-R1")
    workbook.save(path)
    return metadata


def test_saved_native_geometry_and_wire_schedule_are_complete_and_readable(
    tmp_path, native_graph
):
    path = tmp_path / "BOM-D-R1.xlsx"
    metadata = _workbook(path, native_graph)
    assert metadata["wire_count"] == 16 and metadata["device_count"] == 9
    result = verify_native_schedules(path, native_graph, "D-R1")
    assert result["status"] == "verified", result
    workbook = load_workbook(path)
    assert workbook["Original BOM"]["A1"].value == "Original values preserved"
    wire = workbook[WIRE_SHEET]
    assert wire["A6"].value == "W100"
    assert wire["B6"].value == "TB-1:1"
    assert wire["C6"].value == "FU-1:1"
    assert wire["E6"].value == 88  # 23 + 30 + 35 actual mm, not a request value.
    assert wire["B3"].value == native_graph["sha256"]
    assert wire.freeze_panes == "B6" and wire.auto_filter.ref
    assert wire["E6"].data_type == "n"
    assert workbook[LAYOUT_SHEET]["A6"].value == "CB-12"
    assert workbook[LAYOUT_SHEET]["B6"].value == 620
    assert workbook[LAYOUT_SHEET]["D6"].value == 100
    workbook.close()


@pytest.mark.parametrize(
    "mutation",
    [
        "endpoint",
        "length",
        "geometry",
        "delete",
        "duplicate",
        "extra",
        "hash",
        "revision",
        "formula",
        "sheet",
        "header",
    ],
)
def test_modified_or_incomplete_saved_schedule_is_blocked(
    tmp_path, native_graph, mutation
):
    path = tmp_path / "BOM-D-R1.xlsx"
    _workbook(path, native_graph)
    workbook = load_workbook(path)
    wire, layout = workbook[WIRE_SHEET], workbook[LAYOUT_SHEET]
    if mutation == "endpoint":
        wire["C6"] = "CB-13:TC9"
    elif mutation == "length":
        wire["E6"] = 89
    elif mutation == "geometry":
        layout["B6"] = 619
    elif mutation == "delete":
        wire.delete_rows(DATA_ROW + 2)
    elif mutation == "duplicate":
        wire.append([cell.value for cell in wire[DATA_ROW]])
    elif mutation == "extra":
        wire["H6"] = "Unverified extra data"
    elif mutation == "hash":
        wire["B3"] = "0" * 64
    elif mutation == "revision":
        layout["B2"] = "D"
    elif mutation == "formula":
        wire["E6"] = "=88"
    elif mutation == "sheet":
        del workbook[LAYOUT_SHEET]
    elif mutation == "header":
        wire["E5"] = "Estimated length"
    workbook.save(path)
    result = verify_native_schedules(path, native_graph, "D-R1")
    assert result["status"] == "blocked", mutation
    assert any(check["status"] == "fail" for check in result["checks"])


def test_source_revision_and_native_failures_do_not_get_laundered_into_schedules(
    tmp_path, native_graph
):
    for bad in [
        dict(native_graph, status="blocked"),
        dict(native_graph, units="in"),
        dict(native_graph, sha256="not-a-hash"),
    ]:
        with pytest.raises(NativeScheduleError):
            add_native_schedules(Workbook(), bad, "D-R1")
    graph = copy.deepcopy(native_graph)
    graph["wires"][0]["actual_to"] = "CB-13:TC9"
    with pytest.raises(NativeScheduleError, match="endpoint"):
        add_native_schedules(Workbook(), graph, "D-R1")


def test_existing_schedule_is_preserved_and_unreadable_file_never_passes(
    tmp_path, native_graph
):
    workbook = Workbook()
    sheet = workbook.create_sheet(WIRE_SHEET)
    sheet["A1"] = "Existing customer schedule"
    with pytest.raises(NativeScheduleError, match="already exists"):
        add_native_schedules(workbook, native_graph, "D-R1")
    assert sheet["A1"].value == "Existing customer schedule"
    result = verify_native_schedules(tmp_path / "absent.xlsx", native_graph, "D-R1")
    assert result["status"] == "blocked"


def test_two_full_native_cycles_append_fresh_schedules_and_preserve_old_cells(
    tmp_path, native_graph
):
    first = tmp_path / "first-BOM.xlsx"
    _workbook(first, native_graph)
    workbook = load_workbook(first)
    # A plausible old numeric corruption is retained as unverified history;
    # the new measured rows must be derived again from the new native file.
    workbook[WIRE_SHEET]["E6"] = 999
    original = {name: list(workbook[name].values) for name in workbook.sheetnames}
    second_drawing = tmp_path / "second-source.dxf"
    changed = next(
        case["expected"]
        for case in scenario_definitions()
        if case["id"] == "02-relay-relocation"
    )
    write_native(second_drawing, changed, "E-R1")
    current = inspect_native(second_drawing)
    assert current["status"] == "verified", current["checks"]
    metadata = add_native_schedules(workbook, current, "E-R1")
    assert metadata["sheets"] == ["Rivet wiring E-R1", "Rivet layout E-R1"]
    for name, values in original.items():
        assert list(workbook[name].values) == values
    assert workbook[metadata["sheets"][0]]["E6"].value == 88
    second = tmp_path / "second-BOM.xlsx"
    workbook.save(second)
    assert verify_native_schedules(second, current, "E-R1")["status"] == "verified"
    assert verify_native_schedules(first, current, "E-R1")["status"] == "blocked"
    # Relabelling both historical sheets cannot make them current proof.
    for name in (WIRE_SHEET, LAYOUT_SHEET):
        workbook[name]["B2"] = "E-R1"
    workbook.save(second)
    assert verify_native_schedules(second, current, "E-R1")["status"] == "blocked"


def test_revision_names_are_excel_safe_and_current_revision_cannot_be_overwritten(
    tmp_path, native_graph
):
    workbook = Workbook()
    add_native_schedules(workbook, native_graph, "D-R1")
    revision = "VERY-LONG-DRAFT-REVISION-IDENTIFIER-WITH-TRAILING-CHANGES"
    metadata = add_native_schedules(workbook, native_graph, revision)
    assert all(len(name) <= 31 for name in metadata["sheets"])
    assert len(set(metadata["sheets"])) == 2
    path = tmp_path / "long-revision.xlsx"
    workbook.save(path)
    assert verify_native_schedules(path, native_graph, revision)["status"] == "verified"
    before = {name: list(workbook[name].values) for name in workbook.sheetnames}
    with pytest.raises(NativeScheduleError, match="already exist"):
        add_native_schedules(workbook, native_graph, revision)
    assert {name: list(workbook[name].values) for name in workbook.sheetnames} == before


def test_schedules_do_not_create_duplicate_primary_bom_catalog_facts(
    tmp_path, native_graph
):
    from hashlib import sha256

    from backend.equipment.readers import read_source

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Equipment BOM"
    sheet.append(["equipment_tag", "part_number", "quantity"])
    for device in native_graph["devices"]:
        sheet.append([device["tag"], device["attributes"]["PART_NUMBER"], 1])
    add_native_schedules(workbook, native_graph, "D-R1")
    path = tmp_path / "with-schedules.xlsx"
    workbook.save(path)
    source = {
        "id": "saved-bom",
        "name": path.name,
        "path": str(path),
        "role": "bom",
        "revision": "D-R1",
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "lifecycle": "draft",
    }
    report, facts = read_source(source)
    assert report["status"] == "parsed", report
    catalogs = [fact for fact in facts if fact["field"] == "part_number"]
    assert len(catalogs) == len(native_graph["devices"])
    assert all(fact["evidence"]["sheet"] == "Equipment BOM" for fact in catalogs)
