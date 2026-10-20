"""Generate a proposed approval package from one immutable equipment design.

This is a drawing/export adapter, not an engineer or a catalog solver.  It does
not fill missing ratings, dimensions, part numbers or source evidence.  DXFs
use ordinary named blocks and the existing EQUIPMENT_TAG/field attributes so
the established correction and revision readers can read them back.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

import ezdxf
import pymupdf
from ezdxf.addons.drawing import Frontend, RenderContext, config, layout
from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
from ezdxf.addons.importer import Importer
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)

VERSION = "rivet.engineering-generation.v4"
PROPOSED = "PROPOSED - ENGINEER REVIEW REQUIRED - NOT FOR PRODUCTION"
DEFAULT_TEMPLATE = {
    "id": "rivet-schematic-a3",
    "version": "1",
    "elevation_mode": "schematic",
    "blocks": {
        "breaker": "RIVET_BREAKER",
        "title_block": "RIVET_TITLE_BLOCK",
    },
}
MAX_DEVICES = 2000
MAX_SECTIONS = 500
MAX_CUT_SHEET_PAGES = 250
SYSTEM_FIELDS = {
    "service_rating_a": "A",
    "voltage_v": "V",
    "voltage_to_ground_v": "V",
    "current_type": "",
    "frequency_hz": "Hz",
    "phases": "",
    "wires": "",
    "grounding": "",
    "available_fault_ka": "kA",
    "minimum_interrupting_ka": "kA",
    "short_time_withstand_ka": "kA",
    "service_disconnect": "",
    "code_edition": "",
}
BOM_HEADERS = [
    "equipment_tag",
    "source_label",
    "section_label",
    "identity_scope",
    "model_device_id",
    "position_state",
    "quantity",
    "manufacturer",
    "product_family",
    "device_frame",
    "frame_a",
    "trip_a",
    "trip_unit",
    "pole_count",
    "interrupting_ka",
    "catalog_sccr_ka",
    "part_number",
    "section_id",
    "position_id",
    "supplied",
    "nameplate",
    "model_id",
    "model_version",
    "proposal_status",
    "source_references",
]
NAMEPLATE_HEADERS = [
    "equipment_tag",
    "source_label",
    "section_label",
    "identity_scope",
    "model_device_id",
    "position_state",
    "nameplate",
    "text_status",
    "section_id",
    "position_id",
    "model_id",
    "model_version",
    "proposal_status",
]


class GenerationError(ValueError):
    """A model or external drawing asset cannot be exported without guessing."""


def _json_bytes(value):
    return json.dumps(
        value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value, limit=1000):
    if value is None:
        return ""
    # DXF control escapes must not turn source text into a formatting command.
    return str(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")[:limit]


def _cad_text(value, limit=1000):
    return _text(value, limit).replace("%%", "% %").replace("\\", "/")


def _number(value, field):
    if value is None or value == "":
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise GenerationError(f"{field} must be a positive finite number or unknown.")
    return value


def _value(value, unit=""):
    if value is None or value == "":
        return "UNKNOWN"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = f"{value:g}"
    return f"{_text(value)}{(' ' + unit) if unit else ''}"


def _snapshot(design):
    if not isinstance(design, dict):
        raise GenerationError(
            "Select one versioned equipment design before generating drawings."
        )
    if "configuration" in design:
        if not isinstance(design["configuration"], dict):
            raise GenerationError("configuration must be one selected design.")
        model = copy.deepcopy(design["configuration"])
        for field in (
            "model_id",
            "model_version",
            "order_id",
            "order_name",
            "job_number",
            "requirements",
            "requirements_digest",
        ):
            if field in design:
                model[field] = copy.deepcopy(design[field])
    else:
        if "configurations" in design:
            raise GenerationError(
                "Select a configuration explicitly; the generator does not choose a design."
            )
        model = copy.deepcopy(design)
    system = model.get("system") or {}
    if not isinstance(system, dict):
        raise GenerationError(
            "System requirements must be an explicit typed model object."
        )
    for key, unit in SYSTEM_FIELDS.items():
        value = system.get(key)
        if value is None:
            continue
        if unit or key in {"phases", "wires"}:
            _number(value, key)
            if key in {"phases", "wires"} and value != int(value):
                raise GenerationError(f"{key} must be an explicit whole-number count.")
        elif key == "service_disconnect" and not isinstance(value, bool):
            raise GenerationError("service_disconnect must be a boolean or unknown.")
        elif key != "service_disconnect" and not isinstance(value, str):
            raise GenerationError(f"{key} must be source text or unknown.")
    for field in ("model_id", "model_version"):
        if not _text(model.get(field)).strip():
            raise GenerationError(f"A nonempty {field} is required.")
        model[field] = _text(model[field], 200)
    devices, sections = model.get("devices"), model.get("sections")
    if not isinstance(devices, list) or not devices or len(devices) > MAX_DEVICES:
        raise GenerationError(
            f"A selected design needs 1-{MAX_DEVICES} device instances."
        )
    if not isinstance(sections, list) or len(sections) > MAX_SECTIONS:
        raise GenerationError(
            f"sections must be a list of at most {MAX_SECTIONS} sections."
        )
    section_ids = set()
    for section in sections:
        if (
            not isinstance(section, dict)
            or not section.get("id")
            or section["id"] in section_ids
        ):
            raise GenerationError("Every section must have one unique ID.")
        section_ids.add(section["id"])
        for field in ("width_in", "height_in", "depth_in", "bus_a"):
            _number(section.get(field), f"{section['id']}.{field}")
    device_ids, tags = set(), set()
    for device in devices:
        if (
            not isinstance(device, dict)
            or not device.get("id")
            or device["id"] in device_ids
        ):
            raise GenerationError("Every physical device must have one unique ID.")
        device_ids.add(device["id"])
        tag = _text(device.get("tag") or device["id"], 200).strip()
        if not tag or tag.casefold() in tags:
            raise GenerationError(
                "Every device needs one distinct proposed equipment tag or model-local ID."
            )
        tags.add(tag.casefold())
        for field in (
            "frame_a",
            "trip_a",
            "pole_count",
            "interrupting_ka",
            "catalog_sccr_ka",
        ):
            _number(device.get(field), f"{device['id']}.{field}")
        if device.get("supplied") is not None and not isinstance(
            device.get("supplied"), bool
        ):
            raise GenerationError(
                "Device supplied status must be true, false or unknown."
            )
        if _prepared_position(device) and device.get("supplied") is True:
            raise GenerationError(
                "A prepared/future position cannot be exported as a supplied breaker; resolve its state first."
            )
    # A new output is always proposed, even when copied from an earlier approval.
    model["state"] = "proposed"
    model["engineering_approved"] = False
    model["production_released"] = False
    try:
        _json_bytes(model)
    except (TypeError, ValueError) as exc:
        raise GenerationError(
            "Design must be finite, JSON-serializable model data."
        ) from exc
    return model


def _geometry_gaps(model):
    gaps = []
    section_ids = {section["id"] for section in model["sections"]}
    for section in model["sections"]:
        missing = [
            field
            for field in ("width_in", "height_in", "depth_in")
            if section.get(field) is None
        ]
        if missing:
            gaps.append(
                {
                    "section_id": section["id"],
                    "missing_fields": missing,
                    "needs_review": True,
                }
            )
        positions = section.get("positions") or []
        for position in positions:
            if (
                position.get("mounting_space_in") is None
                or position.get("start_in") is None
            ):
                gaps.append(
                    {
                        "section_id": section["id"],
                        "position_id": position.get("id"),
                        "missing_fields": [
                            field
                            for field in ("start_in", "mounting_space_in")
                            if position.get(field) is None
                        ],
                        "needs_review": True,
                    }
                )
    for device in model["devices"]:
        if device.get("section_id") not in section_ids:
            gaps.append(
                {
                    "device_id": device["id"],
                    "missing_fields": ["verified_section_assignment"],
                    "needs_review": True,
                }
            )
    return gaps


def _template(template):
    selected = copy.deepcopy(DEFAULT_TEMPLATE)
    selected.update(
        {key: value for key, value in (template or {}).items() if key != "blocks"}
    )
    selected["blocks"].update((template or {}).get("blocks", {}))
    if selected.get("elevation_mode") not in ("schematic", "production"):
        raise GenerationError("elevation_mode must be schematic or production.")
    if selected.get("library_path"):
        source = Path(selected["library_path"])
        if (
            not selected.get("library_sha256")
            or not source.is_file()
            or source.is_symlink()
            or _sha(source) != selected["library_sha256"]
        ):
            raise GenerationError("Block library must be a hash-pinned local DXF.")
    for name in selected["blocks"].values():
        if not isinstance(name, str) or not name.strip():
            raise GenerationError("Template block names must be nonempty strings.")
    if len(set(selected["blocks"].values())) != len(selected["blocks"]):
        raise GenerationError("Device and title blocks must have different names.")
    return selected


def _drawing_document(template):
    doc = ezdxf.new("R2010")
    doc.units = 4  # sheet/plot millimetres, not verified physical device geometry
    for layer in (
        "ONE_LINE",
        "DEVICE_BODY",
        "DEVICE_DATA",
        "TITLE_BLOCK",
        "MISSING_DIMENSIONS",
        "ELEVATION",
    ):
        doc.layers.new(layer, dxfattribs={"color": 7})
    breaker = doc.blocks.new(template["blocks"]["breaker"])
    breaker.add_lwpolyline(
        [(-5, -5), (5, -5), (5, 5), (-5, 5)],
        close=True,
        dxfattribs={"layer": "DEVICE_BODY"},
    )
    breaker.add_line((-3, -3), (3, 3))
    title = doc.blocks.new(template["blocks"]["title_block"])
    title.add_lwpolyline([(0, 0), (400, 0), (400, 31), (0, 31)], close=True)
    if template.get("library_path"):
        library = ezdxf.readfile(template["library_path"])
        for name in template["blocks"].values():
            if name not in library.blocks:
                raise GenerationError(
                    f"Named block {name} is missing from the supplied library."
                )
            doc.blocks.delete_block(name, safe=False)
        importer = Importer(library, doc)
        importer.import_blocks(list(template["blocks"].values()), rename=False)
        importer.finalize()
    prepared_name = _prepared_block(template)
    if prepared_name in doc.blocks:
        raise GenerationError(
            "Prepared-position block name conflicts with the supplied template."
        )
    doc.linetypes.new(
        "RIVET_PREPARED_DASHED",
        dxfattribs={
            "description": "Prepared position, not installed breaker",
            "pattern": [3.0, 1.5, -1.5],
        },
    )
    prepared = doc.blocks.new(prepared_name)
    prepared.add_lwpolyline(
        [(-5, -5), (5, -5), (5, 5), (-5, 5)],
        close=True,
        dxfattribs={"layer": "DEVICE_BODY", "linetype": "RIVET_PREPARED_DASHED"},
    )
    prepared.add_text(
        "P.S.", dxfattribs={"height": 2, "layer": "DEVICE_DATA"}
    ).set_placement((-3, -1))
    return doc


def _plain(model, key, fallback=""):
    return _text(model.get(key, fallback))


def _section_label(model, section_id):
    for index, section in enumerate(model["sections"], 1):
        if section["id"] == section_id:
            return _text(section.get("label") or section.get("tag") or f"S{index}", 30)
    return "UNASSIGNED"


def _position_label(model, device):
    for section in model["sections"]:
        if section["id"] == device.get("section_id"):
            for index, position in enumerate(section.get("positions") or [], 1):
                if (
                    position.get("id") == device.get("position_id")
                    or position.get("device_id") == device["id"]
                ):
                    return _text(position.get("label") or f"P{index}", 20)
    return "UNKNOWN"


def _put(msp, text, point, height=2.5, layer_name="DEVICE_DATA"):
    return msp.add_text(
        _cad_text(text), dxfattribs={"height": height, "layer": layer_name}
    ).set_placement(point)


def _sheet(doc, model, template, sheet_id, title, offset, digest):
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(offset + 5, 5), (offset + 415, 5), (offset + 415, 292), (offset + 5, 292)],
        close=True,
        dxfattribs={"layer": "TITLE_BLOCK"},
    )
    _put(msp, title, (offset + 15, 277), 5, "TITLE_BLOCK")
    _put(
        msp,
        _plain(model, "order_name", _plain(model, "job_number", model["model_id"])),
        (offset + 15, 267),
        3,
        "TITLE_BLOCK",
    )
    _put(
        msp,
        f"{_value((model.get('system') or {}).get('voltage_v'), 'V')} / "
        f"{_value((model.get('system') or {}).get('phases'))} PH / "
        f"{_value((model.get('system') or {}).get('frequency_hz'), 'Hz')} · "
        "SCHEMATIC / NOT TO SCALE - SYMBOL SIZES ARE NOT PHYSICAL DIMENSIONS",
        (offset + 15, 258),
        2.5,
        "TITLE_BLOCK",
    )
    insert = msp.add_blockref(
        template["blocks"]["title_block"],
        (offset + 10, 10),
        dxfattribs={"layer": "TITLE_BLOCK"},
    )
    for tag, value, point, height in [
        ("STATUS", PROPOSED, (offset + 15, 32), 2.7),
        ("MODEL_ID", model["model_id"], (offset + 15, 22), 2.5),
        (
            "MODEL_VERSION",
            f"MODEL VERSION: {model['model_version']}",
            (offset + 160, 22),
            2.5,
        ),
        ("SHEET_ID", sheet_id, (offset + 350, 22), 3),
        ("MODEL_SHA256", f"MODEL SHA256: {digest[:24]}", (offset + 15, 14), 2),
        (
            "TEMPLATE",
            f"TEMPLATE {template['id']} / {template['version']}",
            (offset + 220, 14),
            2,
        ),
    ]:
        insert.add_attrib(
            tag,
            _cad_text(value),
            point,
            dxfattribs={"height": height, "layer": "TITLE_BLOCK"},
        )
    # Machine-readable model scalars on every actual saved sheet. They do not
    # replace visible ratings or establish that customer requirements are correct.
    metadata = {
        "SYS_SCHEMA": "rivet.engineering-system.v1",
        "SYS_MODEL_ID": model["model_id"],
        "SYS_MODEL_VERSION": model["model_version"],
    }
    metadata.update(
        {
            "SYS_" + key.upper(): json.dumps(
                (model.get("system") or {}).get(key),
                ensure_ascii=False,
                allow_nan=False,
            )
            for key in SYSTEM_FIELDS
        }
    )
    for tag, value in metadata.items():
        insert.add_attrib(
            tag,
            value,
            (offset + 15, 14),
            dxfattribs={"flags": 1, "height": 1, "layer": "TITLE_BLOCK"},
        )


def _product_identity(device, model, field):
    """An existing product never inherits the proposed lineup's identity."""
    if (
        device.get("reuse_existing") is True
        or device.get("product_origin") == "existing_source_equipment"
    ):
        return device.get(field)
    return device.get(field) or model.get(field)


