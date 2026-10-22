"""Public-reference-informed fictional inputs and views of saved native files."""

from __future__ import annotations

from copy import deepcopy
from html import escape
from pathlib import Path

import ezdxf
import pymupdf
from ezdxf.addons.drawing import Frontend, RenderContext, config, layout
from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from backend.equipment.native_model import set_xdata

from .pdu_phase import (
    BOM_FIELDS,
    CONVENTION,
    add_device,
    catalog_attributes,
    connections,
    write_connections,
    write_csv,
)

SOURCES = {
    "R6": {
        "title": "Beyond Pixels: A Vector-to-Graph Framework for Reliable Schematic Auditing (ICASSP 2026)",
        "url": "https://arxiv.org/abs/2602.11678",
        "locator": "abstract and approach",
        "use": "Research reference for explicit connectivity graphs and independent auditing; independently implemented, no author code or claimed benchmark result incorporated. Code: https://github.com/gm-embodied/V2G-Audit",
    },
    "R1": {
        "title": "Larson 30 kVA single-phase portable transformer",
        "url": "https://www.larsonelectronics.com/images/product/Specsheet/302641.PDF",
        "locator": "printed pages 1-2",
        "use": "30 kVA, 480 V single-phase input, 120/240 V output, 60 Hz; rating reference only",
    },
    "R2": {
        "title": "Larson 30 kVA three-phase portable power distribution",
        "url": "https://www.larsonelectronics.com/product/299300/30-kva-portable-power-distro-480v-208y-120v-3ph-1-ar641-1-ar341-25-6-4-soow-line-in-w-npj6485",
        "locator": "Specifications / Additional Information",
        "use": "30 kVA, 480 V delta to 208Y/120 V three-phase, 60 Hz; rating reference only",
    },
    "R3": {
        "title": "Schneider transformer kVA and current calculations",
        "url": "https://www.se.com/us/en/faqs/FA101694/",
        "locator": "single-phase and three-phase equations",
        "use": "Full-load arithmetic; not protection sizing or design approval",
    },
    "R4": {
        "title": "Schneider single-phase source limitation",
        "url": "https://www.se.com/us/en/faqs/FA91854/",
        "locator": "Resolution",
        "use": "A passive transformer is not a single-phase to three-phase converter",
    },
    "R5": {
        "title": "Hubbell HBL2810 L21-30R specification",
        "url": "https://hubbellcdn.com/specsheet/WDK_CA-HBL2810-SPEC-EN.pdf",
        "locator": "search-indexed manufacturer specification",
        "use": "L21-30R, 30 A, 3-phase wye 120/208 V, 4-pole 5-wire; full PDF was inaccessible",
    },
    "DEMO": {
        "title": "Rivet fictional scenario choices",
        "url": "",
        "locator": "Design-brief.pdf and request.json",
        "use": "All loads, candidate breaker ratings, dimensions, part identifiers, terminal conventions and circuit choices are invented; not manufacturer selections",
    },
}


