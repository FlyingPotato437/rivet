"""Generated source controls; no real-page keys or model accuracy assertions."""

import io

import numpy as np
import pymupdf
import pytest
from PIL import Image, ImageDraw

from backend.equipment.evidence import drawing_reader as reader


@pytest.fixture(autouse=True)
def database():
    """Pure rendering tests do not reset a database or call a model."""
    yield


@pytest.fixture(autouse=True)
def orientation(monkeypatch):
    monkeypatch.setattr(reader, "_images", lambda *_a: ([], 0, None))


def test_sparse_vector_strip_is_rerendered_from_pdf_at_text_resolution(monkeypatch):
    with pymupdf.open() as doc:
        page = doc.new_page(width=2592, height=1728)
        page.insert_text((260, 1240), "SYNTHETIC BRANCH 60AT 100AF", fontsize=4)
        page.draw_line((250, 1200), (360, 1200))
        page.draw_line((280, 1200), (280, 1260))
        calls = []
        original = page.get_pixmap

        def render(*a, **kw):
            calls.append(kw)
            return original(*a, **kw)

        monkeypatch.setattr(page, "get_pixmap", render)
        images, rotation, warning, evidence = reader._drawing_images(page)
    info = evidence["rendering"]
    assert rotation == 0 and warning is None
    assert len(images) > 1 and info["tiles"]
    assert all(c["matrix"].a * 72 >= reader.DETAIL_DPI for c in calls if c.get("clip"))
    assert any(c.get("clip") is not None for c in calls)
    overview = Image.open(io.BytesIO(images[0][1]))
    for box, raw in images[1:]:
        tile = Image.open(io.BytesIO(raw))
        assert tile.width > (box[2] - box[0]) * overview.width * 3
        assert max(tile.size) <= reader.MAX_DETAIL_EDGE
        assert np.asarray(tile.convert("L")).min() < 100
    assert info["resolution"]["native_text_target_met"]
    assert not info["coverage_is_equipment_recall"]


def test_source_ink_bounds_avoid_large_blank_margins_without_label_hints():
    image = Image.new("L", (1800, 1400), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 1788, 1388), outline="black", width=1)
    draw.line((200, 900, 420, 900), fill="black", width=2)
    draw.line((300, 900, 300, 1120), fill="black", width=2)
    draw.text((240, 940), "SOURCE LABEL", fill="black")
    regions = reader._content_regions(image)
    assert regions
    target = next(
        r
        for r in regions
        if r["bbox"][0] <= 0.17 <= r["bbox"][2] and r["bbox"][1] <= 0.70 <= r["bbox"][3]
    )
    assert (target["bbox"][2] - target["bbox"][0]) < 0.4
    assert (target["bbox"][3] - target["bbox"][1]) < 0.4


def test_tile_allocation_uses_actual_new_source_ink_not_component_average():
    image = Image.new("L", (1000, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 280, 130), fill="black")
    draw.rectangle((800, 600, 830, 620), fill="black")
    candidates = [
        {"bbox": [.75, .70, .90, .85], "priority": 9000},
        {"bbox": [.05, .05, .35, .20], "priority": 1},
        {"bbox": [.08, .08, .38, .23], "priority": 1},
    ]
    ordered = reader._source_ink_order(candidates, image)
    assert ordered[0]["bbox"] == candidates[1]["bbox"]
    assert ordered[1]["bbox"] == candidates[0]["bbox"]
    assert ordered[2]["uncovered_probe_ink_pixels"] == 0


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_direct_crop_uses_original_page_coordinates_after_upright_rotation(rotation):
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=600)
        page.draw_rect(pymupdf.Rect(80, 330, 120, 360), color=(0, 0, 0), fill=(0, 0, 0))
        source_box = [0.15, 0.50, 0.35, 0.65]
        upright_box = reader._original_box(source_box, (360 - rotation) % 360)
        actual, raw, metadata = reader._render_detail(page, upright_box, rotation, 600)
    assert np.asarray(Image.open(io.BytesIO(raw)).convert("L")).min() == 0
    assert reader._original_box(actual, rotation) == pytest.approx(
        metadata["source_bbox"]
    )
    assert metadata["source_bbox"] == pytest.approx(source_box, abs=0.0005)
    expected = (667, 750) if rotation in (0, 180) else (750, 667)
    assert np.array(metadata["pixel_size"]) == pytest.approx(expected, abs=2)


def test_render_budget_is_explicit_and_detail_is_not_silently_downsampled(monkeypatch):
    monkeypatch.setattr(reader, "MAX_DETAIL_TILES", 2)
    monkeypatch.setattr(reader, "MAX_DETAIL_PIXELS", 8_000_000)
    with pymupdf.open() as doc:
        page = doc.new_page(width=2400, height=1600)
        for x in range(100, 2300, 200):
            for y in range(100, 1500, 100):
                page.insert_text((x, y), "SOURCE DETAIL", fontsize=6)
        images, _rotation, _warning, evidence = reader._drawing_images(page)
    info = evidence["rendering"]
    assert len(images) <= 3 and len(info["tiles"]) <= 2
    assert info["detail_pixels"] <= 8_000_000
    assert (
        info["status"] == "partial_detail_budget" and info["deferred_upright_regions"]
    )
    assert all(t["dpi"] >= reader.DETAIL_DPI for t in info["tiles"])


def test_unreachable_native_glyph_target_is_explicit_not_a_quality_claim():
    with pymupdf.open() as doc:
        page = doc.new_page(width=100, height=100)
        page.insert_text((10, 50), "TINY SOURCE", fontsize=0.5)
        dpi, evidence = reader._detail_resolution(page)
    assert dpi == reader.MAX_DETAIL_DPI
    assert evidence["native_text_target_met"] is False
    assert (
        evidence["estimated_native_cap_height_pixels"] < reader.TARGET_CAP_HEIGHT_PIXELS
    )


def test_blank_source_has_only_overview_and_no_false_content_regions():
    with pymupdf.open() as doc:
        page = doc.new_page(width=200, height=100)
        images, _rotation, _warning, evidence = reader._drawing_images(page)
    assert len(images) == 1
    assert evidence["rendering"]["status"] == "no_ink_regions_established"


def test_rotated_pdf_page_and_extra_orientation_keep_displayed_original_coordinates():
    with pymupdf.open() as doc:
        page = doc.new_page(width=400, height=600)
        page.draw_rect(pymupdf.Rect(80, 330, 120, 360), color=(0, 0, 0), fill=(0, 0, 0))
        page.set_rotation(90)
        # The page's own rotation is already included by page.rect/get_pixmap.
        displayed = pymupdf.Rect(80, 330, 120, 360) * page.rotation_matrix
        source = [
            displayed.x0 / page.rect.width,
            displayed.y0 / page.rect.height,
            displayed.x1 / page.rect.width,
            displayed.y1 / page.rect.height,
        ]
        extra = 270
        upright = reader._original_box(source, (360 - extra) % 360)
        actual, raw, _meta = reader._render_detail(page, upright, extra, 600)
    assert np.asarray(Image.open(io.BytesIO(raw)).convert("L")).min() == 0
    assert reader._original_box(actual, extra) == pytest.approx(source, abs=0.001)
