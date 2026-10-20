"""A real prepared drawing/BOM bundle must survive the next customer revision."""

import math
from contextlib import contextmanager
from hashlib import sha256
from itertools import pairwise
from pathlib import Path

import ezdxf
import pytest
from openpyxl import load_workbook
from reportlab.pdfgen import canvas

from backend.equipment.corrections import plan_corrections, prepare_corrections
from backend.equipment.native_demo import baseline_graph, write_bom, write_native
from backend.equipment.native_model import inspect_native
from backend.equipment.native_schedules import verify_native_schedules
from backend.equipment.native_verification import recheck_package


def _source(path, role, revision, identity):
    return {
        "id": identity,
        "name": path.name,
        "path": str(path),
        "role": role,
        "revision": revision,
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "lifecycle": "draft",
    }


def _request_pdf(path, revision, instructions, *, drawing_sha=None):
    pdf = canvas.Canvas(str(path), pagesize=(1191, 842), invariant=1)
    pdf.setFont("Helvetica-Bold", 17)
    pdf.drawString(40, 780, "Fictional returned drawing change schedule")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(
        40,
        750,
        f"Review revision {revision}; retain all other previously corrected work.",
    )
    if drawing_sha:
        pdf.drawString(40, 727, "Reviewed DXF SHA-256: " + drawing_sha)
    pdf.drawString(
        40,
        690,
        "Draft corrections only. Engineering approval and release remain separate.",
    )
    for index, instruction in enumerate(instructions, 1):
        pdf.drawString(40, 640 - index * 28, f"CHANGE C{index:02}: {instruction}")
    pdf.save()


def _bundle_sources(bundle, folder, prefix):
    sources = []
    for artifact in bundle["artifacts"]:
        if artifact["role"] not in {"drawing", "bom", "preview"}:
            continue
        path = folder / artifact["name"]
        assert sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
        sources.append(
            _source(
                path,
                "bom" if artifact["role"] == "bom" else "drawing",
                bundle["draft_revision"],
                prefix + ":" + artifact["role"],
            )
        )
    return sources


def _path(sources, extension):
    return Path(next(s["path"] for s in sources if s["name"].endswith(extension)))


def _cells(workbook):
    return {
        sheet.title: {
            cell.coordinate: (cell.value, cell.data_type)
            for row in sheet
            for cell in row
        }
        for sheet in workbook
    }


@contextmanager
def _workbook(path):
    workbook = load_workbook(path, data_only=False)
    try:
        yield workbook
    finally:
        workbook.close()


def _actual_wires(path):
    """Read raw saved entities independently of the predicted native graph."""
    doc = ezdxf.readfile(path)
    assert not doc.audit().has_errors
    wires = {}
    for entity in doc.modelspace():
        if not entity.has_xdata("RIVET"):
            continue
        values = [t.value for t in entity.get_xdata("RIVET") if t.code == 1000]
        if values[:1] != ["wire"]:
            continue
        points = (
            [[p[0], p[1]] for p in entity.get_points()]
            if entity.dxftype() == "LWPOLYLINE"
            else [list(entity.dxf.start)[:2], list(entity.dxf.end)[:2]]
        )
        wires[values[1]] = {
            "from": ":".join(values[2:4]),
            "to": ":".join(values[4:6]),
            "net": values[6],
            "points": points,
        }
    return doc, wires


