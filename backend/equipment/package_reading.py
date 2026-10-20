"""Bounded first-day orchestration; partial extraction never becomes absence.

No answer keys reach readers. Native rows remain in source_reports; separate
image observations remain in predictions. Neither is engineering approval.
"""

import hashlib
import json
import multiprocessing
import os
import pickle
import tempfile
import time
from pathlib import Path

import pymupdf

from .readers import read_source
from .schemas import SourceInput

MAX_RESULT_BYTES = 128 * 1024 * 1024


def _budget_child(function, args, kwargs, result_path):
    """Only our own child writes this private result, never customer bytes."""
    try:
        envelope = {"status": "completed", "result": function(*args, **kwargs)}
    except Exception as exc:  # noqa: BLE001 - retain only a class name, never provider response/credentials.
        envelope = {"status": "failed", "error_type": type(exc).__name__}
    temporary = Path(result_path).with_suffix(".pending")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        pickle.dump(envelope, stream, protocol=pickle.HIGHEST_PROTOCOL)
    if temporary.stat().st_size > MAX_RESULT_BYTES:
        temporary.unlink()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            pickle.dump({"status": "failed", "error_type": "ResultSizeLimit"}, stream)
    temporary.replace(result_path)


def _call_with_budget(function, *args, deadline, **kwargs):
    """Spawn a reader with one shared absolute monotonic deadline.

    Returns {status:'completed', result:...} or {status:'timed_out'|'failed',
    error_type:...}, with elapsed_seconds. A private atomic result file avoids
    pipe/queue deadlocks when a large document report exceeds pipe capacity.
    """
    started = time.monotonic()
    if deadline <= started:
        return {
            "status": "timed_out",
            "error_type": "DeadlineExceeded",
            "elapsed_seconds": 0.0,
        }
    process = None
    with tempfile.TemporaryDirectory(prefix="rivet-bounded-reader-") as folder:
        result_path = Path(folder) / "result.bin"
        try:
            process = multiprocessing.get_context("spawn").Process(
                target=_budget_child, args=(function, args, kwargs, str(result_path))
            )
            process.start()
            process.join(max(0.0, deadline - time.monotonic()))
            if process.is_alive():
                process.terminate()
                process.join(1)
                if process.is_alive():
                    process.kill()
                    process.join(1)
                envelope = {"status": "timed_out", "error_type": "DeadlineExceeded"}
            elif process.exitcode != 0 or not result_path.is_file():
                envelope = {"status": "failed", "error_type": "ReaderProcessExited"}
            elif result_path.stat().st_size > MAX_RESULT_BYTES:
                envelope = {"status": "failed", "error_type": "ResultSizeLimit"}
            else:
                # This pickle is created only by the authored child in a fresh
                # owner-only temp directory, not read from any source package.
                with result_path.open("rb") as stream:
                    envelope = pickle.load(stream)
        except Exception as exc:  # noqa: BLE001 - local process/serialization failure stays visible and generic.
            envelope = {"status": "failed", "error_type": type(exc).__name__}
        finally:
            if process is not None and process.is_alive():
                process.terminate()
                process.join(1)
    envelope["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return envelope


def _pdf_page_count(path):
    with pymupdf.open(path) as pdf:
        return len(pdf)


def _preflight(package, base):
    sources, raw_sources = {}, {}
    for raw in package["sources"]:
        if raw["id"] in sources:
            raise ValueError("Duplicate source IDs; no provider request made.")
        path = (base / raw["path"]).resolve()
        if hashlib.sha256(path.read_bytes()).hexdigest() != raw["sha256"].lower():
            raise ValueError(
                "Source changed before equipment reading; no provider request made."
            )
        sources[raw["id"]] = SourceInput(
            **{k: raw[k] for k in ("id", "name", "role", "revision")},
            sha256=raw["sha256"].lower(),
            path=str(path),
        )
        raw_sources[raw["id"]] = raw
    pairs = list(package.get("revision_pairs", []))
    pairing_ambiguous = False
    if not pairs:
        previous = [
            s
            for s in raw_sources.values()
            if s.get("side") == "baseline"
            and s["role"] == "drawing"
            and Path(s["name"]).suffix.lower() == ".pdf"
        ]
        current = [
            s
            for s in raw_sources.values()
            if s.get("side") == "target"
            and s["role"] == "drawing"
            and Path(s["name"]).suffix.lower() == ".pdf"
        ]
        if len(previous) == len(current) == 1:
            pairs = [
                {
                    "baseline_source_id": previous[0]["id"],
                    "target_source_id": current[0]["id"],
                }
            ]
        elif previous and current:
            pairing_ambiguous = True
    seen = set()
    for pair in pairs:
        old, new = pair.get("baseline_source_id"), pair.get("target_source_id")
        if old not in sources or new not in sources or old == new:
            raise ValueError(
                "Revision pair references must identify two supplied sources; no provider request made."
            )
        if (old, new) in seen:
            raise ValueError("Duplicate revision pair; no provider request made.")
        seen.add((old, new))
        for identifier in (old, new):
            source = sources[identifier]
            if (
                source.role != "drawing"
                or Path(source.name).suffix.lower() != ".pdf"
                or Path(source.path).suffix.lower() != ".pdf"
            ):
                raise ValueError(
                    "Revision pairs require drawing PDFs; no provider request made."
                )
    return sources, pairs, pairing_ambiguous


def read_package_equipment(
    package,
    manifest_path,
    output,
    *,
    vision=False,
    model=None,
    time_budget=3600,
    visual_page_limit=500,
):
    started = time.monotonic()
    deadline = started + max(0.0, float(time_budget))
    sources, pairs, pairing_ambiguous = _preflight(
        package, Path(manifest_path).resolve().parent
    )
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    predictions = {
        "schema_version": "rivet.real-predictions.v1",
        "package_id": package["id"],
        "source_hashes": {s.id: s.sha256 for s in sources.values()},
        "producer": {
            "name": "Rivet real equipment package readers",
            "model": model,
            "engineering_verified": False,
        },
        "comments": [],
        "devices": [],
        "bom_rows": [],
        "connections": [],
        "sheet_identities": [],
        "pages": [],
        "revision_checks": [],
        "requires_human_verification": True,
    }
    reports, revision_reports, errors = [], [], []
    review_refs, visual_count = set(), 0
    page_counts, calls = {}, []

    def error(stage, *, source_id=None, **details):
        errors.append(
            {
                "stage": stage,
                **({"source_id": source_id} if source_id else {}),
                **details,
            }
        )

    def deferred(source, pages, status, reason):
        predictions["pages"].extend(
            {
                "source_id": source.id,
                "page": page,
                "status": status,
                "reading_kind": "equipment",
                "reason": reason,
                "requires_human_verification": True,
            }
            for page in pages
        )

    def call(stage, function, *args, source_id=None, **kwargs):
        record = _call_with_budget(function, *args, deadline=deadline, **kwargs)
        calls.append(
            {
                "stage": stage,
                "source_id": source_id,
                **{k: v for k, v in record.items() if k != "result"},
            }
        )
        if record["status"] != "completed":
            error(
                stage,
                source_id=source_id,
                error_type=record.get("error_type", "ReaderFailed"),
                message="Reader did not complete within the shared budget or failed. Original evidence remains unverified.",
            )
            return None
        return record["result"]

    def checkpoint(*, final=False):
        result = {
            "predictions": predictions,
            "source_reports": reports,
            "revision_reports": revision_reports,
            "errors": errors,
            "status": ("completed_with_engine_errors" if errors else "completed")
            if final
            else "in_progress",
            "requires_human_verification": True,
            "revision_sheets_needing_review": len(review_refs),
            "timings": {
                "equipment_and_revision_wall_seconds": round(
                    time.monotonic() - started, 3
                ),
                "time_budget_seconds": time_budget,
                "reader_calls": calls,
            },
        }
        predictions["timings"] = result["timings"]
        path = output / "equipment-reading.json"
        pending = output / "equipment-reading.pending.json"
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
        pending.replace(path)
        path.chmod(0o600)
        return result

    if pairing_ambiguous:
        error(
            "revision_pairing",
            message="Multiple drawing files need explicit issue pairing; no filename/page-order pairing was invented.",
        )
        review_refs.add(("ambiguous_file_pairs",))
    checkpoint()
    for source in sources.values():
        is_pdf = Path(source.path).suffix.lower() == ".pdf"
        if is_pdf and source.role in {"drawing", "bom"}:
            count = call(
                "page_count", _pdf_page_count, source.path, source_id=source.id
            )
            if count is not None:
                page_counts[source.id] = count
        native = call("native_equipment", read_source, source, source_id=source.id)
        if native is None:
            report = {
                "id": source.id,
                "name": source.name,
                "role": source.role,
                "sha256": source.sha256,
                "revision": source.revision,
                "status": "unreadable",
                "observed_bom_rows": [],
                "warnings": [
                    {
                        "code": "reader_did_not_complete",
                        "message": "No complete native reading was established.",
                    }
                ],
            }
        else:
            report, _ = native
            if report.get("status") in {"unreadable", "unsupported"}:
                error(
                    "native_equipment",
                    source_id=source.id,
                    error_type="UnreadableSource",
                    message="The native equipment reader could not inspect this source; no clean zero was inferred.",
                )
        reports.append(report)
        if not is_pdf or source.role not in {"drawing", "bom"}:
            checkpoint()
            continue
        count = page_counts.get(source.id) or report.get("page_count")
        if not count:
            deferred(
                source,
                [None],
                "page_count_unknown",
                "Original page count could not be established within the shared budget.",
            )
            checkpoint()
            continue
        pages = list(range(1, count + 1))
        # A found native row is not proof of complete page/table coverage.
        # Always inspect eligible pages; native reports and image observations
        # remain separate so callers can review/deduplicate explicit overlaps.
        if not vision:
            deferred(
                source,
                pages,
                "requires_visual_reading",
                "Page-image reading was not enabled.",
            )
            checkpoint()
            continue
        from .visual_reader import read_visual_equipment

        offset = 0
        while offset < len(pages):
            remaining = visual_page_limit - visual_count
            if time.monotonic() >= deadline or remaining <= 0:
                deferred(
                    source,
                    pages[offset:],
                    "not_processed_budget",
                    "Another bounded equipment-reading pass is needed.",
                )
                error(
                    "visual_equipment",
                    source_id=source.id,
                    error_type="PageOrTimeBudget",
                    message="Remaining physical pages were not read; no complete extraction claimed.",
                    unprocessed_page_count=len(pages) - offset,
                )
                break
            batch = pages[offset : offset + min(8, remaining)]
            visual_count += len(batch)
            folder = hashlib.sha256(source.id.encode()).hexdigest()[:16]
            read = call(
                "visual_equipment",
                read_visual_equipment,
                source,
                batch,
                output / "equipment" / folder,
                source_id=source.id,
                model=model,
                max_pages=8,
                max_tiles=60,
            )
            if read is None:
                deferred(
                    source,
                    batch,
                    "failed_or_timed_out",
                    "Visual reader did not return a completed batch; cached page evidence may be retried.",
                )
                offset += len(batch)
                checkpoint()
                continue
            returned = {p.get("page") for p in read.get("pages", [])}
            missing = [p for p in batch if p not in returned]
            if missing:
                deferred(
                    source,
                    missing,
                    "not_returned",
                    "Reader returned no page coverage record.",
                )
                error(
                    "visual_equipment",
                    source_id=source.id,
                    error_type="MissingPageRecords",
                    message="Some requested pages returned no coverage record.",
                    unprocessed_page_count=len(missing),
                )
            for page in read.get("pages", []):
                page["reading_kind"] = "equipment"
                if page.get("status") in {"failed", "partial"}:
                    error(
                        "visual_equipment",
                        source_id=source.id,
                        page=page["page"],
                        error_type=page.get("error_type", "IncompletePage"),
                        message="Visual equipment reading was incomplete; inspect the original.",
                    )
            for key, source_key in (
                ("bom_rows", "observed_bom_rows"),
                ("devices", "devices"),
                ("connections", "connections"),
                ("sheet_identities", "sheet_identities"),
                ("pages", "pages"),
            ):
                predictions[key].extend(read.get(source_key, []))
            offset += len(batch)
            checkpoint()

    from .visual_revision import compare_revision_sources

    for index, pair in enumerate(pairs):
        before, after = (
            sources[pair["baseline_source_id"]],
            sources[pair["target_source_id"]],
        )
        if index >= 20:
            review_refs.add((index, "unprocessed_file_pair"))
            error(
                "revision_pairing",
                error_type="PairBudget",
                pair_index=index + 1,
                message="This explicit revision pair exceeds the twenty-pair pass limit.",
            )
            continue
        checked = call(
            "visual_revision",
            compare_revision_sources,
            before,
            after,
            output / "revisions" / f"pair-{index + 1}",
            source_id=after.id,
            visual_observations=predictions,
            model=model,
            vision=vision,
        )
        if checked is None:
            review_refs.add((index, "unprocessed_file_pair"))
            checkpoint()
            continue
        revision_reports.append(checked)
        predictions["revision_checks"].extend(checked.get("revision_checks", []))
        errors.extend(
            {"stage": "visual_revision", **item} for item in checked.get("errors", [])
        )
        alignment = checked.get("sheet_alignment", {})
        for side, pages in alignment.get("unidentified_pages", {}).items():
            review_refs.update((index, side, page) for page in pages)
        for ambiguous in alignment.get("ambiguous", []):
            for side, key in (
                ("baseline", "baseline_pages"),
                ("target", "target_pages"),
            ):
                review_refs.update(
                    (index, side, page) for page in ambiguous.get(key, [])
                )
        for name, side, key in (
            ("removed", "baseline", "baseline_page"),
            ("added", "target", "target_page"),
        ):
            for item in alignment.get(name, []):
                if not item.get("absence_verified"):
                    review_refs.add((index, side, item.get(key)))
        for match in alignment.get("matches", []):
            if (
                match.get("requires_human_verification")
                or match.get("status") == "possible_renumbered"
            ):
                review_refs.add(
                    (index, "sheet_match", match["baseline_page"], match["target_page"])
                )
        pending_count = checked.get("unprocessed_pair_count", 0)
        for number in range(pending_count):
            review_refs.add((index, "unprocessed_sheet_pair", number))
        unresolved = sum(ref[0] == index for ref in review_refs)
        if unresolved:
            error(
                "revision_sheet_matching",
                source_id=after.id,
                error_type="UnverifiedSheetCoverage",
                pair_index=index + 1,
                sheets_needing_review=unresolved,
                unprocessed_sheet_pair_count=pending_count,
                message="Unmatched, ambiguous, renamed or deferred revision sheets require human verification; no complete revision check is claimed.",
            )
        checkpoint()
    return checkpoint(final=True)
