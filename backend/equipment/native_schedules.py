"""Drawing-derived wire and placement schedules in the corrected native BOM.

This module has no editing or model calls. Both the author and the independent
readback consume the graph inspected from the actual saved DXF, not edit intent.
"""

from __future__ import annotations

import math
import re
from hashlib import sha256
from itertools import pairwise
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

WIRE_SHEET = "Rivet wiring"
LAYOUT_SHEET = "Rivet layout"
HEADER_ROW = 5
DATA_ROW = 6
WIRE_HEADERS = (
    "Wire",
    "From terminal",
    "To terminal",
    "Net",
    "Length (mm)",
    "Route points (mm)",
)
LAYOUT_HEADERS = (
    "Device",
    "X (mm)",
    "Y (mm)",
    "Width (mm)",
    "Height (mm)",
    "Rotation (deg)",
    "Native catalog reference",
)


class NativeScheduleError(ValueError):
    """A complete, unambiguous saved-native schedule cannot be established."""


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeScheduleError(
            "The inspected native graph has a nonnumeric coordinate."
        ) from exc
    if not math.isfinite(number):
        raise NativeScheduleError(
            "The inspected native graph has a nonfinite coordinate."
        )
    return round(number, 6) or 0.0


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise NativeScheduleError(
            "A native schedule requires complete XY route points."
        )
    return [_number(value[0]), _number(value[1])]


