"""Run the PDF adapter against external truth without model calls or a database."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pymupdf

from backend.equipment.pdf_evaluation import evaluate_pdf_graph
from backend.equipment.pdf_model import inspect_pdf


def evaluate(root):
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text())
    independent = json.loads(
        (root / manifest["independent_fixture_manifest"]).read_text()
    )
    output, page_copies = root / "evaluations", root / "selected-pages"
    output.mkdir(exist_ok=True)
    page_copies.mkdir(exist_ok=True)
    cases = [
        {
            **item,
            "path": root / "independent" / item["path"],
            "kind": "independent_fixture",
        }
        for item in independent["fixtures"]
    ]
    for source in manifest["sources"]:
        page_number = source["ground_truth"]["annotation_page"]
        path = page_copies / f"{source['id']}-page-{page_number}.pdf"
        original = root / source["path"]
        if hashlib.sha256(original.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("An original public source changed after annotation.")
        with pymupdf.open(original) as pdf, pymupdf.open() as selected:
            selected.insert_pdf(pdf, from_page=page_number - 1, to_page=page_number - 1)
            selected.save(path)
        cases.append(
            {
                "id": source["id"],
                "path": path,
                "kind": "official_public_subset",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "origin": {
                    "url": source["url"],
                    "sha256": source["sha256"],
                    "page": page_number,
                },
                "ground_truth": {**source["ground_truth"], "annotation_page": 1},
            }
        )
    results = []
    for case in cases:
        source = {
            "id": case["id"],
            "name": case["path"].name,
            "path": str(case["path"].resolve()),
            "role": "drawing",
            "revision": "PUBLIC-REFERENCE"
            if case["kind"] == "official_public_subset"
            else "C",
            "sha256": case["sha256"],
            "lifecycle": "draft",
        }
        try:
            report = inspect_pdf(source)
        except (ValueError, RuntimeError) as exc:
            report = {
                "source": source,
                "devices": [],
                "connections": [],
                "coverage": {"status": "unknown"},
                "issues": [{"code": "inspection_blocked", "detail": str(exc)}],
            }
        (output / (case["id"] + ".report.json")).write_text(
            json.dumps(report, indent=2) + "\n"
        )
        metrics = evaluate_pdf_graph(report, case["ground_truth"])
        result = {
            "id": case["id"],
            "kind": case["kind"],
            "source_sha256": source["sha256"],
            "origin": case.get("origin"),
            "metrics": metrics,
        }
        results.append(result)
        print(
            json.dumps(
                {
                    "case": case["id"],
                    "tag_recall": metrics["tags"]["recall"],
                    "net_recall": metrics["connections"]["recall"],
                    "net_precision": metrics["connections"]["precision"],
                    "unknown_preserved": metrics["unknown_preserved"],
                }
            ),
            flush=True,
        )
    summary = {
        "schema_version": "rivet.pdf.graph.eval.v1",
        "cases": results,
        "limits": "Independent circuits have complete authored intent; real public sheets have only explicit hand-annotated subsets. No engineering approval or full-sheet accuracy is inferred.",
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="output/public-drawing-corpus")
    evaluate(parser.parse_args().root)