def _position_state(device):
    return device.get("position_state") or "unknown"


def _prepared_position(device):
    return str(_position_state(device)).casefold() in {
        "prepared",
        "prepared_space",
        "space",
        "future",
    }


def _prepared_block(template):
    return template["blocks"]["breaker"] + "_PREPARED_POSITION"


def _symbol_block(template, device):
    return (
        _prepared_block(template)
        if _prepared_position(device)
        else template["blocks"]["breaker"]
    )


def _identity_scope(device):
    return (
        "source_local"
        if device.get("source_local_identity") is True
        else "declared_model"
        if device.get("tag")
        else "model_local"
    )


def _native_tag(device, model):
    if device.get("tag"):
        return device["tag"]
    # Internal identity is not a claimed customer/manufacturer tag. Its binding
    # is independently checked on each saved block and BOM row.
    payload = json.dumps(
        [str(model["model_id"]), str(device["id"])],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "model:" + hashlib.sha256(payload).hexdigest()[:32]


def _human_label(device):
    if device.get("display_tag") is not None:
        label = _value(device["display_tag"])
        return (
            f"{label} / SOURCE SECTION {_value(device['section_label'])}"
            if device.get("source_local_identity") is True
            and device.get("section_label") is not None
            else label
        )
    return (
        "UNLABELED SOURCE POSITION"
        if device.get("source_local_identity") is True
        else device.get("tag") or "SOURCE TAG UNKNOWN"
    )


def _connection_attrs(device):
    return {
        "layer": "ONE_LINE",
        **({"linetype": "RIVET_PREPARED_DASHED"} if _prepared_position(device) else {}),
    }


def _device_attrs(insert, device, model, x, y):
    values = {
        "EQUIPMENT_TAG": _native_tag(device, model),
        "SOURCE_LABEL": device.get("display_tag"),
        "SECTION_LABEL": device.get("section_label"),
        "IDENTITY_SCOPE": _identity_scope(device),
        "POSITION_STATE": _position_state(device),
        "DEVICE_FRAME": device.get("frame"),
        "FRAME_A": device.get("frame_a"),
        "TRIP_A": device.get("trip_a"),
        "TRIP_UNIT": device.get("trip_type"),
        "POLE_COUNT": device.get("pole_count"),
        "INTERRUPTING_KA": device.get("interrupting_ka"),
        "CATALOG_SCCR_KA": device.get("catalog_sccr_ka"),
        "PART_NUMBER": device.get("part_number"),
        "QUANTITY": 1,
        "MANUFACTURER": _product_identity(device, model, "manufacturer"),
        "PRODUCT_FAMILY": _product_identity(device, model, "family"),
        "NAMEPLATE": device.get("nameplate"),
        "SECTION_ID": device.get("section_id"),
        "POSITION_ID": device.get("position_id"),
        "MODEL_ID": model["model_id"],
        "MODEL_VERSION": model["model_version"],
        "PROPOSAL_STATUS": "PROPOSED",
        "MODEL_DEVICE_ID": device["id"],
        "SUPPLIED": _supplied_text(device),
    }
    visible = {
        "EQUIPMENT_TAG": (x - 22, y - 12, 3),
        "FRAME_A": (x - 22, y - 21, 2.5),
        "TRIP_A": (x + 5, y - 21, 2.5),
        "DEVICE_FRAME": (x - 22, y - 29, 2.5),
        "TRIP_UNIT": (x + 5, y - 29, 2.5),
    }
    if (
        device.get("source_local_identity") is True
        or device.get("display_tag") is not None
        or not device.get("tag")
    ):
        visible["SOURCE_LABEL"] = visible.pop("EQUIPMENT_TAG")
    for field, value in values.items():
        location = visible.get(field, (x, y, 2.5))
        display = _value(value)
        if field in ("FRAME_A", "TRIP_A") and value is not None:
            display += " A"
        insert.add_attrib(
            field,
            _cad_text(display),
            location[:2],
            dxfattribs={
                "height": location[2],
                "layer": "DEVICE_DATA",
                "flags": 0 if field in visible else 1,
            },
        )
    msp = insert.get_layout()
    _put(msp, "FRAME", (x - 22, y - 17), 1.6)
    _put(msp, "TRIP", (x + 5, y - 17), 1.6)
    if (
        device.get("source_local_identity") is True
        and device.get("section_label") is not None
    ):
        _put(
            msp,
            f"SOURCE SECTION {_text(device['section_label'])}",
            (x - 22, y - 7),
            1.6,
        )
    if _prepared_position(device):
        _put(
            msp,
            "FUTURE / P.S."
            if str(_position_state(device)).casefold() == "future"
            else "P.S. / PREPARED POSITION",
            (x - 22, y + 7),
            1.8,
        )


def _capture_page(doc, before, sheet_id, title):
    return {
        "sheet_id": sheet_id,
        "title": title,
        "handles": [
            entity.dxf.handle
            for entity in doc.modelspace()
            if entity.dxf.handle not in before
        ],
    }


def _write_one_line(path, model, template, digest):
    topology = model.get("topology") or {}
    connections = topology.get("connections") or []
    # Only the configurator's explicit design-intent bus is drawn. Do not turn
    # role labels or proximity into unrecorded circuit connections.
    if topology.get("state") == "proposed" and topology.get("bus_id") and connections:
        return _write_connected_one_line(path, model, template, digest)
    doc = _drawing_document(template)
    pages = []
    devices = model["devices"]
    for start in range(0, len(devices), 8):
        before = {entity.dxf.handle for entity in doc.modelspace()}
        sheet_id = f"SL-{start // 8 + 1:02d}"
        offset = len(pages) * 440
        _sheet(
            doc,
            model,
            template,
            sheet_id,
            "PROPOSED SWITCHBOARD ONE-LINE",
            offset,
            digest,
        )
        _put(
            doc.modelspace(),
            "Each branch is proposed from its model role/section assignment; electrical topology is pending engineer confirmation.",
            (offset + 15, 248),
            2.25,
        )
        for index, device in enumerate(devices[start : start + 8]):
            column, row = index % 4, index // 4
            x, y = offset + 58 + column * 96, 207 - row * 91
            # These are single-device symbols, not fabricated inter-device or terminal connections.
            doc.modelspace().add_line(
                (x, y + 5), (x, y + 19), dxfattribs=_connection_attrs(device)
            )
            doc.modelspace().add_line(
                (x, y - 5), (x, y - 8), dxfattribs=_connection_attrs(device)
            )
            insert = doc.modelspace().add_blockref(
                _symbol_block(template, device),
                (x, y),
                dxfattribs={"layer": "ONE_LINE"},
            )
            _device_attrs(insert, device, model, x, y)
            _put(
                doc.modelspace(),
                f"{_value(device.get('role'))} / SECTION {_section_label(model, device.get('section_id'))}",
                (x - 22, y + 23),
                2.4,
            )
            _put(
                doc.modelspace(),
                f"{_value(device.get('pole_count'))}P / {_value(device.get('interrupting_ka'), 'kAIC')}",
                (x - 22, y - 37),
                2.35,
            )
            _put(
                doc.modelspace(),
                f"POSITION {_position_label(model, device)}",
                (x - 22, y - 45),
                2.2,
            )
        pages.append(_capture_page(doc, before, sheet_id, "Proposed one-line"))
    doc.set_modelspace_vport(297, center=(210, 148.5))
    doc.saveas(path)
    return pages


def _write_connected_one_line(path, model, template, digest):
    topology = model["topology"]
    bus_id = topology["bus_id"]
    devices = {device["id"]: device for device in model["devices"]}
    incoming, outgoing, recorded = [], [], set()
    for connection in topology["connections"]:
        if (
            connection.get("to_bus_id") == bus_id
            and connection.get("from_device_id") in devices
        ):
            if connection.get("from_port") != "load":
                raise GenerationError(
                    "Unsupported logical topology: incoming connection must identify the device load side."
                )
            incoming.append(devices[connection["from_device_id"]])
            recorded.add(connection["from_device_id"])
        elif (
            connection.get("from_bus_id") == bus_id
            and connection.get("to_device_id") in devices
        ):
            if connection.get("to_port") != "line":
                raise GenerationError(
                    "Unsupported logical topology: outgoing connection must identify the device line side."
                )
            outgoing.append(devices[connection["to_device_id"]])
            recorded.add(connection["to_device_id"])
        else:
            raise GenerationError(
                "Unsupported or dangling logical connection; do not silently draw a different circuit."
            )
    if len(incoming) != 1 or len(recorded) != len(incoming) + len(outgoing):
        raise GenerationError(
            "This exporter supports one explicit main and distinct common-bus feeder branches; ties and duplicate endpoints require an engineering adapter."
        )
    if set(devices) != recorded:
        raise GenerationError(
            "Logical one-line topology does not account for every proposed device. Confirm the missing device connections."
        )
    _number(topology.get("bus_a"), "topology.bus_a")
    doc = _drawing_document(template)
    pages = []
    feeder_pages = [
        outgoing[start : start + 4] for start in range(0, len(outgoing), 4)
    ] or [[]]
    for page_index, feeders in enumerate(feeder_pages):
        before = {entity.dxf.handle for entity in doc.modelspace()}
        offset = len(pages) * 440
        sheet_id = f"SL-{len(pages) + 1:02d}"
        _sheet(
            doc,
            model,
            template,
            sheet_id,
            "PROPOSED SWITCHBOARD ONE-LINE",
            offset,
            digest,
        )
        msp = doc.modelspace()
        _put(
            msp,
            "Single-main/common-bus DESIGN INTENT from the selected model. Not terminal wiring or an observed as-built circuit.",
            (offset + 15, 248),
            2.25,
        )
        bus_y = 159
        bus_line = msp.add_line(
            (offset + 35, bus_y),
            (offset + 390, bus_y),
            dxfattribs={"layer": "ONE_LINE", "lineweight": 50},
        )
        _generation_xdata(
            doc, bus_line, ["proposed_bus", bus_id, model["model_version"]]
        )
        _put(
            msp,
            f"BUS-1 / {_value(topology.get('bus_a'), 'A')} / PROPOSED",
            (offset + 90, bus_y + 6),
            2.8,
        )
        if page_index == 0:
            main = incoming[0]
            x, y = offset + 57, 211
            msp.add_line((x, y + 5), (x, y + 20), dxfattribs={"layer": "ONE_LINE"})
            insert = msp.add_blockref(
                _symbol_block(template, main), (x, y), dxfattribs={"layer": "ONE_LINE"}
            )
            _device_attrs(insert, main, model, x + 52, y + 11)
            _put(
                msp,
                f"MAIN / SECTION {_section_label(model, main.get('section_id'))}",
                (x + 30, y + 23),
                2.5,
            )
            _put(
                msp,
                f"{_value(main.get('pole_count'))}P / {_value(main.get('interrupting_ka'), 'kAIC')}",
                (x + 30, y - 29),
                2.35,
            )
            line = msp.add_line(
                (x, y - 5), (x, bus_y), dxfattribs=_connection_attrs(main)
            )
            _generation_xdata(
                doc, line, ["proposed_logical_connection", main["id"], "load", bus_id]
            )
            _put(
                msp,
                "SOURCE / SERVICE CONNECTION TO BE CONFIRMED",
                (offset + 15, 241),
                2.1,
            )
        else:
            _put(
                msp,
                f"BUS CONTINUES FROM SL-01 / MAIN {incoming[0].get('tag') or incoming[0]['id']}",
                (offset + 35, 214),
                3,
            )
        for index, device in enumerate(feeders):
            x, y = offset + 58 + index * 96, 113
            insert = msp.add_blockref(
                _symbol_block(template, device),
                (x, y),
                dxfattribs={"layer": "ONE_LINE"},
            )
            _device_attrs(insert, device, model, x, y)
            line = msp.add_line(
                (x, bus_y), (x, y + 5), dxfattribs=_connection_attrs(device)
            )
            _generation_xdata(
                doc, line, ["proposed_logical_connection", bus_id, device["id"], "line"]
            )
            msp.add_line((x, y - 5), (x, y - 8), dxfattribs=_connection_attrs(device))
            _put(
                msp,
                f"FEEDER / SECTION {_section_label(model, device.get('section_id'))}",
                (x - 22, y + 22),
                2.4,
            )
            _put(
                msp,
                f"{_value(device.get('pole_count'))}P / {_value(device.get('interrupting_ka'), 'kAIC')}",
                (x - 22, y - 37),
                2.35,
            )
            _put(
                msp,
                f"POSITION {_position_label(model, device)}",
                (x - 22, y - 45),
                2.2,
            )
        if page_index < len(feeder_pages) - 1:
            _put(
                msp,
                f"BUS CONTINUES ON SL-{page_index + 2:02d}",
                (offset + 290, bus_y - 9),
                2.3,
            )
        pages.append(
            _capture_page(doc, before, sheet_id, "Proposed connected logical one-line")
        )
    doc.set_modelspace_vport(297, center=(210, 148.5))
    doc.saveas(path)
    return pages


def _generation_xdata(doc, entity, values):
    if "RIVET_GENERATION" not in doc.appids:
        doc.appids.new("RIVET_GENERATION")
    entity.set_xdata("RIVET_GENERATION", [(1000, str(value)) for value in values])


def _write_elevation(path, model, template, digest):
    doc = _drawing_document(template)
    pages = []
    sections = copy.deepcopy(model["sections"])
    assigned = {section["id"] for section in sections}
    unassigned = [
        device
        for device in model["devices"]
        if device.get("section_id") not in assigned
    ]
    if unassigned or not sections:
        sections.append(
            {"id": "UNASSIGNED", "kind": "assignment unknown", "positions": []}
        )
    views = []
    for section in sections:
        members = (
            [
                device
                for device in model["devices"]
                if device.get("section_id") == section["id"]
            ]
            if section["id"] != "UNASSIGNED"
            else unassigned
        )
        parts = [members[start : start + 3] for start in range(0, len(members), 3)] or [
            []
        ]
        for part_index, members_part in enumerate(parts):
            views.append((section, members_part, part_index + 1, len(parts)))
    # Dense assignments continue in additional schematic views of the SAME
    # section, not invented physical sections or silently omitted device blocks.
    for start in range(0, len(views), 5):
        before = {entity.dxf.handle for entity in doc.modelspace()}
        offset = len(pages) * 440
        sheet_id = f"FE-{len(pages) + 1:02d}"
        _sheet(
            doc,
            model,
            template,
            sheet_id,
            "PROPOSED FRONT ELEVATION - UNSCALED LAYOUT",
            offset,
            digest,
        )
        _put(
            doc.modelspace(),
            "Equal drawing boxes show assignments only. Unknown physical size/position regions are crossed and require shop dimensions.",
            (offset + 15, 248),
            2.25,
        )
        for index, (section, members, part_index, part_count) in enumerate(
            views[start : start + 5]
        ):
            x, y, width, height = offset + 17 + index * 78, 68, 72, 150
            msp = doc.modelspace()
            msp.add_lwpolyline(
                [(x, y), (x + width, y), (x + width, y + height), (x, y + height)],
                close=True,
                dxfattribs={"layer": "ELEVATION"},
            )
            unknown = any(
                section.get(field) is None
                for field in ("width_in", "height_in", "depth_in")
            )
            if unknown:
                for line in [
                    ((x, y), (x + width, y + height)),
                    ((x + width, y), (x, y + height)),
                ]:
                    msp.add_line(
                        *line, dxfattribs={"layer": "MISSING_DIMENSIONS", "color": 8}
                    )
            _put(
                msp, f"SECTION {_section_label(model, section['id'])}", (x + 3, 233), 3
            )
            _put(msp, _value(section.get("kind")), (x + 3, 225), 2.4)
            if part_count > 1:
                _put(
                    msp,
                    f"ASSIGNMENT VIEW {part_index}/{part_count} - SAME SECTION",
                    (x + 2, 240),
                    1.9,
                )
            _put(
                msp,
                f"W {_value(section.get('width_in'), 'in')} / D {_value(section.get('depth_in'), 'in')}",
                (x + 2, 58),
                2.15,
            )
            _put(
                msp,
                f"H {_value(section.get('height_in'), 'in')} / BUS {_value(section.get('bus_a'), 'A')}",
                (x + 2, 49),
                2.15,
            )
            for index_in_section, device in enumerate(members):
                dx, dy = x + 36, y + height - 15 - index_in_section * 43
                insert = msp.add_blockref(
                    _symbol_block(template, device),
                    (dx, dy),
                    dxfattribs={"layer": "ELEVATION"},
                )
                _device_attrs(insert, device, model, dx, dy)
            if not members:
                _put(msp, "NO ASSIGNED DEVICE", (x + 3, y + height / 2), 2.3)
        pages.append(
            _capture_page(doc, before, sheet_id, "Proposed schematic front elevation")
        )
    doc.set_modelspace_vport(297, center=(210, 148.5))
    doc.saveas(path)
    return pages


def _supplied_text(device):
    if device.get("supplied") is None:
        return "unknown"
    return "yes" if device["supplied"] else "by others"


def _rows(model):
    rows = []
    for device in model["devices"]:
        rows.append(
            {
                "equipment_tag": _native_tag(device, model),
                "source_label": device.get("display_tag"),
                "section_label": device.get("section_label"),
                "identity_scope": _identity_scope(device),
                "model_device_id": device["id"],
                "position_state": _position_state(device),
                "quantity": 1,
                "manufacturer": _product_identity(device, model, "manufacturer"),
                "product_family": _product_identity(device, model, "family"),
                "device_frame": device.get("frame"),
                "frame_a": device.get("frame_a"),
                "trip_a": device.get("trip_a"),
                "trip_unit": device.get("trip_type"),
                "pole_count": device.get("pole_count"),
                "interrupting_ka": device.get("interrupting_ka"),
                "catalog_sccr_ka": device.get("catalog_sccr_ka"),
                "part_number": device.get("part_number"),
                "section_id": device.get("section_id"),
                "position_id": device.get("position_id"),
                "supplied": _supplied_text(device),
                "nameplate": device.get("nameplate"),
                "model_id": model["model_id"],
                "model_version": model["model_version"],
                "proposal_status": "PROPOSED",
                "source_references": _source_labels(device.get("source_refs") or []),
            }
        )
    return rows


def _source_labels(refs):
    labels = []
    for ref in refs:
        if isinstance(ref, str):
            labels.append(ref)
        elif isinstance(ref, dict):
            name = (
                ref.get("source_id")
                or ref.get("document_id")
                or ref.get("title")
                or ref.get("url")
                or "Cited model source"
            )
            page = ref.get("page") or ref.get("physical_page")
            table = ref.get("table") or ref.get("cell")
            labels.append(
                f"{name}{f' / p. {page}' if page else ''}{f' / {table}' if table else ''}"
            )
    return "\n".join(labels)


def _nameplate_rows(model):
    return [
        {
            "equipment_tag": _native_tag(device, model),
            "source_label": device.get("display_tag"),
            "section_label": device.get("section_label"),
            "identity_scope": _identity_scope(device),
            "model_device_id": device["id"],
            "position_state": _position_state(device),
            "nameplate": device.get("nameplate"),
            "text_status": "proposed text - verify"
            if device.get("nameplate")
            else "text needed - unknown",
            "section_id": device.get("section_id"),
            "position_id": device.get("position_id"),
            "model_id": model["model_id"],
            "model_version": model["model_version"],
            "proposal_status": "PROPOSED",
        }
        for device in model["devices"]
    ]


def _csv(path, rows, headers):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["mfr" if field == "manufacturer" else field for field in headers]
        )
        for row in rows:
            # Excel/CSV consumers must not execute customer text as a formula.
            writer.writerow(
                [
                    (
                        "'" + value
                        if isinstance(value, str)
                        and value.lstrip().startswith(("=", "+", "-", "@"))
                        else value
                    )
                    for value in (row.get(key) for key in headers)
                ]
            )