def _checked_graph(graph, revision):
    if (
        not isinstance(graph, dict)
        or graph.get("status") != "verified"
        or graph.get("units") != "mm"
    ):
        raise NativeScheduleError(
            "Only a verified native graph with millimetre units can produce schedules."
        )
    if not isinstance(revision, str) or not revision.strip() or len(revision) > 80:
        raise NativeScheduleError("The draft revision is missing or invalid.")
    checksum = graph.get("sha256", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
        raise NativeScheduleError(
            "The schedule needs the actual inspected drawing hash."
        )
    if any(check.get("status") != "pass" for check in graph.get("checks", [])):
        raise NativeScheduleError(
            "Unresolved native geometry or wiring checks prevent schedule creation."
        )
    if not graph.get("devices"):
        raise NativeScheduleError(
            "The native schedule has no verified device inventory."
        )
    return checksum


def _expected(graph, revision):
    checksum = _checked_graph(graph, revision)
    wires, devices = [], []
    ids = set()
    for wire in sorted(graph.get("wires", []), key=lambda value: value["id"]):
        wire_id = wire["id"]
        if wire_id in ids:
            raise NativeScheduleError(
                "The native graph contains repeated wire identities."
            )
        ids.add(wire_id)
        if wire.get("actual_from") != wire.get("from") or wire.get(
            "actual_to"
        ) != wire.get("to"):
            raise NativeScheduleError(
                "A wire endpoint label differs from actual terminal geometry."
            )
        points = [_point(point) for point in wire["points"]]
        if len(points) < 2:
            raise NativeScheduleError(
                "A native conductor is missing its measured path."
            )
        length = round(sum(math.dist(a, b) for a, b in pairwise(points)), 6)
        route = "; ".join(f"({x:g}, {y:g})" for x, y in points)
        wires.append(
            [
                wire_id,
                wire["actual_from"],
                wire["actual_to"],
                wire["net_id"],
                length,
                route,
            ]
        )
    tags = set()
    for device in sorted(graph["devices"], key=lambda value: value["tag"]):
        tag = device["tag"]
        if tag in tags:
            raise NativeScheduleError(
                "The native graph contains repeated device identities."
            )
        tags.add(tag)
        position = _point(device["position"])
        devices.append(
            [
                tag,
                *position,
                _number(device["width"]),
                _number(device["height"]),
                _number(device.get("rotation", 0)),
                device.get("attributes", {}).get("PART_NUMBER", ""),
            ]
        )
    return checksum, wires, devices


def _sheet(workbook, name, headers, rows, revision, checksum):
    wiring = headers == WIRE_HEADERS
    sheet = workbook.create_sheet(name)
    columns = len(headers)
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=columns)
    sheet.cell(
        1,
        1,
        "Drawing-derived wire schedule"
        if wiring
        else "Drawing-derived device placement",
    )
    sheet.cell(1, 1).font = Font(
        name="Aptos Display", size=18, bold=True, color="18242F"
    )
    sheet.row_dimensions[1].height = 36
    sheet.cell(2, 1, "Draft revision")
    sheet.cell(2, 2, revision)
    sheet.cell(2, 4, "Native units")
    sheet.cell(2, 5, "mm")
    sheet.cell(3, 1, "DXF SHA-256")
    sheet.merge_cells(start_row=3, start_column=2, end_row=3, end_column=columns)
    sheet.cell(3, 2, checksum)
    sheet.cell(3, 2).font = Font(name="Aptos", size=9, color="5C6571")
    sheet.cell(4, 1, "Measured rows")
    sheet.cell(4, 2, len(rows))
    sheet.cell(4, 3, "Schedule schema")
    sheet.cell(4, 4, "rivet.native.wiring.v1" if wiring else "rivet.native.layout.v1")
    for cell in sheet[4]:
        cell.font = Font(name="Aptos", size=8, color="5C6571")
    for index, header in enumerate(headers, 1):
        cell = sheet.cell(HEADER_ROW, index, header)
        cell.fill = PatternFill("solid", fgColor="18242F")
        cell.font = Font(name="Aptos", size=11, bold=True, color="FFFFFF")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[HEADER_ROW].height = 30
    for row_index, values in enumerate(rows, DATA_ROW):
        for column, value in enumerate(values, 1):
            cell = sheet.cell(row_index, column, value)
            if isinstance(value, str):
                cell.data_type = "s"
            cell.font = Font(name="Aptos", size=11, color="18242F")
            cell.alignment = Alignment(
                vertical="center", horizontal="left", indent=1, wrap_text=True
            )
            if isinstance(value, (int, float)):
                cell.number_format = "0.###"
            if row_index % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F1F4F6")
        sheet.row_dimensions[row_index].height = 44 if wiring else 26
    for index, width in enumerate(
        ([17, 23, 23, 24, 18, 76] if wiring else [19, 16, 16, 17, 17, 20, 35]),
        1,
    ):
        sheet.column_dimensions[
            sheet.cell(HEADER_ROW, index).column_letter
        ].width = width
    sheet.freeze_panes = "B6"
    sheet.auto_filter.ref = f"A5:{sheet.cell(max(HEADER_ROW, DATA_ROW + len(rows) - 1), columns).coordinate}"
    sheet.print_title_rows = "1:5"
    sheet.print_options.horizontalCentered = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.oddHeader.right.text = f"DRAFT {revision}"
    sheet.oddFooter.center.text = "Engineering review required. Measured DXF centreline lengths exclude termination allowance and manufacturing slack."
    return sheet


def _rows(sheet):
    rows = list(sheet.iter_rows(min_row=DATA_ROW, values_only=True))
    while rows and all(value is None for value in rows[-1]):
        rows.pop()
    return rows


