"""Run real pinned packages against separate reviewed references, without a DB.

Example:
  python scripts/evaluate_real_packages.py --manifest benchmarks/real-packages/packages.json \
    --package talco-4212 --predictions talco-4212=.data/vision/predictions.json \
    --output-dir .data/real-evaluations/run-01

Original PDFs are never copied into the report directory or committed. External
vision predictions must already exist and cite exact source hashes. This script
makes no provider requests, downloads or engineering-approval claims.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.equipment.real_evaluation import (
    MANIFEST_SCHEMA,
    REPORT_SCHEMA,
    EvaluationError,
    evaluate_package,
    summarize_reports,
)


def _worker(pipe, package, manifest, predictions, output, vector):
    try:
        report = evaluate_package(
            package, manifest, predictions, output, include_vector=vector
        )
        pipe.send({"report_path": report["artifact_paths"]["report"]})
    except Exception as exc:  # noqa: BLE001 - preserve a failed run instead of clean empty metrics.
        pipe.send({"error_type": type(exc).__name__, "message": str(exc)[:2000]})
    finally:
        pipe.close()


def _run(package, manifest, predictions, output, *, vector, timeout):
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(
        target=_worker,
        args=(writer, package, str(manifest), predictions, str(output), vector),
    )
    started = time.perf_counter()
    process.start()
    writer.close()
    if reader.poll(timeout):
        try:
            result = reader.recv()
        except EOFError:
            result = {
                "error_type": "WorkerExited",
                "message": "The evaluation worker exited without a result.",
            }
    else:
        process.terminate()
        result = {
            "error_type": "PackageTimeout",
            "message": f"The package exceeded its {timeout:g}-second processing bound. No accuracy result was inferred.",
        }
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)
    reader.close()
    if result.get("report_path"):
        return json.loads(Path(result["report_path"]).read_text())
    return {
        "schema_version": REPORT_SCHEMA,
        "package_id": package["id"],
        "kind": package.get("kind"),
        "status": "evaluation_failed",
        "scores": None,
        "predictions": None,
        "coverage": {"physical_pages": None},
        "errors": [{"stage": "package_run", **result}],
        "timings": {"total_seconds": time.perf_counter() - started},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / ".data/real-evaluations/latest"
    )
    parser.add_argument("--package", action="append", default=[])
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--predictions", action="append", default=[], metavar="PACKAGE=JSON_PATH"
    )
    parser.add_argument("--timeout-seconds", type=float, default=600)
    parser.add_argument(
        "--no-vector",
        action="store_true",
        help="Skip vector geometry inspection; the report keeps that measurement unavailable.",
    )
    args = parser.parse_args()
    manifest = args.manifest.resolve()
    raw = json.loads(manifest.read_text())
    if raw.get("schema_version") != MANIFEST_SCHEMA or not isinstance(
        raw.get("packages"), list
    ):
        raise EvaluationError("Use an explicit rivet.real-packages.v1 manifest.")
    if args.timeout_seconds <= 0 or (args.limit is not None and args.limit < 1):
        raise EvaluationError("Use positive processing limits.")
    predicted = {}
    for item in args.predictions:
        package_id, separator, path = item.partition("=")
        if not separator or not package_id or not Path(path).is_file():
            raise EvaluationError(
                "Predictions must name PACKAGE=an_existing_JSON_path."
            )
        predicted[package_id] = str(Path(path).resolve())
    packages = [
        package
        for package in raw["packages"]
        if not args.package or package["id"] in args.package
    ]
    if args.limit:
        packages = packages[: args.limit]
    if not packages:
        raise EvaluationError("No requested packages matched the manifest.")
    if len({package["id"] for package in packages}) != len(packages):
        raise EvaluationError("Package IDs must be unique.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    reports = []
    for package in packages:
        print(json.dumps({"package": package["id"], "status": "running"}), flush=True)
        report = _run(
            package,
            manifest,
            predicted.get(package["id"]),
            args.output_dir.resolve(),
            vector=not args.no_vector,
            timeout=args.timeout_seconds,
        )
        reports.append(report)
        print(
            json.dumps(
                {
                    "package": package["id"],
                    "status": report["status"],
                    "physical_pages": report.get("coverage", {}).get("physical_pages"),
                    "seconds": round(report["timings"]["total_seconds"], 3),
                    "scores": report.get("scores", {}).get("extraction")
                    if report.get("scores")
                    else None,
                }
            ),
            flush=True,
        )
    summary = {
        "schema_version": REPORT_SCHEMA,
        "manifest": str(manifest),
        "groups": summarize_reports(reports),
        "packages": [
            {
                "id": report["package_id"],
                "status": report["status"],
                "artifact_paths": report.get("artifact_paths"),
                "errors": report.get("errors", []),
            }
            for report in reports
        ],
        "engineering_accuracy_validated": False,
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {"summary": str(summary_path.resolve()), "groups": summary["groups"]}
        ),
        flush=True,
    )
    return (
        1 if any(report["status"] == "evaluation_failed" for report in reports) else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
