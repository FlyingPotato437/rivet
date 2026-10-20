"""Actual visible PDF vectors / text, with independent circuit truth."""

import math
from hashlib import sha256

import pymupdf
import pytest
from reportlab.pdfgen import canvas

from backend.equipment import pdf_model
from backend.equipment.pdf_corpus import (
    build_vector_pdf_corpus,
    series,
    write_visible_pdf,
)
from backend.equipment.pdf_evaluation import evaluate_pdf_graph
from backend.equipment.pdf_model import PDFModelError, compare_pdf, inspect_pdf


def source(path, **metadata):
    return dict(
        id=metadata.pop("id", path.name),
        name=path.name,
        path=str(path),
        role="drawing",
        revision="C",
        sha256=sha256(path.read_bytes()).hexdigest(),
        **metadata,
    )


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("pdf-engine")
    manifest = build_vector_pdf_corpus(root)
    return root, {item["id"][:2]: item for item in manifest["fixtures"]}


def read(corpus, key):
    root, cases = corpus
    return inspect_pdf(source(root / cases[key]["path"]))


@pytest.mark.parametrize("key", ["01", "02", "03", "04", "05", "06", "07", "08", "09"])
def test_independently_defined_visible_circuit(corpus, key):
    report = read(corpus, key)
    result = evaluate_pdf_graph(report, corpus[1][key]["ground_truth"])
    assert result["tags"]["recall"] == 1
    assert result["ports"]["recall"] == 1
    assert not result["connections"]["missed"]
    assert not result["connections"]["unexpected"]
    assert report["coverage"]["complete_inventory_established"] is False
    if key in {"05", "06", "07", "09"}:
        assert report["coverage"]["status"] == "unknown"
    for page in report["pages"]:
        assert page["physical_scale"] is None and page["units"] == "pdf_points"
        for device in page["devices"]:
            for anchor in device["evidence"]:
                assert anchor["source_id"] == report["source"]["id"]
                assert anchor["sha256"] == report["sha256"]
                assert anchor["page"] == page["page"]
                assert all(0 <= n <= 1 for n in anchor["normalized_bbox"])


def test_rotated_and_cropped_coordinates_are_visible_page_coordinates(corpus):
    rotated = read(corpus, "08")
    tb = next(d for d in rotated["devices"] if d["tag"] == "TB-1")
    assert tb["bbox"] == [260, 70, 380, 170]
    assert next(p for p in tb["ports"] if p["label"] == "1")["position"] == [350, 170]
    cropped = read(corpus, "09")
    tb = next(d for d in cropped["devices"] if d["tag"] == "TB-1")
    assert tb["bbox"] == [30, 30, 130, 150]
    assert next(p for p in tb["ports"] if p["label"] == "1")["position"] == [130, 60]
    assert tb["normalized_bbox"] == pytest.approx(
        [30 / 530, 30 / 180, 130 / 530, 150 / 180], abs=1e-6
    )


def test_real_cad_export_with_outlined_glyphs_stays_unknown(corpus):
    report = read(corpus, "10")
    assert report["coverage"]["status"] == "unknown"
    assert report["devices"] == [] and report["connections"] == []
    assert any(i["code"] == "no_selectable_labels" for i in report["issues"])


def test_revision_diff_requires_actual_geometry_and_topology(corpus, tmp_path):
    root, cases = corpus
    before = root / cases["02"]["path"]
    after = root / cases["03"]["path"]
    change = compare_pdf(source(before), source(after))
    assert change["summary"]["observed_connectivity_changes"] == 3
    assert not change["summary"]["complete_revision_verified"]
    assert (
        change["engineering_approved"] is False
        and change["production_released"] is False
    )
    moved = series()
    moved["devices"][1]["bbox"] = [450, 170, 570, 290]
    moved["devices"][1]["ports"] = {"A1": [450, 200], "A2": [450, 260]}
    moved["wires"] = [[[170, 200], [450, 200]], [[170, 260], [450, 260]]]
    path = tmp_path / "actual-move.pdf"
    write_visible_pdf(path, moved, "All comments fixed")
    delta = compare_pdf(source(root / cases["01"]["path"]), source(path))
    assert any(
        c["kind"] == "schematic_geometry" and c["device_tag"] == "KA-1"
        for c in delta["changes"]
    )
    unchanged = tmp_path / "fixed-note-only.pdf"
    write_visible_pdf(unchanged, series(), "All comments fixed")
    assert (
        compare_pdf(source(root / cases["01"]["path"]), source(unchanged))["changes"]
        == []
    )


def test_verified_hash_precedes_cache_and_cached_evidence_is_rebound(corpus, tmp_path):
    root, cases = corpus
    original = root / cases["01"]["path"]
    copy = tmp_path / "second-source.pdf"
    copy.write_bytes(original.read_bytes())
    first = inspect_pdf(source(original, id="source-A"))
    first["devices"][0]["ports"].clear()
    second = inspect_pdf(source(copy, id="source-B"))
    assert second["devices"][0]["ports"]
    assert second["devices"][0]["evidence"][0]["source_id"] == "source-B"
    stale = source(copy)
    copy.write_bytes(b"different actual bytes")
    with pytest.raises(PDFModelError, match="hash"):
        inspect_pdf(stale)
    markup = source(original)
    markup["role"] = "markup"
    with pytest.raises(PDFModelError, match="actual drawing"):
        inspect_pdf(markup)