def _history(workbook):
    """Recognize structure only; old values never supply current drawing facts."""
    groups = {}
    for sheet in workbook:
        base = next(
            (
                name
                for name in (WIRE_SHEET, LAYOUT_SHEET)
                if sheet.title == name or sheet.title.startswith(name + " ")
            ),
            None,
        )
        if not base:
            continue
        wiring = base == WIRE_SHEET
        headers = WIRE_HEADERS if wiring else LAYOUT_HEADERS
        title = (
            "Drawing-derived wire schedule"
            if wiring
            else "Drawing-derived device placement"
        )
        marker = "rivet.native.wiring.v1" if wiring else "rivet.native.layout.v1"
        if sheet.max_row > 50000 or sheet.max_column > 100:
            raise NativeScheduleError(
                "A native schedule exceeds the complete inspection limit."
            )
        rows = _rows(sheet)
        valid = (
            sheet["A1"].value == title
            and sheet["A2"].value == "Draft revision"
            and isinstance(sheet["B2"].value, str)
            and bool(sheet["B2"].value.strip())
            and sheet["D2"].value == "Native units"
            and sheet["E2"].value == "mm"
            and sheet["A3"].value == "DXF SHA-256"
            and re.fullmatch(r"[a-fA-F0-9]{64}", str(sheet["B3"].value or ""))
            and sheet["A4"].value == "Measured rows"
            and type(sheet["B4"].value) is int
            and sheet["B4"].value == len(rows)
            and sheet["D4"].value == marker
            and [
                sheet.cell(HEADER_ROW, col).value for col in range(1, len(headers) + 1)
            ]
            == list(headers)
            and not any(cell.data_type == "f" for row in sheet for cell in row)
        )
        identities = set()
        for row in rows:
            if len(row) < len(headers) or any(
                value is not None for value in row[len(headers) :]
            ):
                valid = False
                continue
            identity = row[0]
            if (
                not isinstance(identity, str)
                or not identity.strip()
                or identity in identities
            ):
                valid = False
            identities.add(identity)
            numeric_columns = (4,) if wiring else (1, 2, 3, 4, 5)
            if any(
                type(row[index]) not in {int, float} or not math.isfinite(row[index])
                for index in numeric_columns
            ):
                valid = False
            if wiring:
                if any(
                    not isinstance(row[index], str) or not row[index]
                    for index in (1, 2, 3, 5)
                ):
                    valid = False
            elif not isinstance(row[6], str):
                valid = False
        if not valid:
            raise NativeScheduleError(
                "A native schedule sheet already exists with an unknown or incomplete format; preserve it for review."
            )
        suffix = sheet.title[len(base) :]
        groups.setdefault(suffix, {})[base] = {
            "name": sheet.title,
            "revision": sheet["B2"].value,
            "sha256": sheet["B3"].value,
        }
    pairs = []
    for pair in groups.values():
        if set(pair) != {WIRE_SHEET, LAYOUT_SHEET}:
            raise NativeScheduleError(
                "An existing native schedule pair is incomplete; preserve it for review."
            )
        wire, layout = pair[WIRE_SHEET], pair[LAYOUT_SHEET]
        if wire["revision"] != layout["revision"] or wire["sha256"] != layout["sha256"]:
            raise NativeScheduleError(
                "Existing wire and layout schedules identify different source revisions."
            )
        pairs.append(
            {
                "wire": wire["name"],
                "layout": layout["name"],
                "revision": wire["revision"],
                "sha256": wire["sha256"],
            }
        )
    return pairs


def _revision_name(base, revision):
    token = re.sub(r"[^A-Za-z0-9_.-]", "_", revision)
    available = 31 - len(base) - 1
    if len(token) > available:
        token = token[: available - 7] + "-" + sha256(revision.encode()).hexdigest()[:6]
    return base + " " + token


def add_native_schedules(workbook, graph, revision):
    """Append fresh current schedules, preserving every old sheet and its cells."""
    checksum, wires, devices = _expected(graph, revision)
    history = _history(workbook)
    if any(pair["revision"] == revision for pair in history):
        raise NativeScheduleError(
            "Schedules for this draft revision already exist; a new revision is required."
        )
    names = (
        [_revision_name(WIRE_SHEET, revision), _revision_name(LAYOUT_SHEET, revision)]
        if history
        else [WIRE_SHEET, LAYOUT_SHEET]
    )
    if any(name in workbook.sheetnames for name in names):
        raise NativeScheduleError(
            "A native schedule sheet already exists for the requested revision."
        )
    _sheet(workbook, names[0], WIRE_HEADERS, wires, revision, checksum)
    _sheet(workbook, names[1], LAYOUT_HEADERS, devices, revision, checksum)
    return {
        "sheets": names,
        "wire_count": len(wires),
        "device_count": len(devices),
        "source_sha256": checksum,
        "draft_revision": revision,
    }


