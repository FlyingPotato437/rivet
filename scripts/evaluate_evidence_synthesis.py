"""DB-free reconstruction and graph-revision evaluation of saved model snapshots.

Input index example (paths resolve relative to the index):
{
  "models": [{"package_id": "heldout-kansas-main-schedule", "model_path": "model.json"}],
  "comparisons": [{"id": "kansas", "package_id": "kansas-control-revision-development-v2",
    "before_model_path": "before.json", "after_model_path": "after.json",
    "before_side": "baseline", "after_side": "target", "answer_key": "key.json"}]
}

Run saved graphs only: this script never asks an extractor/model to reread values,
calls a database, creates Outlook drafts, or turns source-only row labels into
equipment recall. Synthetic tests are not read by this command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import pymupdf

from backend.equipment.evidence.graph_diff import compare_reconciled_models
from backend.equipment.evidence.synthesis import synthesize_and_compare
from backend.equipment.real_evaluation import score_predictions


def _json(path):
    return json.loads(Path(path).read_text())


def _resolve(base, path):
    path = Path(path)
    return path if path.is_absolute() else (base / path).resolve()


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _freeze(path):
    record = _json(path)
    for file, digest in record.get("files", {}).items():
        candidate = _resolve(Path.cwd(), file)
        if not candidate.is_file() or _hash(candidate) != digest:
            raise ValueError(f"Frozen implementation changed: {file}")
    return record


def _sources(manifests):
    packages = {}
    for manifest in manifests:
        for package in _json(manifest).get("packages", []):
            if package["id"] in packages:
                raise ValueError(f"Duplicate evaluation package: {package['id']}")
            resolved = []
            for source in package.get("sources", []):
                path = _resolve(manifest.parent, source["path"])
                if not path.is_file() or _hash(path) != source["sha256"]:
                    raise ValueError(
                        f"Source hash mismatch: {package['id']}/{source['id']}"
                    )
                resolved.append({**source, "path": str(path)})
            packages[package["id"]] = {
                **package,
                "sources": resolved,
                "manifest_path": str(manifest),
            }
    return packages


def _model(path):
    payload = _json(path)
    graph = payload.get("graph", payload)
    if graph.get("schema_version") != "rivet.evidence-graph.v1":
        raise ValueError("Evaluation requires a saved reconciled evidence graph.")
    if "resolutions" not in graph:
        raise ValueError(
            "Graph has no reconciled resolutions; do not score raw extraction as reconciliation."
        )
    return graph


def _known_pages(graph, source, selected=None):
    if selected:
        return sorted(set(selected))
    values = {
        c.get("source", {}).get("page")
        for c in graph.get("claims", [])
        if c.get("source", {}).get("source_id") == source["id"]
    }
    values |= {
        r.get("page")
        for n in graph.get("nodes", [])
        for r in n.get("source_refs", [])
        if r.get("source_id") == source["id"]
    }
    values.discard(None)
    if values:
        return sorted(values)
    with pymupdf.open(source["path"]) as document:
        return list(range(1, len(document) + 1))


def _revision_predictions(diff, package, answer_key, *, candidates=False):
    """Translate graph assertion types only; visual-reader outputs never enter."""
    result = []
    for change in diff["candidate_changes"] if candidates else diff["changes"]:
        before_refs, after_refs = (
            change.get("before_sources", []),
            change.get("after_sources", []),
        )
        target = next(
            (r for r in after_refs if r.get("source_id") and r.get("page")), None
        )
        if not target:
            targets = [s for s in package["sources"] if s.get("side") == "target"]
            target = (
                {"source_id": targets[0]["id"], "page": None}
                if len(targets) == 1
                else {}
            )
        item = {
            "kind": {
                "connection_added": "drawing_connection_added",
                "connection_removed": "drawing_connection_removed",
                "rating_changed": "drawing_rating_changed",
                "device_added": "drawing_device_added",
                "device_removed": "drawing_device_removed",
            }.get(change["kind"], "graph_attribute_changed"),
            "source_id": target.get("source_id"),
            "page": target.get("page"),
            "before": change.get("before"),
            "after": change.get("after"),
            "graph_change": change,
            "engineering_verified": False,
        }
        if change["kind"] in {"connection_added", "device_added"}:
            item.update(before=False, after=True)
        elif change["kind"] in {"connection_removed", "device_removed"}:
            item.update(before=True, after=False)
        if target.get("bbox"):
            item["target_bbox"] = target["bbox"]
        original = next((r for r in before_refs if r.get("bbox")), None)
        if original:
            item["baseline_bbox"] = original["bbox"]
        result.append(item)
    hashes = {s["id"]: s["sha256"] for s in package["sources"]}
    if any(
        hashes.get(sid) != digest
        for sid, digest in answer_key.get("source_hashes", {}).items()
    ):
        raise ValueError("Revision key does not bind the compared package sources.")
    return {
        "schema_version": "rivet.real-predictions.v1",
        "package_id": package["id"],
        "source_hashes": hashes,
        "comments": [],
        "devices": [],
        "bom_rows": [],
        "issues": [],
        "revision_checks": result,
    }


def evaluate(
    index_path,
    manifests,
    output,
    *,
    region_references=None,
    freeze=None,
    max_pages=50,
    max_dimension=1600,
    ocr=True,
):
    started = time.monotonic()
    index_path, output = Path(index_path).resolve(), Path(output).resolve()
    frozen = _freeze(freeze) if freeze else None
    packages, index = _sources(manifests), _json(index_path)
    references = _json(region_references) if region_references else None
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "rivet.evidence-synthesis-evaluation.v1",
        "input_index_sha256": _hash(index_path),
        "freeze": frozen,
        "models": [],
        "comparisons": [],
        "errors": [],
        "metric_policy": "Source-region localization, spatial ink coverage, missing-model recall and revision check precision/recall remain separate. No synthetic accuracy or former visual-change results are substituted.",
    }
    page_count = 0
    for item in index.get("models", []):
        package = packages[item["package_id"]]
        path = _resolve(index_path.parent, item["model_path"])
        graph = _model(path)
        summary = {
            "case_id": item.get("case_id", package["id"]),
            "package_id": package["id"],
            "split": package.get("split"),
            "kind": package.get("kind"),
            "model_sha256": _hash(path),
            "pages": [],
            "errors": [],
        }
        for source in package["sources"]:
            if Path(source["path"]).suffix.lower() not in {
                ".pdf",
                ".png",
                ".jpg",
                ".jpeg",
            }:
                continue
            if item.get("source_ids") and source["id"] not in item["source_ids"]:
                continue
            selected = (item.get("pages") or {}).get(source["id"])
            pages = _known_pages(graph, source, selected)
            for page in pages:
                if page_count >= max_pages:
                    summary["errors"].append(
                        {
                            "source_id": source["id"],
                            "page": page,
                            "error": "global_page_budget_exhausted",
                        }
                    )
                    continue
                page_count += 1
                case_name = re.sub(
                    r"[^A-Za-z0-9_.-]", "_", item.get("case_id", package["id"])
                )
                source_name = re.sub(r"[^A-Za-z0-9_.-]", "_", source["id"])
                directory = output / case_name / source_name / f"page-{page}"
                try:
                    matching_references = (
                        references
                        if references
                        and any(
                            s.get("id") == source["id"] and s.get("page") == page
                            for s in references.get("sources", [])
                        )
                        else None
                    )
                    result = synthesize_and_compare(
                        graph,
                        source["path"],
                        source["id"],
                        page,
                        directory,
                        sha256=source["sha256"],
                        max_dimension=max_dimension,
                        ocr=ocr,
                        region_references=matching_references,
                    )
                    summary["pages"].append(
                        {
                            "source_id": source["id"],
                            "page": page,
                            "report_path": str(directory / "synthesis-report.json"),
                            **{
                                key: result[key]
                                for key in (
                                    "status",
                                    "spatial_ink_coverage",
                                    "claim_anchor_diagnostics",
                                    "independent_annotated_region_localization",
                                    "independent_unexplained_region_recall",
                                    "equipment_extraction_recall",
                                    "elapsed_seconds",
                                )
                            },
                        }
                    )
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    IndexError,
                    RuntimeError,
                ) as error:
                    summary["errors"].append(
                        {
                            "source_id": source["id"],
                            "page": page,
                            "error": f"{type(error).__name__}: {error}",
                        }
                    )
        report["models"].append(summary)
    for item in index.get("comparisons", []):
        package = packages[item["package_id"]]
        before_path = _resolve(index_path.parent, item["before_model_path"])
        after_path = _resolve(index_path.parent, item["after_model_path"])
        before, after = _model(before_path), _model(after_path)
        directory = output / "graph-diff" / item["id"]
        try:
            diff = compare_reconciled_models(
                before,
                after,
                matches=item.get("matches"),
                scope_matches=item.get("scope_matches"),
                before_side=item.get("before_side"),
                after_side=item.get("after_side"),
                output_dir=directory,
                sources={s["id"]: s for s in package["sources"]},
            )
            (directory / "graph-diff.json").write_text(json.dumps(diff, indent=2))
            summary = {
                "id": item["id"],
                "package_id": package["id"],
                "split": package.get("split"),
                "before_model_sha256": _hash(before_path),
                "after_model_sha256": _hash(after_path),
                "changes": len(diff["changes"]),
                "candidate_changes": len(diff.get("candidate_changes", [])),
                "unknowns": len(diff["unknowns"]),
                "identity_matches": len(diff["identity_matches"]),
                "diff_path": str(directory / "graph-diff.json"),
            }
            if item.get("answer_key"):
                key = _json(_resolve(index_path.parent, item["answer_key"]))
                predictions = _revision_predictions(diff, package, key)
                score = score_predictions(key, predictions)
                (directory / "revision-predictions.json").write_text(
                    json.dumps(predictions, indent=2)
                )
                (directory / "revision-score.json").write_text(
                    json.dumps(score, indent=2)
                )
                summary["revision_score"] = score.get("checks", {}).get(
                    "revision_checks"
                )
                candidate_predictions = _revision_predictions(
                    diff, package, key, candidates=True
                )
                candidate_score = score_predictions(key, candidate_predictions)
                (directory / "candidate-revision-predictions.json").write_text(
                    json.dumps(candidate_predictions, indent=2)
                )
                (directory / "candidate-revision-score.json").write_text(
                    json.dumps(candidate_score, indent=2)
                )
                summary["candidate_revision_score"] = candidate_score.get(
                    "checks", {}
                ).get("revision_checks")
                summary["reference_review"] = key.get("review")
            else:
                summary["revision_score"] = None
                summary["reason"] = (
                    "No independent revision key; change counts are not precision/recall."
                )
            report["comparisons"].append(summary)
        except (OSError, ValueError, KeyError, IndexError, RuntimeError) as error:
            report["errors"].append(
                {"comparison": item["id"], "error": f"{type(error).__name__}: {error}"}
            )
    report["pages_processed"] = sum(len(m["pages"]) for m in report["models"])
    report["page_errors"] = sum(len(m["errors"]) for m in report["models"])
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["status"] = (
        "completed_with_explicit_errors"
        if report["errors"] or report["page_errors"]
        else "completed"
    )
    (output / "evaluation.json").write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-index", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--region-references", type=Path)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--max-dimension", type=int, default=1600)
    parser.add_argument("--no-ocr", action="store_true")
    args = parser.parse_args()
    report = evaluate(
        args.models_index,
        [m.resolve() for m in args.manifest],
        args.output,
        region_references=args.region_references,
        freeze=args.freeze,
        max_pages=args.max_pages,
        max_dimension=args.max_dimension,
        ocr=not args.no_ocr,
    )
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "pages_processed",
                    "page_errors",
                    "elapsed_seconds",
                )
            },
            indent=2,
        )
    )
    if report["errors"] or report["page_errors"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
