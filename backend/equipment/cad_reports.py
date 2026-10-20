"""Read explicit electrical CAD reports without inventing a physical CAD model.

AutoCAD Electrical report labels are defined by Autodesk's Component Data Fields
and Wire From/To Data Fields documentation. HarnessData is the actual published
AU2019 ENR321974 electrical XML export. EPLAN CSV/XLSX labels are configurable;
only explicit English full-DT / source / target signatures are recognized.
Reported connections are declarations, not independently verified line geometry.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .schemas import SourceInput

MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 10000
MAX_COLUMNS = 100
MAX_XML_ELEMENTS = 150000
MAX_XML_DEPTH = 24
MAX_TEXT = 4000


def _id(*parts):
    return hashlib.sha256("\x1f".join(str(x) for x in parts).encode()).hexdigest()[:24]


def _text(value):
    return "" if value is None else str(value).strip()


def _key(value):
    return re.sub(r"[^a-z0-9]", "", _text(value).casefold())


def _literal(value):
    text = _text(value)
    return text if text and len(text) <= MAX_TEXT else None


def _single_tag(value):
    text = _literal(value)
    if (
        text
        and not re.search(r"[,;\n]", text)
        and text.casefold() not in {"total", "subtotal"}
    ):
        return text
    return None


def _quantity(value):
    text = _text(value)
    return int(text) if re.fullmatch(r"[1-9]\d{0,6}", text) else None


def _base(source, fmt):
    return {
        "schema_version": "rivet.cad-report.v1",
        "source_id": source.id,
        "sha256": source.sha256.lower(),
        "format": fmt,
        "status": "read",
        "components": [],
        "connections": [],
        "observed_bom_rows": [],
        "warnings": [],
        "uncertain": [],
        "source_row_counts": {"components": 0, "connections": 0, "bom_rows": 0},
        "imported_counts": {},
        "engineering_verified": False,
        "limitation": "CAD report declarations preserve exported IDs and pins. They do not independently verify physical connectivity, assembly placement, geometry or complete export scope.",
    }


def _warn(report, code, message, **location):
    report["status"] = "partial"
    report["warnings"].append({"code": code, "message": message, **location})


def _anchor(source, **where):
    return {
        "source_id": source.id,
        "source_name": source.name,
        "sha256": source.sha256.lower(),
        **where,
    }


def _component(report, source, tag, properties, evidence, *, pins=(), raw_tag=None):
    report["source_row_counts"]["components"] += 1
    item = {
        "id": _id(source.id, "component", evidence),
        "tag": tag,
        "raw_tag": raw_tag if raw_tag is not None else tag,
        "properties": properties,
        "pins": list(pins),
        "source": _anchor(source, **evidence),
        "confidence": "explicit_report",
        "engineering_verified": False,
    }
    report["components"].append(item)
    if not tag:
        _warn(
            report,
            "component_tag_unknown",
            "The exported component does not contain one explicit device tag.",
            **evidence,
        )
    return item


def _connection(report, source, number, endpoint1, endpoint2, evidence, **properties):
    report["source_row_counts"]["connections"] += 1
    complete = all(x.get("device_tag") and x.get("pin") for x in (endpoint1, endpoint2))
    item = {
        "id": _id(source.id, "connection", evidence),
        "wire_number": number,
        "from": endpoint1,
        "to": endpoint2,
        "properties": properties,
        "source": _anchor(source, **evidence),
        "status": "reported" if complete else "unknown_endpoint",
        "confidence": "explicit_report",
        "geometry_verified": False,
    }
    report["connections"].append(item)
    if not complete:
        _warn(
            report,
            "wire_endpoint_unknown",
            "The source report has a missing device tag or terminal. No endpoint was inferred.",
            **evidence,
        )


def _facts(report, source):
    facts = []
    for component in report["components"]:
        if not component["tag"]:
            continue
        for field in ("part_number", "quantity"):
            value = component["properties"].get(field)
            if value is None:
                continue
            evidence = {
                k: v
                for k, v in component["source"].items()
                if k not in {"source_id", "source_name", "sha256"}
            }
            facts.append(
                {
                    "id": _id(component["id"], field, value),
                    "source_id": source.id,
                    "source_name": source.name,
                    "role": source.role,
                    "revision": source.revision,
                    "sha256": source.sha256.lower(),
                    "lifecycle": source.lifecycle,
                    "device_tag": component["tag"],
                    "field": field,
                    "value": value,
                    "unit": "each" if field == "quantity" else None,
                    "raw_value": str(value),
                    "evidence": evidence,
                }
            )
    report["imported_counts"] = {
        "components": len(report["components"]),
        "connections": len(report["connections"]),
        "connections_with_complete_reported_endpoints": sum(
            x["status"] == "reported" for x in report["connections"]
        ),
        "connections_with_unknown_endpoints": sum(
            x["status"] != "reported" for x in report["connections"]
        ),
        "bom_rows": len(report["observed_bom_rows"]),
        "facts": len(facts),
    }
    return facts


def _xml(data, source):
    # Reject rather than expand a DTD/entity payload; stdlib never fetches a URL.
    decoded = data.decode("utf-8-sig")
    if "\x00" in decoded:
        raise ValueError("CAD XML must use UTF-8 encoding without null bytes.")
    upper = decoded.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise ValueError("CAD XML DTDs and entities are not allowed.")
    parser = ET.XMLPullParser(events=("start", "end"))
    depth = count = 0
    for start in range(0, len(data), 65536):
        parser.feed(data[start : start + 65536])
        for event, _ in parser.read_events():
            if event == "start":
                depth += 1
                count += 1
                if depth > MAX_XML_DEPTH or count > MAX_XML_ELEMENTS:
                    raise ValueError("CAD XML exceeds the depth or element limit.")
            else:
                depth -= 1
    parser.close()
    root = ET.fromstring(data)
    if root.tag != "HarnessData":
        return None
    report = _base(source, "autocad_electrical_harness_xml")
    for hi, harness in enumerate(root.findall("Harness"), 1):
        for i, node in enumerate(harness.findall("Connector"), 1):
            props = {
                n.get("Name", ""): n.get("Value", "") for n in node.findall("Property")
            }
            normalized = {
                "raw_properties": props,
                "definition": node.get("Definition"),
                "part_number": _literal(props.get("CAT")),
                "manufacturer": _literal(props.get("MFG")),
            }
            # Unnamed RATING1..12 remain raw: 100/12.5kV is not a frame current.
            pins = [
                {"pin": p.get("Definition") or None, "source_index": j}
                for j, p in enumerate(node.findall("Pin"), 1)
            ]
            _component(
                report,
                source,
                _single_tag(node.get("RefDes")),
                normalized,
                {"xml_path": f"/HarnessData/Harness[{hi}]/Connector[{i}]"},
                pins=pins,
                raw_tag=node.get("RefDes"),
            )
            names = [p["pin"] for p in pins if p["pin"]]
            if len(names) != len(set(names)):
                _warn(
                    report,
                    "duplicate_exported_pin_labels",
                    "Repeated pin labels are preserved; a report cannot establish unique physical terminal incidence.",
                    xml_path=f"/HarnessData/Harness[{hi}]/Connector[{i}]",
                )

        def wire(node, location, **props):
            ends = []
            for key in ("Pin1", "Pin2"):
                p = node.find(key)
                ends.append(
                    {
                        "device_tag": _literal(p.get("ConnectorRefDes"))
                        if p is not None
                        else None,
                        "pin": _literal(p.get("ConnectorPin"))
                        if p is not None
                        else None,
                    }
                )
            _connection(
                report,
                source,
                _literal(node.get("Name")),
                ends[0],
                ends[1],
                {"xml_path": location},
                definition=node.get("Definition"),
                **props,
            )

        for i, node in enumerate(harness.findall("Wire"), 1):
            wire(node, f"/HarnessData/Harness[{hi}]/Wire[{i}]")
        for ci, cable in enumerate(harness.findall("Cable"), 1):
            for wi, node in enumerate(cable.findall("CableWire"), 1):
                wire(
                    node,
                    f"/HarnessData/Harness[{hi}]/Cable[{ci}]/CableWire[{wi}]",
                    cable_name=cable.get("Name"),
                )
    return report


ALIASES = {
    "tagname": "tag",
    "tag": "tag",
    "fulldt": "tag",
    "devicetagfull": "tag",
    "dtfull": "tag",
    "cat": "part_number",
    "catalognumber": "part_number",
    "partnumber": "part_number",
    "mfg": "manufacturer",
    "manufacturer": "manufacturer",
    "cnt": "quantity",
    "qty": "quantity",
    "quantity": "quantity",
    "wireno": "wire_number",
    "connectiondesignation": "wire_number",
    "cmp1": "from_tag",
    "pin1": "from_pin",
    "cmp2": "to_tag",
    "pin2": "to_pin",
    "cmppin1": "from_combined",
    "cmppin2": "to_combined",
    "source": "from_combined",
    "target": "to_combined",
    "sourcefulldtandconnectionpointdesignation": "from_combined",
    "targetfulldtandconnectionpointdesignation": "to_combined",
    "desc1": "description",
    "description": "description",
    "functiontext": "description",
    "inst": "installation",
    "loc": "location",
    "sh": "drawing_sheet",
    "ref": "drawing_reference",
}


def _tables(data, suffix):
    if suffix in {".csv", ".tsv", ".txt"}:
        text = data.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError(
                "CAD text report contains null bytes; export UTF-8 CSV/TSV."
            )
        try:
            dialect = csv.Sniffer().sniff(text[:32768], delimiters=",;\t")
        except csv.Error:
            # Report titles often precede a differently sized header; Sniffer
            # rejects that legitimate layout. Header signatures still gate parsing.
            delimiter = max(
                (",", ";", "\t"),
                key=lambda d: max(
                    (line.count(d) for line in text.splitlines()[:100]), default=0
                ),
            )
            if delimiter not in text:
                return []
            rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        else:
            rows = list(csv.reader(io.StringIO(text), dialect))
        if len(rows) > MAX_ROWS or any(len(r) > MAX_COLUMNS for r in rows):
            raise ValueError("CAD report exceeds the row or column limit.")
        return [("Report", list(enumerate(rows, 1)), (), set())]
    if suffix == ".xlsx":
        from openpyxl import load_workbook

        with ZipFile(io.BytesIO(data)) as archive:
            if (
                len(archive.infolist()) > 2000
                or sum(n.file_size for n in archive.infolist()) > 100 * 1024 * 1024
            ):
                raise ValueError(
                    "CAD workbook archive exceeds the expanded size limit."
                )
        book = load_workbook(io.BytesIO(data), data_only=False, read_only=False)
        result = []
        try:
            for sheet in book:
                if sheet.max_row > MAX_ROWS or sheet.max_column > MAX_COLUMNS:
                    raise ValueError("CAD workbook exceeds the row or column limit.")
                result.append(
                    (
                        sheet.title,
                        list(enumerate(sheet.iter_rows(values_only=True), 1)),
                        tuple(sheet.merged_cells.ranges),
                        {
                            cell.row
                            for row in sheet.iter_rows()
                            for cell in row
                            if cell.data_type == "f"
                        },
                    )
                )
        finally:
            book.close()
        return result
    return []


def _native_equipment_header(values):
    """Native equipment tables retain their richer per-device semantics."""
    keys = {_key(value) for value in values}
    return bool(keys & {"equipmenttag", "devicetag"}) and bool(
        keys & {
            "framea", "tripa", "nameplate", "nameplatelabel", "tripunit",
            "deviceframe", "controlvoltagev", "interruptingka", "polecount",
            "positionstate", "ratedcurrenta", "buscurrenta", "sensora",
        }
    )


def _cad_table_header(values):
    available = {ALIASES.get(_key(value)) for value in values} - {None}
    return (
        "wire_number" in available
        and (
            {"from_tag", "from_pin", "to_tag", "to_pin"} <= available
            or {"from_combined", "to_combined"} <= available
        )
        or "tag" in available
        and bool({"part_number", "manufacturer", "quantity"} & available)
        or {"part_number", "quantity", "manufacturer"} <= available
    )


def _tabular(data, suffix, source):
    tables = _tables(data, suffix)
    native_tables, cad_tables = [], []
    for sheet, rows, _merges, _formula_rows in tables:
        for rownum, values in rows:
            if _native_equipment_header(values):
                native_tables.append({"sheet": sheet, "row": rownum})
            elif _cad_table_header(values):
                cad_tables.append({"sheet": sheet, "row": rownum})
    if native_tables:
        if not cad_tables:
            return None  # The native reader preserves names, ratings and local IDs.
        report = _base(source, "mixed_native_and_electrical_report_tables")
        report["deferred_native_tables"] = native_tables
        report["deferred_cad_tables"] = cad_tables
        _warn(
            report,
            "mixed_native_cad_tables_unsupported",
            "This source contains both native equipment and electrical CAD report tables. Combined coverage is unsupported; neither table is accepted as a complete device inventory.",
        )
        return report
    report = _base(source, "electrical_report_table")
    recognized = False
    for sheet, rows, merges, formula_rows in tables:
        header = None
        for rownum, values in rows:
            if not any(_text(v) for v in values):
                continue
            if header is None:
                fields = [ALIASES.get(_key(x)) for x in values]
                available = {f for f in fields if f}
                wire = "wire_number" in available and (
                    {"from_tag", "from_pin", "to_tag", "to_pin"} <= available
                    or {"from_combined", "to_combined"} <= available
                )
                component = "tag" in available and bool(
                    {"part_number", "manufacturer", "quantity"} & available
                )
                bom = {"part_number", "quantity", "manufacturer"} <= available
                if not (wire or component or bom):
                    continue
                if len([f for f in fields if f]) != len(available):
                    _warn(
                        report,
                        "duplicate_report_columns",
                        "Multiple columns map to the same property; this table is not interpreted.",
                        sheet=sheet,
                        row=rownum,
                    )
                    break
                recognized = True
                header = (
                    fields,
                    list(values),
                    "wire" if wire else "component" if component else "bom",
                )
                continue
            fields, labels, kind = header
            if [_key(v) for v in values] == [_key(v) for v in labels]:
                continue
            evidence = {"sheet": sheet, "row": rownum}
            mapped = {
                name: values[i] if i < len(values) else None
                for i, name in enumerate(fields)
                if name
            }
            raw = {
                str(labels[i]): _text(values[i]) if i < len(values) else ""
                for i in range(len(labels))
            }
            if any(m.min_row <= rownum <= m.max_row for m in merges):
                _warn(
                    report,
                    "merged_cad_report_row",
                    "Merged data row retained as uncertain; values were not forward-filled.",
                    **evidence,
                )
                report["uncertain"].append({**evidence, "raw_values": raw})
                continue
            if rownum in formula_rows:
                _warn(
                    report,
                    "cad_report_formula",
                    "Formula row retained as uncertain; formulas are not evaluated.",
                    **evidence,
                )
                report["uncertain"].append({**evidence, "raw_values": raw})
                continue
            if kind == "wire":
                ends = []
                for end in ("from", "to"):
                    tag, pin = (
                        _literal(mapped.get(end + "_tag")),
                        _literal(mapped.get(end + "_pin")),
                    )
                    combined = _literal(mapped.get(end + "_combined"))
                    if combined:
                        # Last colon is the explicit pin separator; IEC structure stays intact.
                        tag, separator, pin = combined.rpartition(":")
                        if not separator:
                            tag, pin = combined, None
                    ends.append({"device_tag": tag or None, "pin": pin or None})
                _connection(
                    report,
                    source,
                    _literal(mapped.get("wire_number")),
                    *ends,
                    evidence,
                    raw_values=raw,
                )
                continue
            tag = _single_tag(mapped.get("tag"))
            props = {
                key: _literal(value)
                for key, value in mapped.items()
                if key not in {"tag", "quantity"}
            }
            props["quantity"] = _quantity(mapped.get("quantity"))
            props["raw_values"] = raw
            if kind == "component":
                _component(
                    report,
                    source,
                    tag,
                    props,
                    evidence,
                    raw_tag=_text(mapped.get("tag")),
                )
            report["source_row_counts"]["bom_rows"] += 1
            report["observed_bom_rows"].append(
                {
                    "id": _id(source.id, sheet, rownum),
                    "source_id": source.id,
                    "source_name": source.name,
                    "sha256": source.sha256.lower(),
                    "role": source.role,
                    "sheet": sheet,
                    "row": rownum,
                    "raw_headers": list(map(str, labels)),
                    "cells": [
                        {
                            "column": i + 1,
                            "header": str(label),
                            "text": _text(values[i]) if i < len(values) else "",
                        }
                        for i, label in enumerate(labels)
                    ],
                    "fields": {
                        k: v
                        for k, v in props.items()
                        if k != "raw_values" and v is not None
                    },
                    "device_tag": tag,
                    "quantity": props["quantity"],
                    "status": "observed_row",
                    "confidence": "explicit_report",
                    "warnings": [],
                }
            )
    return report if recognized else None


def read_cad_report(source: SourceInput) -> tuple[dict, list[dict]] | None:
    """Read a hash-pinned recognized report; return None for other source formats.

    Caller must authorize source paths. Returned evidence never exposes a local
    path. XML is bounded and rejects DTD/entity declarations before parsing.
    """
    suffix = Path(source.name).suffix.lower()
    if suffix not in {".xml", ".csv", ".tsv", ".txt", ".xlsx"}:
        return None
    path = Path(source.path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("CAD report exceeds the 20 MiB input limit.")
    data = path.read_bytes()
    if len(data) > MAX_BYTES:
        raise ValueError("CAD report exceeds the 20 MiB input limit.")
    if hashlib.sha256(data).hexdigest() != source.sha256.lower():
        raise ValueError("CAD report source hash mismatch.")
    try:
        report = (
            _xml(data, source) if suffix == ".xml" else _tabular(data, suffix, source)
        )
    except (ET.ParseError, UnicodeDecodeError) as exc:
        raise ValueError(
            "CAD report is malformed or uses unsupported text encoding."
        ) from exc
    if report is None:
        return None
    return report, _facts(report, source)