def overlay(corpus, tmp_path, name, draw):
    root, cases = corpus
    output = tmp_path / name
    with pymupdf.open(root / cases["01"]["path"]) as pdf:
        draw(pdf[0])
        pdf.save(output)
    return inspect_pdf(source(output))


def test_partial_wire_obscuration_cannot_create_a_through_connection(corpus, tmp_path):
    report = overlay(
        corpus,
        tmp_path,
        "covered-wire.pdf",
        lambda page: page.draw_rect(
            pymupdf.Rect(215, 195, 235, 205), color=None, fill=(1, 1, 1)
        ),
    )
    assert len(report["connections"]) == 1
    assert report["connections"][0]["terminal_ids"] == ["KA-1:A2", "TB-1:2"]
    assert any(i["code"] == "covered_conductor" for i in report["issues"])
    assert report["coverage"]["status"] == "unknown"


def test_hidden_text_cannot_replace_an_obscured_device_label(corpus, tmp_path):
    def draw(page):
        page.draw_rect(pymupdf.Rect(80, 215, 160, 240), color=None, fill=(1, 1, 1))
        page.insert_text((101, 234), "TB-1", fontsize=11, render_mode=3)

    report = overlay(corpus, tmp_path, "hidden-label.pdf", draw)
    assert "TB-1" not in {d["tag"] for d in report["devices"]}
    assert report["coverage"]["status"] == "unknown"


def test_hidden_optional_layer_has_no_device_identity(tmp_path):
    path = tmp_path / "hidden-layer.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=300)
        oc = pdf.add_ocg("Hidden equipment", on=False)
        page.draw_rect(pymupdf.Rect(100, 100, 200, 200), oc=oc)
        page.insert_text((130, 150), "CB-12", oc=oc)
        pdf.save(path)
    report = inspect_pdf(source(path))
    assert report["devices"] == [] and report["connections"] == []
    assert report["coverage"]["status"] == "unknown"


def test_white_open_circle_at_crossing_is_not_a_filled_junction(corpus, tmp_path):
    root, cases = corpus
    path = tmp_path / "white-crossing.pdf"
    with pymupdf.open(root / cases["02"]["path"]) as pdf:
        pdf[0].draw_circle(pymupdf.Point(350, 255), 3, fill=(1, 1, 1), color=(0, 0, 0))
        pdf.save(path)
    report = inspect_pdf(source(path))
    assert {tuple(c["terminal_ids"]) for c in report["connections"]} == {
        ("CB-12:TC1", "TB-1:1"),
        ("KA-1:13", "PL-1:X1"),
    }
    assert not report["pages"][0]["junctions"]


def test_bounded_complexity_is_explicit_unknown_not_empty_success(corpus, monkeypatch):
    root, cases = corpus
    pdf_model._CACHE.clear()
    monkeypatch.setattr(pdf_model, "MAX_PAIR_TESTS", 0)
    report = inspect_pdf(source(root / cases["02"]["path"]))
    assert report["coverage"]["status"] == "unknown"
    assert any(i["code"] == "page_complexity_or_coverage" for i in report["issues"])
    assert report["devices"] == [] and report["connections"] == []
    pdf_model._CACHE.clear()


def test_flattened_polygon_terminal_and_split_filled_dot_are_actual_ink(tmp_path):
    path = tmp_path / "polygon-export.pdf"
    c = canvas.Canvas(str(path), pagesize=(500, 300), invariant=1)
    c.setLineWidth(0.4)
    c.line(80, 250, 420, 250)
    for x, label in ((140, "13"), (360, "15")):
        c.line(x, 250, x, 180)
        # Older CAD export: terminal circles are explicit line approximations.
        p = c.beginPath()
        points = [
            (x + 3 * math.cos(i * math.tau / 16), 180 + 3 * math.sin(i * math.tau / 16))
            for i in range(17)
        ]
        p.moveTo(*points[0])
        for point in points[1:]:
            p.lineTo(*point)
        c.drawPath(p)
        c.drawString(x + 6, 177, label)
        # The junction is visibly two filled closed half-polygons, no metadata.
        for start in (0, math.pi):
            p = c.beginPath()
            p.moveTo(x, 250)
            for i in range(17):
                a = start + i * math.pi / 16
                p.lineTo(x + 2 * math.cos(a), 250 + 2 * math.sin(a))
            p.close()
            c.drawPath(p, fill=1, stroke=1)
    c.save()
    report = inspect_pdf(source(path))
    assert report["devices"] == []
    terminals = {t["id"]: t["label"] for t in report["unassigned_terminals"]}
    assert {terminals[t] for t in report["connections"][0]["terminal_ids"]} == {
        "13",
        "15",
    }
    assert len(report["pages"][0]["junctions"]) == 2
    assert report["coverage"]["status"] == "unknown"


