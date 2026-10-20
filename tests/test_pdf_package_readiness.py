"""Large immutable PDFs, real page provenance, and title-block revision identity."""

from hashlib import sha256
from types import SimpleNamespace

import pymupdf
import pytest
from reportlab.pdfgen import canvas

from backend.equipment import readers
from backend.equipment.pdf_batches import PDF_BATCH_PAGES, PDF_MAX_BYTES
from backend.equipment.pdf_model import compare_pdf, inspect_pdf
from backend.equipment.readers import clean_tag, read_source
from backend.ingestion import parser
from backend.storage.models import Span


def source(path):
    return {
        "id": path.name,
        "name": path.name,
        "path": str(path),
        "role": "drawing",
        "revision": "C",
        "sha256": sha256(path.read_bytes()).hexdigest(),
    }


def sheets(path, entries):
    c = canvas.Canvas(str(path), pagesize=(600, 800), invariant=1)
    for drawing, tag, title, sheet in entries:
        c.setFont("Helvetica", 10)
        for y, text in (
            (700, tag),
            (680, "Frame: 800 A"),
            (660, "Trip: 600 A"),
            (640, "Part number: BRK-800"),
            (620, "Quantity: 1"),
            (105, f"Drawing No: {drawing}"),
            (85, f"Sheet: {sheet}"),
            (65, f"Title: {title}"),
            (45, "Revision: C"),
        ):
            c.drawString(70, y, text)
        c.rect(60, 610, 200, 110)
        c.showPage()
    c.save()


@pytest.fixture(scope="module")
def large_package(tmp_path_factory):
    root = tmp_path_factory.mktemp("large-real-format-pdf")
    small = root / "basis.pdf"
    entries = [
        (f"E-{n:03d}", f"CB-{n}", f"Feeder {n} equipment", str(n))
        for n in range(1, 301)
    ]
    sheets(small, entries)
    large = root / "300-sheet-package.pdf"
    with pymupdf.open(small) as doc:
        for n, page in enumerate(doc):
            if n == 150:
                # A genuine blank/unreadable source page in the middle must not
                # erase facts on the later149 pages.
                xref = doc.get_new_xref()
                doc.update_object(xref, "<<>>")
                doc.update_stream(
                    xref, b"% unreadable blank original page\n", compress=False
                )
                page.set_contents(xref)
            else:
                content = (
                    page.read_contents()
                    + b"\n%"
                    + b" original CAD export padding" * 3300
                    + b"\n"
                )
                xref = doc.get_new_xref()
                doc.update_object(xref, "<<>>")
                doc.update_stream(xref, content, compress=False)
                page.set_contents(xref)
        doc.save(large, deflate=False)
    assert 20 * 1024 * 1024 < large.stat().st_size < PDF_MAX_BYTES
    return large


def test_300_sheet_over20mb_package_is_batched_and_late_facts_keep_original_identity(
    large_package,
):
    original = large_package.read_bytes()
    report, facts = read_source(source(large_package))
    assert report["page_count"] == 300 and len(report["processing_batches"]) == 12
    assert all(
        b["last_page"] - b["first_page"] + 1 <= PDF_BATCH_PAGES
        for b in report["processing_batches"]
    )
    assert report["status"] == "partial"
    assert {f["evidence"]["page"] for f in facts} == set(range(1, 301)) - {151}
    late = next(
        f for f in facts if f["device_tag"] == "CB-300" and f["field"] == "trip_a"
    )
    assert late["value"] == 600 and late["evidence"]["page"] == 300
    assert late["sha256"] == sha256(original).hexdigest()
    assert report["page_coverage"][150]["status"] == "unreadable"
    assert any(
        w["code"] == "unreadable_page" and w["page"] == 151 for w in report["warnings"]
    )
    assert large_package.read_bytes() == original