def _equal(left, right):
    if isinstance(right, (int, float)):
        return (
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and math.isfinite(left)
            and abs(left - right) <= 0.000001
        )
    return type(left) is type(right) and left == right


def verify_native_schedules(bom_path: str | Path, graph, revision):
    """Read back both complete saved schedules and compare to saved-DXF facts."""
    checks = []

    def check(identity, passed, label, reason):
        checks.append(
            {
                "id": "schedule:" + identity,
                "status": "pass" if passed else "fail",
                "label": label,
                "reason": reason,
            }
        )

    try:
        checksum, wires, devices = _expected(graph, revision)
        workbook = load_workbook(
            bom_path, read_only=True, data_only=False, keep_links=False
        )
        try:
            pairs = [
                pair for pair in _history(workbook) if pair["revision"] == revision
            ]
            if len(pairs) != 1 or pairs[0]["sha256"] != checksum:
                raise NativeScheduleError(
                    "One unique schedule pair must identify this exact current draft revision and drawing hash."
                )
            pair = pairs[0]
            for name, headers, rows in [
                (pair["wire"], WIRE_HEADERS, wires),
                (pair["layout"], LAYOUT_HEADERS, devices),
            ]:
                exists = name in workbook.sheetnames
                check(
                    name + ":exists",
                    exists,
                    name,
                    "The saved workbook must contain the drawing-derived schedule.",
                )
                if not exists:
                    continue
                sheet = workbook[name]
                if sheet.max_row > 50000 or sheet.max_column > 100:
                    raise NativeScheduleError(
                        "A native schedule exceeds the complete inspection limit."
                    )
                check(
                    name + ":revision",
                    sheet["B2"].value == revision,
                    "Schedule revision",
                    "The saved schedule must use the current draft revision.",
                )
                check(
                    name + ":hash",
                    sheet["B3"].value == checksum,
                    "Schedule source",
                    "The saved schedule must identify the exact inspected corrected DXF.",
                )
                actual_headers = [
                    sheet.cell(HEADER_ROW, column).value
                    for column in range(1, len(headers) + 1)
                ]
                check(
                    name + ":headers",
                    actual_headers == list(headers),
                    "Schedule columns",
                    "The saved schedule column identities must be intact.",
                )
                actual = _rows(sheet)
                # Exact row inventory: blank/hidden/duplicate/extra rows do not
                # get silently excluded. Formatting beyond the table is okay.
                while actual and all(value is None for value in actual[-1]):
                    actual.pop()
                complete = len(actual) == len(rows) and all(
                    len(row) >= len(headers)
                    and all(
                        _equal(value, expected)
                        for value, expected in zip(row[: len(headers)], required)
                    )
                    and not any(value is not None for value in row[len(headers) :])
                    for row, required in zip(actual, rows)
                )
                check(
                    name + ":contents",
                    complete,
                    "Complete saved schedule",
                    "Every saved row and value must match the actual corrected drawing's complete wire/device inventory.",
                )
                no_formulas = all(
                    cell.data_type != "f"
                    for row in sheet.iter_rows(min_row=1, max_row=sheet.max_row)
                    for cell in row
                )
                check(
                    name + ":literal",
                    no_formulas,
                    "Literal measured values",
                    "Drawing-derived schedules must not contain unevaluated formulas.",
                )
        finally:
            workbook.close()
    except Exception as exc:  # noqa: BLE001 - unreadable files never count as verified.
        reason = (
            str(exc)
            if isinstance(exc, NativeScheduleError)
            else "The saved native schedules could not be read completely."
        )
        check("read", False, "Schedule readback", reason)
    return {
        "status": "verified"
        if checks and all(item["status"] == "pass" for item in checks)
        else "blocked",
        "checks": checks,
    }