def test_second_revision_keeps_first_rewire_and_appends_current_native_schedules(
    tmp_path,
):
    originals = tmp_path / "originals"
    originals.mkdir()
    graph = baseline_graph()
    baseline, target = [], []
    for revision, group in (("C", baseline), ("D", target)):
        dxf, bom = (
            originals / f"panel-{revision}.dxf",
            originals / f"BOM-{revision}.xlsx",
        )
        write_native(dxf, graph, revision)
        write_bom(bom, graph, revision)
        group.extend(
            [
                _source(dxf, "drawing", revision, revision + ":drawing"),
                _source(bom, "bom", revision, revision + ":bom"),
            ]
        )
    first_request = originals / "returned-trip-rewire.pdf"
    _request_pdf(
        first_request,
        "D",
        [
            "Rewire W102 from K86-1:12 to CB-13:TC1.",
            "Rewire W104 from K86-1:22 to CB-12:TC1.",
        ],
    )
    requirements = [_source(first_request, "markup", "D", "first:return")]
    original_bytes = {
        s["path"]: Path(s["path"]).read_bytes()
        for s in [*baseline, *target, *requirements]
    }

    first_folder = tmp_path / "first-corrected-bundle"
    first = prepare_corrections(
        baseline, target, requirements, destination=first_folder, target_revision="D-R1"
    )
    assert first["attachable"] and first["native_verification"]["status"] == "verified"
    first_sources = _bundle_sources(first, first_folder, "first")
    first_dxf, first_bom = _path(first_sources, ".dxf"), _path(first_sources, ".xlsx")
    _, first_wires = _actual_wires(first_dxf)
    assert first_wires["W102"]["to"] == "CB-13:TC1"
    assert first_wires["W102"]["points"][-1] == [570, 365]
    assert first_wires["W104"]["to"] == "CB-12:TC1"
    assert first_wires["W104"]["points"][-1] == [570, 525]
    with _workbook(first_bom) as workbook:
        old_cells = _cells(workbook)
        assert workbook["Rivet wiring"]["B2"].value == "D-R1"
        assert (
            workbook["Rivet layout"]["B3"].value
            == sha256(first_dxf.read_bytes()).hexdigest()
        )
    first_bytes = {str(path): path.read_bytes() for path in first_folder.iterdir()}

    # The next run consumes the actual prior output bytes, never a fresh demo
    # source graph or the first run's predicted JSON. Only this new request is
    # in force; the existing trip rewiring must remain intact without reasking.
    previous_native = [s for s in first_sources if not s["name"].endswith(".pdf")]
    second_request = originals / "returned-relay-move.pdf"
    _request_pdf(
        second_request,
        "E",
        ["Move KA-1 to (500, 235) mm."],
        drawing_sha=sha256(first_dxf.read_bytes()).hexdigest(),
    )
    next_requirements = [_source(second_request, "markup", "E", "second:return")]
    second_plan = plan_corrections(previous_native, previous_native, next_requirements)
    assert second_plan["status"] == "ready", second_plan["blockers"]
    second_folder = tmp_path / "second-corrected-bundle"
    second = prepare_corrections(
        previous_native,
        previous_native,
        next_requirements,
        destination=second_folder,
        expected_plan_id=second_plan["plan_id"],
        target_revision="E-R1",
    )
    assert second["attachable"] and second["originals_unchanged"]
    assert not second["engineering_approved"] and not second["production_released"]
    second_sources = _bundle_sources(second, second_folder, "second")
    second_dxf, second_bom = (
        _path(second_sources, ".dxf"),
        _path(second_sources, ".xlsx"),
    )
    saved, second_wires = _actual_wires(second_dxf)
    relay = next(
        e
        for e in saved.modelspace().query("INSERT")
        if e.get_attrib_text("EQUIPMENT_TAG") == "KA-1"
    )
    assert list(relay.dxf.insert) == [500, 235, 0]
    for wire_id in ("W102", "W104"):
        assert second_wires[wire_id] == first_wires[wire_id]
    assert any(
        second_wires[w]["points"] != first_wires[w]["points"]
        for w in ("W201", "W202", "W203", "W204")
    )

    first_graph, second_graph = inspect_native(first_dxf), inspect_native(second_dxf)
    assert (
        verify_native_schedules(second_bom, first_graph, "D-R1")["status"] == "verified"
    )
    assert (
        verify_native_schedules(second_bom, second_graph, "E-R1")["status"]
        == "verified"
    )
    with _workbook(second_bom) as workbook:
        for name, cells in old_cells.items():
            assert name in workbook.sheetnames
            assert {
                c.coordinate: (c.value, c.data_type)
                for row in workbook[name]
                for c in row
            } == cells
        current = [
            s
            for s in workbook
            if s.title.startswith(("Rivet wiring", "Rivet layout"))
            and s["B2"].value == "E-R1"
        ]
        assert len(current) == 2
        assert all(
            s["B3"].value == sha256(second_dxf.read_bytes()).hexdigest()
            for s in current
        )
        wiring = next(s for s in current if s.title.startswith("Rivet wiring"))
        for row in wiring.iter_rows(min_row=6, values_only=True):
            native = second_wires[row[0]]
            assert row[1:4] == (native["from"], native["to"], native["net"])
            assert row[4] == pytest.approx(
                sum(math.dist(a, b) for a, b in pairwise(native["points"]))
            )
        layout = next(s for s in current if s.title.startswith("Rivet layout"))
        row = next(
            r for r in layout.iter_rows(min_row=6, values_only=True) if r[0] == "KA-1"
        )
        assert row[1:3] == (500, 235)

    checked = recheck_package(
        previous_native,
        second_sources,
        next_requirements,
        original_target=previous_native,
        native_plan=second_plan["native_plan"],
        literal_edits=second_plan["edits"],
    )
    assert checked["summary"]["outcome"] == "supported_fields_match"
    assert checked["summary"]["native_changes_verified"]
    assert all(c["status"] == "pass" for c in checked["native_verification"]["checks"])
    for path, data in {**original_bytes, **first_bytes}.items():
        assert Path(path).read_bytes() == data
