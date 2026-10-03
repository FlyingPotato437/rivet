"""Regenerate the tiny fictional, text-layer PDF replay fixtures without dependencies."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "examples" / "order-replay"

SPEC = {
    "rated_voltage_v": "480 V",
    "frequency_hz": "60 Hz",
    "phases": "3",
    "wires": "4",
    "main_bus_a": "3200 A",
    "neutral_bus_percent": "100%",
    "short_circuit_ka": "65 kA",
    "short_time_ka": "65 kA",
    "short_time_duration_s": "0.5 s",
    "main_breaker_frame_a": "3200 A",
    "main_breaker_trip_a": "3000 A",
    "main_breaker_poles": "3",
    "main_breaker_interrupting_ka": "100 kA",
    "feeder_breaker_frame_a": "800 A",
    "feeder_breaker_trip_a": "600 A",
    "enclosure_type": "NEMA 3R",
    "bus_material": "copper",
    "bus_plating": "silver",
    "cable_entry": "top",
    "control_voltage_v": "120 V",
    "metering": "multifunction",
    "communications_protocol": "Modbus TCP",
    "surge_protection": "yes",
    "ground_fault_protection": "yes",
    "arc_resistant": "no",
    "seismic_qualification": "Project-specific review required",
    "enclosure_width_mm": "2400 mm",
    "enclosure_height_mm": "2200 mm",
    "enclosure_depth_mm": "1000 mm",
    "quantity": "1",
    "manufacturer": "Fictional Aster Equipment",
    "catalog_number": "ASTER-LV-3200-DEMO",
    "nameplate_text": "MSB-01 / 480 V / 85 kA",
    "lead_time_weeks": "28 weeks",
}
REV_B = {
    **SPEC,
    "bus_material": "aluminum",
    "main_breaker_trip_a": "2500 A",
    "enclosure_type": "NEMA 1",
    "communications_protocol": "Modbus RTU",
    "cable_entry": "bottom",
    "enclosure_width_mm": "2200 mm",
    "nameplate_text": "MSB-01 / 480 V / 65 kA",
}
REV_C = {
    **SPEC,
    "bus_material": "aluminum",
}  # SCCR remains 65 despite the drafter's claim.
FIXED = [
    "main_breaker_trip_a",
    "enclosure_type",
    "communications_protocol",
    "cable_entry",
    "enclosure_width_mm",
    "nameplate_text",
]


def _escape(text):
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .encode("latin-1", errors="replace")
    )


def write_pdf(path, title, lines, annotations=None):
    """Valid PDF 1.4 with selectable Helvetica text and real annotation objects."""
    annotations = annotations or []
    per_page = 35
    chunks = [
        lines[index : index + per_page] for index in range(0, len(lines), per_page)
    ] or [[]]
    objects = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    page_refs = []
    for page_number, chunk in enumerate(chunks, 1):
        page_ref = len(objects) + 1
        objects.extend([b"", b""])
        stream_ref = page_ref + 1
        page_refs.append(page_ref)
        page_annots = []
        for annotation in annotations:
            if annotation.get("page", 1) != page_number:
                continue
            ref = len(objects) + 1
            page_annots.append(ref)
            rect = annotation.get("rect", [350, 92, 560, 165])
            kind = annotation.get("kind", "FreeText")
            additional = (
                b" /DA (/Helvetica 9 Tf 0.5 0.1 0.5 rg)"
                if kind == "FreeText"
                else b" /Vertices [350 92 560 92 560 165 350 165] /BE << /S /C /I 1 >>"
            )
            objects.append(
                b"<< /Type /Annot /Subtype /"
                + kind.encode()
                + b" /Rect ["
                + " ".join(map(str, rect)).encode()
                + b"] /Contents ("
                + _escape(annotation.get("text", ""))
                + b") /NM ("
                + _escape(annotation["id"])
                + b") /T ("
                + _escape(annotation.get("author", ""))
                + b") /M ("
                + _escape(annotation.get("date", ""))
                + b") /C [0.55 0.28 0.85] /F 4"
                + additional
                + b" >>"
            )
        stream = (
            b"BT /F1 17 Tf 42 795 Td ("
            + _escape(title)
            + b") Tj /F1 8 Tf 0 -22 Td (FICTIONAL REPLAY - not a design certification or commercial offer) Tj ET\n"
        )
        for index, line in enumerate(chunk):
            stream += (
                b"BT /F1 9 Tf 42 "
                + str(747 - index * 18).encode()
                + b" Td ("
                + _escape(line)
                + b") Tj ET\n"
            )
        stream += (
            b"BT /F1 8 Tf 42 30 Td (Rivet replay / page "
            + str(page_number).encode()
            + b") Tj ET\n"
        )
        objects[stream_ref - 1] = (
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"endstream"
        )
        annot_refs = (
            b" /Annots ["
            + b" ".join(f"{ref} 0 R".encode() for ref in page_annots)
            + b"]"
            if page_annots
            else b""
        )
        objects[page_ref - 1] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 3 0 R >> >> /Contents "
            + str(stream_ref).encode()
            + b" 0 R"
            + annot_refs
            + b" >>"
        )
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = (
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{ref} 0 R".encode() for ref in page_refs)
        + b"] /Count "
        + str(len(page_refs)).encode()
        + b" >>"
    )
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + item + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(output)


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    cases = []

    def document(filename, title, role, lines, revision=None, annotations=None):
        header = [f"Document role: {role}"] + (
            [f"Revision: {revision}"] if revision else []
        )
        write_pdf(ROOT / filename, title, header + lines, annotations)
        cases.append({"file": filename, "role": role, "revision_label": revision})

    document(
        "01-project-specification.pdf",
        "Larkspur DC-04 / switchgear specification",
        "specification",
        [
            "Device: MSB-01",
            "Alias: MSB Main -> MSB-01",
            "Alias: 52-M1 -> MSB-01",
            "Alias: BOM line 3 -> MSB-01",
            *[f"MSB-01 | {key}: {value}" for key, value in SPEC.items()],
        ],
    )
    document(
        "02-customer-purchase-order.pdf",
        "Larkspur DC-04 / customer purchase order",
        "purchase_order",
        [
            "Order: DEMO-4512",
            "MSB-01 | short_circuit_ka: 85 kA",
            "PO clarification: assembly SCCR required at 480 V.",
        ],
    )
    document(
        "03-accepted-exception.pdf",
        "Larkspur DC-04 / accepted exception E-01",
        "accepted_exception",
        [
            "Fictional customer acceptance: E-01, recorded 2026-09-20.",
            "MSB Main | bus_material: aluminum",
            "Copper bus requested in the spec is superseded by this accepted exception.",
        ],
    )
    document(
        "04-approval-drawing-Rev-B.pdf",
        "Larkspur DC-04 / approval drawing",
        "drawing",
        [*[f"52-M1 | {key}: {value}" for key, value in REV_B.items()]],
        "B",
    )
    comments = [
        "Comment 1 | MSB-01 | main_breaker_trip_a: 3000 A | Revise the main trip setting.",
        "Comment 2 | MSB-01 | enclosure_type: NEMA 3R | Show the specified enclosure.",
        "Comment 3 | MSB-01 | communications_protocol: Modbus TCP | Match the controls schedule.",
        "Comment 4 | MSB-01 | cable_entry: top | Coordinate top-entry cables.",
        "Comment 5 | MSB-01 | enclosure_width_mm: 2400 mm | Correct the overall width.",
        "Comment 6 | MSB-01 | nameplate_text: MSB-01 / 480 V / 85 kA | Correct the label.",
        "Comment 8 | MSB-01 | bus_material: copper | Provide copper per specification.",
        "Comment 9 | MSB-01 | metering: multifunction | Confirm the meter type shown.",
        "Comment 10 | MSB-01 | bus_plating: silver | Confirm bus plating.",
        "Comment 11 | MSB-01 | Confirm lifting-eye clearance; detail is unclear.",
    ]
    document(
        "05-customer-markups-Rev-B.pdf",
        "Larkspur DC-04 / customer engineer markups",
        "markup",
        comments,
        "B",
        [
            {
                "id": "review-comment-7",
                "kind": "FreeText",
                "rect": [80, 180, 530, 250],
                "text": "Comment 7 | MSB-01 | short_circuit_ka: 85 kA | Match the purchase order rating.",
            },
            {
                "id": "cloud-unlabelled-clearance",
                "kind": "Polygon",
                "rect": [350, 92, 560, 165],
                "text": "",
            },
        ],
    )
    document(
        "04-approval-drawing-Rev-C.pdf",
        "Larkspur DC-04 / approval drawing",
        "drawing",
        [
            *[f"MSB-01 | {key}: {value}" for key, value in REV_C.items()],
            "Drafter response to comment 7: Fixed in Rev C.",
        ],
        "C",
    )
    document(
        "07-bom-Rev-B.pdf",
        "Larkspur DC-04 / bill of materials",
        "bom",
        [
            "BOM line 3 | main_breaker_trip_a: 2500 A",
            "BOM line 3 | catalog_number: ASTER-LV-3200-DEMO",
            "BOM line 3 | quantity: 1",
        ],
        "B",
    )
    document(
        "08-supplier-PO-Rev-B.pdf",
        "Larkspur DC-04 / breaker supplier PO",
        "supplier_po",
        [
            "MSB-01 | main_breaker_trip_a: 2500 A",
            "MSB-01 | main_breaker_frame_a: 3200 A",
        ],
        "B",
    )
    document(
        "09-nameplate-Rev-B.pdf",
        "Larkspur DC-04 / nameplate schedule",
        "nameplate",
        ["MSB-01 | nameplate_text: MSB-01 / 480 V / 65 kA"],
        "B",
    )
    expectations = {
        "title": "Larkspur DC-04 · switchgear replay",
        "customer": "Fictional Larkspur Data Centers",
        "number": "DEMO-4512",
        "synthetic": True,
        "category": "Low-voltage switchgear",
        "attribute_count": len(SPEC),
        "device": "MSB-01",
        "documents": cases,
        "precedence": [
            {
                "attribute": "short_circuit_ka",
                "effective": 85,
                "source_role": "purchase_order",
                "overridden": 65,
            },
            {
                "attribute": "bus_material",
                "effective": "aluminum",
                "source_role": "accepted_exception",
                "overridden": "copper",
            },
        ],
        "rev_b_failed_attributes": [*FIXED, "short_circuit_ka"],
        "rev_c_resolved_attributes": FIXED,
        "rev_c_still_failed_attributes": ["short_circuit_ka"],
        "claimed_fixed_but_unchanged": {
            "comment": "7",
            "attribute": "short_circuit_ka",
            "before": 65,
            "after": 65,
            "required": 85,
        },
        "comment_count": 11,
        "conflicts_with_exception": ["8"],
        "already_supported_comments": ["9", "10"],
        "manual_review_comments": ["11"],
        "unlabelled_annotations": ["cloud-unlabelled-clearance"],
        "propagation_targets": ["bom", "supplier_po", "nameplate"],
        "release": "Blocked: short-circuit rating, exception decision, and unresolved review evidence require disposition.",
    }
    (ROOT / "expectations.json").write_text(json.dumps(expectations, indent=2) + "\n")


if __name__ == "__main__":
    main()
