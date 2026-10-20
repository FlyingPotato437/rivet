"""Independent visible-ink PDF fixtures and explicit, external ground truth.

No PDF contains equipment graph metadata, attachments or adapter predictions.
Coordinates below are manually authored visible-page points, not millimetres.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
from reportlab.pdfgen import canvas

WIDTH, HEIGHT = 700, 550


def _device(tag, box, ports):
    return {"tag": tag, "bbox": list(box), "ports": ports}


def series():
    return {
        "devices": [
            _device("TB-1", [70, 170, 170, 290], {"1": [170, 200], "2": [170, 260]}),
            _device("KA-1", [410, 170, 530, 290], {"A1": [410, 200], "A2": [410, 260]}),
        ],
        "wires": [[[170, 200], [410, 200]], [[170, 260], [410, 260]]],
        "junctions": [],
        "nets": [["TB-1:1", "KA-1:A1"], ["TB-1:2", "KA-1:A2"]],
    }


def crossing(dot=False):
    return {
        "devices": [
            _device("TB-1", [60, 225, 150, 285], {"1": [150, 255]}),
            _device("CB-12", [550, 225, 640, 285], {"TC1": [550, 255]}),
            _device("KA-1", [305, 80, 395, 140], {"13": [350, 140]}),
            _device("PL-1", [305, 370, 395, 430], {"X1": [350, 370]}),
        ],
        "wires": [[[150, 255], [550, 255]], [[350, 140], [350, 370]]],
        "junctions": [[350, 255]] if dot else [],
        "nets": [["TB-1:1", "CB-12:TC1", "KA-1:13", "PL-1:X1"]]
        if dot
        else [["TB-1:1", "CB-12:TC1"], ["KA-1:13", "PL-1:X1"]],
    }


def branch(dot=True):
    return {
        "devices": [
            _device("TB-1", [60, 190, 150, 250], {"1": [150, 220]}),
            _device("KA-1", [550, 190, 640, 250], {"A1": [550, 220]}),
            _device("PL-1", [305, 370, 395, 430], {"X1": [350, 370]}),
        ],
        "wires": [[[150, 220], [550, 220]], [[350, 220], [350, 370]]],
        "junctions": [[350, 220]] if dot else [],
        "nets": [["TB-1:1", "KA-1:A1", "PL-1:X1"]] if dot else [["TB-1:1", "KA-1:A1"]],
    }


def _text(c, text, x, y, size=10, centered=False):
    c.setFont("Helvetica", size)
    (c.drawCentredString if centered else c.drawString)(x, HEIGHT - y, text)


def write_visible_pdf(
    path, drawing, title, *, hidden_port=None, duplicate_labels=False
):
    """Draw conventional boxes/contacts as ordinary selectable text and vectors."""
    c = canvas.Canvas(str(path), pagesize=(WIDTH, HEIGHT), invariant=1)
    c.setTitle("Fictional electrical control drawing - visible ink evaluation")
    c.setAuthor("Rivet independent test corpus")
    c.setLineWidth(0.8)
    _text(c, "RIVET / FICTIONAL SWITCHBOARD WORKS", 32, 31, 13)
    _text(c, title, 32, 51, 10)
    for points in drawing["wires"]:
        p = c.beginPath()
        p.moveTo(points[0][0], HEIGHT - points[0][1])
        for x, y in points[1:]:
            p.lineTo(x, HEIGHT - y)
        c.drawPath(p)
    for x, y in drawing["junctions"]:
        c.circle(x, HEIGHT - y, 3.2, stroke=1, fill=1)
    for device in drawing["devices"]:
        x0, y0, x1, y1 = device["bbox"]
        c.rect(x0, HEIGHT - y1, x1 - x0, y1 - y0)
        _text(c, device["tag"], (x0 + x1) / 2, (y0 + y1) / 2 + 4, 11, True)
        for label, (x, y) in device["ports"].items():
            c.circle(x, HEIGHT - y, 2.4, stroke=1, fill=0)
            if hidden_port == device["tag"] + ":" + label:
                continue
            printed = "1" if duplicate_labels and device["tag"] == "TB-1" else label
            if x == x0:
                _text(c, printed, x + 9, y + 3, 9)
            elif x == x1:
                c.setFont("Helvetica", 9)
                c.drawRightString(x - 9, HEIGHT - y - 3, printed)
            elif y == y0:
                _text(c, printed, x, y + 14, 9, True)
            else:
                _text(c, printed, x, y - 9, 9, True)
    c.line(30, HEIGHT - 485, WIDTH - 30, HEIGHT - 485)
    _text(c, "FICTIONAL / NOT FOR FABRICATION", 32, 503, 9)
    _text(c, "CONTROL SCHEMATIC  /  E-31  /  REV C  /  SCALE NTS", 32, 520, 9)
    _text(
        c,
        "Circles: terminals. Filled dots: junctions. Undotted crossings: separate conductors.",
        32,
        538,
        8,
    )
    c.save()


def _truth(drawing, *, unknown=False, hidden_port=None, duplicate_labels=False):
    ports = [
        f"{device['tag']}:{label}"
        for device in drawing["devices"]
        for label in device["ports"]
        if hidden_port != f"{device['tag']}:{label}"
        and not (duplicate_labels and device["tag"] == "TB-1")
    ]
    return {
        "annotation_method": "Manually authored circuit intent; never copied from adapter output.",
        "scope": "complete_defined_circuit",
        "expected_tags": sorted(device["tag"] for device in drawing["devices"]),
        "expected_ports": sorted(ports),
        "expected_nets": drawing["nets"],
        "expected_devices": drawing["devices"],
        "unknown_required": unknown,
        "physical_dimensions_verified": False,
    }


def build_vector_pdf_corpus(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fixtures = []

    def add(
        name, drawing, title, *, unknown=False, hidden_port=None, duplicate_labels=False
    ):
        path = root / (name + ".pdf")
        write_visible_pdf(
            path,
            drawing,
            title,
            hidden_port=hidden_port,
            duplicate_labels=duplicate_labels,
        )
        truth = _truth(
            drawing,
            unknown=unknown,
            hidden_port=hidden_port,
            duplicate_labels=duplicate_labels,
        )
        if hidden_port:
            truth["expected_nets"] = [
                net for net in drawing["nets"] if hidden_port not in net
            ]
        if duplicate_labels:
            truth["expected_nets"] = []
        if name == "05-ambiguous-undotted-branch":
            truth["ambiguous_pairs"] = [["PL-1:X1", "TB-1:1"], ["PL-1:X1", "KA-1:A1"]]
        fixtures.append(
            {"id": name, "path": path.name, "fictional": True, "ground_truth": truth}
        )
        return path

    original = add(
        "01-parallel-control-wires", series(), "Two independent control circuits"
    )
    add("02-crossing-without-junction", crossing(), "Crossing conductors / no junction")
    add(
        "03-crossing-with-junction",
        crossing(True),
        "Crossing conductors / explicit junction",
    )
    add(
        "04-three-terminal-branch",
        branch(),
        "Three terminals on one explicit branched net",
    )
    add(
        "05-ambiguous-undotted-branch",
        branch(False),
        "An undotted endpoint touching a conductor needs confirmation",
        unknown=True,
    )
    add(
        "06-missing-terminal-label",
        series(),
        "A physical contact has no readable terminal identity",
        unknown=True,
        hidden_port="KA-1:A2",
    )
    add(
        "07-duplicate-terminal-labels",
        series(),
        "Two contacts have the same printed terminal label",
        unknown=True,
        duplicate_labels=True,
    )

    rotated = root / "08-rotated-page.pdf"
    with pymupdf.open(original) as pdf:
        pdf[0].set_rotation(90)
        pdf.save(rotated)
    rotated_truth = _truth(series())
    rotated_truth["expected_devices"] = []
    rotated_truth["rotation_degrees"] = 90
    fixtures.append(
        {
            "id": "08-rotated-page",
            "path": rotated.name,
            "fictional": True,
            "ground_truth": rotated_truth,
        }
    )

    cropped = root / "09-cropped-page.pdf"
    with pymupdf.open(original) as pdf:
        pdf[0].set_cropbox(pymupdf.Rect(40, 140, 570, 320))
        pdf.save(cropped)
    cropped_truth = _truth(series())
    cropped_truth["expected_devices"] = []
    cropped_truth["cropbox_original_points"] = [40, 140, 570, 320]
    fixtures.append(
        {
            "id": "09-cropped-page",
            "path": cropped.name,
            "fictional": True,
            "ground_truth": cropped_truth,
        }
    )

    # A real CAD export path, independent of the PDF extraction adapter.
    # The native source graph is hand-authored in the earlier acceptance corpus.
    import ezdxf
    from ezdxf.addons.drawing import Frontend, RenderContext, config, layout
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend

    from .native_demo import baseline_graph, write_native

    native = root / "10-independent-cad-source.dxf"
    cad_pdf = root / "10-cad-exported-control-panel.pdf"
    graph = baseline_graph()
    write_native(native, graph, "C")
    drawing = ezdxf.readfile(native)
    backend = PyMuPdfBackend()
    Frontend(
        RenderContext(drawing),
        backend,
        config=config.Configuration(background_policy=config.BackgroundPolicy.WHITE),
    ).draw_layout(drawing.modelspace(), finalize=True)
    cad_pdf.write_bytes(backend.get_pdf_bytes(layout.Page(420, 297, layout.Units.mm)))
    net_groups = {}
    for wire in graph["wires"].values():
        net_groups.setdefault(wire["net_id"], set()).update(
            ":".join(wire[end]) for end in ("from", "to")
        )
    fixtures.append(
        {
            "id": "10-cad-exported-control-panel",
            "path": cad_pdf.name,
            "fictional": True,
            "native_source": native.name,
            "ground_truth": {
                "annotation_method": "Independent hand-authored native fixture exported by ezdxf's CAD renderer; not the PDF adapter.",
                "scope": "complete_defined_circuit",
                "expected_tags": sorted(graph["devices"]),
                "expected_ports": sorted(
                    f"{tag}:{label}"
                    for tag, device in graph["devices"].items()
                    for label in device["terminals"]
                ),
                "expected_nets": [sorted(values) for values in net_groups.values()],
                "unknown_required": True,
                "physical_dimensions_verified": False,
                "notes": "CAD-rendered stroke-font labels may require OCR. No native XDATA is present in the PDF.",
            },
        }
    )
    for fixture in fixtures:
        path = root / fixture["path"]
        fixture["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with pymupdf.open(path) as pdf:
            fixture["pages"] = [
                {
                    "page": i + 1,
                    "width_pt": page.rect.width,
                    "height_pt": page.rect.height,
                    "text_characters": len(page.get_text()),
                    "vector_paths": len(page.get_drawings()),
                    "raster_images": len(page.get_images()),
                }
                for i, page in enumerate(pdf)
            ]
    manifest = {"schema_version": "rivet.pdf.graph.fixtures.v1", "fixtures": fixtures}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
