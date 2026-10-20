"""Adaptive-raster controls use generated pages only, never evaluation pages."""

import hashlib
import math

import pymupdf
import pytest

from backend.equipment.evidence.table_reader import (
    MAX_RENDER_DIMENSION,
    MAX_RENDER_PIXELS,
    MINIMUM_RENDER_DPI,
    _render_plan,
    read_table_evidence,
)


@pytest.fixture(autouse=True)
def database():
    """Generated-PDF raster controls have no database or mailbox dependency."""
    yield


def source(path):
    return {
        "id": "synthetic-large-format",
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


@pytest.mark.parametrize(("width", "height"), [(36 * 72, 24 * 72), (24 * 72, 36 * 72)])
def test_normal_36_by_24_sheet_adapts_inside_both_bounds(width, height):
    plan = _render_plan(pymupdf.Rect(0, 0, width, height), 216)
    assert plan["adaptive"] and MINIMUM_RENDER_DPI <= plan["effective_dpi"] < 216
    assert max(plan["planned_pixel_size"]) <= MAX_RENDER_DIMENSION
    assert math.prod(plan["planned_pixel_size"]) <= MAX_RENDER_PIXELS
    assert plan["legibility_floor_met"]
    assert plan["source_page_points"] == [width, height]


def test_letter_development_page_keeps_requested_resolution():
    plan = _render_plan(pymupdf.Rect(0, 0, 612, 792), 216)
    assert plan["effective_dpi"] == plan["requested_dpi"] == 216
    assert plan["planned_pixel_size"] == [1836, 2376]
    assert not plan["adaptive"]


def test_extreme_page_is_visibly_unknown_before_render_or_ocr(tmp_path, monkeypatch):
    path = tmp_path / "synthetic-200-by-200.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=200 * 72, height=200 * 72)
        doc.save(path)
    monkeypatch.setattr(
        "backend.equipment.evidence.table_reader._orient",
        lambda _: pytest.fail("Below-floor render must not reach OCR"),
    )
    report = read_table_evidence(source(path), [1])
    page = report["pages"][0]
    assert (
        page["status"] == "unknown"
        and page["error_type"] == "render_below_legibility_floor"
    )
    assert page["render_dpi"] is None
    assert page["render_plan"]["effective_dpi"] < MINIMUM_RENDER_DPI
    assert page["unknown_regions"][0]["bbox"] == [0, 0, 1, 1]
    assert not report["claims"] and not report["rows"]


def test_large_format_table_is_read_with_actual_dpi_and_original_source_cell_boxes(
    tmp_path,
):
    # Entirely generated source: realistic physical sheet size, no gold hints.
    path = tmp_path / "synthetic-36-by-24.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=36 * 72, height=24 * 72)
        xs, ys = [150, 370, 590, 810], [150, 210, 270, 330, 390, 450]
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]), width=1)
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y), width=1)
        for i, header in enumerate(["CIRCUIT NO", "TRIP AMP", "FRAME AMP"]):
            page.insert_text((xs[i] + 15, 185), header, fontsize=20)
        for row, (circuit, trip, frame) in enumerate(
            [(11, 100, 250), (12, 200, 400), (13, 250, 600), (14, 300, 800)]
        ):
            for col, text in enumerate([str(circuit), f"{trip}A", f"{frame}A"]):
                page.insert_text((xs[col] + 20, ys[row + 1] + 38), text, fontsize=22)
        doc.save(path)
    report = read_table_evidence(source(path), [1])
    page = report["pages"][0]
    assert page["render_plan"]["adaptive"] and page["render_dpi"] < 216
    assert max(page["render_pixel_size"]) <= MAX_RENDER_DIMENSION
    assert math.prod(page["render_pixel_size"]) <= MAX_RENDER_PIXELS
    assert page["render_dpi"] == page["render_plan"]["effective_dpi"]
    assert len(report["rows"]) == 4
    trips = [c for c in report["claims"] if c["feature"] == "trip_a"]
    assert [c["value"] for c in trips] == [100, 200, 250, 300]
    for claim in trips:
        assert claim["source"]["sha256"] == report["sha256"]
        assert claim["source"]["page"] == 1
        x0, y0, x1, y1 = claim["source"]["bbox"]
        assert x0 == pytest.approx(370 / (36 * 72), abs=0.002)
        assert x1 == pytest.approx(590 / (36 * 72), abs=0.002)
        assert 0 < y0 < y1 < 1
        assert claim["raw_value"] in {"100A", "200A", "250A", "300A"}
