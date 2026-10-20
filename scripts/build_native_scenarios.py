"""Build labelled fictional native drawing/BOM acceptance scenarios."""

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.equipment.native_demo import build_native_corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("output/native-revision-scenarios")
    )
    args = parser.parse_args()
    manifest = build_native_corpus(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "fictional": True,
                "cases": len(manifest["cases"]),
                "sources": len(manifest["sources"]),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
