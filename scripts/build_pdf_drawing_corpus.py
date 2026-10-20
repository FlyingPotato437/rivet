"""Download official public originals and build independently annotated PDF cases."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pymupdf

from backend.equipment.pdf_corpus import build_vector_pdf_corpus

SOURCES = [
    {
        "id": "mndot-generator-control",
        "publisher": "Minnesota Department of Transportation",
        "title": "Generator control / transfer switch connections",
        "url": "https://edocs-public.dot.state.mn.us/edocs_public/DMResultSet/download?docId=19625901",
        "index_url": "https://www.dot.state.mn.us/freeway-operations/its.html",
        "pages": [1],
        "subset": [235, 160, 900, 665],
        "ground_truth": {
            "expected_tags": [],
            "expected_ports": [],
            "expected_nets": [],
            "visible_assembly_labels": [
                "GENERATOR CONTROL CONNECTION PANEL",
                "TRANSFER SWITCH",
            ],
            "observations": [
                {
                    "local_id": "generator_panel",
                    "label": "GENERATOR CONTROL CONNECTION PANEL",
                    "bbox": [243, 172, 491, 478],
                    "terminals": ["E1", "E2"],
                },
                {
                    "local_id": "transfer_switch",
                    "label": "TRANSFER SWITCH",
                    "bbox": [767, 239, 866, 377],
                    "terminals": ["E1", "E2", "T1", "T2"],
                },
            ],
            "notes": "The sheet labels assemblies but provides no unique equipment tags. Curved cable/conduit graphics are not assumed to be orthogonal conductor nets. Terminal membership is a manually observed subset; no whole-sheet connectivity is established.",
        },
    },
    {
        "id": "mndot-ezpass-wiring",
        "publisher": "Minnesota Department of Transportation",
        "title": "E-ZPass equipment system wiring diagram",
        "url": "https://edocs-public.dot.state.mn.us/edocs_public/DMResultSet/download?docId=19625887",
        "index_url": "https://www.dot.state.mn.us/freeway-operations/its.html",
        "pages": [1],
        "subset": [360, 420, 620, 577],
        "ground_truth": {
            "expected_tags": [],
            "expected_ports": [],
            "expected_nets": [],
            "observations": [
                {
                    "label": "SSR",
                    "instances": 2,
                    "terminals": ["3/A1", "4/A2", "1/L1", "2/T1"],
                },
                {
                    "label": "TIME DELAY RELAY",
                    "instances": 2,
                    "terminals": ["A1", "S", "A2", "25", "26", "28", "15", "16", "18"],
                },
            ],
            "notes": "Repeated SSR and time-delay relay labels denote component types, not unique device IDs. This manually observed subset deliberately does not invent unique tags or treat cable-colour callouts as verified nets.",
        },
    },
    {
        "id": "mndot-ricws-sample",
        "publisher": "Minnesota Department of Transportation",
        "title": "RICWS sample plan - field wiring sheet 11 and quantity sheet 2",
        "url": "https://www.dot.state.mn.us/trafficeng/signals/plansheets/ricws2lane.PDF",
        "pages": [2, 11],
        "subset_page": 11,
        "subset": [300, 150, 820, 365],
        "ground_truth": {
            "expected_tags": [
                "D6-1",
                "D6-2",
                "D6-3",
                "D8-1",
                "D8-2",
                "D4-1",
                "D4-2",
                "D2-1",
                "D2-2",
                "F4-1",
                "F4-2",
                "F-6",
                "F8-1",
                "F8-2",
                "F-2",
            ],
            "expected_ports": [],
            "expected_nets": [],
            "observations": [
                {
                    "sheet": 11,
                    "kind": "field_cable_routing",
                    "examples": ["8-11,22", "12-13", "GR.RD."],
                }
            ],
            "notes": "Visible device labels are hand-transcribed from sheet 11. Routed lines include multiconductor cable bundles and handholes; they are not automatically equivalent to individual electrical nets. No internal equipment pin inventory is asserted.",
        },
    },
    {
        "id": "sel-121f-data-sheet",
        "publisher": "Schweitzer Engineering Laboratories",
        "title": "SEL-121F data sheet - Figure 10 typical DC connections",
        "url": "https://selinc.com/api/download/2836/",
        "pages": [18, 19],
        "subset_page": 19,
        "subset": [100, 405, 535, 650],
        "ground_truth": {
            "expected_tags": ["52T1", "52T2", "52C"],
            "expected_ports": [
                "FIG10:13",
                "FIG10:15",
                "FIG10:17",
                "FIG10:27",
                "FIG10:37",
                "FIG10:47",
            ],
            "unassigned_terminal_scope": {
                "namespace": "FIG10",
                "labels": ["13", "15", "17", "27", "37", "47", "39", "41", "43", "45"],
            },
            "evaluated_terminal_ids": [
                "FIG10:13",
                "FIG10:15",
                "FIG10:17",
                "FIG10:27",
                "FIG10:37",
                "FIG10:47",
                "FIG10:39",
                "FIG10:41",
                "FIG10:43",
                "FIG10:45",
            ],
            "expected_nets": [
                [
                    "FIG10:13",
                    "FIG10:15",
                    "FIG10:17",
                    "FIG10:27",
                    "FIG10:37",
                    "FIG10:47",
                ]
            ],
            "observations": [
                {
                    "figure": 10,
                    "label": "SEL-121F (partial)",
                    "positive_rail_y_pt": 420,
                    "direct_terminals": [13, 15, 47, 17, 27, 37],
                }
            ],
            "notes": "The six named upper terminals visibly join the same upper supply rail. FIG10 is only a local figure locator for visually unique terminal labels; no relay product label is promoted to an enterprise equipment tag. Coil/contact semantics, other DC circuits and the AC figure on the same page remain unannotated.",
        },
    },
    {
        "id": "sel-251-data-sheet",
        "publisher": "Schweitzer Engineering Laboratories",
        "title": "SEL-251 data sheet - Figure 6 typical DC external connections",
        "url": "https://selinc.com/api/download/3067",
        "pages": [21],
        "subset": [105, 100, 540, 345],
        "ground_truth": {
            "expected_tags": ["52T1", "52T2", "52C"],
            "expected_ports": [],
            "expected_nets": [],
            "notes": "The diagram's small labels are rendered as vector outlines rather than selectable text. Labels were visually observed; the page must remain unverified until visual/OCR evidence establishes their identities. The surrounding prose is selectable but does not establish diagram coverage.",
        },
    },
]


def build(root, *, download=False):
    root = Path(root)
    originals, evidence = root / "originals", root / "evidence"
    originals.mkdir(parents=True, exist_ok=True)
    evidence.mkdir(parents=True, exist_ok=True)
    records = []
    for source in SOURCES:
        path = originals / (source["id"] + ".pdf")
        if download and not path.is_file():
            import requests

            response = requests.get(source["url"], timeout=45)
            response.raise_for_status()
            if not response.content.startswith(b"%PDF"):
                raise ValueError(f"{source['id']} did not return a PDF original")
            path.write_bytes(response.content)
        if not path.is_file():
            raise FileNotFoundError(
                f"Download the official original first: {source['url']}"
            )
        item = {
            **source,
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "retrieved_at": "2026-10-18",
            "fictional": False,
            "rights": "Publicly available official documentation. Publisher copyright retained; no open redistribution licence inferred. Local research/evaluation only; not customer data.",
            "ground_truth": {
                "annotation_method": "Independent visual inspection of original pages, before adapter execution.",
                "scope": "partial_subset",
                "unknown_required": True,
                "physical_dimensions_verified": False,
                **source["ground_truth"],
            },
        }
        with pymupdf.open(path) as pdf:
            item["page_count"] = len(pdf)
            item["page_statistics"] = []
            for page_number in source["pages"]:
                page = pdf[page_number - 1]
                item["page_statistics"].append(
                    {
                        "page": page_number,
                        "width_pt": page.rect.width,
                        "height_pt": page.rect.height,
                        "text_characters": len(page.get_text()),
                        "vector_paths": len(page.get_drawings()),
                        "raster_images": len(page.get_images()),
                    }
                )
                image = evidence / f"{source['id']}-page-{page_number}.png"
                page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).save(image)
            page_number = source.get("subset_page", source["pages"][0])
            item["ground_truth"]["annotation_page"] = page_number
            if source["id"] != "mndot-ricws-sample":
                item["ground_truth"]["evaluation_bbox"] = source["subset"]
            crop = evidence / f"{source['id']}-subset.png"
            pdf[page_number - 1].get_pixmap(
                matrix=pymupdf.Matrix(3, 3), clip=pymupdf.Rect(source["subset"])
            ).save(crop)
            item["evidence_crop"] = {
                "path": crop.relative_to(root).as_posix(),
                "page": page_number,
                "bbox": source["subset"],
                "coordinate_system": "visible-page-points",
            }
        records.append(item)
    independent = build_vector_pdf_corpus(root / "independent")
    result = {
        "schema_version": "rivet.public.pdf.corpus.v1",
        "sources": records,
        "independent_fixture_manifest": "independent/manifest.json",
        "independent_fixture_count": len(independent["fixtures"]),
        "limitations": [
            "A public illustrative drawing is not a real customer return or a fabrication instruction.",
            "Only hand-annotated subsets have ground truth; unannotated sheet regions remain unknown.",
            "No reading accuracy or complete electrical topology is claimed from text availability alone.",
        ],
    }
    (root / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="output/public-drawing-corpus")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    result = build(args.root, download=args.download)
    print(
        json.dumps(
            {
                "official_sources": len(result["sources"]),
                "independent_fixtures": result["independent_fixture_count"],
            }
        )
    )