def fixture():
    parts = {}

    def part(
        identity,
        kind,
        description,
        pins,
        *,
        poles=0,
        phases=0,
        voltage=0,
        kva=0,
        amps=0,
        connection="",
        ref="DEMO",
    ):
        parts[identity] = {
            "part_number": identity,
            "kind": kind,
            "description": description,
            "terminals": ",".join(pins),
            "poles": poles,
            "phases": phases,
            "voltage_v": voltage,
            "kva": kva,
            "amps": amps,
            "connection": connection,
            "source_ref": ref,
        }
        return identity

    def breaker(identity, poles, amps, voltage):
        return part(
            identity,
            "breaker",
            f"Candidate breaker {poles}P {amps} A / {voltage} V",
            [
                *(f"L{i}" for i in range(1, poles + 1)),
                *(f"T{i}" for i in range(1, poles + 1)),
            ],
            poles=poles,
            phases=3 if poles == 3 else 1,
            voltage=voltage,
            amps=amps,
        )

    single_loads = [
        {
            "id": f"C{i}",
            "va": 2400 if i < 7 else 6000,
            "voltage_v": 120 if i < 7 else 240,
            "lines": ["A" if i < 4 else "B"] if i < 7 else ["A", "B"],
        }
        for i in range(1, 8)
    ]
    target_loads = deepcopy(single_loads)
    for i, load in enumerate(target_loads):
        load["lines"] = ["ABC"[i % 3]] if i < 6 else ["A", "B", "C"]
        if i == 6:
            load.update(voltage_v=208, replacement_load_confirmed=True)
    selections = []
    for n, loads in ((1, single_loads), (3, target_loads)):
        count = 2 if n == 1 else 3
        selected = {
            "IN-1": part(
                f"RVT-IN-{count}L",
                "inlet",
                f"480 V {n} PH input terminals + PE",
                [*(f"L{i}" for i in range(1, count + 1)), "G"],
                phases=n,
                voltage=480,
            ),
            "XF-1": part(
                f"RVT-TX30-{n}P",
                "transformer",
                f"30 kVA / 480 V / {'120-240 V 1 PH' if n == 1 else '208Y-120 V 3 PH'}",
                [
                    *(f"H{i}" for i in range(1, count + 1)),
                    *(f"X{i}" for i in range(1, count + 1)),
                    "X0",
                    "G",
                ],
                phases=n,
                voltage=480,
                kva=30,
                connection="480-120/240" if n == 1 else "480D-208Y/120",
                ref="R1" if n == 1 else "R2",
            ),
            "CB-M1": breaker(f"RVT-PRI-{count}P", count, 80 if n == 1 else 50, 480),
            "CB-S1": breaker(f"RVT-SEC-{count}P", count, 150 if n == 1 else 100, 240),
            "PNL-1": part(
                f"RVT-PANEL-{n}P",
                "panel",
                f"Distribution panel / {n} PH / {'120-240' if n == 1 else '208Y-120'} V",
                [
                    *(f"L{i}" for i in range(1, count + 1)),
                    "G",
                    *(
                        f"C{load['id'][1:]}_{line}"
                        for load in loads
                        for line in load["lines"]
                    ),
                ],
                phases=n,
                voltage=240 if n == 1 else 208,
                amps=150 if n == 1 else 100,
            ),
            "NB-1": part(
                "RVT-NEUTRAL",
                "neutral_bar",
                "Isolated neutral bar; bond location unverified",
                ["IN", *(f"N{i}" for i in range(1, 8))],
                amps=150,
            ),
            "GB-1": part(
                "RVT-EARTH",
                "earth_bar",
                "Protective earth terminal bar",
                ["IN", "XF", "PNL", *(f"G{i}" for i in range(1, 8))],
            ),
        }
        for i in range(1, 8):
            poles = 1 if i < 7 else count
            selected[f"CB-{i}"] = breaker(f"RVT-BR-{poles}P", poles, 30, 240)
            selected[f"R-{i}"] = part(
                f"RVT-REC-{poles}P",
                "receptacle",
                "120 V / 30 A L5-30R candidate"
                if i < 7
                else "120-240 V L14-30R candidate"
                if n == 1
                else "208Y-120 V L21-30R candidate",
                [*(f"L{j}" for j in range(1, poles + 1)), "N", "G"],
                poles=poles + 1,
                phases=1 if i < 7 or n == 1 else 3,
                voltage=120 if i < 7 else 240 if n == 1 else 208,
                amps=30,
                connection="L5-30R" if i < 7 else "L14-30R" if n == 1 else "L21-30R",
                ref="R5" if i == 7 and n == 3 else "DEMO",
            )
        selections.append(selected)
    catalog = {
        "fictional": True,
        "schema": "rivet.pdu-demo-catalog.v1",
        "sources": SOURCES,
        "parts": parts,
    }
    request = {
        "fictional": True,
        "revision": "B",
        "target": "three_phase",
        "supply_phases": 3,
        "primary_v": 480,
        "secondary_ll_v": 208,
        "secondary_ln_v": 120,
        "frequency_hz": 60,
        "power_factor": 1,
        "parts": selections[1],
        "loads": target_loads,
        "instruction": "Replace the single-phase PDU assembly with a three-phase assembly. Three-phase supply is available. Retain C1-C6 at 120 V; redistribute two on each phase. Replace C7's old 240 V load and outlet with explicitly compatible 208 V three-phase equipment. Prepare draft copies; do not release.",
    }
    return catalog, selections[0], single_loads, request


def _text(doc, value, xy, height=6, layer="E03_TABLES", kind="table"):
    ent = doc.modelspace().add_text(
        str(value), dxfattribs={"height": height, "layer": layer}
    )
    ent.set_placement(xy)
    set_xdata(ent, ["pdu_owned", kind])
    return ent


