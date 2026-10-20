"""Bounded automatic PDF batches, retaining the immutable original page identity.

Batch copies are temporary in-memory parser inputs. They never replace the
uploaded original, its SHA-256, or its original 1-based page coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pymupdf

PDF_MAX_BYTES = 200 * 1024 * 1024
PDF_MAX_PAGES = 500
PDF_BATCH_PAGES = 25
PDF_MAX_PAGE_CONTENT = 12 * 1024 * 1024


def original_checksum(path, expected=None):
    digest = sha256()
    total = 0
    with Path(path).open("rb") as original:
        while block := original.read(1024 * 1024):
            total += len(block)
            if total > PDF_MAX_BYTES:
                raise ValueError("The original PDF exceeds the bounded 200 MB size.")
            digest.update(block)
    if not total:
        raise ValueError("The original PDF is empty.")
    actual = digest.hexdigest()
    if expected and actual != expected.lower():
        raise ValueError("The original PDF bytes no longer match their source hash.")
    return actual


@dataclass(frozen=True)
class PDFBatch:
    index: int
    first_page: int
    last_page: int
    total_pages: int

    def public(self):
        return {
            "batch": self.index,
            "first_page": self.first_page,
            "last_page": self.last_page,
            "original_page_count": self.total_pages,
        }


def batch_plan(page_count):
    if page_count < 0:
        raise ValueError("A PDF page count cannot be negative.")
    return [
        PDFBatch(
            i // PDF_BATCH_PAGES + 1,
            i + 1,
            min(i + PDF_BATCH_PAGES, page_count, PDF_MAX_PAGES),
            page_count,
        )
        for i in range(0, min(page_count, PDF_MAX_PAGES), PDF_BATCH_PAGES)
    ]


def document_batches(document):
    """Yield isolated parser copies of at most 25 original pages at a time."""
    if document.needs_pass:
        raise ValueError("encrypted_pdf")
    for batch in batch_plan(len(document)):
        with pymupdf.open() as chunk:
            chunk.insert_pdf(
                document, from_page=batch.first_page - 1, to_page=batch.last_page - 1
            )
            yield batch, chunk


def original_page(page):
    return getattr(page, "_rivet_original_page", page.number + 1)


def original_pages(batch, chunk):
    for local_index in range(len(chunk)):
        page = chunk[local_index]
        page._rivet_original_page = batch.first_page + local_index
        yield page
