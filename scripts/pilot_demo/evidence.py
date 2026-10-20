"""Export actual revision overlays and source crops; never edit the drawing image."""

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.equipment.visual_revision import compare_revision_sources


def main(run):
    root = ROOT / f"output/pilot-demo-20261019/rehearsal-{run}"
    package = root / "package"
    destination = root / "revision-evidence"
    destination.mkdir(exist_ok=True)

    def source(rev):
        p = package / f"PDU-75_Rev-{rev}.pdf"
        return {
            "id": p.name,
            "name": p.name,
            "path": str(p),
            "role": "drawing",
            "revision": rev,
            "sha256": sha256(p.read_bytes()).hexdigest(),
        }

    report = compare_revision_sources(
        source("A"), source("B"), destination, vision=False
    )
    (destination / "visual-comparison.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n"
    )
    # Locate the missed field from the real printed device tag, not an invented
    # after image. Both crops must still show SPARE in the source PDFs.
    crops = []
    for rev in ["A", "B"]:
        p = package / f"PDU-75_Rev-{rev}.pdf"
        with pymupdf.open(p) as d:
            page = d[2]
            tag = page.search_for("CB-13")
            if len(tag) != 1:
                raise ValueError("Expected one nameplate-sheet CB-13 anchor")
            box = tag[0] + (-18, -15, 480, 65)
            text = page.get_textbox(box)
            if "SPARE" not in text:
                raise ValueError("Source no longer demonstrates the missed fix")
            file = destination / f"missed-CB-13-Rev-{rev}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=box).save(file)
            crops.append(
                {
                    "revision": rev,
                    "source_sha256": sha256(p.read_bytes()).hexdigest(),
                    "page": 3,
                    "bbox": list(box),
                    "observed_text": text,
                    "crop": str(file),
                }
            )
    summary = {
        "fictional": True,
        "method": "Printed tag location + actual source crops; no model call",
        "missed_change": {
            "device_tag": "CB-13",
            "field": "nameplate",
            "required": "COMMISSIONING TOOLS",
            "before": "SPARE",
            "after": "SPARE",
            "result": "missed in Rev B",
        },
        "crops": crops,
        "limitations": "Visual ink differences locate changes; the independent native DXF/BOM check establishes the supported field values. No engineering approval or geometry correction is claimed.",
    }
    (destination / "missed-change.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(
        json.dumps(
            {"output": str(destination), "source_crops": len(crops), "model_calls": 0}
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=int, required=True)
    main(p.parse_args().run)