def update_owned_tables(doc, parts, loads, revision):
    phase = int(parts["XF-1"]["phases"])
    out = "120/240 V / 1 PH / 3W+PE" if phase == 1 else "208Y/120 V / 3 PH / 4W+PE"
    _text(doc, f"OUTPUT: {out} / 60 Hz / REV {revision}", (30, 675), 10, "E01_TABLES")
    _text(
        doc,
        f"INPUT: 480 V / {phase} PH / {'2' if phase == 1 else '3'}W+PE / 30 kVA",
        (30, 650),
        9,
        "E01_TABLES",
    )

    def grid(x, top, widths, headers, rows, layer, row_height=24):
        columns = [x]
        for width in widths:
            columns.append(columns[-1] + width)
        bottom = top - row_height * (len(rows) + 1)
        for xx in columns:
            ent = doc.modelspace().add_line(
                (xx, top), (xx, bottom), dxfattribs={"layer": layer}
            )
            set_xdata(ent, ["pdu_owned", "table_grid"])
        for row in range(len(rows) + 2):
            yy = top - row * row_height
            ent = doc.modelspace().add_line(
                (x, yy), (columns[-1], yy), dxfattribs={"layer": layer}
            )
            set_xdata(ent, ["pdu_owned", "table_grid"])
        for i, values in enumerate([headers, *rows]):
            for col, value in enumerate(values):
                ent = _text(
                    doc,
                    value,
                    (columns[col] + 8, top - i * row_height - 16),
                    7.4,
                    layer,
                )
                if layer == "E03_TABLES" and i and col == 1:
                    set_xdata(
                        ent, ["pdu_owned", "table", rows[i - 1][0], "part_number"]
                    )

    grid(
        2630,
        667,
        [95, 250, 420, 345],
        ["TAG", "CANDIDATE PART", "DESCRIPTION", "TERMINALS"],
        [
            [tag, p["part_number"], p["description"], p["terminals"]]
            for tag, p in parts.items()
        ],
        "E03_TABLES",
    )
    grid(
        1330,
        667,
        [100, 150, 150, 220, 220],
        ["CIRCUIT", "LOAD (VA)", "VOLTAGE (V)", "PHASE(S)", "CANDIDATE CB"],
        [
            [
                p["id"],
                str(p["va"]),
                str(p["voltage_v"]),
                ",".join(p["lines"]),
                f"30 A / {len(p['lines'])}P",
            ]
            for p in loads
        ],
        "E02_TABLES",
        28,
    )
    wires = connections(parts, loads)
    _text(
        doc,
        "WIRE CONNECTION REPORT / terminal identities, not conductor sizing",
        (1330, 380),
        8,
        "E02_TABLES",
    )
    for i, wire in enumerate(wires):
        col, row = divmod(i, 28)
        _text(
            doc,
            f"{wire['wire_id']}  {wire['from_tag']}:{wire['from_pin']}  -  {wire['to_tag']}:{wire['to_pin']}",
            (1330 + col * 365, 350 - row * 10),
            5.5,
            "E02_TABLES",
        )
    _text(
        doc,
        "Drawing units mm; schematic geometry NTS. Enclosure outline is illustrative.",
        (2630, 100),
        7,
    )
    _text(
        doc,
        "Assembly replacement; no winding or core redesign is claimed.",
        (2630, 80),
        7,
    )


