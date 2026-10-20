"""Evaluate frozen real observations through evidence reconciliation, without a DB.

Index has development/evaluation/unscored entry lists. Each entry has id,
package_id, prediction_paths, optional comparison_path/table_report_paths,
read_table_pages={source_id:[page]}, and answer_key. All paths are index-relative.
Development keys fit clustered reliability; evaluation keys are opened only after
their graph and canonical values have been produced. Reader outputs remain local.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

from backend.equipment.evidence.calibration import fit_development
from backend.equipment.evidence.evaluation import (
    calibration_examples,
    merge_frozen_predictions,
    score_reconciliation,
    table_report_prediction,
)
from backend.equipment.evidence.pipeline import (
    CORRESPONDENCE_POLICY,
    build_evidence_graph,
    prepare_evidence_inputs,
)
from backend.equipment.evidence.reconcile import reconcile_graph
from backend.equipment.real_evaluation import score_predictions


def _read(path):
    return json.loads(Path(path).read_text())


def _path(base, value):
    value = Path(value)
    return value if value.is_absolute() else (base / value).resolve()


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _name(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)[:120]


def _packages(manifests):
    result = {}
    for manifest in manifests:
        for package in _read(manifest).get("packages", []):
            if package["id"] in result:
                raise ValueError("Duplicate package ID across evaluation manifests.")
            result[package["id"]] = package, manifest
    return result


def _load_case(entry, group, packages, base, output, *, correspondence_policy="none"):
    _validate_correspondence_policy(correspondence_policy)
    package, manifest = packages[entry["package_id"]]
    if group != "unscored" and package.get("kind") != "real_public":
        raise ValueError(
            "Only real public packages count in this accuracy report; synthetic/vendor examples remain unscored."
        )
    if group == "development" and package.get("split") in {
        "unseen_test",
        "heldout",
        "evaluation",
    }:
        raise ValueError("Heldout package cannot enter development calibration.")
    sources, contexts, pages = prepare_evidence_inputs(package, manifest)
    paths = [_path(base, p) for p in entry.get("prediction_paths", [])]
    predictions = [_read(p) for p in paths]
    hashes = {
        sid: source.sha256 if hasattr(source, "sha256") else source["sha256"]
        for sid, source in sources.items()
    }
    frozen = merge_frozen_predictions(
        predictions, package_id=package["id"], source_hashes=hashes
    )
    table_paths = [_path(base, p) for p in entry.get("table_report_paths", [])]
    reports = [_read(p) for p in table_paths]
    for sid, selected in entry.get("read_table_pages", {}).items():
        from backend.equipment.evidence.table_reader import read_table_evidence

        table_output = output / _name(entry["id"]) / "source-table" / _name(sid)
        table_output.mkdir(parents=True, exist_ok=True)
        try:
            report = read_table_evidence(
                sources[sid], selected, output_dir=table_output
            )
        except (ValueError, RuntimeError, OSError, TimeoutError) as error:
            report = {
                "version": "source_table_explicit_failure",
                "source_id": sid,
                "sha256": hashes[sid],
                "status": "failed",
                "rows": [],
                "claims": [],
                "pages": [
                    {
                        "page": p,
                        "status": "failed",
                        "error": f"{type(error).__name__}: {error}",
                    }
                    for p in selected
                ],
            }
        (table_output / "report.json").write_text(json.dumps(report, indent=2))
        reports.append(report)
    comparison = (
        _read(_path(base, entry["comparison_path"]))
        if entry.get("comparison_path")
        else None
    )
    observations = (
        _read(_path(base, entry["source_observations_path"]))
        if entry.get("source_observations_path")
        else {}
    )
    if entry.get("cad_report_paths"):
        observations["cad_reports"] = [
            *observations.get("cad_reports", []),
            *[_read(_path(base, p)) for p in entry["cad_report_paths"]],
        ]
    graph = build_evidence_graph(
        sources=sources,
        source_context=contexts,
        scope_id="real-evaluation/" + entry["id"],
        comparison=comparison,
        source_observations=observations,
        predictions=frozen,
        table_reports=reports,
        verified_identities=entry.get("verified_identities"),
        reconcile=False,
        correspondence_policy=None if correspondence_policy == "none" else correspondence_policy,
    )
    ancestry = (package.get("parent_original") or {}).get("sha256")
    if not ancestry:
        parents = {
            s.get("derivation", {}).get("parent_sha256") or s.get("sha256")
            for s in package.get("sources", [])
        }
        ancestry = hashlib.sha256(json.dumps(sorted(parents)).encode()).hexdigest()
    return {
        "entry": entry,
        "group": group,
        "package": package,
        "sources": sources,
        "contexts": contexts,
        "pages": pages,
        "graph": graph,
        "frozen": frozen,
        "table_reports": reports,
        "package_ancestry": ancestry,
        "input_hashes": {str(p): _digest(p) for p in paths},
        "table_report_input_hashes": {str(p): _digest(p) for p in table_paths},
        "correspondence_policy": correspondence_policy,
    }


def _score_case(case, graph, base):
    if not case["entry"].get("answer_key"):
        return None, None, []
    key = _read(_path(base, case["entry"]["answer_key"]))
    score = score_reconciliation(key, case["frozen"], graph)
    table_scores = []
    for report in case["table_reports"]:
        if report.get("status") == "failed":
            table_scores.append(
                {
                    "source_id": report.get("source_id"),
                    "reader_status": "failed",
                    "reader_pages": report.get("pages", []),
                    "raw_source_only": None,
                    "reconciled_source_only": None,
                    "reason": "Optional source-table reader did not process this page; its accuracy is unavailable, not zero. Frozen visual-reader values remain scored separately.",
                }
            )
            continue
        frozen_table = table_report_prediction(
            report,
            package_id=case["package"]["id"],
            source_hashes=case["frozen"]["source_hashes"],
        )
        table_scores.append(
            {
                "source_id": report.get("source_id"),
                "reader_status": report.get("status"),
                "reader_pages": report.get("pages", []),
                "raw_source_only": score_predictions(key, frozen_table),
                "reconciled_source_only": score_reconciliation(
                    key, frozen_table, graph
                ),
            }
        )
    return key, score, table_scores


def _validate_correspondence_policy(policy):
    if policy not in {"none", CORRESPONDENCE_POLICY}:
        raise ValueError("Unknown evidence correspondence policy")


def evaluate(index_path, manifests, output, *, freeze=None, correspondence_policy="none"):
    _validate_correspondence_policy(correspondence_policy)
    started = time.monotonic()
    index_path, output = Path(index_path).resolve(), Path(output).resolve()
    frozen_implementation = _read(freeze) if freeze else None
    if frozen_implementation:
        for file, digest in frozen_implementation.get("files", {}).items():
            if _digest(_path(Path.cwd(), file)) != digest:
                raise ValueError(f"Frozen evaluation implementation changed: {file}")
    index, base, packages = _read(index_path), index_path.parent, _packages(manifests)
    output.mkdir(parents=True, exist_ok=True)
    cases, examples = [], []
    # Fit only development outcomes. Frozen observations/graphs remain key-free.
    for entry in index.get("development", []):
        case = _load_case(entry, "development", packages, base, output, correspondence_policy=correspondence_policy)
        graph = reconcile_graph(case["graph"])
        _, score, _ = _score_case(case, graph, base)
        if score:
            examples.extend(
                calibration_examples(
                    score,
                    split="development",
                    package_ancestry=case["package_ancestry"],
                )
            )
        cases.append(case)
    calibration = fit_development(examples)
    (output / "development-calibration.json").write_text(
        json.dumps(calibration, indent=2)
    )
    for group in ("evaluation", "unscored"):
        cases.extend(
            _load_case(entry, group, packages, base, output, correspondence_policy=correspondence_policy)
            for entry in index.get(group, [])
        )
    final = {
        "schema_version": "rivet.reconciled-equipment-evaluation.v2" if correspondence_policy != "none" else "rivet.reconciled-equipment-evaluation.v1",
        "correspondence_policy": correspondence_policy,
        "correspondence_policy_description": (
            "Exact unique printed circuit within one source hash/page/ruled table; source-cell and word geometry required. Retained source-only OCR is projected only through proven local links; no key-based reader selection or cross-drawing identity."
            if correspondence_policy != "none" else "Disabled for compatibility; OCR and visual entities remain separate unless an explicit cited identity was supplied."
        ),
        "index_sha256": _digest(index_path),
        "implementation_freeze": frozen_implementation,
        "calibration": calibration,
        "cases": [],
        "evaluation_policy": "Previously frozen heldout pages are regression pages now; source-specific results are not a new independent customer validation. No synthetic/vendor data counts as real accuracy. Keys never create a claim or choose a reader.",
    }
    synthesis_index = {"models": [], "comparisons": []}
    case_paths = {}
    for case in cases:
        entry, package = case["entry"], case["package"]
        directory = output / _name(entry["id"])
        directory.mkdir(parents=True, exist_ok=True)
        graph = reconcile_graph(case["graph"], calibration=calibration)
        path = directory / "reconciled-graph.json"
        path.write_text(json.dumps(graph, indent=2))
        (directory / "frozen-predictions.json").write_text(
            json.dumps(case["frozen"], indent=2)
        )
        # The heldout answer key is opened only after the model snapshot exists.
        _, score, table_scores = _score_case(case, graph, base)
        if score:
            (directory / "score.json").write_text(json.dumps(score, indent=2))
        if table_scores:
            (directory / "source-table-scores.json").write_text(
                json.dumps(table_scores, indent=2)
            )
        summary = {
            "id": entry["id"],
            "package_id": package["id"],
            "split": case["group"],
            "package_ancestry": case["package_ancestry"],
            "kind": package.get("kind"),
            "input_hashes": case["input_hashes"],
            "table_report_input_hashes": case["table_report_input_hashes"],
            "correspondence_policy": correspondence_policy,
            "source_local_correspondence": graph.get("metadata", {}).get("source_local_correspondence"),
            "model_path": str(path),
            "model_sha256": _digest(path),
            "labelled_cell_policy": score["labelled_cell_policy"] if score else None,
            "all_resolution_policy": score["all_resolution_policy"]
            if score
            else graph.get("reconciliation"),
            "score_path": str(directory / "score.json") if score else None,
            "table_score_path": str(directory / "source-table-scores.json")
            if table_scores
            else None,
        }
        summary["reader_failures"] = [
            {"source_id": report.get("source_id"), "pages": report.get("pages", [])}
            for report in case["table_reports"]
            if report.get("status") == "failed"
        ]
        if score:
            summary["before_bom"] = score["before"]["extraction"]["bom_rows"]
            summary["candidate_bom"] = score["candidate"]["extraction"]["bom_rows"]
            summary["accepted_bom"] = score["accepted_only"]["extraction"]["bom_rows"]
        final["cases"].append(summary)
        case_paths[entry["id"]] = str(path)
        synthesis_index["models"].append(
            {
                "case_id": entry["id"],
                "package_id": package["id"],
                "model_path": str(path),
                "source_ids": entry.get(
                    "synthesis_source_ids", list(case["frozen"]["source_hashes"])
                ),
                "pages": entry.get("synthesis_pages", {}),
            }
        )
    for comparison in index.get("comparisons", []):
        item = dict(comparison)
        if item.get("before_case_id"):
            item["before_model_path"] = case_paths[item.pop("before_case_id")]
        if item.get("after_case_id"):
            item["after_model_path"] = case_paths[item.pop("after_case_id")]
        if item.get("answer_key"):
            item["answer_key"] = str(_path(base, item["answer_key"]))
        synthesis_index["comparisons"].append(item)
    final["elapsed_seconds"] = round(time.monotonic() - started, 3)
    final["status"] = (
        "completed_with_explicit_reader_errors"
        if any(c["reader_failures"] for c in final["cases"])
        else "completed"
    )
    (output / "evaluation.json").write_text(json.dumps(final, indent=2))
    (output / "synthesis-model-index.json").write_text(
        json.dumps(synthesis_index, indent=2)
    )
    return final


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--correspondence-policy", choices=("none", CORRESPONDENCE_POLICY), default="none",
                        help="Versioned source-only row linkage; default none preserves prior evaluation behavior.")
    args = parser.parse_args()
    result = evaluate(
        args.index,
        [p.resolve() for p in args.manifest],
        args.output,
        freeze=args.freeze,
        correspondence_policy=args.correspondence_policy,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "correspondence_policy": result["correspondence_policy"],
                "cases": len(result["cases"]),
                "elapsed_seconds": result["elapsed_seconds"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
