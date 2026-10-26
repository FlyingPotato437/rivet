"""Find bounded engineering review placements; never manufacture a passing option."""

import argparse
import json
from pathlib import Path

from backend.engineering.physical_placement import build_search


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    case = json.loads(args.case.read_text())
    report = build_search(
        case["native_path"], case["plan"], case["sources"], args.output
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("status", "views", "ranked_candidates", "saved_candidates")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
