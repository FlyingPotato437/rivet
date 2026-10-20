"""Conflicting or only partly supported source requests must never turn green."""

import csv
from hashlib import sha256

import pytest
from reportlab.pdfgen import canvas

from backend.equipment.corrections import (
    CorrectionError,
    plan_corrections,
    prepare_corrections,
)
from backend.equipment.native_demo import (
    HEADERS,
    baseline_graph,
    bom_rows,
    write_bom,
    write_native,
)


def _source(path, role, revision):
    return {
        "id": path.name,
        "name": path.name,
        "path": str(path),
        "role": role,
        "revision": revision,
        "sha256": sha256(path.read_bytes()).hexdigest(),
        "lifecycle": "draft",
    }


def _pdf(path, lines):
    document = canvas.Canvas(str(path))
    for index, line in enumerate(lines):
        document.drawString(35, 800 - index * 24, line)
    document.save()


@pytest.fixture
def packages(tmp_path):
    graph = baseline_graph()
    groups = []
    for revision in ("C", "D"):
        drawing, bom = (
            tmp_path / f"source-{revision}.dxf",
            tmp_path / f"source-{revision}.xlsx",
        )
        write_native(drawing, graph, revision)
        write_bom(bom, graph, revision)
        groups.append(
            [_source(drawing, "drawing", revision), _source(bom, "bom", revision)]
        )
    return groups


@pytest.mark.parametrize("position", ["before", "after"])
@pytest.mark.parametrize("tag", ["KA-1", "K86-1"])
def test_unsupported_native_instruction_is_not_hidden_by_another_valid_change(
    tmp_path, packages, position, tag
):
    valid = "CHANGE C01: Move KA-1 to (500, 235) mm."
    unsupported = f"Rotate {tag} by 90 degrees."
    path = tmp_path / "partly-supported-return.pdf"
    lines = [unsupported, valid] if position == "before" else [valid, unsupported]
    _pdf(
        path,
        ["Fictional customer return - both instructions require completion", *lines],
    )
    request = _source(path, "markup", "R1")
    plan = plan_corrections(*packages, [request])
    assert plan["status"] == "blocked", (
        "The unexecuted rotation must remain an explicit blocker."
    )
    assert any(
        item["code"] == "unsupported_geometry_request" for item in plan["blockers"]
    )
    with pytest.raises(CorrectionError):
        prepare_corrections(
            *packages, [request], destination=tmp_path / "no-partial-output"
        )
    assert not (tmp_path / "no-partial-output").exists()


REMOVAL = [
    "CHANGE C01: Remove wire W201.",
    "CHANGE C02: Remove wire W202.",
    "CHANGE C03: Remove wire W203.",
    "CHANGE C04: Remove wire W204.",
    "CHANGE C05: Remove device KA-1.",
]


@pytest.mark.parametrize("role", ["markup", "specification"])
def test_explicit_retained_device_requirement_conflicts_with_removal(
    tmp_path, packages, role
):
    path = tmp_path / f"conflicting-{role}.pdf"
    quantity = "Target quantity: 1" if role == "markup" else "Quantity: 1"
    _pdf(
        path,
        [
            "Fictional customer return - do not guess which instruction governs",
            "KA-1",
            quantity,
            *REMOVAL,
        ],
    )
    request = _source(path, role, "R1")
    plan = plan_corrections(*packages, [request])
    assert plan["status"] == "blocked", (
        "A requested quantity of one must not be laundered into a verified deletion."
    )
    with pytest.raises(CorrectionError):
        prepare_corrections(
            *packages, [request], destination=tmp_path / "no-conflicting-output"
        )
    assert not (tmp_path / "no-conflicting-output").exists()


def test_baseline_markup_retained_request_is_not_discarded_during_native_removal(
    tmp_path, packages
):
    old = tmp_path / "baseline-retained-request.pdf"
    _pdf(old, ["Fictional retained customer requirement", "KA-1", "Target quantity: 1"])
    packages[0].append(_source(old, "markup", "C"))
    request = tmp_path / "remove-request.pdf"
    _pdf(request, ["Fictional new customer return", *REMOVAL])
    plan = plan_corrections(*packages, [_source(request, "markup", "R1")])
    assert plan["status"] == "blocked"


