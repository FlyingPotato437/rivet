"""Create a bounded, private, read-only ASCII DXF locator snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.engineering.cad_snapshot import (
    DEFAULT_LIMITS,
    SnapshotError,
    write_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new directory; must not already exist",
    )
    parser.add_argument(
        "--max-seconds", type=float, default=DEFAULT_LIMITS["max_seconds"]
    )
    parser.add_argument(
        "--max-supported-records",
        type=int,
        default=DEFAULT_LIMITS["max_supported_records"],
    )
    args = parser.parse_args()
    try:
        output = write_snapshot(
            args.source,
            args.output_dir,
            {
                "max_seconds": args.max_seconds,
                "max_supported_records": args.max_supported_records,
            },
        )
    except (SnapshotError, FileExistsError, OSError) as exc:
        parser.error(str(exc))
    data = json.loads(output.read_text(encoding="utf-8"))
    summary = {
        k: data[k]
        for k in (
            "source_sha256",
            "source_bytes",
            "elapsed_seconds",
            "header",
            "counts_by_scope_and_type",
            "indexed_blocks",
            "indexed_supported_records",
            "omissions",
            "warnings",
            "coverage_complete",
        )
    }
    summary["located_record_counts"] = {
        "text": len(data["placed_text"]),
        "line": len(data["placed_lines"]),
        "insert": len(data["placed_inserts"]),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"snapshot: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
