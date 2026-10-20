"""Generated source-only schedule localization controls, never evaluation keys."""

import hashlib
import io
import shutil

import pymupdf
import pytest
from PIL import Image, ImageDraw

from backend.engineering import schedule_regions as regions


@pytest.fixture(autouse=True)
def database():
    """No database, provider, order or engineering design state is involved."""
    yield


def _word(text, box, confidence=95, line=("1", "1", "1")):
    return {"text": text, "box": box, "confidence": confidence, "line": line}


def _source(path):
    return {
        "id": "synthetic-source",
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _pdf(path, *, scanned=False, title=True, headings=True, bottom=True):
    with pymupdf.open() as doc:
        page = doc.new_page(width=612, height=792)
        for x in (60, 552):
            page.draw_line((x, 60), (x, 600), width=1)
        for y in (60, 105, 170, *([600] if bottom else [])):
            page.draw_line((60, y), (552, y), width=1)
        for x in (140, 230, 330, 430):
            page.draw_line((x, 105), (x, 600), width=1)
        if title:
            page.insert_text((90, 88), "POWER CIRCUIT BREAKER SCHEDULE", fontsize=17)
        if headings:
            for x, text in (
                (70, "DEVICE"),
                (150, "FRAME"),
                (240, "CURRENT"),
                (340, "TRIP"),
                (440, "REMARKS"),
            ):
                page.insert_text((x, 135), text, fontsize=11)
        # Sparse printed rows, full-height shared arrows, and notes at the bottom
        # must not cause the region to stop after the last row label.
        page.insert_text((70, 200), "FDR-1", fontsize=12)
        page.insert_text((70, 235), "FDR-2", fontsize=12)
        page.draw_line((180, 200), (180, 525), width=1)
        page.draw_line((174, 515), (180, 525), width=1)
        page.draw_line((186, 515), (180, 525), width=1)
        page.insert_text((440, 550), "SPARE", fontsize=10)
        page.insert_text((70, 580), "NOTE: source note inside schedule", fontsize=10)
        if scanned:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            with pymupdf.open() as scan:
                raster_page = scan.new_page(width=612, height=792)
                raster_page.insert_image(raster_page.rect, stream=pix.tobytes("png"))
                scan.save(path)
        else:
            doc.save(path)
    return _source(path)


def _geometry(*, bottom=True, side=True, columns=True):
    image = Image.new("L", (600, 700), 255)
    draw = ImageDraw.Draw(image)
    for y in (80, 130, 170, *([620] if bottom else [])):
        draw.line((50, y, 550, y), fill=0, width=3)
    draw.line((50, 80, 50, 620), fill=0, width=3)
    if side:
        draw.line((550, 80, 550, 620), fill=0, width=3)
    if columns:
        for x in (170, 300, 430):
            draw.line((x, 130, x, 620), fill=0, width=3)
    return image


def test_title_requires_literal_schedule_and_electrical_concept():
    title = [_word("BREAKER", [10, 10, 70, 15]), _word("SCHEDULE", [90, 10, 90, 15])]
    assert len(regions._titles(title, 55)) == 1
    assert not regions._titles([_word("SCHEDULE", [10, 10, 90, 15])], 55)
    assert not regions._titles(
        [_word("BREAKER", [10, 10, 70, 15]), _word("SCHEDULE", [90, 10, 90, 15], 20)],
        55,
    )
    assert not regions._titles([_word("BREAKER SCHEDVLE", [10, 10, 180, 15])], 55)


def test_heading_patterns_establish_columns_without_parsing_values():
    words = [
        _word("DEVICE", [0, 0, 40, 10]),
        _word("FRAME", [50, 0, 40, 10]),
        _word("1200", [50, 30, 40, 10]),
    ]
    assert set(regions._heading_families(words, 55)) == {"identity", "frame"}


def test_complete_geometry_includes_sparse_rows_arrows_and_bottom_notes():
    found = regions._enclosures(_geometry(), {"bbox": [110, 92, 490, 116]})
    assert len(found) == 1
    assert found[0]["bbox"] == pytest.approx([50, 80, 550, 620], abs=2)
    assert found[0]["outer_rules_complete"]
    assert found[0]["interior_column_rules"] == 3
    assert found[0]["title_band_bottom"] == pytest.approx(130, abs=2)


@pytest.mark.parametrize("missing", ["bottom", "side", "columns"])
def test_missing_enclosure_or_columns_stays_unknown(missing):
    assert not regions._enclosures(
        _geometry(**{missing: False}), {"bbox": [110, 92, 490, 116]}
    )


def test_crop_plan_adapts_from_original_page_and_exposes_unusable_dpi():
    rect = pymupdf.Rect(0, 0, 612, 792)
    clip, dpi = regions._crop_plan([0, 0, 1, 1], rect, 360, 3000)
    assert clip == rect
    assert 250 <= dpi < 360
    _, dpi = regions._crop_plan([0, 0, 1, 1], pymupdf.Rect(0, 0, 2592, 1728), 360, 3000)
    assert dpi < 250


def test_source_hash_validation_precedes_pdf_or_ocr(tmp_path, monkeypatch):
    path = tmp_path / "not-a-pdf.pdf"
    path.write_bytes(b"source hash guard")
    monkeypatch.setattr(regions, "_orient", lambda _: pytest.fail("OCR must not run"))
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        regions.locate_schedule_regions({**_source(path), "sha256": "f" * 64}, 1)


@pytest.mark.parametrize("pages", [[], [1, 1], [1, 2, 3, 4, 5]])
def test_page_budget_precedes_pdf_or_ocr(tmp_path, pages):
    path = tmp_path / "not-a-pdf.pdf"
    path.write_bytes(b"page budget guard")
    with pytest.raises(ValueError, match="page_budget"):
        regions.locate_schedule_regions(_source(path), pages)


@pytest.mark.parametrize("page", [True, 0, -1, 2])
def test_physical_page_validation_before_ocr(tmp_path, monkeypatch, page):
    source = _pdf(tmp_path / "source.pdf")
    monkeypatch.setattr(regions, "_orient", lambda _: pytest.fail("OCR must not run"))
    with pytest.raises(ValueError, match="invalid_physical_page"):
        regions.locate_schedule_regions(source, page)


def test_weak_orientation_conservatively_returns_no_region(tmp_path, monkeypatch):
    source = _pdf(tmp_path / "source.pdf")
    monkeypatch.setattr(regions, "_orient", lambda _: (0, False, []))
    result = regions.locate_schedule_regions(source, 1)
    assert result["regions"] == []
    assert result["diagnostics"][0]["reasons"] == ["orientation_not_established"]


@pytest.mark.parametrize("scanned", [False, True])
@pytest.mark.skipif(not shutil.which("tesseract"), reason="Local Tesseract required")
def test_real_ocr_generated_source_complete_region_and_direct_crop(tmp_path, scanned):
    source = _pdf(tmp_path / "source.pdf", scanned=scanned)
    report = regions.locate_schedule_regions(source, 1, output_dir=tmp_path / "crops")
    assert report["status"] == "regions_located", report["diagnostics"]
    assert len(report["regions"]) == 1
    region = report["regions"][0]
    assert region["page"] == 1 and region["sha256"] == source["sha256"]
    assert region["bbox"] == pytest.approx(
        [60 / 612, 60 / 792, 552 / 612, 600 / 792], abs=0.005
    )
    assert region["bbox"][3] * 792 > 580  # In-table bottom note is included.
    assert 250 <= region["crop"]["render_dpi"] <= 360
    image = Image.open(io.BytesIO(region["png"]))
    assert list(image.size) == region["crop"]["pixel_size"]
    assert max(image.size) <= 3000
    assert hashlib.sha256(region["png"]).hexdigest() == region["crop"]["sha256"]
    assert region["requires_human_verification"] and not report["coverage_complete"]
    assert region["crop"]["rendered_directly_from_source"]
    assert "frame" in region["evidence"]["heading_families"]
    assert report["provider_model_calls"] == 0


def test_immutable_crop_storage_never_overwrites_other_bytes(tmp_path):
    path = tmp_path / "crop.png"
    first = regions._save_crop(path, b"first immutable crop")
    assert regions._save_crop(path, b"first immutable crop") == first
    with pytest.raises(ValueError, match="different_bytes"):
        regions._save_crop(path, b"different crop")
    assert path.read_bytes() == b"first immutable crop"
