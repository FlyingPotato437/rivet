"""Join saved PDF markup to citable spans on the same immutable original."""

from collections import defaultdict
from collections.abc import Mapping
from hashlib import file_digest
from pathlib import Path

import pymupdf

_VISIBLE_PAGE = "visible-page-v1"
_BOX_TOLERANCE = 0.000002  # Accounts for native parser rounding to six places.
_SUPPORT_KINDS = {"Line", "Highlight", "StrikeOut"}
_NO_TEXT_DETAIL = (
    "A graphical annotation has no extractable text. Manual interpretation is required."
)


def _get(row, key, default=None):
    return (
        row.get(key, default)
        if isinstance(row, Mapping)
        else getattr(row, key, default)
    )


def _annotation_spans(spans):
    for span in spans:
        location = _get(span, "location", {})
        if location.get("annotation_id"):
            yield span, location


def annotation_coverage(coverage, spans, *, document_id=None):
    """Group no-text support marks by page, retaining every source reference."""
    anchors = defaultdict(list)
    for span, loc in _annotation_spans(spans):
        key = (
            _get(span, "document_id", document_id),
            loc.get("page"),
            loc["annotation_id"],
        )
        anchors[key].append((span, loc))
    output, groups = [], {}
    for part in coverage:
        annotation_id = part.get("annotation_id")
        if not annotation_id or (
            part.get("kind") != "unread_graphical_annotation"
            and part.get("detail") != _NO_TEXT_DETAIL
        ):
            output.append(part)
            continue
        # Earlier coverage records stored the page in the label only.
        label = part.get("label", "").split()
        page = part.get("page") or (
            int(label[1])
            if len(label) >= 2 and label[0] == "Page" and label[1].isdigit()
            else None
        )
        doc_id = part.get("document_id", document_id)
        matches = anchors[(doc_id, page, annotation_id)]
        if source_id := part.get("source_id"):
            matches = [pair for pair in matches if _get(pair[0], "id") == source_id]
        if len(matches) != 1 or str(_get(matches[0][0], "text", "")).strip():
            output.append(part)
            continue
        span, location = matches[0]
        kind = location.get("annotation_type", "Mark")
        mark = {
            **part,
            "page": page,
            "source_id": _get(span, "id"),
            "annotation_type": kind,
            "bbox": location.get("bbox"),
        }
        if kind not in _SUPPORT_KINDS:
            mark["detail"] = (
                f"Unidentified {kind.lower()} mark. Review the original area for a possible instruction."
            )
            output.append(mark)
            continue
        key = (doc_id, page)
        if key not in groups:
            groups[key] = {
                **({"document_id": doc_id} if doc_id else {}),
                "label": f"Page {page} supporting markup",
                "page": page,
                "state": "unsupported",
                "kind": "supporting_markup",
                "marks": [],
            }
            output.append(groups[key])
        group = groups[key]
        group["marks"].append(mark)
        count = len(group["marks"])
        group["detail"] = (
            f"{count} graphical support {'mark' if count == 1 else 'marks'} "
            "without a written instruction. Review these in the original page context."
        )
    return output


def _box(value):
    if (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in value)
        and value[0] < value[2]
        and value[1] < value[3]
    ):
        return value
    return None


def _same_box(left, right, tolerance):
    return (
        isinstance(left, (list, tuple))
        and right is not None
        and len(left) == len(right) == 4
        and all(
            isinstance(a, (int, float)) and abs(a - b) <= tolerance
            for a, b in zip(left, right, strict=True)
        )
    )


def _visible_box(page, rect):
    rect = pymupdf.Rect(rect) * page.rotation_matrix
    return _box(
        [
            min(1.0, max(0.0, (v - origin) / size))
            for v, origin, size in zip(
                rect,
                [page.rect.x0, page.rect.y0] * 2,
                [page.rect.width, page.rect.height] * 2,
                strict=True,
            )
        ]
    )


def _callout_text_box(pdf, page, annotation):
    # /RD is the inner FreeText rectangle's left, bottom, right, top inset
    # from the annotation /Rect in PDF coordinates. The arrow stays in /Rect.
    kind, offsets = pdf.xref_get_key(annotation.xref, "RD")
    if annotation.type[1] != "FreeText" or kind != "array":
        return None
    try:
        left, bottom, right, top = map(float, offsets.strip("[]").split())
    except ValueError:
        return None
    if min(left, bottom, right, top) < 0:
        return None
    rect = annotation.rect
    text_box = pymupdf.Rect(
        rect.x0 + left, rect.y0 + top, rect.x1 - right, rect.y1 - bottom
    )
    return _visible_box(page, text_box) if not text_box.is_empty else None


def _ground_document(path, sha256, spans, items):
    candidates = defaultdict(list)
    for span, loc in _annotation_spans(spans):
        if (
            loc.get("original_sha256") == sha256
            and loc.get("coordinate_system") == _VISIBLE_PAGE
        ):
            candidates[loc.get("page")].append((span, loc))
    with pymupdf.open(path) as pdf:
        for number, rows in items.items():
            if not rows or not 1 <= number <= len(pdf):
                continue
            page = pdf[number - 1]
            annotations = defaultdict(list)
            for annotation in page.annots() or ():
                anchor = annotation.info.get("id") or str(annotation.xref)
                annotations[anchor].append(annotation)
            for row in rows:
                _ground_item(pdf, page, row, annotations, candidates[number])


