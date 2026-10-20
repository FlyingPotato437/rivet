"""Reuse the source-bound drawing reader for customer engineer one-lines."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import httpx

from .requirements import drawing_note_documents, merge_drawing_readings


def read_customer_drawings(
    requirements,
    documents,
    *,
    output_dir,
    enabled=None,
    model=None,
    provider=None,
    max_pages=6,
    read_notes=False,
    verify_values=False,
    verification_provider=None,
    read_schedules=True,
    schedule_provider=None,
):
    from backend.equipment.evidence.drawing_reader import (
        engineering_drawing_output_tokens,
        engineering_drawing_timeout,
        read_drawing_evidence,
        transcribe_drawing,
    )

    result = requirements
    model = model or os.getenv("RIVET_ENGINEERING_VISION_MODEL", "gpt-6.1-sol")
    enabled = (
        enabled
        if enabled is not None
        else os.getenv(
            "RIVET_ENGINEERING_VISION_ENABLED",
            os.getenv("RIVET_VISION_ENABLED", "false"),
        ).lower()
        == "true"
    )
    reports = []
    provider_is_default = provider is None
    if provider is None:
        # General drawing-reader callers keep their historical transport cap.
        # The engineering inspection path may spend longer on dense originals.
        timeout = engineering_drawing_timeout()
        output_tokens = engineering_drawing_output_tokens()

        def provider(images, *, model):
            return transcribe_drawing(
                images,
                model=model,
                timeout_seconds=timeout,
                max_output_tokens=output_tokens,
            )

    budget = max_pages
    for document in documents:
        if document.get("role") != "customer_one_line":
            continue
        if not enabled:
            result["warnings"].append(
                {
                    "source_id": document["id"],
                    "reason": "Page-image drawing extraction is disabled; drawing devices remain unverified.",
                    "verification_required": True,
                }
            )
            continue
        import pymupdf

        path = Path(document["path"])
        if path.suffix.lower() != ".pdf":
            result["warnings"].append(
                {
                    "source_id": document["id"],
                    "reason": "Customer one-line image reading requires PDF; export PDF alongside native CAD.",
                    "verification_required": True,
                }
            )
            continue
        with pymupdf.open(path) as pdf:
            requested = document.get("pages", list(range(1, len(pdf) + 1)))
            if (
                not isinstance(requested, list)
                or any(
                    not isinstance(p, int)
                    or isinstance(p, bool)
                    or not 1 <= p <= len(pdf)
                    for p in requested
                )
                or len(set(requested)) != len(requested)
            ):
                raise ValueError(
                    "Customer drawing pages must be unique original one-based PDF pages"
                )
            pages = requested[: max(0, budget)]
            if len(requested) > len(pages):
                result["unreadable_pages"].append(
                    {
                        "document_id": document["id"],
                        "state": "deferred",
                        "pages": requested[len(pages) :],
                        "reason": "Drawing page budget reached",
                        "verification_required": True,
                    }
                )
        if not pages:
            continue
        for physical in pages:
            try:
                report = read_drawing_evidence(
                    {
                        "id": document["id"],
                        "name": document["name"],
                        "sha256": document["sha256"],
                        "path": str(path),
                        "role": "drawing",
                        "revision": document.get("revision", "Customer input"),
                    },
                    [physical],
                    output_dir=Path(output_dir)
                    / document["sha256"][:16]
                    / f"page-{physical}",
                    model=model,
                    provider=provider,
                    max_pages=max_pages,
                )
                if verify_values:
                    from .drawing_verification import verify_drawing_values

                    try:
                        checked = verify_drawing_values(
                            {
                                "id": document["id"],
                                "name": document["name"],
                                "sha256": document["sha256"],
                                "path": str(path),
                                "role": "drawing",
                                "revision": document.get("revision", "Customer input"),
                            },
                            report,
                            output_dir=Path(output_dir)
                            / document["sha256"][:16]
                            / f"page-{physical}"
                            / "value-verification",
                            models=(model, model),
                            max_pages=min(max_pages, 6),
                            provider=verification_provider,
                        )
                        report = checked["projected_report"]
                        audit = checked["verification_report"]
                        summary = {
                            "printed_values": len(audit["cells"]),
                            "corroborated_original": sum(
                                bool(c["retained_original_value"])
                                for c in audit["cells"]
                            ),
                            "withheld_unknown": sum(
                                not c["retained_original_value"] for c in audit["cells"]
                            ),
                            "deferred_devices": len(audit["deferred"]),
                        }
                        result.setdefault("drawing_value_verification", []).append(
                            {
                                "source_id": document["id"],
                                "summary": summary,
                                "proposed_corrections": audit["proposed_corrections"],
                                "engineering_approved": False,
                            }
                        )
                        withheld = summary["withheld_unknown"]
                        if withheld:
                            result["warnings"].append(
                                {
                                    "source_id": document["id"],
                                    "reason": f"{withheld} printed trip/frame values could not be corroborated in both blind rereads; values remain unknown.",
                                    "verification_required": True,
                                }
                            )
                    except (OSError, ValueError, httpx.HTTPError) as exc:
                        # A verifier failure cannot silently restore an unchecked
                        # numeric claim. Original raw reads remain in their files.
                        report = deepcopy(report)
                        for record in report["pages"]:
                            for obj in record["reading"]["objects"]:
                                for prop in obj.get("properties", []):
                                    if prop["feature"] in {"frame_a", "trip_a"}:
                                        prop.update(value=None, confidence="unknown")
                        result["warnings"].append(
                            {
                                "source_id": document["id"],
                                "reason": "Drawing value verification failed; trip/frame values withheld: "
                                + str(exc)[:300],
                                "verification_required": True,
                            }
                        )
                reports.append(report)
            except (OSError, ValueError, httpx.HTTPError) as exc:
                result["warnings"].append(
                    {
                        "source_id": document["id"],
                        "page": physical,
                        "reason": "Drawing reader failed: " + str(exc)[:500],
                        "provider_metadata": getattr(exc, "provider_metadata", None),
                        "verification_required": True,
                    }
                )
                result["unreadable_pages"].append(
                    {
                        "document_id": document["id"],
                        "pages": [physical],
                        "state": "failed",
                        "verification_required": True,
                    }
                )
            # A full table context preserves row/column/continuation semantics
            # which independent one-line tiles cannot safely infer. The locator
            # sees only the current original, never labels or expected values.
            # Injected graph-only providers keep their existing offline contract.
            if read_schedules and (
                schedule_provider is not None or provider_is_default
            ):
                from .schedule_batches import read_schedule_batches
                from .schedule_reader import (
                    combine_schedule_and_drawing,
                )
                from .schedule_regions import locate_schedule_regions

                try:
                    located = locate_schedule_regions(
                        document,
                        physical,
                        output_dir=Path(output_dir)
                        / document["sha256"][:16]
                        / f"page-{physical}"
                        / "schedule-regions",
                    )
                    if not located["regions"]:
                        from .schedule_region_vision import (
                            locate_schedule_regions_vision,
                        )

                        native_diagnostics = located["diagnostics"]
                        located = locate_schedule_regions_vision(
                            document,
                            physical,
                            output_dir=Path(output_dir)
                            / document["sha256"][:16]
                            / f"page-{physical}"
                            / "schedule-region-vision",
                            model=model,
                        )
                        located["diagnostics"] = [
                            {
                                "deterministic_locator": native_diagnostics,
                                "vision_fallback": located["diagnostics"],
                            }
                        ]
                    result.setdefault("schedule_region_coverage", []).append(
                        {
                            "source_id": document["id"],
                            "page": physical,
                            "regions": len(located["regions"]),
                            "diagnostics": located["diagnostics"],
                            "verification_required": True,
                        }
                    )
                    if located["regions"]:
                        schedule = read_schedule_batches(
                            document,
                            physical,
                            located["regions"],
                            output_dir=Path(output_dir)
                            / document["sha256"][:16]
                            / f"page-{physical}"
                            / "schedule-reading",
                            model=model,
                            provider=schedule_provider,
                        )
                        for failed_table in schedule.get("batch_errors", []):
                            result["warnings"].append(
                                {
                                    "source_id": document["id"],
                                    "page": physical,
                                    "reason": "Equipment table failed; other tables retained",
                                    "table_failure": failed_table,
                                    "verification_required": True,
                                }
                            )
                        if verify_values:
                            # The existing blind verifier covered graph inputs,
                            # not these subsequently read table cells. Never
                            # inherit its receipt or restore a withheld value.
                            for schedule_page in schedule["pages"]:
                                for obj in schedule_page["reading"]["objects"]:
                                    for prop in obj["properties"]:
                                        if prop["feature"] in {"frame_a", "trip_a"}:
                                            prop.update(
                                                value=None,
                                                confidence="unknown",
                                                value_verification={
                                                    "state": "withheld_schedule_outside_blind_verification",
                                                    "engineering_approved": False,
                                                },
                                            )
                            result["warnings"].append(
                                {
                                    "source_id": document["id"],
                                    "page": physical,
                                    "reason": "Schedule frame/trip observations are outside the requested blind verification; retained as unknown.",
                                    "verification_required": True,
                                }
                            )
                        existing = next(
                            (
                                r
                                for r in reports
                                if r["sha256"] == document["sha256"]
                                and any(p["page"] == physical for p in r["pages"])
                            ),
                            None,
                        )
                        if existing is None:
                            reports.append(schedule)
                        else:
                            reports[reports.index(existing)] = (
                                combine_schedule_and_drawing(existing, schedule)
                            )
                except (OSError, ValueError, httpx.HTTPError) as exc:
                    result["warnings"].append(
                        {
                            "source_id": document["id"],
                            "page": physical,
                            "reason": "Equipment schedule reading failed; table coverage unverified: "
                            + str(exc)[:500],
                            "provider_metadata": getattr(
                                exc, "provider_metadata", None
                            ),
                            "verification_required": True,
                        }
                    )
        budget -= len(pages)
    result = merge_drawing_readings(result, reports)
    if read_notes and reports:
        from .text_reader import read_customer_text

        result, notes = drawing_note_documents(result, reports)
        result, _ = read_customer_text(
            result, notes, output_dir=Path(output_dir) / "note-reading", model=model
        )
    return result, reports