def test_clipped_form_text_cannot_name_a_different_visible_outline(tmp_path):
    path = tmp_path / "clipped-form.pdf"
    with pymupdf.open() as clipped:
        page = clipped.new_page(width=400, height=300)
        page.insert_text((130, 150), "CB-12")
        with pymupdf.open() as pdf:
            output = pdf.new_page(width=400, height=300)
            # The form's visible crop excludes CB-12, though texttrace returns it.
            output.show_pdf_page(
                pymupdf.Rect(0, 0, 200, 300),
                clipped,
                0,
                clip=pymupdf.Rect(0, 0, 80, 300),
            )
            output.draw_rect(pymupdf.Rect(160, 100, 260, 200))
            output.draw_line(pymupdf.Point(100, 150), pymupdf.Point(160, 150))
            pdf.save(path)
    report = inspect_pdf(source(path))
    assert report["devices"] == []
    assert any(i["code"] == "unresolved_text_clipping" for i in report["issues"])
    assert report["coverage"]["status"] == "unknown"


def test_raster_sheet_is_explicit_unknown_even_when_text_is_present(tmp_path):
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as raster_source:
        page = raster_source.new_page(width=400, height=300)
        page.draw_rect(pymupdf.Rect(100, 100, 200, 200))
        page.insert_text((130, 150), "CB-12")
        pix = page.get_pixmap()
        with pymupdf.open() as pdf:
            target = pdf.new_page(width=400, height=300)
            target.insert_image(target.rect, stream=pix.tobytes("png"))
            target.insert_text((20, 20), "All comments fixed")
            pdf.save(path)
    report = inspect_pdf(source(path))
    assert report["devices"] == [] and report["connections"] == []
    assert any(i["code"] == "raster_content" for i in report["issues"])
    assert report["coverage"]["status"] == "unknown"


def test_repeated_tagged_symbols_do_not_merge_terminals(tmp_path):
    path = tmp_path / "repeated-tag.pdf"
    drawing = series()
    drawing["devices"][1]["tag"] = "TB-1"
    write_visible_pdf(path, drawing, "Two symbols repeat the same tag")
    report = inspect_pdf(source(path))
    assert all(
        d["symbol"]["status"] == "unknown_multiunit_identity" for d in report["devices"]
    )
    assert not report["connections"]
    assert any(i["code"] == "duplicate_or_multiunit_tag" for i in report["issues"])


def test_unrequested_annotations_are_not_drawing_completion(tmp_path, corpus):
    root, cases = corpus
    path = tmp_path / "review-only.pdf"
    with pymupdf.open(root / cases["01"]["path"]) as pdf:
        pdf[0].add_text_annot((280, 200), "Connect all terminals. Done, all fixed.")
        pdf.save(path)
    report = inspect_pdf(source(path))
    assert len(report["connections"]) == 2
    assert any(i["code"] == "annotations_uninterpreted" for i in report["issues"])
    assert (
        compare_pdf(source(root / cases["01"]["path"]), source(path))["changes"] == []
    )


def test_many_spatially_separate_fills_do_not_exhaust_visibility_work(
    corpus, tmp_path, monkeypatch
):
    root, cases = corpus
    path = tmp_path / "many-fills.pdf"
    with pymupdf.open(root / cases["01"]["path"]) as pdf:
        for n in range(60):
            pdf[0].draw_rect(
                pymupdf.Rect(n * 2, 60, n * 2 + 1, 61), color=None, fill=(1, 1, 1)
            )
        pdf.save(path)
    pdf_model._CACHE.clear()
    monkeypatch.setattr(pdf_model, "MAX_ASSOCIATION_WORK", 1000)
    report = inspect_pdf(source(path))
    assert report["coverage"]["status"] == "unknown"
    # The bound is on actual overlapping candidates, not the former quadratic
    # word-count × every-fill estimate. Original visible labels still survive.
    assert not any(
        "visibility testing exceeds" in i["reason"] for i in report["issues"]
    )
    assert {d["tag"] for d in report["devices"]} == {
        d["tag"] for d in inspect_pdf(source(root / cases["01"]["path"]))["devices"]
    }
    pdf_model._CACHE.clear()


def test_fill_only_rectangle_is_not_a_visible_device_outline(tmp_path):
    path = tmp_path / "invisible-white-body.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=300)
        page.draw_rect(pymupdf.Rect(100, 100, 200, 200), color=None, fill=(1, 1, 1))
        page.insert_text((130, 150), "CB-12")
        page.draw_line(pymupdf.Point(50, 150), pymupdf.Point(100, 150))
        page.insert_text((105, 145), "A1")
        pdf.save(path)
    report = inspect_pdf(source(path))
    assert report["devices"] == [] and report["connections"] == []
    assert report["coverage"]["status"] == "unknown"