def baseline(root, catalog, selected, loads):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    doc = ezdxf.new("R2010")
    doc.units = 4
    doc.appids.new("RIVET")
    for name, color in [
        ("PDU_IDENTITY", 8),
        ("E01_SYMBOLS", 7),
        ("E01_PINS", 7),
        ("E01_WIRES", 8),
        ("E01_NEUTRAL", 4),
        ("E01_PE", 3),
        ("E01_TABLES", 7),
        ("E02_TABLES", 7),
        ("E03_TABLES", 7),
        ("E01_FRAME", 7),
        ("E02_FRAME", 7),
        ("E03_FRAME", 7),
    ]:
        doc.layers.new(name, dxfattribs={"color": color})
    for i, title in enumerate(
        (
            "POWER SCHEMATIC / TERMINAL CONNECTIONS",
            "LOADS AND CONNECTION SCHEDULE",
            "COMPONENT AND NAMEPLATE SCHEDULE",
        )
    ):
        x = i * 1300
        space = doc.modelspace()
        space.add_lwpolyline(
            [(x + 15, 15), (x + 1185, 15), (x + 1185, 745), (x + 15, 745)],
            close=True,
            dxfattribs={"layer": f"E0{i + 1}_FRAME"},
        )
        space.add_text(
            "RIVET ENGINEERING PROTOTYPE / FICTIONAL DEMO",
            dxfattribs={"height": 13, "layer": f"E0{i + 1}_FRAME"},
        ).set_placement((x + 30, 715))
        space.add_text(
            title, dxfattribs={"height": 9, "layer": f"E0{i + 1}_FRAME"}
        ).set_placement((x + 30, 695))
        space.add_text(
            f"RVT-PDU-30 / E-0{i + 1} / DRAFT / NOT FOR FABRICATION",
            dxfattribs={"height": 8, "layer": f"E0{i + 1}_FRAME"},
        ).set_placement((x + 30, 30))
    doc.modelspace().add_text(
        CONVENTION, dxfattribs={"height": 3, "layer": "PDU_IDENTITY"}
    ).set_placement((30, 18))
    positions = {
        "IN-1": (80, 500),
        "CB-M1": (220, 500),
        "XF-1": (390, 500),
        "CB-S1": (555, 500),
        "PNL-1": (720, 430),
        "NB-1": (455, 240),
        "GB-1": (610, 210),
    }
    for i in range(1, 8):
        positions[f"CB-{i}"] = (900, 605 - (i - 1) * 76)
        positions[f"R-{i}"] = (1090, 605 - (i - 1) * 76)
    chosen = {tag: catalog["parts"][key] for tag, key in selected.items()}
    for tag, part in chosen.items():
        add_device(doc, tag, part, positions[tag])
    wires = connections(chosen, loads)
    write_connections(doc, wires)
    update_owned_tables(doc, chosen, loads, "A")
    # Deliberately unowned content is a preservation sentinel and a real note.
    doc.modelspace().add_text(
        "ENGINEERING HOLD: neutral bond, protection, cable sizes and mechanical fit require review.",
        dxfattribs={"height": 8, "layer": "E01_FRAME"},
    ).set_placement((30, 80))
    doc.modelspace().add_text(
        "Crossings do not connect. Only named terminals define electrical incidence.",
        dxfattribs={"height": 8, "layer": "E01_FRAME"},
    ).set_placement((30, 100))
    doc.saveas(root / "PDU-30-Rev-A.dxf")
    rows = []
    for tag, part in chosen.items():
        rows.append(
            {
                "equipment_tag": tag,
                "quantity": 1,
                **{k.lower(): v for k, v in catalog_attributes(part).items()},
                "source_ref": part["source_ref"],
                "notes": "Fictional candidate; engineer review required",
            }
        )
    write_csv(root / "BOM-Rev-A.csv", rows, BOM_FIELDS)
    write_csv(root / "Connections-Rev-A.csv", wires, list(wires[0]))
    return root


def drawing_pdf(dxf, out):
    """Render actual saved CAD entities into vector PDF; not a second drawing."""
    doc = ezdxf.readfile(dxf)
    with pymupdf.open() as pdf:
        for sheet_index, sheet in enumerate(("E01", "E02", "E03")):
            backend = PyMuPdfBackend()
            Frontend(
                RenderContext(doc),
                backend,
                config=config.Configuration(
                    background_policy=config.BackgroundPolicy.WHITE,
                    color_policy=config.ColorPolicy.BLACK,
                ),
            ).draw_layout(
                doc.modelspace(),
                finalize=True,
                filter_func=lambda e, sheet=sheet: e.dxf.layer.startswith(sheet),
            )
            data = backend.get_pdf_bytes(layout.Page(420, 297, layout.Units.mm))
            with pymupdf.open(stream=data, filetype="pdf") as page:
                # Outlined CAD fonts get a matching selectable text layer from
                # the SAVED entities. No text comes from the intended model.
                canvas = page[0]
                scale = canvas.rect.width / 1170
                top = (canvas.rect.height - 730 * scale) / 2
                offset = sheet_index * 1300

                def text_layer(
                    entity, canvas=canvas, scale=scale, top=top, offset=offset
                ):
                    if entity.dxftype() == "INSERT":
                        for child in entity.virtual_entities():
                            text_layer(child)
                        for attr in entity.attribs:
                            if not attr.is_invisible:
                                text_layer(attr)
                    elif entity.dxftype() in {"TEXT", "ATTRIB"}:
                        point = entity.dxf.insert
                        canvas.insert_text(
                            (
                                (point.x - offset - 15) * scale,
                                top + (745 - point.y) * scale,
                            ),
                            entity.dxf.text,
                            fontsize=entity.dxf.height * scale,
                            render_mode=3,
                        )

                for entity in doc.modelspace():
                    if entity.dxf.layer.startswith(sheet):
                        text_layer(entity)
                pdf.insert_pdf(page)
        pdf.save(out)


