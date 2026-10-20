"""Measure a native report importer against explicit source-record references.

Vendor samples are always separated from manufacturer/public-PDF evaluation.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from backend.equipment.cad_reports import read_cad_report
from backend.equipment.schemas import SourceInput


def identity(item):
    return (
        item.get("wire_number"),
        item.get("from", {}).get("device_tag"),
        item.get("from", {}).get("pin"),
        item.get("to", {}).get("device_tag"),
        item.get("to", {}).get("pin"),
    )


def score(expected, found):
    a, b = Counter(expected), Counter(found)
    tp = sum((a & b).values())
    return {
        "expected": sum(a.values()),
        "imported": sum(b.values()),
        "correct": tp,
        "missed": sum((a - b).values()),
        "false_records": sum((b - a).values()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("benchmarks/real-equipment-v2/cad-sources.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    base = args.manifest.resolve().parent
    key = json.loads((base / manifest["answer_key"]).read_text())
    reports = []
    for spec in manifest["sources"]:
        source = SourceInput(
            **{k: spec[k] for k in ("id", "name", "role", "revision", "sha256")},
            path=str((base / spec["path"]).resolve()),
        )
        if source.id != key["source_id"] or source.sha256 != key["source_sha256"]:
            raise ValueError("CAD reference does not match source identity/hash.")
        started = time.monotonic()
        report, facts = read_cad_report(source)
        components = score(
            [(x["tag"], x["part_number"]) for x in key["components"]],
            [
                (x["tag"], x["properties"].get("part_number"))
                for x in report["components"]
            ],
        )
        connections = score(
            [identity(x) for x in key["connections"]],
            [identity(x) for x in report["connections"]],
        )
        reports.append(
            {
                "source_id": source.id,
                "sha256": source.sha256,
                "dataset_kind": spec["dataset_kind"],
                "manufacturer_accuracy": False,
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "extraction": {
                    "components_and_catalog_values": components,
                    "connection_records_and_literal_endpoints": connections,
                },
                "checking": {
                    "physical_geometry_and_engineering_correctness": None,
                    "reason": "Source report declarations are not independent physical geometry truth.",
                },
                "source_limitations": {
                    "unknown_endpoint_rows": report["imported_counts"][
                        "connections_with_unknown_endpoints"
                    ],
                    "expected_unknown_endpoint_rows": key[
                        "expected_unknown_endpoint_rows"
                    ],
                },
                "report": report,
                "facts": facts,
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"schema_version": "rivet.cad-report-evaluation.v1", "reports": reports},
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            [
                {
                    k: r[k]
                    for k in (
                        "source_id",
                        "dataset_kind",
                        "elapsed_seconds",
                        "extraction",
                        "source_limitations",
                    )
                }
                for r in reports
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
