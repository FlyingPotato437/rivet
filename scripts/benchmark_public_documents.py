"""Fetch pinned public originals and exercise Rivet's real PDF ingestion path.

No model requests, synthetic customers, or database changes. The independent
PyMuPDF oracle measures native annotation text, NOT all visual comment recall.
"""

import argparse
import hashlib
import json
import os
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "benchmarks/documents/sources.json"
CORPUS = ROOT / ".data/real-document-corpus"


def normalized(text):
    return " ".join(str(text).split())


def fetch(item):
    path = CORPUS / item["file"]
    if (
        not path.exists()
        or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
    ):
        response = httpx.get(item["url"], follow_redirects=True, timeout=120)
        response.raise_for_status()
        if hashlib.sha256(response.content).hexdigest() != item["sha256"]:
            raise ValueError(
                f"Publisher changed {item['id']}; review and repin it explicitly."
            )
        path.write_bytes(response.content)
    return path


class Capture:
    def __init__(self):
        self.spans = []

    def add(self, span):
        self.spans.append(span)

    def flush(self):
        pass


def evaluate(item):
    from backend.ingestion import parser

    os.environ["RIVET_VISION_ENABLED"] = "false"
    path = CORPUS / item["file"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    expected = Counter()
    image_only = []
    with pymupdf.open(path) as pdf:
        for index, page in enumerate(pdf, 1):
            for a in page.annots() or []:
                text = normalized(a.info.get("content", ""))
                if text and a.type[1] not in {"Link", "Widget", "Popup"}:
                    expected[(index, text)] += 1
            if not page.get_text().strip() and page.get_images():
                image_only.append(index)
        pages = len(pdf)
    parser.BLOBS = CORPUS
    document = SimpleNamespace(
        id=item["id"], blob=item["file"], meta={}, coverage=[], state="queued"
    )
    captured = Capture()
    parser.parse(captured, document)
    actual = Counter(
        (s.location["page"], normalized(s.text))
        for s in captured.spans
        if s.location.get("annotation_id") and normalized(s.text)
    )
    missed = expected - actual
    invalid = [
        s.location
        for s in captured.spans
        if "bbox" in s.location
        and not (
            len(s.location["bbox"]) == 4
            and all(0 <= x <= 1 for x in s.location["bbox"])
            and s.location["bbox"][0] < s.location["bbox"][2]
            and s.location["bbox"][1] < s.location["bbox"][3]
        )
    ]
    covered = {c["label"] for c in document.coverage}
    absent = [p for p in range(1, pages + 1) if f"Page {p}" not in covered]
    silent_scans = [
        p
        for p in image_only
        if not any(
            c["label"] == f"Page {p}" and c["state"] in {"scanned", "unsupported"}
            for c in document.coverage
        )
    ]
    return {
        "id": item["id"],
        "url": item["url"],
        "sha256": item["sha256"],
        "pages": pages,
        "native_annotation_texts": sum(expected.values()),
        "native_annotation_texts_found": sum((expected & actual).values()),
        "missed_native_annotation_texts": [
            {"page": k[0], "text": k[1], "count": n} for k, n in missed.items()
        ],
        "invalid_anchors": len(invalid),
        "pages_without_coverage": absent,
        "image_only_pages": image_only,
        "image_only_pages_without_warning": silent_scans,
        "spans": len(captured.spans),
        "passed": not missed and not invalid and not absent and not silent_scans,
    }


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--fetch", action="store_true")
    args.add_argument("--workers", type=int, default=2)
    args.add_argument("--limit", type=int)
    options = args.parse_args()
    CORPUS.mkdir(parents=True, exist_ok=True)
    items = json.loads(CATALOG.read_text())["items"][: options.limit]
    if options.fetch:
        for item in items:
            fetch(item)
    with ProcessPoolExecutor(max_workers=options.workers) as pool:
        results = list(pool.map(evaluate, items))
    total = sum(r["native_annotation_texts"] for r in results)
    found = sum(r["native_annotation_texts_found"] for r in results)
    report = {
        "packages": len(results),
        "physical_pages": sum(r["pages"] for r in results),
        "native_annotation_text_recall": found / total if total else None,
        "native_annotation_texts": total,
        "found_native_annotation_texts": found,
        "all_visual_comment_recall": None,
        "answer_accuracy": None,
        "pm_edits_before_send": None,
        "limitations": [
            "Native annotation text includes stamps and graphical labels, not only actionable comments.",
            "Visual handwriting/cloud recall requires independently reviewed gold labels; not measured here.",
            "No private customer documents, model answer evaluation, or PM corrections are represented.",
            "Public availability does not confer permission to redistribute original packages.",
        ],
        "results": results,
    }
    output = ROOT / "benchmarks/documents/latest-report.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2),
        flush=True,
    )
    raise SystemExit(0 if all(r["passed"] for r in results) else 1)


if __name__ == "__main__":
    main()
