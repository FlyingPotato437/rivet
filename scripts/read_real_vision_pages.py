"""Run Rivet's actual page-vision reader; save hash-bound raw evidence and predictions.

This performs no engineering approval and never sends mail. Private originals and
transcripts should stay under .data. Explicit pages make evaluation costs visible.
"""

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from backend.ingestion.markup_classification import actionability, atomic_findings
from backend.ingestion.vision import VISION_INSTRUCTIONS, render, transcribe


def run(source, pages, output, model=None, workers=2):
    path = Path(source["path"]).resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != source["sha256"]:
        raise ValueError("The original changed; review and repin the source first.")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)

    started_run = time.monotonic()
    prompt_hash = hashlib.sha256(VISION_INSTRUCTIONS.encode()).hexdigest()

    def read(page):
        saved = output / f"{source['id']}-page-{page}.json"
        if saved.exists():
            old = json.loads(saved.read_text())
            if (
                old.get("sha256") == digest
                and old.get("status") == "processed"
                and old.get("prompt_sha256") == prompt_hash
                and old.get("model") == model
            ):
                return old
        started = time.monotonic()
        record = {
            "source_id": source["id"],
            "page": page,
            "sha256": digest,
            "prompt_sha256": prompt_hash,
            "model": model,
        }
        try:
            transcript, usage = transcribe(render(path, page), model=model)
            record.update(
                status="processed" if transcript.coverage_complete else "unreadable",
                transcript=transcript.model_dump(),
                usage=usage,
            )
        except Exception as exc:  # noqa: BLE001 - persist a failed page without leaking provider bodies.
            # No API response body or credentials are copied into a report.
            record.update(status="failed", error_type=type(exc).__name__)
        record["elapsed_seconds"] = round(time.monotonic() - started, 3)
        saved.write_text(json.dumps(record, indent=2) + "\n")
        saved.chmod(0o600)
        print(f"{source['id']} page {page}: {record['status']}", flush=True)
        return record

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 3))) as pool:
        results = list(pool.map(read, pages))
    predictions = {
        "schema_version": "rivet.real-predictions.v1",
        "package_id": source.get("package_id", source["id"]),
        "source_hashes": {source["id"]: digest},
        "producer": {
            "name": "Rivet ingestion.vision.transcribe",
            "model": model or "configured_default",
        },
        "timings": {
            "wall_seconds_this_invocation": round(time.monotonic() - started_run, 3),
            "page_request_seconds_sum": sum(r["elapsed_seconds"] for r in results),
        },
        "comments": [],
        "pages": [],
        "raw_evidence_files": [f"{source['id']}-page-{p}.json" for p in pages],
    }
    for result in results:
        page = result["page"]
        predictions["pages"].append(
            {k: result[k] for k in ("source_id", "page", "status")}
        )
        transcript = result.get("transcript", {})
        for index, finding in enumerate(
            atomic_findings(transcript.get("comments", []))
        ):
            predictions["comments"].append(
                {
                    **finding,
                    "actionable": actionability(finding),
                    "id": f"vision-{page}-{index}",
                    "source_id": source["id"],
                    "page": page,
                    "origin": "vision",
                }
            )
        for index, finding in enumerate(transcript.get("unreadable_regions", [])):
            predictions["comments"].append(
                {
                    **finding,
                    "id": f"unreadable-{page}-{index}",
                    "source_id": source["id"],
                    "page": page,
                    "origin": "vision",
                    "unreadable": True,
                }
            )
    (output / "predictions.json").write_text(json.dumps(predictions, indent=2) + "\n")
    return predictions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument(
        "--pages", required=True, help="Original physical pages, e.g. 1,2,79 or 1-9"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    package = next(p for p in manifest["packages"] if p["id"] == args.package)
    if len(package["sources"]) != 1:
        raise ValueError(
            "Select a single-source package manifest for this page-reading command."
        )
    source = {**package["sources"][0], "package_id": package["id"]}
    source["path"] = str((args.manifest.parent / source["path"]).resolve())
    pages = []
    for token in args.pages.split(","):
        bounds = [int(v) for v in token.split("-")]
        pages.extend(range(bounds[0], bounds[-1] + 1))
    run(source, sorted(set(pages)), args.output, args.model, args.workers)


if __name__ == "__main__":
    main()