def _ground_item(pdf, page, row, annotations, candidates):
    text = row.get("text", "").strip()
    anchor = row.get("annotation_id")
    vision = row.get("extraction_method") == "vision"
    region, text_bbox, annotation_type = None, None, row.get("annotation_type")
    if not vision:
        matches = [
            annotation
            for annotation in annotations[anchor]
            if annotation.info.get("content", "").strip() == text
            and _same_box(row.get("bbox"), annotation.rect, 0.0002)
        ]
        if len(matches) != 1:
            return
        annotation = matches[0]
        annotation_type = annotation.type[1]
        region = _visible_box(page, annotation.rect)
        text_bbox = _callout_text_box(pdf, page, annotation)
    matches = [
        (span, loc)
        for span, loc in candidates
        if _get(span, "text", "").strip() == text
        and (loc.get("extraction_method") == "vision") == vision
        and (not annotation_type or loc.get("annotation_type") == annotation_type)
        and _box(loc.get("bbox"))
        and (
            loc["annotation_id"] == anchor
            if vision
            else _same_box(region, loc["bbox"], _BOX_TOLERANCE)
        )
    ]
    if len(matches) != 1:
        return
    span, loc = matches[0]
    method = "vision" if vision else "native_pdf_annotation"
    confidence = loc.get(
        "transcription_confidence", "low" if vision or not text else "high"
    )
    row.update(
        source_id=_get(span, "id"),
        source_location={
            **loc,
            **({"text_bbox": text_bbox} if text_bbox else {}),
            "extraction_method": method,
            "transcription_confidence": confidence,
        },
        extraction_method=method,
        transcription_confidence=confidence,
        grounding_status="approximate" if vision or loc.get("approximate") else "exact",
    )


def ground_redesign_items(s, order, items):
    """Read-only enrichment of old run rows, including PDFs without /NM IDs."""
    from backend.domain.service import scoped
    from backend.storage import db
    from backend.storage.models import Document, Span

    result = [
        {
            **{
                k: v
                for k, v in item.items()
                if k not in {"source_id", "source_location"}
            },
            "grounding_status": "unavailable",
        }
        for item in items
    ]
    by_doc = defaultdict(lambda: defaultdict(list))
    for row in result:
        if isinstance(row.get("page"), int) and row.get("source_document_id"):
            by_doc[row["source_document_id"]][row["page"]].append(row)
    if not by_doc:
        return result
    docs = list(
        s.scalars(
            scoped(Document).where(
                Document.project_id == order.project_id, Document.id.in_(by_doc)
            )
        )
    )
    spans_by_doc = defaultdict(list)
    for span in s.scalars(
        scoped(Span).where(
            Span.document_id.in_([doc.id for doc in docs]),
            Span.location.has_key("annotation_id"),
        )
    ):
        spans_by_doc[span.document_id].append(span)
    for doc in docs:
        path = db.BLOBS / doc.blob
        if (
            Path(doc.blob).name != doc.blob
            or path.suffix.lower() != ".pdf"
            or path.is_symlink()
            or not path.is_file()
        ):
            continue
        bound = {
            page: [row for row in rows if row.get("source_sha256") == doc.sha256]
            for page, rows in by_doc[doc.id].items()
        }
        if not any(bound.values()):
            continue
        try:
            with path.open("rb") as original:
                if file_digest(original, "sha256").hexdigest() != doc.sha256:
                    continue
            _ground_document(path, doc.sha256, spans_by_doc[doc.id], bound)
        except (OSError, pymupdf.FileDataError):
            continue
    return result


def stored_visual_redesign_items(s, order, sources):
    """Expose additional visual findings for review, outside native CAD decisions."""
    from backend.domain.service import scoped
    from backend.ingestion.vision import Finding, native_duplicate
    from backend.storage.models import Document, Span

    originals = {
        source["id"]: source
        for source in sources
        if Path(source["path"]).suffix.lower() == ".pdf"
    }
    if not originals:
        return []
    spans = list(
        s.scalars(
            scoped(Span)
            .join(Document, Document.id == Span.document_id)
            .where(
                Document.project_id == order.project_id,
                Span.document_id.in_(originals),
                Span.location.has_key("annotation_id"),
            )
            .order_by(Span.created_at, Span.id)
        )
    )
    native_by_page = defaultdict(list)
    for span in spans:
        loc = span.location
        if loc.get("extraction_method") != "vision":
            native_by_page[(span.document_id, loc.get("page"))].append(span)

    items = []
    for span in spans:
        original = originals[span.document_id]
        loc = span.location
        page = loc.get("page")
        if (
            loc.get("extraction_method") != "vision"
            or loc.get("actionable") is False
            or loc.get("original_sha256") != original["sha256"]
            or loc.get("coordinate_system") != _VISIBLE_PAGE
            or not isinstance(page, int)
            or page < 1
            or not _box(loc.get("bbox"))
        ):
            continue
        confidence = loc.get("transcription_confidence", "low")
        finding = Finding(
            text=span.text.removeprefix("Rivet couldn’t read this confidently: "),
            kind=loc.get("annotation_type", "ambiguous_mark"),
            bbox=loc["bbox"],
            confidence=confidence,
        )
        if native_duplicate(finding, native_by_page[(span.document_id, page)], page):
            continue
        items.append(
            {
                "id": span.id,
                "text": span.text,
                "source_document_id": span.document_id,
                "source_name": original["name"],
                "source_sha256": original["sha256"],
                "page": page,
                "annotation_id": loc["annotation_id"],
                "source_id": span.id,
                "source_location": {**loc, "approximate": True},
                "extraction_method": "vision",
                "transcription_confidence": confidence,
                "grounding_status": "approximate",
                "status": "needs_information",
                "explanation": "Model-assisted reading. Verify the marked area on the original before treating it as an engineering instruction.",
                "change_ids": [],
            }
        )
    return items
