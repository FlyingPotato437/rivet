"""Read real drawing/schedule pages with hash-bound visual equipment evidence."""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.equipment.schemas import SourceInput
from backend.equipment.visual_reader import read_visual_equipment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--pages", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--max-tiles", type=int, default=60)
    args = parser.parse_args()
    load_dotenv()
    manifest = json.loads(args.manifest.read_text())
    package = next(p for p in manifest["packages"] if p["id"] == args.package)
    raw = next(s for s in package["sources"] if s["id"] == args.source)
    source = SourceInput(
        **{k: raw[k] for k in ("id", "name", "role", "revision", "sha256")},
        path=str((args.manifest.parent / raw["path"]).resolve()),
    )
    pages = []
    for token in args.pages.split(","):
        bounds = [int(v) for v in token.split("-")]
        pages.extend(range(bounds[0], bounds[-1] + 1))
    result = read_visual_equipment(
        source,
        pages,
        args.output,
        model=args.model,
        max_pages=args.max_pages,
        max_tiles=args.max_tiles,
    )
    result["package_id"] = package["id"]
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "predictions.json"
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    target.chmod(0o600)
    print(
        json.dumps(
            {
                "status": result["status"],
                "pages": len(result["pages"]),
                "rows": len(result["observed_bom_rows"]),
                "devices": len(result["devices"]),
                "timings": result["timings"],
                "predictions": str(target),
            }
        )
    )


if __name__ == "__main__":
    main()
