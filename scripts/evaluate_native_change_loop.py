"""Local, no-model native change/recheck evaluation from a private manifest.

Private originals and labels are not embedded in source control. A key measures
only its named cells; unknown/unlabelled values do not become engineering facts.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from backend.engineering.comment_redesign import build_package, file_hash


def evaluate(manifest_path, output, key_path=None):
    manifest_path, output = Path(manifest_path), Path(output)
    manifest = json.loads(manifest_path.read_text())
    sources = []
    for original in manifest["sources"]:
        path = Path(original["path"])
        if not path.is_absolute():
            path = manifest_path.parent / path
        sources.append({**original, "path": str(path.resolve())})
    cad = next(s for s in sources if s["id"] == manifest["native_source_id"])
    # Validate BEFORE the builder writes anything, including the key binding.
    if any(file_hash(s["path"]) != s["sha256"] for s in sources):
        raise ValueError("A manifest source hash does not match")
    key = json.loads(Path(key_path).read_text()) if key_path else None
    if key and key["source_sha256"] != cad["sha256"]:
        raise ValueError("Answer key belongs to a different native original")
    started = time.monotonic()
    report = build_package([s for s in sources if s["id"] != cad["id"]], cad, output)
    evidence = json.loads((output / "Equipment-evidence.json").read_text())
    scores = {
        "status": "not_scored",
        "reason": "No independent labelled answer key supplied",
    }
    if key:
        failures = []
        matched = 0
        populated_matched = 0
        tables = evidence["schedules"]
        for expected in key["cells"]:
            table = next(
                (
                    t
                    for t in tables
                    if t["columns"][0]["header"]["evidence"][0]["locator"]
                    == expected["table_header_locator"]
                ),
                None,
            )
            row = (
                next((r for r in table["rows"] if r["row"] == expected["row"]), None)
                if table
                else None
            )
            cell = row["cells"].get(expected["field"]) if row else None
            if (
                cell
                and cell["value"] == expected["value"]
                and cell["status"] == expected["status"]
            ):
                matched += 1
                populated_matched += expected["value"] is not None
            else:
                failures.append(
                    {
                        "expected": expected,
                        "actual": {k: cell[k] for k in ("value", "status")}
                        if cell
                        else None,
                    }
                )
        scores = {
            "status": "scored",
            "split": key["split"],
            "label_method": key["label_method"],
            "cells_matched": matched,
            "cells_labelled": len(key["cells"]),
            "populated_cells_matched": populated_matched,
            "populated_cells_labelled": sum(
                c["value"] is not None for c in key["cells"]
            ),
            "blank_cells_preserved": matched - populated_matched,
            "mismatches": failures,
            "unseen_accuracy": None,
        }
    result = {
        "dataset": manifest.get("dataset", "private manufacturer package"),
        "seconds": round(time.monotonic() - started, 2),
        "originals_preserved": all(
            file_hash(s["path"]) == s["sha256"] for s in sources
        ),
        "extraction": {
            **evidence["counts"],
            "score": scores,
            "coverage_complete": evidence["drawing_coverage_complete"],
            "omissions": evidence["omissions"],
        },
        "checking": {
            "native_text_edits": len(report["changes"]),
            "drawing_arithmetic_edits": sum(
                c["kind"] == "cad_calculation" for c in report["changes"]
            ),
            "saved_calculation_check": report["saved_calculation_check"],
            "saved_check": report["saved_drawing_check"],
            "comments": report["totals"],
            "fully_addressed_comments": report["totals"]["changed"],
            "physical_or_electrical_changes_verified": 0,
            "production_bom_verified": False,
            "real_next_revision_available": manifest.get(
                "real_next_revision_available", False
            ),
        },
        "engineering_approved": False,
        "limitations": report["remaining_issues"],
        "source_hashes": {s["id"]: s["sha256"] for s in sources},
    }
    (output / "evaluation.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False)
    )
    (output / "evaluation.json").chmod(0o600)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--answer-key", type=Path)
    args = parser.parse_args()
    result = evaluate(args.manifest, args.output, args.answer_key)
    print(
        json.dumps(
            {
                "seconds": result["seconds"],
                "originals_preserved": result["originals_preserved"],
                "extraction": {
                    k: v for k, v in result["extraction"].items() if k != "omissions"
                },
                "edits": result["checking"]["native_text_edits"],
                "comments": result["checking"]["comments"],
                "independent_check_passed": result["checking"]["saved_check"]["passed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
