"""Bound each model read to one table and preserve successful neighboring reads."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import httpx

from .schedule_reader import VERSION, read_schedule_evidence


def read_schedule_batches(
    source, page, regions, *, output_dir, model=None, provider=None, workers=2
):
    if isinstance(workers, bool) or workers not in (1, 2):
        raise ValueError("Schedule reads allow one or two concurrent table requests")
    regions = list(regions)
    primary = [r for r in regions if r.get("kind") != "context_notes"]
    if not primary or len(regions) > 16:
        raise ValueError("Schedule batches need 1–16 bounded source regions")

    def read(index, region):
        context = [
            r
            for r in regions
            if r.get("kind") == "context_notes"
            and region.get("key") in r.get("related_schedule_keys", [])
        ]
        group = [region, *context]
        ident = hashlib.sha256(json.dumps(region["bbox"]).encode()).hexdigest()[:16]
        directory = Path(output_dir) / f"table-{index}-{ident}"
        try:
            report = read_schedule_evidence(
                source,
                page,
                group,
                output_dir=directory,
                model=model,
                provider=provider,
            )
            return index, report, None
        except (OSError, ValueError, httpx.HTTPError) as exc:
            return (
                index,
                None,
                {
                    "table_index": index,
                    "bbox": region["bbox"],
                    "reason": str(exc)[:500],
                    "error_type": type(exc).__name__,
                    "provider_metadata": getattr(exc, "provider_metadata", None),
                    "diagnostic_path": getattr(exc, "diagnostic_path", None),
                    "verification_required": True,
                },
            )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(read, i, r) for i, r in enumerate(primary)]
        results = [f.result() for f in futures]
    successful = [r for _, r, e in results if r is not None]
    errors = [e for _, r, e in results if e is not None]
    reading = {
        "objects": [],
        "conductors": [],
        "labels": [],
        "sections": [],
        "ambiguities": [],
        "unknown_regions": [],
        "non_equipment_regions": [],
        "identity": None,
    }
    receipts = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    for index, report, error in results:
        if report is None:
            reading["unknown_regions"].append(
                {
                    "bbox": error["bbox"],
                    "reason": "Equipment table read failed; neighboring results retained: "
                    + error["reason"],
                }
            )
            continue
        for record in report["pages"]:
            for key, value in reading.items():
                if isinstance(value, list):
                    value.extend(deepcopy(record["reading"].get(key, [])))
            for field in usage:
                usage[field] += record.get("usage", {}).get(field, 0)
        receipts.append({"table_index": index, "report": report})
    prompts = [r["prompt_sha256"] for r in successful]
    return {
        "version": VERSION + "+per-table-batches-v1",
        "source_id": source["id"],
        "sha256": source["sha256"],
        "model": model,
        "prompt_sha256": hashlib.sha256(json.dumps(prompts).encode()).hexdigest(),
        "engineering_approved": False,
        "production_released": False,
        "batch_errors": errors,
        "table_batches": {
            "requested": len(primary),
            "accepted": len(successful),
            "failed": len(errors),
            "workers": workers,
        },
        "pages": [
            {
                "page": page,
                "reading": reading,
                "usage": usage,
                "schedule_batches": receipts,
            }
        ],
    }
