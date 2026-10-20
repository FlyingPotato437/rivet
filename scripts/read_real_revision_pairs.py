"""Run source-hash-bound real revision pairs without feeding answer keys to readers."""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.equipment.schemas import SourceInput
from backend.equipment.visual_revision import compare_revision_sources


def run(manifest_path, package_id, output, *, model=None, vision=True):
    manifest_path = Path(manifest_path).resolve()
    package = next(
        p
        for p in json.loads(manifest_path.read_text())["packages"]
        if p["id"] == package_id
    )
    sources = {}
    for raw in package["sources"]:
        sources[raw["id"]] = SourceInput(
            **{k: raw[k] for k in ("id", "name", "role", "revision", "sha256")},
            path=str((manifest_path.parent / raw["path"]).resolve()),
        )
    reports = []
    for index, pair in enumerate(package.get("revision_pairs", [])):
        report = compare_revision_sources(
            sources[pair["baseline_source_id"]],
            sources[pair["target_source_id"]],
            Path(output) / f"pair-{index + 1}",
            model=model,
            vision=vision,
        )
        reports.append(report)
    predictions = {
        "schema_version": "rivet.real-predictions.v1",
        "package_id": package_id,
        "source_hashes": {s.id: s.sha256 for s in sources.values()},
        "revision_checks": [c for r in reports for c in r["revision_checks"]],
        "producer": {
            "name": "Rivet visual_revision",
            "model": model,
            "engineering_verified": False,
        },
        "timings": {"revision_seconds": sum(r["elapsed_seconds"] for r in reports)},
    }
    Path(output).mkdir(parents=True, exist_ok=True)
    (Path(output) / "predictions.json").write_text(
        json.dumps(predictions, indent=2) + "\n"
    )
    return reports


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--no-vision", action="store_true")
    args = parser.parse_args()
    reports = run(
        args.manifest,
        args.package,
        args.output,
        model=args.model,
        vision=not args.no_vision,
    )
    print(
        json.dumps(
            {
                "pairs": len(reports),
                "observed_structural_changes": sum(
                    len(r["revision_checks"]) for r in reports
                ),
                "errors": [e for r in reports for e in r["errors"]],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
