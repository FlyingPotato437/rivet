"""Shared upload boundaries; PDF processing uses original-page batches."""

from pathlib import Path

import pymupdf

from backend.equipment.pdf_batches import PDF_MAX_BYTES, PDF_MAX_PAGES

OTHER_MAX_BYTES = 20 * 1024 * 1024
DWG_REQUEST = (
    "Native DWG conversion is not configured. Export this drawing as DXF "
    "(with block attributes and units retained) and a matching PDF. Rivet "
    "cannot verify or edit the DWG itself."
)


def upload_max_bytes(name):
    return PDF_MAX_BYTES if Path(name).suffix.lower() == ".pdf" else OTHER_MAX_BYTES


def validate_pdf_upload(data):
    if not data.startswith(b"%PDF"):
        raise ValueError("This file is not a valid PDF.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass:
                raise ValueError("Export an unlocked PDF before uploading.")
            pages = len(document)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            "This PDF cannot be opened. Export an unlocked PDF and try again."
        ) from exc
    if pages > PDF_MAX_PAGES:
        raise ValueError(
            f"PDFs above {PDF_MAX_PAGES} pages must be split into named sections; "
            "retain the original page numbers in each section."
        )
    return pages