def _sheet_table(ws, headers, rows, model):
    # The generic equipment reader accepts mfr. 'manufacturer + part_number +
    # quantity' is a CAD-report signature in the existing import router and
    # would otherwise discard this workbook's explicit rating columns.
    ws.append(["mfr" if field == "manufacturer" else field for field in headers])
    for row in rows:
        ws.append([row.get(field) for field in headers])
    for row in ws:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"  # preserve literal source text, including =...
            cell.font = Font(name="Calibri", size=10, color="172635")
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if cell.row == 1:
                cell.fill = PatternFill("solid", fgColor="172635")
                cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
            elif cell.row % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F0F3F5")
        ws.row_dimensions[row[0].row].height = 30 if row[0].row == 1 else 28
    for column in ws.columns:
        field = column[0].value
        ws.column_dimensions[column[0].column_letter].width = (
            42
            if field == "source_references"
            else 30
            if field in ("nameplate", "model_id", "text_status")
            else 18
        )
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:1"
    ws.oddHeader.center.text = (
        f"{model['model_id']} | MODEL VERSION {model['model_version']}"
    )
    ws.oddFooter.center.text = PROPOSED
    ws.oddFooter.right.text = "Page &P of &N"


def _write_workbook(path, model, digest, gaps):
    wb = Workbook()
    ws = wb.active
    ws.title = "Equipment BOM"
    _sheet_table(ws, BOM_HEADERS, _rows(model), model)
    for row_index, device in enumerate(model["devices"], 2):
        refs = device.get("source_refs") or []
        if refs:
            ws.cell(
                row_index, BOM_HEADERS.index("source_references") + 1
            ).comment = Comment(
                json.dumps(refs, indent=2, ensure_ascii=False), "Rivet source evidence"
            )
            ws.row_dimensions[row_index].height = min(120, max(28, 14 * len(refs)))
    system_sheet = wb.create_sheet("System requirements")
    system_sheet.append(
        [
            "Field",
            "Value JSON",
            "Unit",
            "Model ID",
            "Model version",
            "Source references",
        ]
    )
    system = model.get("system") or {}
    evidence = system.get("provenance") or system.get("field_evidence") or {}
    for key, unit in SYSTEM_FIELDS.items():
        system_sheet.append(
            [
                key,
                json.dumps(system.get(key), ensure_ascii=False, allow_nan=False),
                unit,
                model["model_id"],
                model["model_version"],
                json.dumps(
                    evidence.get(key, {}).get("source_refs", []), ensure_ascii=False
                ),
            ]
        )
    for row in system_sheet:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for column in system_sheet.columns:
        system_sheet.column_dimensions[column[0].column_letter].width = 32
    system_sheet.freeze_panes = "A2"
    # Nameplates have a separate CSV/PDF schedule. Repeating those same cells
    # on another equipment-tagged workbook sheet would create two BOM evidence
    # anchors and make the established literal correction adapter ambiguous.
    info = wb.create_sheet("Issue information")
    for row in [
        ("Status", PROPOSED),
        ("Model ID", model["model_id"]),
        ("Model version", model["model_version"]),
        ("Model SHA256", digest),
        ("Rule pack", model.get("rule_pack_id")),
        ("Rule pack version", model.get("rule_pack_version")),
        (
            "Physical dimensions",
            "Schematic layout only; unknown dimensions remain unknown.",
        ),
        (
            "Quantity",
            "One row per proposed physical position. Prepared/future rows reserve slots and do not establish populated or supplied devices. Printed source labels are local identities, not enterprise tags; unknown supply remains unknown.",
        ),
        ("Engineer approval", "Not approved. No production release."),
        ("Unresolved geometry", json.dumps(gaps, ensure_ascii=False)),
        (
            "Unresolved questions",
            json.dumps(model.get("unresolved_questions") or [], ensure_ascii=False),
        ),
    ]:
        info.append(row)
    for row in info:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
            cell.font = Font(name="Calibri", size=11)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        info.row_dimensions[row[0].row].height = 32
    info.column_dimensions["A"].width = 28
    info.column_dimensions["B"].width = 110
    wb.properties.title = f"{model['model_id']} - proposed equipment model version {model['model_version']}"
    wb.properties.subject = PROPOSED
    wb.properties.description = f"Model SHA256 {digest}"
    wb.save(path)