def _report_styles():
    styles = getSampleStyleSheet()
    for name, size, leading, color in (
        ("BodyText", 10, 15, "#30343a"),
        ("Title", 26, 29, "#15181b"),
        ("Heading2", 11, 15, "#15181b"),
    ):
        styles[name].fontName = "Helvetica-Bold" if name != "BodyText" else "Helvetica"
        styles[name].fontSize, styles[name].leading = size, leading
        styles[name].textColor = colors.HexColor(color)
        styles[name].alignment = 0
        styles[name].spaceAfter = 10
    styles.add(
        ParagraphStyle(
            "Meta",
            fontName="Helvetica",
            fontSize=8,
            leading=12,
            textColor=colors.HexColor("#626b73"),
            spaceAfter=10,
        )
    )
    styles.add(
        ParagraphStyle(
            "Cell",
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#30343a"),
        )
    )
    styles.add(
        ParagraphStyle("HeadCell", parent=styles["Cell"], fontName="Helvetica-Bold")
    )
    return styles


def _report_table(rows, widths, styles, padding=8):
    cells = [
        [
            Paragraph(escape(str(value)), styles["HeadCell" if index == 0 else "Cell"])
            for value in row
        ]
        for index, row in enumerate(rows)
    ]
    table = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eff1f2")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), padding),
                ("BOTTOMPADDING", (0, 0), (-1, -1), padding),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dce0e3")),
            ]
        )
    )
    return table


def _report_build(path, story, code):
    def furniture(canvas, document):
        width, height = document.pagesize
        canvas.setFillColor(colors.HexColor("#171a1f"))
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(44, height - 34, "rivet.")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(
            width - 44, height - 31, code + "  /  ENGINEERING REVIEW"
        )
        canvas.setStrokeColor(colors.HexColor("#a6adb4"))
        canvas.setLineWidth(0.5)
        canvas.line(44, height - 45, width - 44, height - 45)
        canvas.line(44, 38, width - 44, 38)
        canvas.setFont("Helvetica", 7)
        canvas.drawString(44, 25, "FICTIONAL DEMO  /  DRAFT  /  NOT FOR FABRICATION")
        canvas.drawRightString(width - 44, 25, str(document.page))

    SimpleDocTemplate(
        str(path),
        pagesize=(8.5 * inch, 11 * inch),
        leftMargin=44,
        rightMargin=44,
        topMargin=68,
        bottomMargin=54,
        title=code + " | PDU phase assembly",
        author="Rivet",
    ).build(story, onFirstPage=furniture, onLaterPages=furniture)