def test_independent_verifier_does_not_override_conflicting_requested_presence():
    # Isolate the readback closure as well as the planner: a durable old bundle
    # must not get a green result merely because the planning bug was repaired.
    from backend.equipment.native_verification import _inventory_checks

    request_fact = {"device_tag": "KA-1", "field": "quantity", "value": 1}
    comparison = {
        "checks": [{"device_tag": "KA-1", "field": "quantity", "status": "missing"}],
        "requirements": {"facts": [], "requested_changes": [request_fact]},
        "baseline": {"requested_changes": [], "facts": []},
        "target": {"facts": [], "sources": [], "requested_changes": []},
    }
    result = _inventory_checks(
        comparison,
        [
            {
                "kind": "remove_device",
                "device_tag": "KA-1",
                "evidence": {"source_id": "request"},
            }
        ],
    )
    assert result["checks"][0]["status"] != "verified"


@pytest.mark.parametrize("kind", ["ragged_csv", "invalid_xlsx"])
def test_malformed_bom_becomes_actionable_blocker_instead_of_crashing(
    tmp_path, packages, kind
):
    if kind == "ragged_csv":
        bom = tmp_path / "ragged-source.csv"
        with bom.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(HEADERS)
            for row in bom_rows(baseline_graph(), "D"):
                writer.writerow(
                    [row["equipment_tag"]]
                    if row["equipment_tag"] == "KA-1"
                    else [row[field] for field in HEADERS]
                )
    else:
        bom = tmp_path / "invalid-source.xlsx"
        bom.write_bytes(b"This is not a readable workbook archive.")
    packages[1][1] = _source(bom, "bom", "D")
    request = tmp_path / "move-request.pdf"
    _pdf(
        request,
        [
            "Fictional requested native revision",
            "CHANGE C01: Move KA-1 to (500, 235) mm.",
        ],
    )
    plan = plan_corrections(*packages, [_source(request, "markup", "R1")])
    assert plan["status"] == "blocked"
    assert plan["blockers"]


@pytest.mark.parametrize(
    "unsupported",
    [
        "Remove all breakers.",
        "Remove all devices",
        "Add two new feeders",
        "Rewire every relay",
    ],
)
def test_unaddressed_plural_or_global_actions_are_not_display_headings(
    tmp_path, unsupported
):
    from backend.equipment.corrections import _unsupported_requests
    from backend.equipment.schemas import SourceInput

    path = tmp_path / "unaddressed-real-request.pdf"
    _pdf(
        path,
        [
            "Fictional customer requested revision",
            unsupported,
            "CHANGE C01: Move KA-1 to (500, 235) mm.",
        ],
    )
    blocked = _unsupported_requests(
        [SourceInput.model_validate(_source(path, "markup", "R1"))]
    )
    assert blocked, (
        "Unknown targets require clarification; a second valid action must not hide this instruction."
    )


def test_pdf_annotation_request_is_not_silently_ignored_beside_selectable_change(
    tmp_path, packages
):
    import pymupdf

    path = tmp_path / "customer-return-with-sticky-note.pdf"
    _pdf(
        path,
        [
            "Fictional marked-up customer return",
            "CHANGE C01: Move KA-1 to (500, 235) mm.",
        ],
    )
    with pymupdf.open(path) as document:
        annotation = document[0].add_text_annot(
            pymupdf.Point(90, 180), "Rotate KA-1 by 90 degrees."
        )
        annotation.update()
        document.saveIncr()
    plan = plan_corrections(*packages, [_source(path, "markup", "R1")])
    assert plan["status"] == "blocked", (
        "A real PDF annotation cannot disappear because another request is selectable text."
    )