def test_vector_reader_processes_all300_original_pages_in_bounded_batches(
    large_package,
):
    report = inspect_pdf(source(large_package))
    assert (
        report["coverage"]["processed_pages"]
        == report["coverage"]["total_pages"]
        == 300
    )
    assert len(report["processing_batches"]) == 12
    assert report["pages"][299]["page"] == 300
    assert report["pages"][299]["sheet_identity"]["drawing_id"] == "E-300"
    evidence = report["pages"][299]["sheet_identity"]["evidence"]["drawing_id"]
    assert all(a["page"] == 300 and a["sha256"] == report["sha256"] for a in evidence)
    assert report["coverage"]["status"] == "unknown"


class Capture:
    def __init__(self):
        self.spans = []

    def add(self, item):
        assert isinstance(item, Span)
        self.spans.append(item)

    def flush(self):
        pass


def test_ingestion_accepts300_pages_with_original_hash_and_page300(
    large_package, monkeypatch
):
    from backend.ingestion import vision

    monkeypatch.setattr(parser, "BLOBS", large_package.parent)
    monkeypatch.setattr(vision, "schedule", lambda *_: None)
    d = SimpleNamespace(
        id="original-large-document",
        blob=large_package.name,
        sha256=sha256(large_package.read_bytes()).hexdigest(),
        meta={},
        coverage=[],
        state="queued",
    )
    session = Capture()
    parser.parse(session, d)
    assert d.state == "ready" and len(d.coverage) == 300
    final = [s for s in session.spans if s.location["page"] == 300]
    assert any("CB-300" in s.text for s in final)
    assert all(
        s.document_id == d.id and s.location["original_sha256"] == d.sha256
        for s in session.spans
    )


def test_revision_matching_uses_titleblock_identity_not_page_position(tmp_path):
    before, after = tmp_path / "before.pdf", tmp_path / "after.pdf"
    a = ("E-101", "CB-1", "Incoming feeder", "1")
    b = ("E-102", "CB-2", "Motor feeder", "2")
    c = ("E-103", "CB-3", "Lighting feeder", "3")
    sheets(before, [a, b, c])
    sheets(after, [c, a, ("E-104", "CB-4", "New spare feeder", "4")])
    report = compare_pdf(source(before), source(after))
    match = report["sheet_matches"]
    assert {(m["baseline_page"], m["target_page"]) for m in match["matches"]} == {
        (1, 2),
        (3, 1),
    }
    assert [m["identity"]["drawing_id"] for m in match["removed"]] == ["E-102"]
    assert [m["identity"]["drawing_id"] for m in match["added"]] == ["E-104"]
    assert (
        match["complete_sheet_identity_coverage"]
        and not match["page_order_used_for_matching"]
    )
    assert not any(
        c["kind"] == "schematic_geometry" and c["device_tag"] in {"CB-1", "CB-3"}
        for c in report["changes"]
    )


def test_explicit_renumbering_and_repeated_sheet_identity_remain_distinct(tmp_path):
    before, after = tmp_path / "old.pdf", tmp_path / "new.pdf"
    sheets(before, [("E-201", "CB-1", "Incoming feeder equipment", "1")])
    sheets(after, [("E-901", "CB-1", "Incoming feeder equipment", "7")])
    match = compare_pdf(source(before), source(after))["sheet_matches"]
    assert len(match["matches"]) == 1 and match["matches"][0]["status"] == "renumbered"
    assert match["matches"][0]["method"] == "unique_title_and_unchanged_body"
    sheets(before, [("E-201", "CB-1", "Same duplicate title", "1")] * 2)
    sheets(after, [("E-201", "CB-1", "Same duplicate title", "1")] * 2)
    match = compare_pdf(source(before), source(after))["sheet_matches"]
    assert match["ambiguous"] and not match["matches"]
    assert not match["complete_sheet_identity_coverage"]


