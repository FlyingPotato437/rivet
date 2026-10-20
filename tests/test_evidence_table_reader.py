"""Reader contracts and the already declared Kansas H3 development page only.

These checks are not production accuracy measurements. No heldout source,
vision transcript, region reference, catalog, or answer key is read here.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from backend.equipment.evidence.schema import make_claim, stable_id
from backend.equipment.evidence.table_reader import (
    _grid_cells,
    _normalize_candidate,
    _Transform,
    normalize_header,
    read_table_evidence,
)


@pytest.fixture(autouse=True)
def database():
    """These source/geometry checks need no database or global schema resets."""
    yield


def test_header_normalization_uses_literal_aliases_only():
    assert normalize_header("DEVICE/FRAME\nRATING") == "device_frame"
    assert normalize_header("TRIP\nAMP") == "trip_a"
    assert normalize_header("Catalog No.") == "part_number"
    assert normalize_header("CKT NO") == "circuit_no"
    assert normalize_header("TR1P AMP") is None
    assert normalize_header("CAT NO 12345") is None


@pytest.mark.parametrize(
    "raw", ["2?0A", "20OA", "200/250A", "200", "-", "(100A", "100A)"]
)
def test_numeric_normalization_does_not_repair_ocr(raw):
    result = _normalize_candidate("trip_a", raw, 98, minimum=75)
    assert result["value"] is None
    assert not result["valid"]


def test_ocr_confidence_is_retained_without_becoming_truth():
    assert _normalize_candidate("trip_a", "200A", 74, minimum=75)["value"] is None
    assert _normalize_candidate("trip_a", "(100A)", 90, minimum=75)["value"] == 100
    assert _normalize_candidate("pole_count", "3P", 90, minimum=75)["value"] == 3
    assert _normalize_candidate("quantity", "3P", 99, minimum=75)["value"] is None
    assert _normalize_candidate("device_frame", "LJ", 40, minimum=75)["value"] is None


@pytest.mark.parametrize(
    ("clockwise", "expected"),
    [
        (0, [0.1, 0.2, 0.3, 0.4]),
        (90, [0.2, 0.7, 0.4, 0.9]),
        (180, [0.7, 0.6, 0.9, 0.8]),
        (270, [0.6, 0.1, 0.8, 0.3]),
    ],
)
def test_inverse_orthogonal_source_coordinates(clockwise, expected):
    transform = _Transform((1000, 1000), (1000, 1000), (1000, 1000), clockwise, 0)
    assert transform.source_box([100, 200, 300, 400]) == pytest.approx(expected)


def test_inverse_deskew_retains_all_four_corners():
    transform = _Transform((1000, 1000), (1000, 1000), (1000, 1000), 0, 1)
    box = transform.source_box([100, 200, 300, 400])
    assert box == pytest.approx([0.1018062, 0.1930647, 0.3052662, 0.3965248])


def test_line_geometry_detects_merged_header_without_text_or_region_hints():
    image = Image.new("L", (1000, 600), 255)
    draw = ImageDraw.Draw(image)
    for y in (100, 160, 220, 280, 340, 400):
        draw.line((100, y, 900, y), fill=0, width=3)
    for x in (100, 300, 600, 900):
        draw.line((x, 160 if x in (300, 600) else 100, x, 400), fill=0, width=3)
    tables = _grid_cells(image)
    assert len(tables) == 1
    cells = tables[0]["cells"]
    assert len(cells) == 13
    title = next(c for c in cells if c["grid_row"] == 1)
    assert title["column_span"] == 3
    assert title["geometry_valid"]
    # The three-pixel ruling and tolerated scan gaps bound its measured ink hull.
    assert title["bbox"] == pytest.approx([100, 100, 900, 160], abs=3)
    assert all(c["geometry_valid"] for c in cells)


def test_hash_validation_precedes_render_and_ocr(tmp_path, monkeypatch):
    path = tmp_path / "hash-validation.pdf"
    path.write_bytes(b"bytes that must never reach a renderer")
    monkeypatch.setattr(
        "backend.equipment.evidence.table_reader._orient",
        lambda _: pytest.fail("OCR must not run"),
    )
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        read_table_evidence(
            {"id": "hash-control", "path": str(path), "sha256": "a" * 64}, [1]
        )


def test_page_budget_precedes_render(tmp_path):
    path = tmp_path / "page-budget.pdf"
    path.write_bytes(b"not a PDF; page budget must precede render")
    source = {
        "id": "budget-control",
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    with pytest.raises(ValueError, match="page_budget"):
        read_table_evidence(source, [1, 2], max_pages=1)


@pytest.fixture(scope="module")
def development_report():
    root = Path(__file__).resolve().parents[1]
    path = (
        root
        / ".data/real-document-corpus/new-official/kansas-docking-switchboard/H3-circuit-schedule-page-246.pdf"
    )
    if not path.exists() or not shutil.which("tesseract"):
        pytest.skip("Kansas H3 development PDF and Tesseract are required")
    source = {
        "id": "kansas-h3-schedule",
        "path": str(path),
        "sha256": "d09363b2a683ad7f697fb76d534a8ade89e23428ba9b9d0a17d7a904bef776b6",
    }
    return read_table_evidence(source, [1])


def test_h3_development_reader_finds_literal_ruled_rows(development_report):
    report = development_report
    assert report["pages"][0]["orientation_clockwise"] == 90
    assert report["pages"][0]["orientation_established"]
    assert len(report["pages"][0]["orientation_scores"]) == 4
    assert len(report["pages"][0]["tables"]) == 1
    assert len(report["rows"]) == 9
    assert [row["fields"]["trip_a"] for row in report["rows"]] == [
        None,
        100,
        100,
        200,
        250,
        100,
        100,
        30,
        600,
    ]
    assert all(row["tag"] is None for row in report["rows"])
    assert all(c["feature"] != "quantity" for c in report["claims"])
    assert report["requires_human_verification"]
    assert not report["coverage_complete"]


def test_h3_claim_anchors_are_measured_source_cells(development_report):
    report = development_report
    by_claim = {c["id"]: c for c in report["claims"]}
    for row in report["rows"]:
        for cid in row["claim_ids"]:
            claim = by_claim[cid]
            cell = next(c for c in row["cells"] if c["field"] == claim["feature"])
            assert claim["source"]["bbox"] == cell["bbox"]
            assert claim["source"]["text"] == claim["raw_value"] == cell["raw_text"]
            assert claim["raw_confidence"] == cell["ocr_confidence"]
            assert claim["source"]["coordinate_system"] == "normalized_top_left"
            assert cell["geometry_valid"]
            x0, y0, x1, y1 = cell["bbox"]
            assert 0.18 <= x0 < x1 <= 0.42
            assert 0.22 <= y0 < y1 <= 0.94
            assert cell["bbox"] != row["bbox"]
    # The printed 600A cell is at the last source row, not an entire-row anchor.
    last = next(
        c for c in report["claims"] if c["feature"] == "trip_a" and c["value"] == 600
    )
    assert last["source"]["bbox"][0] > 0.38
    assert 0.76 < last["source"]["bbox"][1] < 0.80
    json.dumps(report, allow_nan=False)


def test_h3_ocr_and_other_page_readers_have_one_shared_ancestry(development_report):
    report = development_report
    expected = stable_id("source-page-ancestry", report["sha256"], 1, None, None)
    assert {c["independence_group"] for c in report["claims"]} == {expected}
    visual = make_claim(
        "another-subject",
        "trip_a",
        600,
        source=report["claims"][0]["source"],
        extractor={"name": "vision-reader", "version": "test"},
    )
    assert visual["independence_group"] == expected