def test_exact_native_pdf_annotation_has_cited_geometry_and_remains_a_request(tmp_path):
    import pymupdf

    from backend.equipment.design_requests import extract_requests
    from backend.equipment.readers import read_source

    path = tmp_path / "exact-native-sticky-note.pdf"
    _pdf(
        path,
        [
            "Fictional returned change schedule with readable coverage; see the attached note."
        ],
    )
    with pymupdf.open(path) as document:
        annotation = document[0].add_text_annot(
            pymupdf.Point(90, 180), "Move KA-1 to (500, 235) mm."
        )
        annotation.update()
        document.saveIncr()
    source = _source(path, "markup", "R1")
    compiled = extract_requests([source])
    assert not compiled["blockers"]
    assert len(compiled["requests"]) == 1
    request = compiled["requests"][0]
    assert request["kind"] == "move_device" and request["to"] == [500, 235]
    assert request["evidence"]["annotation_xref"] > 0
    assert request["evidence"]["annotation_type"] == "Text"
    assert request["evidence"]["page"] == 1
    assert len(request["evidence"]["bbox"]) == 4
    assert request["evidence"]["sha256"] == source["sha256"]
    report, facts = read_source(source, requested_changes=True)
    assert report["status"] == "parsed" and report["native_request_count"] == 1
    assert not facts, "A proposed move is not an observed design fact."


@pytest.mark.parametrize("kind", ["unknown_text", "ink", "empty_highlight"])
def test_all_unconsumed_annotation_types_block_even_beside_valid_body_request(
    tmp_path, kind
):
    import pymupdf

    from backend.equipment.design_requests import extract_requests

    path = tmp_path / f"unconsumed-{kind}.pdf"
    _pdf(
        path,
        [
            "Fictional complete returned change schedule",
            "CHANGE C01: Move KA-1 to (500, 235) mm.",
        ],
    )
    with pymupdf.open(path) as document:
        page = document[0]
        if kind == "unknown_text":
            annotation = page.add_text_annot(
                pymupdf.Point(90, 180), "Move the red marked item somewhere else."
            )
        elif kind == "ink":
            annotation = page.add_ink_annot([[(90, 180), (120, 170), (145, 185)]])
        else:
            annotation = page.add_highlight_annot(pymupdf.Rect(90, 180, 160, 200))
        annotation.update()
        document.saveIncr()
    compiled = extract_requests([_source(path, "markup", "R1")])
    assert compiled["blockers"]
    assert compiled["blockers"][0]["annotation_xref"] > 0
    assert compiled["blockers"][0]["page"] == 1


def test_literal_identical_body_and_annotation_preserve_both_source_anchors(tmp_path):
    import pymupdf

    from backend.equipment.design_requests import extract_requests

    path = tmp_path / "duplicate-source-anchors.pdf"
    literal = "Move KA-1 to (500, 235) mm."
    _pdf(path, ["Fictional returned change schedule", "CHANGE C01: " + literal])
    with pymupdf.open(path) as document:
        document[0].add_text_annot(pymupdf.Point(90, 180), literal)
        document.saveIncr()
    compiled = extract_requests([_source(path, "markup", "R1")])
    assert not compiled["blockers"] and len(compiled["requests"]) == 1
    request = compiled["requests"][0]
    assert not request["evidence"].get("annotation_xref")
    assert request["supporting_evidence"][0]["annotation_xref"] > 0
    assert len(compiled["recognized_lines"]) == 2


def test_native_request_does_not_launder_uninterpreted_raster_page(tmp_path):
    import pymupdf

    from backend.equipment.design_requests import extract_requests

    path = tmp_path / "partially-selectable-scan.pdf"
    _pdf(
        path,
        [
            "Fictional returned package with an embedded scanned instruction",
            "CHANGE C01: Move KA-1 to (500, 235) mm.",
        ],
    )
    with pymupdf.open() as scan:
        page = scan.new_page(width=400, height=200)
        page.insert_text((30, 100), "Remove every breaker - raster only.")
        raster = page.get_pixmap().tobytes("png")
    with pymupdf.open(path) as document:
        document[0].insert_image(pymupdf.Rect(40, 200, 500, 430), stream=raster)
        document.saveIncr()
    compiled = extract_requests([_source(path, "markup", "R1")])
    assert any(
        item["code"] == "uninterpreted_request_image" for item in compiled["blockers"]
    )