def brief_pdf(path, request):
    styles = _report_styles()
    styles["Meta"].spaceAfter = 4
    styles["BodyText"].spaceAfter = 8
    styles["Heading2"].spaceAfter = 8
    p = lambda value: Paragraph(escape(value), styles["BodyText"])
    story = [
        Paragraph("DESIGN INPUT  /  RVT-PDU-30", styles["Meta"]),
        Paragraph("Phase assembly replacement", styles["Title"]),
        p(request["instruction"]),
        Spacer(1, 8),
    ]
    story += [
        _report_table(
            [
                ["Design basis", "Original A", "Target B"],
                ["Transformer", "30 kVA / single-phase", "30 kVA / three-phase"],
                [
                    "Input",
                    "480 V / single-phase",
                    "480 V / three-phase; supply assumed available",
                ],
                ["Output", "120/240 V / 60 Hz", "208Y/120 V / 60 Hz"],
                [
                    "Rated full-load current",
                    "Primary 62.50 A; secondary 125.00 A",
                    "Primary 36.08 A; secondary 83.27 A",
                ],
            ],
            [120, 170, 234],
            styles,
            padding=4,
        ),
        Spacer(1, 10),
        Paragraph("Circuit loads and phase allocation", styles["Heading2"]),
    ]
    story += [
        _report_table(
            [["Circuit", "Load (VA)", "Voltage (V)", "Phase(s)"]]
            + [
                [
                    load["id"],
                    str(load["va"]),
                    str(load["voltage_v"]),
                    ", ".join(load["lines"]),
                ]
                for load in request["loads"]
            ],
            [100, 140, 140, 144],
            styles,
            padding=4,
        ),
        Spacer(1, 10),
        p(
            "C7 requires replacement of the old 240 V load and outlet with explicitly compatible 208 V three-phase equipment. All candidate parts, loads and terminal mappings are fictional; public product ratings provide reference points only."
        ),
        p(
            "Engineer review remains required for protection, conductors, grounding, mechanical fit and assembly suitability. No winding or core redesign is included."
        ),
        Paragraph("References", styles["Heading2"]),
    ]
    for key, ref in SOURCES.items():
        if ref["url"]:
            story.append(
                Paragraph(
                    f"{key} / <link href='{escape(ref['url'], quote=True)}' color='#3c4960'>{escape(ref['title'])}</link>",
                    styles["Meta"],
                )
            )
    _report_build(path, story, "BASIS-001")


def summary_pdf(path, result, plan, fault_results):
    from reportlab.platypus import PageBreak

    styles = _report_styles()
    p = lambda value: Paragraph(escape(value), styles["BodyText"])
    story = [
        Paragraph("CHANGE REVIEW  /  RVT-PDU-30  /  REVISION A TO B", styles["Meta"]),
        Paragraph("Single-phase to three-phase", styles["Title"]),
        p(
            "Draft assembly replacement with native drawing, BOM and connection exports. The following results apply to the supported fictional sample."
        ),
        Spacer(1, 12),
    ]
    story += [
        _report_table(
            [
                ["Parameter", "Original A", "Proposed B"],
                ["Transformer", "30 kVA / 1 phase", "30 kVA / 3 phase"],
                [
                    "Primary supply",
                    "480 V / 1 phase",
                    "480 V / 3 phase (assumed available)",
                ],
                ["Secondary", "120/240 V", "208Y/120 V"],
                [
                    "Full-load primary",
                    "62.50 A",
                    f"{result['calculations']['primary_full_load_a']:.2f} A",
                ],
                [
                    "Full-load secondary",
                    "125.00 A",
                    f"{result['calculations']['secondary_full_load_a']:.2f} A",
                ],
                [
                    "C7 outlet and load",
                    "240 V single-phase",
                    "208 V three-phase replacements",
                ],
                [
                    "Connected load",
                    "20.40 kVA (sample)",
                    f"{plan['calculations']['total_load_va'] / 1000:.2f} kVA",
                ],
            ],
            [136, 160, 228],
            styles,
        ),
        Spacer(1, 20),
        Paragraph("Saved-file verification", styles["Heading2"]),
        p(
            f"Reopened and checked {result['devices_checked']} saved devices and {result['connections_checked']} terminal connections. Native block attributes, actual conductor endpoints, the visible schedule and the exported BOM were checked against the explicit change request. Original files remain unchanged."
        ),
        p(
            f"{len(plan['changes'])} component records changed. The independent terminal graph checks phase separation and each requested panel bus path; visual line crossings are not treated as electrical junctions."
        ),
        Paragraph("Ideal AC recheck", styles["Heading2"]),
        p(
            "; ".join(
                f"{leg}: {va / 1000:.2f} kVA, {result['power_flow']['phase_current_a'][leg]:.2f} A"
                for leg, va in result["power_flow"]["phase_load_va"].items()
            )
            + f". Fundamental neutral current: {result['power_flow']['fundamental_neutral_a']:.3f} A."
        ),
        Paragraph(
            "Unity power factor, nominal balanced voltage and zero line impedance. This does not establish inrush, harmonic performance, protection coordination or code compliance.",
            styles["Meta"],
        ),
        PageBreak(),
        Paragraph(
            "VERIFICATION RECORD  /  SYNTHETIC SOFTWARE CONTROLS", styles["Meta"]
        ),
        Paragraph("Checks and remaining holds", styles["Title"]),
        _report_table(
            [["Deliberate defect", "Saved-file result"]]
            + [
                [r["name"], "Rejected" if r["detected"] else "MISSED"]
                for r in fault_results
            ],
            [374, 150],
            styles,
        ),
        Spacer(1, 14),
        p(
            f"{sum(r['detected'] for r in fault_results)}/{len(fault_results)} injected defects were rejected. These controls measure software behavior on the fictional sample; they are not real-manufacturer accuracy results."
        ),
        Paragraph("Manufacturer validation still required", styles["Heading2"]),
        p(
            "The writer supports this sample CAD convention only. Larson's 50 MVA drawing is a separate, read-only intake. An actual redesign needs the manufacturer's circuit model, approved parts, connection reports, load requirements and engineering rules; a matching BOM is still missing."
        ),
        p(
            "Protection, SCCR, conductors, bonding, thermal and mechanical constraints remain unverified. Files are drafts and carry no engineering approval or release."
        ),
        Paragraph("Research attribution", styles["Heading2"]),
        Paragraph(
            "Inspired by <link href='https://arxiv.org/abs/2602.11678' color='#3c4960'>Beyond Pixels: A Vector-to-Graph Framework for Reliable Schematic Auditing</link> and <link href='https://github.com/gm-embodied/V2G-Audit' color='#3c4960'>V2G-Audit</link>. Rivet's terminal graph is independently implemented. No author code, training data or benchmark accuracy is incorporated.",
            styles["Meta"],
        ),
    ]
    _report_build(path, story, "CHANGE-001")


