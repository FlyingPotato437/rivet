"""Independent requested intent -> real native files -> BOM closure scenarios."""

import json
from hashlib import sha256
from pathlib import Path

import ezdxf
import pytest
from openpyxl import load_workbook

from backend.equipment.corrections import (
    CorrectionError,
    plan_corrections,
    prepare_corrections,
)
from backend.equipment.design_requests import extract_requests, parse_instruction
from backend.equipment.native_demo import build_native_corpus


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-package-originals")
    manifest = build_native_corpus(root)
    sources = {
        s["id"]: {**s, "path": str(root / s["path"])} for s in manifest["sources"]
    }
    return root, manifest, sources


def inputs(corpus, number):
    _root, manifest, sources = corpus
    case = next(c for c in manifest["cases"] if c["id"].startswith(f"{number:02d}-"))
    return case, [
        [sources[key] for key in case[side]]
        for side in ("baseline", "target", "requirements")
    ]


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5, 13, 14])
def test_native_edits_change_saved_geometry_and_topology_to_independently_authored_intent(
    corpus, tmp_path, number
):
    from backend.equipment.native_model import inspect_native

    case, groups = inputs(corpus, number)
    snapshot = {
        s["path"]: sha256(Path(s["path"]).read_bytes()).hexdigest()
        for group in groups
        for s in group
    }
    plan = plan_corrections(*groups)
    assert plan["status"] == "ready", plan["blockers"]
    result = prepare_corrections(
        *groups, destination=tmp_path / "new-draft", expected_plan_id=plan["plan_id"]
    )
    assert result["geometry_changed"] and result["originals_unchanged"]
    assert result["native_verification"]["status"] == "verified"
    assert all(c["status"] == "pass" for c in result["native_verification"]["checks"])
    assert result["verification"]["summary"]["outcome"] == "supported_fields_match"
    assert (
        result["engineering_approved"] is False
        and result["production_released"] is False
    )
    artifacts = {
        a["role"]: tmp_path / "new-draft" / a["name"] for a in result["artifacts"]
    }
    actual = inspect_native(artifacts["drawing"])
    expected = json.loads((corpus[0] / case["expected"]["graph_file"]).read_text())
    actual_devices = {d["tag"]: d for d in actual["devices"]}
    assert set(actual_devices) == set(expected["devices"])
    for device in expected["devices"].values():
        assert actual_devices[device["tag"]]["position"] == device["position"]
    actual_wires = {w["id"]: w for w in actual["wires"]}
    assert set(actual_wires) == set(expected["wires"])
    for wire in expected["wires"].values():
        observed = actual_wires[wire["id"]]
        assert observed["from"] == ":".join(wire["from"])
        assert observed["to"] == ":".join(wire["to"])
        assert observed["net_id"] == wire["net_id"]
    # This checks the actual serializer, not the planner's predicted JSON.
    saved = ezdxf.readfile(artifacts["drawing"])
    assert not saved.audit().has_errors
    for artifact in result["artifacts"]:
        assert (
            sha256((tmp_path / "new-draft" / artifact["name"]).read_bytes()).hexdigest()
            == artifact["sha256"]
        )
    for path, checksum in snapshot.items():
        assert sha256(Path(path).read_bytes()).hexdigest() == checksum


@pytest.mark.parametrize("number", range(6, 13))
def test_native_scenarios_reject_invalid_changes_without_publishing_partial_files(
    corpus, tmp_path, number
):
    _, groups = inputs(corpus, number)
    plan = plan_corrections(*groups)
    assert plan["status"] == "blocked"
    assert plan["blockers"]
    with pytest.raises(CorrectionError):
        prepare_corrections(*groups, destination=tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_requested_native_instructions_retain_the_exact_source_and_page(corpus):
    _, groups = inputs(corpus, 1)
    compiled = extract_requests(groups[2])
    assert not compiled["blockers"] and len(compiled["requests"]) == 2
    for request in compiled["requests"]:
        assert request["kind"] == "rewire"
        assert request["evidence"]["page"] == 1
        assert len(request["evidence"]["sha256"]) == 64
        assert request["wire_id"] in request["evidence"]["text"]
    stale = {**groups[2][0], "sha256": "0" * 64}
    assert extract_requests([stale])["blockers"]
    assert not extract_requests([stale])["requests"]


@pytest.mark.parametrize(
    "line",
    [
        "Add device KA-2 using block RELAY at (3,4) mm; PART_NUMBER==HYPERLINK(X); QUANTITY=1",
        "Move KA-1 somewhere near the meter.",
        "Rewire W102 from K86-1:12 to something.",
        "Remove all devices.",
    ],
)
def test_incomplete_or_formula_design_instructions_never_compile(line):
    with pytest.raises(ValueError):
        parse_instruction(line)


def test_post_save_bom_tamper_is_detected_independently(corpus, tmp_path):
    from backend.equipment.native_bom import verify_inventory

    _, groups = inputs(corpus, 3)
    result = prepare_corrections(*groups, destination=tmp_path / "draft")
    artifacts = {a["role"]: tmp_path / "draft" / a["name"] for a in result["artifacts"]}
    wb = load_workbook(artifacts["bom"])
    sheet = wb["Equipment BOM"]
    headers = [c.value for c in sheet[1]]
    part = next(i for i, h in enumerate(headers, 1) if h == "part_number")
    for row in sheet.iter_rows(min_row=2):
        if row[0].value == "KA-2":
            sheet.cell(row[0].row, part, "WRONG-RELAY")
    wb.save(artifacts["bom"])
    wb.close()
    checked = verify_inventory(artifacts["drawing"], artifacts["bom"])
    assert checked["status"] == "blocked"
    assert any(c["status"] == "fail" for c in checked["checks"])
