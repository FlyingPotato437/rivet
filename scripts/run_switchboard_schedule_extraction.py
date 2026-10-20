"""Freeze a source-only schedule prediction for a separate scoring process.

This command never opens answer keys, imports a scorer, confirms requirements,
configures equipment, or generates drawings. It reads only declared original
customer-side inputs; schedule observations remain proposed and unapproved.
Run explicitly with --run to permit the existing schedule model transport.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERSION = "rivet.source-only-schedule-extraction-run.v1"
CUSTOMER_ROLES = {
    "specification",
    "customer_one_line",
    "addendum",
    "quote_exception",
    "purchase_order",
}


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _exclusive_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
    return _sha(content)


def _exclusive_json(path, value):
    return _exclusive_bytes(
        path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    )


def _implementation_files():
    # All backend Python includes engineering and its transitive application
    # helpers. The scorer is archived as bytes only and is never imported/run.
    paths = {
        *ROOT.joinpath("backend").rglob("*.py"),
        *ROOT.joinpath("backend/engineering").rglob("*.json"),
        ROOT / "scripts/evaluate_switchboard_reconstruction.py",
        Path(__file__).resolve(),
    }
    for name in ("pyproject.toml", "requirements.txt", "uv.lock"):
        if (ROOT / name).is_file():
            paths.add(ROOT / name)
    return {str(path.relative_to(ROOT)): path for path in sorted(paths)}


def _freeze_implementation(output):
    files, hashes = _implementation_files(), {}
    for relative, path in files.items():
        content = path.read_bytes()
        hashes[relative] = _exclusive_bytes(
            output / "implementation" / relative, content
        )
    _exclusive_json(output / "implementation-before.json", {"files": hashes})
    return files, hashes


def _source_inputs(package, manifest_path, output):
    import pymupdf

    sources, bindings, seen = [], [], set()
    # Resolve the declared key name only to exclude it from source inputs.
    # Its contents are never opened, hashed, copied or passed to any reader.
    key_path = package.get("answer_key")
    excluded_key = None
    if isinstance(key_path, str):
        excluded_key = Path(key_path)
        if not excluded_key.is_absolute():
            excluded_key = manifest_path.parent / excluded_key
        excluded_key = excluded_key.resolve()
    raw_sources = package.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ValueError("Package must declare source originals")
    for index, raw in enumerate(raw_sources):
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("Source IDs must be nonempty and unique")
        seen.add(identifier)
        if raw.get("role") not in CUSTOMER_ROLES:
            raise ValueError("Only declared customer-side source roles are accepted")
        original = Path(raw["path"])
        if not original.is_absolute():
            original = manifest_path.parent / original
        original = original.resolve()
        if original == excluded_key:
            raise ValueError("A declared answer key cannot be a source input")
        content = original.read_bytes()
        checksum = _sha(content)
        if checksum != raw.get("sha256"):
            raise ValueError("Original source hash mismatch: " + identifier)
        authority = raw.get(
            "source_authority",
            package.get("source_authority", "customer_inputs_unconfirmed"),
        )
        frozen = (
            output
            / "source-originals"
            / f"source-{index + 1}-{checksum}{original.suffix.lower()}"
        )
        _exclusive_bytes(frozen, content)
        pages = []
        if raw["role"] == "customer_one_line":
            if original.suffix.lower() != ".pdf":
                raise ValueError("Schedule drawing inputs require original PDFs")
            with pymupdf.open(frozen) as pdf:
                pages = raw.get("pages", list(range(1, len(pdf) + 1)))
                if (
                    not isinstance(pages, list)
                    or not pages
                    or any(
                        not isinstance(p, int)
                        or isinstance(p, bool)
                        or not 1 <= p <= len(pdf)
                        for p in pages
                    )
                    or len(set(pages)) != len(pages)
                ):
                    raise ValueError(
                        "Source pages must be unique original one-based PDF pages"
                    )
        binding = {
            "id": identifier,
            "role": raw["role"],
            "sha256": checksum,
            "source_authority": authority,
            "original_path": str(original),
            "frozen_path": str(frozen.resolve()),
            "pages": pages,
            "schedule_reading_applicable": raw["role"] == "customer_one_line",
        }
        bindings.append(binding)
        sources.append(
            {
                "id": identifier,
                "name": raw.get("name", original.name),
                "path": str(frozen.resolve()),
                "sha256": checksum,
                "role": raw["role"],
                "source_authority": authority,
                "pages": pages,
            }
        )
    _exclusive_json(
        output / "source-only-inputs.json",
        {"sources": bindings, "answer_key_opened": False},
    )
    return sources, bindings


def run_manifest(
    manifest_path,
    package_id,
    output,
    *,
    model="gpt-6.1-sol",
    max_pages=6,
    provider=None,
):
    """Python entry point; `provider` is a local test seam, never a CLI input.

    A new directory is reserved exclusively. Even interrupted/failed runs cannot
    be overwritten by rerunning this command. Model calls occur only after the
    implementation, manifest and source-original copies have been frozen.
    """
    from backend.engineering.requirements import (
        extract_requirements,
        merge_drawing_readings,
    )
    from backend.engineering.schedule_batches import read_schedule_batches
    from backend.engineering.schedule_regions import locate_schedule_regions

    if (
        isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or not 1 <= max_pages <= 50
    ):
        raise ValueError("Page budget must be 1–50")
    manifest_path, output = Path(manifest_path).resolve(), Path(output)
    if output.is_symlink() or any(parent.is_symlink() for parent in output.parents):
        raise ValueError("Output cannot use a symlink")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        output.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise ValueError(
            "Output already exists; preserve that run and choose a new directory"
        ) from exc
    started = time.monotonic()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    candidates = [p for p in manifest.get("packages", []) if p.get("id") == package_id]
    if len(candidates) != 1:
        raise ValueError("Choose exactly one manifest package ID")
    package = candidates[0]
    _exclusive_bytes(output / "manifest-frozen.json", manifest_bytes)
    files, before = _freeze_implementation(output)
    sources, bindings = _source_inputs(package, manifest_path, output)
    _exclusive_json(
        output / "run-before.json",
        {
            "version": VERSION,
            "started_at": datetime.now(UTC).isoformat(),
            "package_id": package_id,
            "model": model,
            "max_pages": max_pages,
            "manifest_sha256": _sha(manifest_bytes),
            "answer_key_opened": False,
            "scoring_enabled": False,
            "configuration_enabled": False,
            "generation_enabled": False,
            "transport": "injected_test_provider"
            if provider is not None
            else "existing_schedule_provider",
        },
    )
    requirements = extract_requirements([], scope_id=package_id)
    reports, coverage, errors, deferred = [], [], [], []
    remaining, stopped = max_pages, False
    for source in sources:
        if source["role"] != "customer_one_line":
            coverage.append(
                {
                    "source_id": source["id"],
                    "status": "not_applicable",
                    "reason": "Schedule-only runner reads customer one-line PDF schedules; other originals remain frozen inputs.",
                }
            )
            continue
        for physical in source["pages"]:
            if remaining <= 0 or stopped:
                deferred.append(
                    {
                        "source_id": source["id"],
                        "page": physical,
                        "reason": "prior_reader_failure" if stopped else "page_budget",
                    }
                )
                continue
            remaining -= 1
            directory = (
                output
                / "schedule-readings"
                / hashlib.sha256(source["id"].encode()).hexdigest()[:20]
                / f"page-{physical}"
            )
            try:
                located = locate_schedule_regions(
                    source, physical, output_dir=directory / "regions"
                )
                if not located["regions"]:
                    from backend.engineering.schedule_region_vision import (
                        locate_schedule_regions_vision,
                    )

                    native_diagnostics = located["diagnostics"]
                    located = locate_schedule_regions_vision(
                        source,
                        physical,
                        output_dir=directory / "region-vision",
                        model=model,
                    )
                    located["diagnostics"] = [
                        {
                            "deterministic_locator": native_diagnostics,
                            "vision_fallback": located["diagnostics"],
                        }
                    ]
                _exclusive_json(
                    directory / "region-diagnostics.json",
                    {
                        "source_id": source["id"],
                        "sha256": source["sha256"],
                        "page": physical,
                        "diagnostics": located["diagnostics"],
                        "regions": [
                            {k: v for k, v in region.items() if k != "png"}
                            for region in located["regions"]
                        ],
                    },
                )
                coverage.append(
                    {
                        "source_id": source["id"],
                        "page": physical,
                        "regions": len(located["regions"]),
                        "status": located.get("status", "unknown"),
                        "verification_required": True,
                    }
                )
                if not located["regions"]:
                    continue
                report = read_schedule_batches(
                    source,
                    physical,
                    located["regions"],
                    output_dir=directory / "reader",
                    model=model,
                    provider=provider,
                )
                errors.extend(
                    {"source_id": source["id"], "page": physical, **e}
                    for e in report.get("batch_errors", [])
                )
                if report.get("batch_errors"):
                    stopped = True
                if report.get("table_batches", {}).get("accepted") == 0:
                    continue
                report["source_authority"] = source["source_authority"]
                _exclusive_json(directory / "schedule-report.json", report)
                reports.append(report)
            except (OSError, ValueError) as exc:
                errors.append(
                    {
                        "source_id": source["id"],
                        "page": physical,
                        "error_type": type(exc).__name__,
                        "reason": str(exc)[:500],
                        "verification_required": True,
                    }
                )
                stopped = True  # No blind retries or subsequent provider calls.
    requirements = merge_drawing_readings(requirements, reports)
    after_files = _implementation_files()
    after = {
        relative: _sha(path.read_bytes()) for relative, path in after_files.items()
    }
    implementation_changed = before != after or set(files) != set(after_files)
    source_changes = [
        b["id"]
        for b in bindings
        if _sha(Path(b["original_path"]).read_bytes()) != b["sha256"]
    ]
    implementation = {
        "before": before,
        "after": after,
        "changed_during_run": implementation_changed,
    }
    _exclusive_json(output / "implementation-after.json", implementation)
    status = (
        "invalid_implementation_changed"
        if implementation_changed
        else "invalid_original_source_changed"
        if source_changes
        else "completed_with_reader_errors"
        if errors
        else "completed"
    )
    prediction = {
        "schema_version": VERSION,
        "package_id": package_id,
        "kind": package.get("kind"),
        "declared_split": package.get("split"),
        "source_authority": package.get(
            "source_authority", "customer_inputs_unconfirmed"
        ),
        "source_hashes": {s["id"]: s["sha256"] for s in sources},
        "source_bindings": bindings,
        "reader": "source_only_schedule_locator_and_reader",
        "model": model,
        "run_status": status,
        "requirements": requirements,
        "drawing_reports": reports,
        "schedule_region_coverage": coverage,
        "reader_errors": errors,
        "deferred_pages": deferred,
        "implementation": implementation,
        "original_sources_changed_during_run": source_changes,
        "elapsed_seconds": time.monotonic() - started,
        "extraction_only": True,
        "answer_key_used_as_input": False,
        "answer_key_opened": False,
        "configuration": None,
        "design": None,
        "artifacts": None,
        "engineering_approved": False,
        "production_released": False,
        "limits": [
            "Source observations only; no independent accuracy or successful-design claim.",
            "Model/header conventions and source scope remain proposed for human review.",
            "Prediction must be scored separately against an independently held key.",
        ],
    }
    prediction_hash = _exclusive_json(output / "prediction-frozen.json", prediction)
    receipt = {
        "version": VERSION,
        "package_id": package_id,
        "run_status": status,
        "prediction_sha256": prediction_hash,
        "prediction_path": str((output / "prediction-frozen.json").resolve()),
        "manifest_sha256": _sha(manifest_bytes),
        "source_hashes": prediction["source_hashes"],
        "reports": len(reports),
        "requirement_items": len(requirements["items"]),
        "reader_errors": len(errors),
        "deferred_pages": len(deferred),
        "answer_key_opened": False,
        "score_produced": False,
        "prediction_frozen_before_any_separate_scoring": True,
    }
    _exclusive_json(output / "run-complete.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--max-pages", type=int, default=6)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Explicitly run the existing source-only schedule transport; may incur model usage.",
    )
    args = parser.parse_args(argv)
    if not args.run:
        parser.error(
            "--run is required; this command executes source-only schedule extraction"
        )
    result = run_manifest(
        args.manifest,
        args.package,
        args.output,
        model=args.model,
        max_pages=args.max_pages,
    )
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    result = main()
    raise SystemExit(0 if result["run_status"] == "completed" else 2)