def preview_html(root, result, plan, faults):
    e = lambda x: escape(str(x), quote=True)
    changes = "".join(
        f"<tr><td>{e(c['tag'])}</td><td>{e(c['fields'].get('DESCRIPTION', c['fields'].get('PART_NUMBER', {'before': 'terminal convention', 'after': 'new terminal convention'}))['before'])}</td><td>{e(c['fields'].get('DESCRIPTION', c['fields'].get('PART_NUMBER', {'before': 'terminal convention', 'after': 'new terminal convention'}))['after'])}</td></tr>"
        for c in plan["changes"]
    )
    fault_rows = "".join(
        f"<li>{e(f['name'])}<b>{'Caught' if f['detected'] else 'MISSED'}</b></li>"
        for f in faults
    )
    refs = "".join(
        f"<li><a href='{e(r['url'])}'>{key} / {e(r['title'])}</a><p>{e(r['use'])}</p></li>"
        for key, r in SOURCES.items()
        if r["url"]
    )
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Rivet | Phase-change prototype</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#111215;color:#ececf1;font:15px/1.6 -apple-system,BlinkMacSystemFont,Arial,sans-serif}}main{{max-width:1180px;margin:auto;padding:42px 30px 90px}}a{{color:#c5b5e9}}header{{display:flex;justify-content:space-between;border-bottom:1px solid #303139;padding-bottom:24px}}.logo{{font-size:24px;font-weight:750}}small,.muted{{color:#a3a4af}}h1{{font-size:40px;line-height:1.15;letter-spacing:-1.2px;max-width:760px;margin:30px 0 18px}}h2{{font-size:21px;margin:32px 0 14px}}p{{max-width:900px}}.status{{padding:15px 18px;border-left:3px solid #b4a2ce;background:#1c1b23}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}figure{{margin:0;background:white;border-radius:8px;overflow:hidden}}figure img{{display:block;width:100%}}figcaption{{padding:12px 18px;color:#33313e;font-weight:600}}nav{{display:flex;flex-wrap:wrap;gap:10px;margin:22px 0}}nav a,button{{padding:10px 15px;border:1px solid #3c3c47;border-radius:6px;text-decoration:none;background:#202128;color:#ececf1;font:inherit;cursor:pointer}}table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:13px 10px;border-bottom:1px solid #303139;vertical-align:top}}th{{color:#a3a4af;font-size:12px}}ul{{list-style:none;padding:0}}li{{padding:10px 0;border-bottom:1px solid #303139}}li b{{float:right;color:#bad1bf}}.metrics{{display:flex;gap:35px;margin:28px 0}}.metrics strong{{display:block;font-size:28px}}.metrics span{{color:#a3a4af}}details summary{{cursor:pointer;padding:12px 0}}#changes[hidden],#evidence[hidden]{{display:none}}@media(max-width:800px){{.grid{{grid-template-columns:1fr}}h1{{font-size:30px}}.metrics{{flex-wrap:wrap}}}}</style>
<main><header><div class="logo">rivet.</div><small>FICTIONAL ENGINEERING SAMPLE / 30 kVA PDU</small></header><h1>From a requested phase change to editable drawings and a checked BOM.</h1><p class="muted">Single-phase 480 V to 120/240 V assembly replaced with a three-phase 480 V to 208Y/120 V assembly. The source files are preserved.</p><div class="status"><b>Draft files prepared. Engineer review required.</b><br>File consistency checks passed. Protection, conductor sizes, grounding and physical fit remain unverified.</div>
<div class="metrics"><div><strong>{result["devices_checked"]}</strong><span>saved devices checked</span></div><div><strong>{result["connections_checked"]}</strong><span>terminal connections checked</span></div><div><strong>{len(plan["changes"])}</strong><span>component records changed</span></div><div><strong>{sum(f["detected"] for f in faults)}/{len(faults)}</strong><span>injected defects caught</span></div></div>
<nav><a href="draft/PDU-30-Rev-B.pdf" target="_blank">Open proposed drawing</a><a href="draft/PDU-30-Rev-B.dxf" download>Editable DXF</a><a href="draft/BOM-Rev-B.xlsx" download>Revised Excel BOM</a><a href="Review-draft.eml" download>Unsent email with files</a><a href="Phase-change-summary.pdf" target="_blank">Results and limitations</a><a href="Phase-change-demo.zip" download>Whole package</a></nav>
<h2>Actual CAD, before and after</h2><p class="muted">These previews are rendered from the saved DXFs, not separate illustrations. Open the PDFs to inspect all three sheets.</p><div class="grid"><figure><a href="baseline/PDU-30-Rev-A.pdf" target="_blank"><img src="before.png" alt="Saved original single-phase schematic"></a><figcaption>Original A / single-phase</figcaption></figure><figure><a href="draft/PDU-30-Rev-B.pdf" target="_blank"><img src="after.png" alt="Saved proposed three-phase schematic"></a><figcaption>Proposed B / three-phase</figcaption></figure></div>
<h2>What changed</h2><table><tr><th>Device</th><th>Original</th><th>Proposed</th></tr>{changes}</table>
<h2>Independent checks</h2><p>Primary full-load current: 62.50 A becomes {result["calculations"]["primary_full_load_a"]:.2f} A. Secondary: 125.00 A becomes {result["calculations"]["secondary_full_load_a"]:.2f} A. Each target phase carries 6,800 VA of the stated 20,400 VA load.</p><p class="muted">Changing phase requires new components, connections, phase allocation and load compatibility. The old 240 V C7 equipment is explicitly replaced; it is not silently assumed to work at 208 V. A transformer does not manufacture three-phase supply from single-phase service.</p><ul>{fault_rows}</ul>
<details><summary>Inputs, connection report and machine-readable evidence</summary><nav><a href="Design-brief.pdf" target="_blank">Design brief</a><a href="request.json" target="_blank">Exact request</a><a href="draft/Connections-Rev-B.csv" download>Connection report</a><a href="draft/recheck.json" target="_blank">Saved-file recheck</a><a href="plan.json" target="_blank">Change plan</a><a href="fault-injection.json" target="_blank">Fault results</a></nav></details>
<h2>Public references and sample limits</h2><p class="muted">The product examples below supply realistic rating conventions. This is not Larson's submitted project or a manufacturer-approved design. Candidate part IDs, loads, breaker choices, terminal mappings and drawing geometry are fictional. The adapter currently supports only this explicit sample convention.</p><ul>{refs}</ul><p>To test on a real job: provide native CAD and references, matching BOM, the exact requested change, loads and manufacturer design rules. A previously completed engineer revision can be withheld as a benchmark.</p></main></html>"""
    page = page.replace(
        "Each target phase carries 6,800 VA of the stated 20,400 VA load.",
        "Actual phase loads: "
        + "; ".join(
            f"{leg} {va:,.0f}"
            for leg, va in result["power_flow"]["phase_load_va"].items()
        )
        + f" VA. Ideal fundamental neutral current: {result['power_flow']['fundamental_neutral_a']:.3f} A (unity PF, zero line impedance).",
    )
    (Path(root) / "index.html").write_text(page)