def _identity_equal(a, b):
    return " ".join(_text(a).casefold().split()) == " ".join(
        _text(b).casefold().split()
    )


def _select_cut_sheets(model, cut_sheets):
    selected, excluded = [], []
    devices = {device["id"]: device for device in model["devices"]}
    page_total = 0
    for index, item in enumerate(cut_sheets or []):
        reason = None
        if not isinstance(item, dict):
            excluded.append(
                {"index": index, "reason": "Invalid cut-sheet declaration."}
            )
            continue
        path = Path(item.get("path") or "")
        ids, identity, pages = (
            item.get("component_ids"),
            item.get("identity") or {},
            item.get("pages"),
        )
        try:
            if item.get("verified") is not True or not item.get("source_ref"):
                reason = "A verified, cited component-to-source mapping is required."
            elif (
                not ids
                or not isinstance(ids, list)
                or any(device_id not in devices for device_id in ids)
            ):
                reason = "Cut sheet is not mapped exclusively to devices in the selected design."
            elif any(
                devices[device_id].get("supplied") is not True for device_id in ids
            ):
                reason = "Mapped component supply is unknown or by others."
            elif (
                not path.is_file()
                or path.is_symlink()
                or not item.get("sha256")
                or _sha(path) != item["sha256"]
            ):
                reason = (
                    "Local cut-sheet PDF is missing or its source hash does not match."
                )
            elif (
                not isinstance(pages, list)
                or not pages
                or any(
                    isinstance(page, bool) or not isinstance(page, int) or page < 1
                    for page in pages
                )
                or len(set(pages)) != len(pages)
            ):
                reason = "Explicit, unique physical PDF page numbers are required."
            elif page_total + len(pages) > MAX_CUT_SHEET_PAGES:
                reason = "Cut-sheet page budget exceeded."
            else:
                for device_id in ids:
                    device = devices[device_id]
                    if device.get("part_number"):
                        fields = ("part_number",)
                    else:
                        fields = ("manufacturer", "family", "frame")
                    if any(
                        not identity.get(field)
                        or not (device.get(field) or model.get(field))
                        or not _identity_equal(
                            identity[field], device.get(field) or model.get(field)
                        )
                        for field in fields
                    ):
                        reason = "Declared cut-sheet identity does not establish the supplied component's known part or manufacturer/family/frame."
                        break
                    for field in ("manufacturer", "family", "frame", "part_number"):
                        known = device.get(field) or model.get(field)
                        if (
                            identity.get(field)
                            and known
                            and not _identity_equal(identity[field], known)
                        ):
                            reason = "Declared cut-sheet identity conflicts with the selected component."
                            break
                if not reason:
                    with pymupdf.open(path) as pdf:
                        if pdf.needs_pass or any(
                            page > pdf.page_count for page in pages
                        ):
                            reason = (
                                "A selected source page is unavailable or encrypted."
                            )
        except (OSError, ValueError, pymupdf.FileDataError) as exc:
            reason = f"Source PDF could not be verified: {type(exc).__name__}."
        if reason:
            excluded.append(
                {
                    "index": index,
                    "path": str(path),
                    "component_ids": ids,
                    "reason": reason,
                }
            )
        else:
            page_total += len(pages)
            selected.append(copy.deepcopy(item))
    return selected, excluded