@pytest.mark.parametrize("tag", ["52-12", "BKR-12", "FDR 12", "86T", "1086M"])
def test_real_electrical_tag_styles_are_exact_and_source_backed(tmp_path, tag):
    path = tmp_path / "tags.pdf"
    sheets(path, [("E-1", tag, "Device equipment schedule", "1")])
    report, facts = read_source(source(path))
    assert report["status"] == "parsed"
    assert tag in report["observed_device_tags"]
    assert any(f["device_tag"] == tag and f["field"] == "part_number" for f in facts)
    assert clean_tag(tag) == tag
    from backend.equipment.pdf_corpus import series, write_visible_pdf

    drawing = series()
    drawing["devices"][0]["tag"] = tag
    schematic = tmp_path / "schematic-tags.pdf"
    write_visible_pdf(schematic, drawing, "Source equipment symbols")
    graph = inspect_pdf(source(schematic))
    assert tag in {d["tag"] for d in graph["devices"]}


def test_separator_variants_are_never_silently_aliased(tmp_path):
    path = tmp_path / "variants.pdf"
    sheets(
        path,
        [
            ("E-1", "FDR 12", "Feeder equipment", "1"),
            ("E-2", "FDR-12", "Other feeder equipment", "2"),
        ],
    )
    report, facts = read_source(source(path))
    assert {f["device_tag"] for f in facts} == {"FDR 12", "FDR-12"}
    assert any(w["code"] == "ambiguous_tag_styles" for w in report["warnings"])
    from backend.equipment.pdf_corpus import series, write_visible_pdf

    with pymupdf.open() as package:
        for n, tag in enumerate(("FDR 12", "FDR-12")):
            drawing = series()
            drawing["devices"][0]["tag"] = tag
            part = tmp_path / f"symbol-{n}.pdf"
            write_visible_pdf(part, drawing, "Source equipment symbols")
            with pymupdf.open(part) as pdf:
                package.insert_pdf(pdf)
        combined = tmp_path / "symbol-package.pdf"
        package.save(combined)
    graph = inspect_pdf(source(combined))
    assert any(i["code"] == "ambiguous_tag_styles" for i in graph["issues"])


def test_one_failed_page_does_not_erase_later_source_facts(tmp_path, monkeypatch):
    path = tmp_path / "single-failed-page.pdf"
    sheets(
        path, [(f"E-{n}", f"CB-{n}", "Equipment source", str(n)) for n in range(1, 4)]
    )
    original = readers._read_pdf_page

    def fails_middle(page, collector):
        if page._rivet_original_page == 2:
            raise RuntimeError("damaged page object")
        return original(page, collector)

    monkeypatch.setattr(readers, "_read_pdf_page", fails_middle)
    report, facts = read_source(source(path))
    assert report["status"] == "partial"
    assert {f["device_tag"] for f in facts} == {"CB-1", "CB-3"}
    assert report["page_coverage"][1]["status"] == "unreadable"


def test_invisible_ocr_property_text_is_not_actual_equipment_state(tmp_path):
    path = tmp_path / "hidden-rating.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((70, 100), "CB-12")
        page.insert_text((70, 120), "Part number: BRK-800")
        page.insert_text((70, 140), "Nameplate: Incoming service")
        page.insert_text((70, 160), "Trip: 1200 A", render_mode=3)
        pdf.save(path)
    _, facts = read_source(source(path))
    assert any(f["field"] == "part_number" for f in facts)
    assert not any(f["field"] == "trip_a" for f in facts)


def test_ingestion_refuses_stale_original_hash_before_creating_any_span(
    tmp_path, monkeypatch
):
    path = tmp_path / "changed-original.pdf"
    sheets(path, [("E-1", "CB-1", "Equipment source", "1")])
    monkeypatch.setattr(parser, "BLOBS", tmp_path)
    document = SimpleNamespace(
        id="changed",
        blob=path.name,
        sha256="0" * 64,
        meta={},
        coverage=[],
        state="queued",
    )
    capture = Capture()
    with pytest.raises(ValueError, match="source hash"):
        parser.parse(capture, document)
    assert capture.spans == []
