"""Run a source-bound physical CAD trial without models, uploads or release."""

import argparse
import json
from pathlib import Path

from backend.engineering.physical_move import build_trial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    case = json.loads(args.case.read_text())
    result = build_trial(
        case["native_path"], case["plan"], case["sources"], args.output
    )
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "status",
                    "write",
                    "saved_geometry",
                    "layout",
                    "annotation_check",
                    "bom",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