def _pdf_drawing(doc, page):
    backend = PyMuPdfBackend()
    handles = set(page["handles"])
    Frontend(
        RenderContext(doc),
        backend,
        config=config.Configuration(background_policy=config.BackgroundPolicy.WHITE),
    ).draw_layout(
        doc.modelspace(),
        finalize=True,
        filter_func=lambda entity: entity.dxf.handle in handles,
    )
    return backend.get_pdf_bytes(layout.Page(420, 297, layout.Units.mm))


def _p(text, style):
    return Paragraph(escape(_text(text, 12000)), style)


def _schedule_pdf(model, digest, gaps, cut_sheets, exclusions):
    stream = io.BytesIO()
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "TableCell",
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#172635"),
        )
    )
    styles.add(
        ParagraphStyle(
            "SmallNote",
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#475569"),
        )
    )
    styles["Title"].fontName = "Helvetica-Bold"
    story = [
        _p("PROPOSED SWITCHBOARD APPROVAL PACKAGE", styles["Title"]),
        Spacer(1, 14),
        _p(
            _plain(model, "order_name", _plain(model, "job_number", model["model_id"])),
            styles["Heading1"],
        ),
        _p(
            f"Model {model['model_id']} | Version {model['model_version']}",
            styles["Heading2"],
        ),
        _p(PROPOSED, styles["Heading2"]),
        _p(
            "System: "
            + " | ".join(
                f"{key.replace('_', ' ')}: {_value((model.get('system') or {}).get(key), unit)}"
                for key, unit in SYSTEM_FIELDS.items()
            ),
            styles["SmallNote"],
        ),
        Spacer(1, 18),
        _p(
            f"{len(model['devices'])} proposed physical positions | {len(model['sections'])} proposed sections",
            styles["Normal"],
        ),
        Spacer(1, 12),
        _p(
            "The DXF sheets, equipment BOM and nameplate schedule are generated from the same immutable model snapshot. Ratings are proposed model values, not independent verification. Schematic symbol and section box dimensions are plotting geometry only; this package cannot be used to fabricate or release equipment.",
            styles["BodyText"],
        ),
        Spacer(1, 12),
        _p(f"Source model SHA256: {digest}", styles["SmallNote"]),
        _p(
            f"Rule pack: {_value(model.get('rule_pack_id'))} / {_value(model.get('rule_pack_version'))}",
            styles["SmallNote"],
        ),
        _p(
            f"Verified supplied-component cut sheets included: {len(cut_sheets)}. Excluded declarations: {len(exclusions)}.",
            styles["SmallNote"],
        ),
        Spacer(1, 12),
        _p("Items requiring engineering confirmation", styles["Heading2"]),
    ]
    questions = model.get("unresolved_questions") or []
    if gaps:
        story.append(
            _p(
                f"{len(gaps)} section, position or assignment records have missing physical information. Front elevation is schematic and not to scale.",
                styles["BodyText"],
            )
        )
    else:
        story.append(
            _p(
                "Supplied dimensions remain proposed. Manufacturer layout, clearances, mechanical details and production drafting standards still require engineering review.",
                styles["BodyText"],
            )
        )
    for question in questions:
        text = (
            question
            if isinstance(question, str)
            else question.get("question")
            or question.get("message")
            or question.get("reason")
            or json.dumps(question, ensure_ascii=False)
        )
        scope = ""
        if isinstance(question, dict) and question.get("device_id"):
            device = next(
                (
                    item
                    for item in model["devices"]
                    if item["id"] == question["device_id"]
                ),
                None,
            )
            scope = f"{device.get('tag') or device['id']}: " if device else ""
        story.append(_p(f"- {scope}{text}", styles["BodyText"]))
    for excluded in exclusions:
        story.append(
            _p(f"Cut sheet excluded: {excluded['reason']}", styles["SmallNote"])
        )
    if model.get("rule_results"):
        story += [Spacer(1, 14), _p("Configuration rule results", styles["Heading2"])]
        rule_data = [
            [_p(label, styles["TableCell"]) for label in ("Rule", "Result", "Detail")]
        ]
        for rule in model["rule_results"]:
            rule_data.append(
                [
                    _p(
                        rule.get("rule_id") or rule.get("id") or "Unspecified",
                        styles["TableCell"],
                    ),
                    _p(rule.get("status") or "unknown", styles["TableCell"]),
                    _p(
                        rule.get("reason")
                        or rule.get("message")
                        or rule.get("explanation")
                        or "",
                        styles["TableCell"],
                    ),
                ]
            )
        table = LongTable(
            rule_data, colWidths=[180, 100, 790], repeatRows=1, hAlign="LEFT"
        )
        table.setStyle(_table_style())
        story.append(table)
    story += [
        PageBreak(),
        _p("PROPOSED EQUIPMENT BILL OF MATERIALS", styles["Heading1"]),
    ]
    headers = (
        "Tag",
        "Role / supply",
        "Frame",
        "Frame A",
        "Trip A",
        "Poles / trip",
        "kAIC",
        "Section / position",
        "Part number",
    )
    data = [[_p(label, styles["TableCell"]) for label in headers]]
    for device, row in zip(model["devices"], _rows(model), strict=True):
        data.append(
            [
                _p(value, styles["TableCell"])
                for value in (
                    _human_label(device),
                    f"{_value(device.get('role'))} / {row['position_state']} / {row['supplied']}",
                    _value(row["device_frame"]),
                    _value(row["frame_a"]),
                    _value(row["trip_a"]),
                    f"{_value(row['pole_count'])}P / {_value(row['trip_unit'])}",
                    _value(row["interrupting_ka"]),
                    f"{_section_label(model, device.get('section_id'))} / {_position_label(model, device)}",
                    _value(row["part_number"]),
                )
            ]
        )
    table = LongTable(
        data,
        colWidths=[100, 155, 80, 80, 80, 135, 70, 180, 190],
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(_table_style())
    story += [
        table,
        Spacer(1, 12),
        _p(
            "Quantity: one per listed physical position. Prepared/future rows reserve a position and are not populated-device or purchase claims. Source labels are literal local drawing labels, not enterprise tags. Supply status 'unknown' is not evidence that an item is supplied. Interrupting capacity is device kAIC; no assembly SCCR is inferred.",
            styles["SmallNote"],
        ),
        PageBreak(),
        _p("PROPOSED NAMEPLATE SCHEDULE", styles["Heading1"]),
    ]
    data = [
        [
            _p(label, styles["TableCell"])
            for label in (
                "Equipment tag",
                "Nameplate text",
                "Text status",
                "Section / position",
            )
        ]
    ]
    for row, device in zip(_nameplate_rows(model), model["devices"], strict=True):
        data.append(
            [
                _p(value, styles["TableCell"])
                for value in (
                    _human_label(device),
                    _value(row["nameplate"]),
                    row["text_status"],
                    f"{_section_label(model, device.get('section_id'))} / {_position_label(model, device)}",
                )
            ]
        )
    table = LongTable(data, colWidths=[180, 420, 260, 210], repeatRows=1, hAlign="LEFT")
    table.setStyle(_table_style())
    story.append(table)
    doc = SimpleDocTemplate(
        stream,
        pagesize=landscape(A3),
        leftMargin=55,
        rightMargin=55,
        topMargin=48,
        bottomMargin=55,
        title=f"{model['model_id']} - proposed model version {model['model_version']}",
        author="Rivet",
    )

    def footer(canvas, document):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#475569"))
        canvas.drawString(
            55,
            30,
            f"{model['model_id']} | MODEL VERSION {model['model_version']} | {PROPOSED}",
        )

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()


def _table_style():
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8EF")),
            (
                "ROWBACKGROUNDS",
                (0, 1),
                (-1, -1),
                [colors.white, colors.HexColor("#F4F6F8")],
            ),
            ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor("#64748B")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]
    )


