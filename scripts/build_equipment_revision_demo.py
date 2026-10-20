"""Create realistic fictional equipment packages without changing live orders."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.equipment.demo_documents import build_demo_corpus


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("output/equipment-revision-demo")
    )
    args = parser.parse_args()
    manifest = build_demo_corpus(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "sources": len(manifest["sources"]),
                "cases": len(manifest["cases"]),
                "fictional": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
