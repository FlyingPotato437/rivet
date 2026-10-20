"""Acceptance fixture geometry and expected targets are independent of edits."""

import hashlib
import json
from itertools import pairwise

import ezdxf
import pymupdf
import pytest
from openpyxl import load_workbook

from backend.equipment.native_demo import (
    DISCLAIMER,
    baseline_graph,
    build_native_corpus,
    scenario_definitions,
    terminal,
)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-acceptance")
    return root, build_native_corpus(root)


def _sources(root, manifest):
    return {
        source["id"]: {**source, "path": root / source["path"]}
        for source in manifest["sources"]
    }


def test_corpus_is_explicit_fiction_with_genuine_drawings_workbooks_and_pdf_pages(
    corpus,
):
    root, manifest = corpus
    assert manifest["fictional"] is True
    assert len(manifest["cases"]) >= 12
    assert len(manifest["sources"]) >= 99
    for source in manifest["sources"]:
        path = root / source["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]
        if path.suffix == ".dxf":
            drawing = ezdxf.readfile(path)
            assert drawing.units == 4
            assert len(list(drawing.modelspace().query("INSERT"))) >= 8
        elif path.suffix == ".xlsx":
            workbook = load_workbook(path, read_only=True, data_only=False)
            assert workbook["Equipment BOM"]["A1"].value == "equipment_tag"
            assert workbook["Equipment BOM"].max_row >= 9
            assert "invented" in workbook["Issue record"]["B8"].value
            workbook.close()
        else:
            with pymupdf.open(path) as document:
                text = "\n".join(page.get_text() for page in document)
                assert DISCLAIMER in text
                if source["role"] == "drawing":
                    assert len(document) == 2
                    assert "TERMINAL CONNECTION LIST" in text
                else:
                    assert len(document) == 1 and "CHANGE C01:" in text


def test_physical_sources_include_actual_terminals_wires_enclosure_and_inventory(
    corpus,
):
    root, manifest = corpus
    sources = _sources(root, manifest)
    baseline = sources[manifest["cases"][0]["baseline"][0]]["path"]
    drawing = ezdxf.readfile(baseline)
    graph = baseline_graph()
    devices = {}
    wires = {}
    for entity in drawing.modelspace():
        if not entity.has_xdata("RIVET"):
            continue
        data = [tag.value for tag in entity.get_xdata("RIVET")]
        if data[0] == "device":
            devices[data[1]] = entity
        elif data[0] == "wire":
            wires[data[1]] = (entity, data)
    assert set(devices) == set(graph["devices"])
    assert set(wires) == set(graph["wires"])
    assert {"K86-1", "CB-12", "CB-13", "KA-1", "CT-1", "MTR-1"} <= set(devices)
    for wire_id, (entity, metadata) in wires.items():
        points = [[point[0], point[1]] for point in entity.get_points()]
        expected = graph["wires"][wire_id]
        assert points == expected["points"]
        assert metadata[2:4] == expected["from"]
        assert metadata[4:6] == expected["to"]
        assert metadata[6] == expected["net_id"]
        assert points[0] == terminal(graph, metadata[2:4])
        assert points[-1] == terminal(graph, metadata[4:6])
        assert all(a[0] == b[0] or a[1] == b[1] for a, b in pairwise(points))
    for tag, insert in devices.items():
        actual = {
            str(child.get_xdata("RIVET")[1].value): [
                child.dxf.location.x,
                child.dxf.location.y,
            ]
            for child in drawing.blocks[insert.dxf.name].query("POINT")
            if child.has_xdata("RIVET")
        }
        assert actual == graph["devices"][tag]["terminals"]


def test_expected_targets_actually_change_native_circuit_layout_and_bom(corpus):
    root, manifest = corpus
    definitions = {case["id"]: case for case in scenario_definitions()}
    source_map = _sources(root, manifest)
    for case in manifest["cases"]:
        if case["expected"]["status"] != "ready":
            continue
        graph = json.loads((root / case["expected"]["graph_file"]).read_text())
        assert graph == definitions[case["id"]]["expected"]
        expected_dxf = source_map[case["expected"]["package"]["dxf"]]["path"]
        actual = ezdxf.readfile(expected_dxf)
        inserts = {
            item.get_attrib_text("EQUIPMENT_TAG"): item
            for item in actual.modelspace().query("INSERT")
        }
        assert set(inserts) == set(graph["devices"])
        for tag, item in graph["devices"].items():
            assert list(inserts[tag].dxf.insert)[:2] == item["position"]
        workbook = load_workbook(
            source_map[case["expected"]["package"]["bom"]]["path"], read_only=True
        )
        rows = list(workbook["Equipment BOM"].values)
        values = {row[0]: dict(zip(rows[0], row)) for row in rows[1:]}
        assert set(values) == set(graph["devices"])
        for tag, item in graph["devices"].items():
            assert values[tag]["part_number"] == item["part_number"]
            assert values[tag]["quantity"] == 1
            assert values[tag]["nameplate"] == item["nameplate"]
        workbook.close()
    targets = {
        case["id"]: definitions[case["id"]].get("expected")
        for case in manifest["cases"]
    }
    assert targets["01-trip-rewire"]["wires"]["W102"]["to"] == ["CB-13", "TC1"]
    assert targets["02-relay-relocation"]["devices"]["KA-1"]["position"] == [500, 235]
    assert "KA-2" in targets["03-add-alarm-relay"]["devices"]
    assert "W206" in targets["03-add-alarm-relay"]["wires"]
    assert "KA-1" not in targets["04-remove-relay"]["devices"]


def test_request_pdf_text_contains_each_complete_visible_instruction(corpus):
    root, manifest = corpus
    source_map = _sources(root, manifest)
    from backend.equipment.design_requests import extract_requests

    for case in manifest["cases"]:
        sources = [
            {**source_map[source_id], "path": str(source_map[source_id]["path"])}
            for source_id in case["requirements"]
        ]
        request = extract_requests(sources)
        assert not request["blockers"], case["id"]
        assert len(request["requests"]) == len(case["expected"]["request_actions"])
        assert all(item["evidence"]["page"] == 1 for item in request["requests"])
        assert all(item["evidence"].get("bbox") for item in request["requests"])


def test_expected_baseline_and_success_graphs_pass_actual_physical_inspector(corpus):
    root, manifest = corpus
    from backend.equipment.native_model import inspect_native

    source_map = _sources(root, manifest)
    baseline = inspect_native(source_map[manifest["cases"][0]["baseline"][0]]["path"])
    assert baseline["status"] == "verified", baseline["checks"]
    for case in manifest["cases"]:
        if case["expected"]["status"] == "ready":
            drawing = source_map[case["expected"]["package"]["dxf"]]["path"]
            actual = inspect_native(drawing)
            assert actual["status"] == "verified", (case["id"], actual["checks"])


def test_bad_scenarios_are_physically_distinct_failures_not_canned_warnings(corpus):
    root, manifest = corpus
    source_map = _sources(root, manifest)
    cases = {case["id"]: case for case in manifest["cases"]}
    source = source_map[cases["12-unmodelled-wire"]["target"][0]]["path"]
    drawing = ezdxf.readfile(source)
    wire = next(
        entity
        for entity in drawing.modelspace().query("LWPOLYLINE")
        if entity.has_xdata("RIVET")
        and [tag.value for tag in entity.get_xdata("RIVET")][:2] == ["wire", "W102"]
    )
    last = list(wire.get_points())[-1]
    assert last[1] != terminal(baseline_graph(), ["CB-12", "TC1"])[1]
    source = source_map[cases["09-stale-bom"]["target"][1]]["path"]
    workbook = load_workbook(source, read_only=True)
    assert any(
        row[1] == "RVT-OBSOLETE-RELAY" for row in workbook["Equipment BOM"].values
    )
    workbook.close()
    assert (
        len(
            [
                case
                for case in manifest["cases"]
                if case["expected"]["status"] == "blocked"
            ]
        )
        >= 7
    )