def _write_pdf(path, model, digest, gaps, cut_sheets, exclusions, drawing_pages, work):
    with pymupdf.open(
        stream=_schedule_pdf(model, digest, gaps, cut_sheets, exclusions),
        filetype="pdf",
    ) as schedules:
        assembled = pymupdf.open()
        # Cover, actual CAD renders, then schedules. No separately drawn PDF geometry.
        assembled.insert_pdf(schedules, from_page=0, to_page=0)
        for filename, pages in drawing_pages:
            doc = ezdxf.readfile(work / filename)
            for page in pages:
                with pymupdf.open(
                    stream=_pdf_drawing(doc, page), filetype="pdf"
                ) as rendered:
                    for rendered_page in rendered:
                        rendered_page.insert_text(
                            (20, rendered_page.rect.height - 7),
                            f"PROPOSED | MODEL VERSION {model['model_version']} | {page['sheet_id']} | MODEL {digest[:20]}",
                            fontsize=7,
                        )
                    assembled.insert_pdf(rendered)
        if schedules.page_count > 1:
            assembled.insert_pdf(schedules, from_page=1)
        for item in cut_sheets:
            # Recheck exact bytes at use time; verification cannot race replacement.
            if _sha(item["path"]) != item["sha256"]:
                raise GenerationError("Cut-sheet source changed during generation.")
            with pymupdf.open(item["path"]) as source:
                for physical_page in item["pages"]:
                    assembled.insert_pdf(
                        source, from_page=physical_page - 1, to_page=physical_page - 1
                    )
                    page = assembled[-1]
                    # Put the versioned status in a new footer margin, without covering source content.
                    old = page.rect
                    target = pymupdf.open()
                    expanded = target.new_page(width=old.width, height=old.height + 30)
                    expanded.show_pdf_page(
                        pymupdf.Rect(0, 0, old.width, old.height),
                        assembled,
                        assembled.page_count - 1,
                    )
                    expanded.insert_text(
                        (8, old.height + 12),
                        f"PROPOSED | MODEL VERSION {model['model_version']} | supplied: {', '.join(item['component_ids'])}",
                        fontsize=7,
                    )
                    expanded.insert_text(
                        (8, old.height + 22),
                        f"Source {item['sha256'][:20]} / physical page {physical_page}",
                        fontsize=6,
                    )
                    assembled.delete_page(assembled.page_count - 1)
                    assembled.insert_pdf(target)
                    target.close()
        assembled.set_metadata(
            {
                "title": f"{model['model_id']} - proposed version {model['model_version']}",
                "author": "Rivet",
                "subject": f"{PROPOSED}; model SHA256 {digest}",
            }
        )
        for page_index, page in enumerate(assembled):
            page.insert_textbox(
                pymupdf.Rect(
                    page.rect.width - 125,
                    page.rect.height - 18,
                    page.rect.width - 10,
                    page.rect.height - 2,
                ),
                f"Package page {page_index + 1} of {assembled.page_count}",
                fontsize=7,
                align=pymupdf.TEXT_ALIGN_RIGHT,
            )
        assembled.save(path, garbage=4, deflate=True)
        assembled.close()


