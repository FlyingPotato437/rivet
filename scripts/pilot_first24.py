"""One-command pilot intake -> real extraction/model/checks -> honest findings.

Run: uv run python -m scripts.pilot_first24 --manifest /private/package.json
     --package PACKAGE_ID --output-dir .data/pilot-runs/first-pass

Add --vision only under the pilot's agreed model-processing policy. Without an
independently reviewed answer key the report explicitly cannot claim accuracy.
No customer mail, CAD correction, approval, or release is performed by this run.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from backend.equipment.real_evaluation import MANIFEST_SCHEMA
from backend.ingestion.limits import DWG_REQUEST, upload_max_bytes, validate_pdf_upload
from scripts.evaluate_real_packages import _run

PRIVATE_DATA_ROOT = (Path(__file__).resolve().parents[1] / ".data").resolve()


def validate_remote_processing(package, *, requested):
    """A redaction receipt alone does not authorize a private API upload.

    This checks the operator's recorded customer authorization, not the legal
    sufficiency of that authorization. Bind it to this exact source set so an
    old approval cannot silently extend to later files. Local reads and cached
    scoring do not require new remote-processing permission.
    """
    if not requested or package.get("kind") != "real_private":
        return
    receipt = package.get("model_processing")
    source_ids = [source["id"] for source in package["sources"]]
    valid = isinstance(receipt, dict) and len(source_ids) == len(set(source_ids))
    if valid:
        valid = (
            receipt.get("external_ai_allowed") is True
            and isinstance(receipt.get("authorization_reference"), str)
            and bool(receipt["authorization_reference"].strip())
            and isinstance(receipt.get("recorded_by"), str)
            and bool(receipt["recorded_by"].strip())
            and receipt.get("source_hashes")
            == {source["id"]: source["sha256"] for source in package["sources"]}
        )
        try:
            recorded = datetime.fromisoformat(receipt.get("recorded_at", ""))
            valid = (
                valid and recorded.tzinfo is not None and recorded.utcoffset() is not None
            )
        except (ValueError, TypeError):
            valid = False
    if not valid:
        raise ValueError(
            "Private package external AI processing is not authorized for these sources. "
            "Record the customer's authorization reference, operator, timestamp and exact "
            "source hashes in model_processing before using --vision or --drawing-vision. "
            "Redaction confirmation alone is insufficient. No package was sent to a model."
        )


def prepare_output(path, *, private):
    destination = Path(path).resolve()
    if private:
        if not destination.is_relative_to(PRIVATE_DATA_ROOT):
            raise ValueError(
                "Keep private package reports under the ignored .data directory."
            )
        try:
            # Fresh owner-only storage also prevents old child-directory
            # symlinks from redirecting vision transcripts outside .data.
            destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        except FileExistsError as exc:
            raise ValueError(
                "Use a fresh output directory for each private pilot run."
            ) from exc
    else:
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    return destination


def output_stream(path, *, private, newline=None):
    if not private:
        return path.open("w", newline=newline)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, "w", newline=newline)


def write_output(path, text, *, private):
    with output_stream(path, private=private) as stream:
        stream.write(text)


def metric_text(metric):
    if metric is None:
        return "Not measured: independent reference labels are missing."
    return f"{metric['tp']} matched; {metric['fn']} missed; {metric['fp'] if metric['fp'] is not None else 'unscored'} false alarms."


def findings(report, package, elapsed):
    coverage = report.get("coverage", {})
    scores = report.get("scores") or {}
    extraction = scores.get("extraction", {})
    checks = scores.get("checks", {})
    rows = extraction.get("bom_rows") or {}
    cells = rows.get("field_values")
    prep = report.get("preparation", {})
    external_pages = coverage.get("external_pages", [])
    equipment_pages = [
        p for p in external_pages if p.get("reading_kind") == "equipment"
    ]
    comment_pages = [p for p in external_pages if p.get("reading_kind") == "comments"]
    deferred = [
        p for p in external_pages if p.get("status") not in {"processed", "read"}
    ]
    marks = (report.get("predictions") or {}).get("comments", [])
    uncertain = sum(
        mark.get("unreadable")
        or mark.get("actionable") is None
        or mark.get("confidence") == "low"
        for mark in marks
    )
    lines = [
        f"# First-pass findings: {package['id']}",
        "",
        f"Completed {datetime.now(UTC).isoformat()}; elapsed {elapsed:.1f} seconds.",
        "",
        "## Coverage",
        "",
        f"- Original pages: {coverage.get('physical_pages', 'unavailable')}.",
        f"- Deterministic equipment-reader pages attempted: {coverage.get('equipment_pages_attempted')}; readable: {coverage.get('equipment_pages_processed')}; unreadable: {coverage.get('equipment_pages_unreadable')}.",
        f"- Page-image equipment reads supplied: {len(equipment_pages)}; comment reads supplied: {len(comment_pages)}. These are separate passes over physical pages, not additional pages.",
        f"- Incomplete/deferred page-read entries: {len(deferred)}. Read status alone does not establish complete equipment extraction.",
        f"- Structured CAD sources imported: {prep.get('cad_sources', 0)}; revision source pairs attempted: {prep.get('revision_pairs', 0)}; deferred/unmatched revision sheets: {prep.get('revision_sheets_needing_review', 'not recorded')}.",
        "",
        "## Extraction, measured separately",
        "",
        f"- Reviewer comments: {metric_text(extraction.get('actionable_comments'))}",
        f"- Device tags: {metric_text(extraction.get('device_tags'))}",
        f"- BOM/schedule rows: {metric_text(extraction.get('bom_rows'))}",
        f"- Labelled BOM values: {cells['correct']} correct, {cells['wrong']} wrong, {cells['missing']} missing."
        if cells
        else "- BOM value accuracy: not measured; independent cell labels are missing.",
        f"- Retained uncertain/unreadable marks: {uncertain}. These are not silently counted as answered comments.",
        "",
        f"- Observed equipment model devices: {(report.get('equipment_model') or {}).get('observed_device_count', 'unavailable')}. This is extracted coverage, not complete inventory.",
        "- Image-read equipment and declared CAD connections are retained in source-observations.json with source locations and confidence. They remain human-unverified; zero supported vector devices does not erase those observations.",
        "",
        "## Checking, measured separately",
        "",
        f"- Issues caught/missed/false alarms: {metric_text(checks.get('issues'))}",
        f"- Revision changes: {metric_text(checks.get('revision_checks'))}",
        "",
    ]
    if not any(s.get("side") == "baseline" for s in package["sources"]):
        lines += [
            "No baseline was provided. This run cannot verify that a reviewer request was implemented in a later drawing/BOM.",
            "",
        ]
    errors = report.get("errors", [])
    if report.get("external_prediction_timings"):
        lines += [
            "## Timing scope",
            "",
            "The elapsed time above covers this command. Saved vision was generated earlier; its separately recorded timings are "
            + json.dumps(report["external_prediction_timings"], sort_keys=True)
            + ". A cached replay is not an end-to-end timing measurement.",
            "",
        ]
    canonical = report.get("evidence_reconciliation", {}).get("summary")
    if canonical:
        lines += [
            "## Reconciled equipment result — authoritative first-pass decisions",
            "",
            f"- Source claims retained: {canonical['claim_count']}; typed value decisions: {canonical['typed_value_decisions']}.",
            f"- Accepted literal/calibrated decisions: {canonical['accepted_literal_or_calibrated_decisions']}; values requiring human verification: {canonical['values_needing_human_review']}; conflicting decisions: {canonical['conflicting_value_decisions']}.",
            f"- Source-table candidate rows: {canonical['table_candidate_rows']}; source-accounting pages: {canonical['synthesis_pages_processed']}/{canonical['eligible_source_pages']}; unexplained source regions: {canonical['unexplained_regions']}.",
            f"- Explicit graph revision pairs: {canonical['graph_revision_pairs_attempted']}; model changes supported: {canonical['graph_changes']}.",
            "- Reader extraction scores above are retained separately. They do not score the newly reconciled values or establish equipment recall. Source ink accounting is diagnostic, not proof that all equipment was found.",
            "- Canonical-equipment-graph.json and verification-summary.json contain the current evidence decisions. All engineering approval and release states remain false.",
            "",
        ]
        drawing = canonical.get("drawing_reader", {})
        if drawing:
            lines += [
                f"- Independent drawing-graph pages processed: {drawing.get('pages_processed', 0)}; deferred by the bounded drawing budget: {drawing.get('deferred_pages', 0)}.",
                f"- Provisional drawing changes: {drawing.get('candidate_changes', 0)}; confirmed engineering changes: 0. These observations remain pending review.",
                f"- Drawing-stage model pages reused from pinned cache: {drawing.get('cached_model_pages', 0)}. Replays exclude the original model-call time; the raw page reports retain cache and timing provenance.",
                "",
            ]
    reconciled = report.get("reconciled_accuracy")
    if reconciled:
        policy = reconciled["labelled_cell_policy"]
        values = (reconciled["candidate"]["extraction"].get("bom_rows") or {}).get(
            "field_values"
        )
        lines += ["## Reconciled values against independent labels", ""]
        if values:
            lines.append(
                f"- Candidate values: {values['correct']} correct, {values['wrong']} wrong, {values['missing']} unknown/missing. Flagged values still count in candidate accuracy."
            )
        lines += [
            f"- Values needing a person: {policy['needs_review']} / {policy['labelled_values']}; accepted values: {policy['accepted_values']}; wrong values left unflagged: {policy['wrong_values_unflagged']}.",
            "- No accepted values means accepted-value accuracy is undefined. It does not mean extraction is accurate.",
            "",
        ]
    lines += ["## Items requiring review", ""]
    if errors:
        lines += [
            f"- {error.get('stage', 'processing')}: {error.get('error_type', 'error')} — {error.get('message', 'See source diagnostics.')}"
            for error in errors
        ]
    else:
        lines += [
            "- Inspect unreadable/ambiguous source regions and the missed/false-alarm lists in report.json."
        ]
    if cells and cells.get("errors"):
        lines += ["", "Labelled cell disagreements requiring verification:", ""]
        lines += [
            "- "
            + str(item["reference_id"])
            + " / "
            + str(item["field"])
            + ": reference "
            + json.dumps(item.get("expected"), ensure_ascii=False)
            + "; extracted "
            + json.dumps(item.get("observed"), ensure_ascii=False)
            + "."
            for item in cells["errors"][:10]
        ]
        if len(cells["errors"]) > 10:
            lines.append("- Remaining disagreements are retained in report.json.")
    engineering = report.get("engineering_design", {})
    if engineering:
        lines += ["", "## Proposed engineering package", ""]
        if engineering.get("status") == "completed":
            proposal = engineering["result"]
            lines += [f"- {proposal.get('requirements_proposed', 0)} proposed requirements; {proposal.get('conflicts', 0)} conflicts; {proposal.get('unreadable_pages', 0)} source coverage gaps.",
                f"- {proposal.get('devices_proposed', 0)} proposed devices; {proposal.get('rule_questions', 0)} engineer questions. Counts are not accuracy.",
                "- Model-generated files are proposed. No engineering approval, production release or customer email was performed."]
        else:
            lines += ["- Engineering stages did not complete: " + str(engineering.get("reason") or engineering.get("error_type") or engineering.get("status")) + "."]
    lines += [
        "",
        "These are first-pass observations, not an engineering approval or release. Preliminary assistant reference labels need a manufacturer/engineer second review. A blank accuracy metric is not a clean bill of health.",
        "",
    ]
    return "\n".join(lines)


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--vision", action="store_true")
    parser.add_argument("--vision-model")
    parser.add_argument(
        "--drawing-vision",
        action="store_true",
        help="Allow the source-only drawing graph reader under the same remote-model policy as --vision; useful with saved earlier predictions.",
    )
    parser.add_argument(
        "--drawing-page-budget",
        type=int,
        default=500,
        help="Bound independent drawing reads across supplied issues (1–500); shared deadline and deferred pages stay explicit.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument(
        "--evidence-correspondence",
        choices=("none", "source-local-v2"),
        default="source-local-v2",
        help="Versioned source-cell correspondence; use none for an explicit v1-policy replay.",
    )
    args = parser.parse_args()
    if args.vision and args.predictions:
        raise ValueError("Use saved predictions or a fresh vision pass, not both.")
    if args.timeout_seconds <= 0 or args.timeout_seconds > 86400:
        raise ValueError("Use a positive processing budget of at most 24 hours.")
    if not 1 <= args.drawing_page_budget <= 500:
        raise ValueError("Use a drawing page budget between 1 and 500.")
    started = time.perf_counter()
    deadline = time.monotonic() + args.timeout_seconds
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError(
            "Use the real-package manifest with explicit source roles and revisions."
        )
    package = next(p for p in manifest["packages"] if p["id"] == args.package)
    if package.get("kind") not in {"real_public", "real_private"}:
        raise ValueError("Declare this first-day package real_public or real_private.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", package["id"]):
        raise ValueError("Use a bounded filename-safe package ID.")
    for original in package["sources"]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", original["id"]):
            raise ValueError("Use bounded filename-safe source IDs.")
        path = (manifest_path.parent / original["path"]).resolve()
        if path.suffix.lower() == ".dwg":
            raise ValueError(DWG_REQUEST)
        if path.suffix.lower() not in {".pdf", ".dxf", ".xlsx", ".csv", ".tsv", ".xml"}:
            raise ValueError(
                "Use an original PDF, DXF, XLSX, CSV/TSV or electrical CAD report XML for the pilot run."
            )
        if path.stat().st_size > upload_max_bytes(path.name):
            raise ValueError(
                "Split this original into named sections within the upload limits before running vision."
            )
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != original["sha256"].lower():
            raise ValueError(
                "An original changed from its pinned hash. No package was sent to a model."
            )
        if path.suffix.lower() == ".pdf":
            validate_pdf_upload(data)
    private = package.get("kind") == "real_private"
    if private and manifest.get("redaction_review", {}).get("confirmed") is not True:
        raise ValueError(
            "Confirm redaction and sharing permission in the private intake receipt first."
        )
    validate_remote_processing(package, requested=args.vision or args.drawing_vision)
    destination = prepare_output(args.output_dir, private=private)
    from backend.engineering.intake import run_customer_intake
    from backend.equipment.package_reading import _call_with_budget
    # This is the same first-day run, with explicit customer inputs feeding the
    # main engineering stages before the existing equipment/revision audit.
    engineering = _call_with_budget(run_customer_intake, package, manifest_path, destination / "engineering",
        remote_reading=args.vision, model=args.vision_model or "gpt-6.1-sol",
        max_drawing_pages=min(args.drawing_page_budget, 50), deadline=deadline) if package.get("engineering_inputs") else {
            "status": "not_run", "reason": "No customer engineering inputs classified; manufacturer outputs were not promoted to requirements"}
    predictions = str(args.predictions.resolve()) if args.predictions else None
    from backend.equipment.package_reading import (
        _call_with_budget,
        read_package_equipment,
    )

    equipment = read_package_equipment(
        package,
        manifest_path,
        destination / "source-reading",
        vision=args.vision,
        model=args.vision_model or "gpt-6-luna",
        time_budget=max(0, deadline - time.monotonic()),
    )
    combined = equipment["predictions"]
    if predictions:
        saved_predictions = json.loads(Path(predictions).read_text())
        if saved_predictions.get("package_id") != package["id"] or any(
            combined["source_hashes"].get(k) != v
            for k, v in saved_predictions.get("source_hashes", {}).items()
        ):
            raise ValueError(
                "Saved predictions do not match this package's original hashes."
            )
        for key in (
            "comments",
            "devices",
            "bom_rows",
            "connections",
            "sheet_identities",
            "pages",
            "revision_checks",
        ):
            combined[key].extend(saved_predictions.get(key, []))
        combined["timings"]["saved_prediction_timings"] = saved_predictions.get(
            "timings"
        )
    if args.vision:
        from backend.ingestion.vision import candidate_pages
        from scripts.read_real_vision_pages import run

        for original in package["sources"]:
            path = (manifest_path.parent / original["path"]).resolve()
            combined["source_hashes"][original["id"]] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            if path.suffix.lower() != ".pdf":
                continue
            source = {**original, "path": str(path), "package_id": package["id"]}
            try:
                result = _call_with_budget(candidate_pages, path, deadline=deadline)
                if result["status"] != "completed":
                    raise RuntimeError(result.get("error_type", "PageSelectionFailed"))
                pages = result["result"]
            except (OSError, RuntimeError, ValueError) as exc:
                equipment["errors"].append(
                    {
                        "stage": "comment_page_selection",
                        "source_id": original["id"],
                        "error_type": type(exc).__name__,
                        "message": "Comment page selection did not complete; original requires another bounded pass.",
                    }
                )
                continue
            for offset in range(0, len(pages), 8):
                if time.monotonic() >= deadline:
                    equipment["errors"].append(
                        {
                            "stage": "comment_vision",
                            "source_id": original["id"],
                            "message": "Remaining comment pages need another bounded pass.",
                        }
                    )
                    combined["pages"].extend(
                        {
                            "source_id": original["id"],
                            "page": p,
                            "status": "not_processed_budget",
                            "reading_kind": "comments",
                        }
                        for p in pages[offset:]
                    )
                    break
                try:
                    result = _call_with_budget(
                        run,
                        source,
                        pages[offset : offset + 8],
                        destination / "vision" / original["id"],
                        args.vision_model or "gpt-6-luna",
                        deadline=deadline,
                    )
                    if result["status"] != "completed":
                        raise RuntimeError(
                            result.get("error_type", "CommentReadingFailed")
                        )
                    read = result["result"]
                except (OSError, RuntimeError, ValueError) as exc:
                    equipment["errors"].append(
                        {
                            "stage": "comment_vision",
                            "source_id": original["id"],
                            "error_type": type(exc).__name__,
                            "message": "Comment reading failed or reached its deadline; remaining pages require another pass.",
                        }
                    )
                    combined["pages"].extend(
                        {
                            "source_id": original["id"],
                            "page": p,
                            "status": "not_processed_error",
                            "reading_kind": "comments",
                        }
                        for p in pages[offset:]
                    )
                    break
                combined["comments"].extend(read["comments"])
                combined["pages"].extend(
                    {**p, "reading_kind": "comments"} for p in read["pages"]
                )
                equipment["errors"].extend(
                    {
                        "stage": "comment_vision",
                        "source_id": original["id"],
                        "page": p["page"],
                        "error_type": "IncompletePage",
                        "message": "This comment page was not fully read; check the retained original.",
                    }
                    for p in read["pages"]
                    if p.get("status") != "processed"
                )
        combined["timings"]["preparation_wall_seconds"] = time.perf_counter() - started
    saved = destination / "source-predictions.json"
    write_output(saved, json.dumps(combined, indent=2) + "\n", private=private)
    predictions = str(saved)
    report = _run(
        package,
        manifest_path,
        predictions,
        destination,
        vector=True,
        timeout=max(0.01, deadline - time.monotonic()),
    )
    report["preparation"] = {
        "equipment_and_revision_timings": equipment["timings"],
        "revision_pairs": len(equipment["revision_reports"]),
        "revision_sheets_needing_review": equipment.get(
            "revision_sheets_needing_review", 0
        ),
        "cad_sources": sum(
            bool(s.get("cad_report")) for s in equipment["source_reports"]
        ),
        "requires_human_verification": True,
    }
    report.setdefault("errors", []).extend(equipment["errors"])
    if equipment["errors"]:
        report["status"] = "completed_with_engine_errors"
    from backend.equipment.evidence.pipeline import run_evidence_pipeline

    # Only source observations cross this boundary. In particular, independent
    # answer keys, scored disagreements and reference regions never reach any
    # evidence reader, reconciliation decision or synthesis step.
    evidence_package = {
        key: package[key]
        for key in (
            "id",
            "kind",
            "sources",
            "revision_pairs",
            "source_context",
            "verified_identities",
            "drawing_lineage",
        )
        if key in package
    }
    try:
        canonical = run_evidence_pipeline(
            evidence_package,
            manifest_path,
            comparison=(report.get("raw_revision_checks") or {}).get("equipment"),
            source_observations=report.get("source_observations") or {},
            predictions=combined,
            output_dir=destination / package["id"] / "evidence",
            deadline=deadline,
            correspondence_policy=None
            if args.evidence_correspondence == "none"
            else args.evidence_correspondence,
            drawing_vision=args.vision or args.drawing_vision,
            drawing_model=args.vision_model or "gpt-6-luna",
            drawing_page_budget=args.drawing_page_budget,
        )
        report["evidence_reconciliation"] = canonical
        # Independent labels are opened only after all evidence decisions have
        # been frozen into the archived graph. They never enter a reader.
        if package.get("answer_key") and report.get("scores") is not None:
            from backend.equipment.evidence.evaluation import score_reconciliation

            reference = json.loads(
                (manifest_path.parent / package["answer_key"]).resolve().read_text()
            )
            decided_graph = json.loads(
                Path(canonical["artifacts"]["canonical_graph"]).read_text()
            )
            reconciled_score = score_reconciliation(
                reference, report["predictions"], decided_graph
            )
            score_file = destination / package["id"] / "reconciled-scoring-report.json"
            write_output(
                score_file,
                json.dumps(reconciled_score, indent=2) + "\n",
                private=private,
            )
            report["reconciled_accuracy"] = reconciled_score
            canonical["artifacts"]["reconciled_scoring_report"] = str(score_file)
        report["authoritative_equipment_result"] = {
            "graph": canonical["artifacts"]["canonical_graph"],
            "verification_summary": canonical["artifacts"]["verification_summary"],
            "requires_human_verification": True,
            "legacy_extraction_scores_are_not_reconciled_accuracy": True,
        }
        report.setdefault("artifact_paths", {}).update(
            {"canonical_" + key: value for key, value in canonical["artifacts"].items()}
        )
        report["errors"].extend(canonical["errors"])
        if canonical["errors"]:
            report["status"] = "completed_with_engine_errors"
    except (OSError, ValueError, RuntimeError) as exc:
        report["errors"].append(
            {
                "stage": "evidence_reconciliation",
                "error_type": type(exc).__name__,
                "message": "Canonical evidence processing did not complete. Retained reader outputs do not establish reconciled accuracy or engineering acceptance.",
            }
        )
        report["status"] = "completed_with_engine_errors"
    folder = destination / package["id"]
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    report_file = folder / "report.json"
    report["engineering_design"] = engineering
    if package.get("engineering_inputs") and engineering.get("status") != "completed":
        report["errors"].append({"stage": "engineering_design", "error_type": engineering.get("error_type", "IncompleteEngineeringIntake"), "message": "Customer-input design stages did not complete within the declared source/deadline constraints."})
        report["status"] = "completed_with_engine_errors"
    write_output(report_file, json.dumps(report, indent=2) + "\n", private=private)
    write_output(
        folder / "findings-summary.md",
        findings(report, package, time.perf_counter() - started),
        private=private,
    )
    with output_stream(
        folder / "comment-log.csv", private=private, newline=""
    ) as stream:
        columns = ["source_id", "page", "text", "confidence", "actionable", "origin"]
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for comment in (report.get("predictions") or {}).get("comments", []):
            # A CSV may be opened in Excel; source strings are never formulas.
            row = {k: comment.get(k, "") for k in columns}
            for key, value in row.items():
                if isinstance(value, str) and value.lstrip().startswith(
                    ("=", "+", "-", "@")
                ):
                    row[key] = "'" + value
            writer.writerow(row)
    receipt = {
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "source_hashes": {s["id"]: s["sha256"] for s in package["sources"]},
        "completed_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "report_status": report["status"],
        "customer_mail_sent": False,
        "external_ai_requested": bool(args.vision or args.drawing_vision),
        "processing_authorization_recorded": bool(
            private and package.get("model_processing", {}).get("external_ai_allowed") is True
        ),
        "evidence_archive_receipt": report.get("evidence_reconciliation", {}).get(
            "archive_receipt"
        ),
    }
    write_output(
        folder / "run-receipt.json",
        json.dumps(receipt, indent=2) + "\n",
        private=private,
    )
    for path in destination.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    print(
        json.dumps(
            {
                "status": report["status"],
                "report": str(report_file),
                "summary": str(folder / "findings-summary.md"),
                "elapsed_seconds": receipt["elapsed_seconds"],
            }
        )
    )
    raise SystemExit(1 if report.get("errors") else 0)


if __name__ == "__main__":
    main()