def generate_package(
    design: dict,
    *,
    output_dir,
    template: dict | None = None,
    cut_sheets: list[dict] | None = None,
) -> dict:
    """Write a version-locked, proposed package, without sending or releasing it.

    Cut sheets require explicit physical pages, a verified source mapping,
    source hash and identity, and supplied=True on every mapped model device.
    A family/frame catalog page is allowed only when the model has no exact
    part number; it is never promoted into a selected SKU.

    A caller may supply a hash-pinned DXF library and named breaker/title blocks.
    Their units are sheet/plot mm in v1; physical manufacturer geometry remains
    outside this schematic adapter. Production elevation requests fail closed.
    """
    model = _snapshot(design)
    selected_template = _template(template)
    gaps = _geometry_gaps(model)
    if selected_template["elevation_mode"] == "production":
        raise GenerationError(
            "Production elevation is unavailable: v1 generates proposed schematic assignments only; manufacturer geometry and engineering approval are required."
        )
    selected, exclusions = _select_cut_sheets(model, cut_sheets)
    digest = hashlib.sha256(_json_bytes(model)).hexdigest()
    fingerprint = hashlib.sha256(
        _json_bytes(
            {
                "version": VERSION,
                "model_sha256": digest,
                "template": selected_template,
                "cut_sheets": cut_sheets or [],
            }
        )
    ).hexdigest()
    destination = Path(output_dir).resolve()
    if Path(output_dir).is_symlink():
        raise GenerationError("Output directory cannot be a symbolic link.")
    manifest_path = destination / "manifest.json"
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        if old.get("generation_fingerprint") != fingerprint:
            raise GenerationError(
                "This output directory already contains a different design or asset version; generate into a new version directory."
            )
        if all(
            Path(item["path"]).is_file()
            and not Path(item["path"]).is_symlink()
            and _sha(item["path"]) == item["sha256"]
            for item in old.get("artifacts", [])
        ):
            return old
        raise GenerationError(
            "An existing generated artifact is missing or changed; preserve that history and create a new package version."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and any(destination.iterdir()):
        raise GenerationError(
            "Output directory must be empty or contain the matching complete package."
        )
    work = Path(tempfile.mkdtemp(prefix=".rivet-generation-", dir=destination.parent))
    os.chmod(work, 0o700)
    try:
        (work / "model-snapshot.json").write_bytes(_json_bytes(model))
        one_line_pages = _write_one_line(
            work / "one-line.dxf", model, selected_template, digest
        )
        elevation_pages = _write_elevation(
            work / "front-elevation.dxf", model, selected_template, digest
        )
        _write_workbook(work / "bom.xlsx", model, digest, gaps)
        _csv(work / "bom.csv", _rows(model), BOM_HEADERS)
        _csv(work / "nameplates.csv", _nameplate_rows(model), NAMEPLATE_HEADERS)
        _write_pdf(
            work / "submittal.pdf",
            model,
            digest,
            gaps,
            selected,
            exclusions,
            [
                ("one-line.dxf", one_line_pages),
                ("front-elevation.dxf", elevation_pages),
            ],
            work,
        )
        kinds = {
            "one-line.dxf": "one_line_dxf",
            "front-elevation.dxf": "front_elevation_dxf",
            "bom.xlsx": "bom_xlsx",
            "bom.csv": "bom_csv",
            "nameplates.csv": "nameplate_schedule_csv",
            "submittal.pdf": "submittal_pdf",
            "model-snapshot.json": "model_snapshot",
        }
        manifest = {
            "version": VERSION,
            "model_id": model["model_id"],
            "model_version": model["model_version"],
            "status": "proposed",
            "engineering_approved": False,
            "production_released": False,
            "model_sha256": digest,
            "generation_fingerprint": fingerprint,
            "template": selected_template,
            "artifacts": [
                {
                    "kind": kinds[path.name],
                    "name": path.name,
                    "path": str(destination / path.name),
                    "sha256": _sha(path),
                    "size_bytes": path.stat().st_size,
                    "model_version": model["model_version"],
                    "status": "proposed",
                }
                for path in sorted(work.iterdir())
            ],
            "drawing_sheets": [
                {
                    "file": filename,
                    "sheets": [
                        {key: value for key, value in page.items() if key != "handles"}
                        for page in pages
                    ],
                }
                for filename, pages in [
                    ("one-line.dxf", one_line_pages),
                    ("front-elevation.dxf", elevation_pages),
                ]
            ],
            "drawing_reference_labels": {
                "sections": [
                    {
                        "model_section_id": section["id"],
                        "drawing_label": _section_label(model, section["id"]),
                    }
                    for section in model["sections"]
                ],
                "positions": [
                    {
                        "model_device_id": device["id"],
                        "model_position_id": device.get("position_id"),
                        "drawing_label": _position_label(model, device),
                    }
                    for device in model["devices"]
                ],
                "label_scope": "Proposed model-local drawing references, not asserted customer sheet identities.",
            },
            "device_instances": len(model["devices"]),
            "section_instances": len(model["sections"]),
            "unresolved_geometry": gaps,
            "unresolved_questions": model.get("unresolved_questions") or [],
            "cut_sheets": [
                {
                    key: item.get(key)
                    for key in (
                        "sha256",
                        "pages",
                        "component_ids",
                        "identity",
                        "source_ref",
                    )
                }
                for item in selected
            ],
            "cut_sheet_exclusions": exclusions,
            "limitations": [
                "Schematic device symbols and equal-width section boxes are plotting geometry, not manufacturer dimensions.",
                "Inter-device topology, wiring, plan views, mechanical clearances and production release are not verified by this exporter.",
                "No exact manufacturer part number is inferred from a frame name.",
                "Every generated value remains proposed until an engineer reviews it.",
            ],
        }
        (work / "manifest.json").write_bytes(_json_bytes(manifest))
        if destination.exists():
            destination.rmdir()  # still-empty caller-created output folder
        work.rename(destination)
        return manifest
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
