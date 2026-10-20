"""Bounded deterministic readers for actual equipment documents.

Labels, cells and native block attributes are interpreted; narrative promises,
PDF annotations and markup responses are not design facts. Scanned diagrams
and connectivity/geometry remain explicitly unsupported.
"""

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from zipfile import ZipFile

from .pdf_batches import (
    PDF_MAX_BYTES,
    PDF_MAX_PAGES,
    batch_plan,
    document_batches,
    original_pages,
)
from .schemas import SourceInput

MAX_BYTES = 30 * 1024 * 1024
MAX_PAGES = PDF_MAX_PAGES
MAX_ROWS = 10000
MAX_COLUMNS = 100
MAX_CAD_ENTITIES = 50000

ALIASES = {
    "device_tag": (
        "equipment tag",
        "equipment_tag",
        "device tag",
        "device_tag",
        "device",
        "tag",
        "tag1",
        "component tag",
        "reference",
        "ref",
    ),
    "frame_a": (
        "frame_a",
        "frame a",
        "frame",
        "frame amps",
        "frame rating",
        "frame current",
        "af",
    ),
    "trip_a": (
        "trip_a",
        "trip a",
        "trip",
        "trip amps",
        "trip rating",
        "trip current",
        "at",
    ),
    "trip_unit": ("trip_unit", "trip unit", "trip unit model", "trip model"),
    "part_number": (
        "part_number",
        "part number",
        "part no",
        "part no.",
        "catalog number",
        "catalog no",
        "catalog",
        "p/n",
        "pn",
        "mfr part number",
    ),
    "quantity": ("quantity", "qty", "qty."),
    "nameplate": ("nameplate", "nameplate text", "name plate", "designation"),
    "rated_current_a": ("rated_current_a", "rated current", "current rating"),
    "device_frame": ("device_frame", "frame designation", "frame type"),
    "bus_current_a": ("bus_current_a", "bus current", "bus ampacity"),
    "sensor_a": ("sensor_a", "sensor rating", "sensor amps"),
    "pole_count": ("pole_count", "pole count", "poles"),
    "frequency_hz": ("frequency_hz", "frequency", "frequency hz"),
    "manufacturer": ("manufacturer", "mfr", "make"),
    "product_family": ("product_family", "product family"),
    "position_state": ("position_state", "position state"),
    "voltage_v": ("voltage_v", "rated voltage", "operating voltage", "voltage v"),
    "current_type": ("current_type", "current type", "ac dc", "current mode"),
    "rating_standard": ("rating_standard", "rating standard", "interrupting standard"),
    "control_voltage_v": (
        "control_voltage_v",
        "control voltage v",
        "control voltage",
        "control supply",
        "control volts",
    ),
    "interrupting_ka": (
        "interrupting_ka",
        "interrupting ka",
        "interrupting",
        "interrupting rating",
        "interrupting capacity",
        "aic",
        "kaic",
    ),
}


def _key(text):
    return re.sub(r"[^a-z0-9]+", "", str(text).casefold())


HEADER_MAP = {
    _key(alias): field for field, aliases in ALIASES.items() for alias in aliases
}
from .tags import TAG_FULL, TAG_RE, ambiguous_tag_styles

SKIP_REGION = re.compile(
    r"\b(?:revision (?:history|notes?|description|schedule)|change (?:log|notes?|summary)|review (?:notes?|comments?|responses?)|title ?block|drawing register)\b",
    re.IGNORECASE,
)
ASSERTION = re.compile(
    r"\b(?:changed|updated|corrected|resolved|fixed|please|requested|revise|replace|superseded|was|previously|formerly|shall|must|will)\b|(?:→|->)",
    re.IGNORECASE,
)


def stable_id(*parts):
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:24]


def clean_tag(value):
    value = str(value or "").strip()
    return value.upper() if TAG_FULL.fullmatch(value) else None


SOURCE_LOCAL_TAG = re.compile(r"drawing:[a-f0-9]{24,64}")
MODEL_LOCAL_TAG = re.compile(r"model:[a-f0-9]{32}")


def _source_local_identity(tag, metadata):
    """Recognize explicit model identity, never a physical/enterprise alias."""
    if not isinstance(tag, str) or not SOURCE_LOCAL_TAG.fullmatch(tag):
        return None

    def one(key):
        values = metadata.get(key, [])
        return values[0] if len(values) == 1 else None

    model_id, version = one("modelid"), one("modelversion")
    if (
        one("identityscope") != "source_local"
        or one("equipmenttag") != tag
        or one("modeldeviceid") != tag
        or not isinstance(model_id, str)
        or not model_id.strip()
        or model_id.strip().casefold() in {"unknown", "none", "null"}
        or not isinstance(version, (str, int))
        or isinstance(version, bool)
        or not str(version).strip()
        or str(version).strip().casefold() in {"unknown", "none", "null"}
    ):
        return None
    return {
        "identity_scope": "source_local",
        "source_local_identity": True,
        "model_id": model_id,
        "model_version": str(version),
        "model_device_id": tag,
        "source_label": one("sourcelabel"),
        "section_label": one("sectionlabel"),
        "position_state": one("positionstate"),
        "physical_identity_verified": False,
    }


def _model_local_identity(tag, metadata):
    """Bind an unlabelled model instance; never invent a source equipment tag."""
    if not isinstance(tag, str) or not MODEL_LOCAL_TAG.fullmatch(tag):
        return None

    def one(key):
        values = metadata.get(key, [])
        return values[0] if len(values) == 1 else None

    declared_tags = [
        value
        for key, values in metadata.items()
        if HEADER_MAP.get(key) == "device_tag"
        for value in values
    ]
    model_id, device_id, version = (
        one("modelid"),
        one("modeldeviceid"),
        one("modelversion"),
    )
    if (
        one("identityscope") != "model_local"
        or one("equipmenttag") != tag
        or declared_tags != [tag]
        or not isinstance(model_id, str)
        or not model_id.strip()
        or model_id.strip().casefold() in {"unknown", "none", "null"}
        or not isinstance(device_id, str)
        or not device_id.strip()
        or not isinstance(version, (str, int))
        or isinstance(version, bool)
        or not str(version).strip()
        or str(version).strip().casefold() in {"unknown", "none", "null"}
    ):
        return None
    expected = (
        "model:"
        + hashlib.sha256(
            json.dumps(
                [str(model_id), str(device_id)],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:32]
    )
    if tag != expected:
        return None
    return {
        "identity_scope": "model_local",
        "model_local_identity": True,
        "source_local_identity": False,
        "model_id": model_id,
        "model_version": str(version),
        "model_device_id": device_id,
        "source_label": one("sourcelabel"),
        "section_label": one("sectionlabel"),
        "position_state": one("positionstate"),
        "physical_identity_verified": False,
    }


def _register_model_local(col, tag, identity):
    if not identity or identity.get("identity_scope") != "model_local":
        return
    binding = (identity["model_id"], identity["model_device_id"])
    if tag in col.model_local_identities or binding in col.model_local_bindings:
        raise ValueError("duplicate_model_local_identity")
    col.model_local_bindings.add(binding)
    col.model_local_identities[tag] = identity


def normalize_value(field, raw):
    """Return (normalized value, unit, error). Do not discard unknown units."""
    text = re.sub(r"\s+", " ", str(raw or "").strip())
    if not text or text.casefold() in {
        "n/a",
        "na",
        "none",
        "tbd",
        "unknown",
        "-",
        "—",
        "?",
    }:
        return None, None, "empty_or_unspecified_value"
    if text.startswith("="):
        return None, None, "formula_not_evaluated"
    if field in {
        "rated_current_a",
        "device_frame",
        "bus_current_a",
        "sensor_a",
        "pole_count",
        "frequency_hz",
        "manufacturer",
        "product_family",
        "current_type",
        "rating_standard",
    }:
        from .evidence.ontology import normalize_feature_value

        normalized = normalize_feature_value(field, text)
        return (
            normalized["value"],
            normalized["unit"],
            None if normalized["valid"] else normalized["reason"],
        )
    if field in {
        "frame_a",
        "trip_a",
        "control_voltage_v",
        "voltage_v",
        "interrupting_ka",
        "quantity",
    }:
        # Thousands separators are accepted only in groups of three.
        number = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
        match = re.fullmatch(rf"({number})\s*([A-Za-z /]+)?", text)
        if not match:
            return None, None, "ambiguous_numeric_value"
        value = float(match[1].replace(",", ""))
        suffix = re.sub(r"\s+", "", (match[2] or "")).casefold()
        units = {
            "frame_a": {
                "": 1,
                "a": 1,
                "amp": 1,
                "amps": 1,
                "ampere": 1,
                "amperes": 1,
                "ka": 1000,
            },
            "trip_a": {
                "": 1,
                "a": 1,
                "amp": 1,
                "amps": 1,
                "ampere": 1,
                "amperes": 1,
                "ka": 1000,
            },
            "voltage_v": {"": 1, "v": 1, "volt": 1, "volts": 1, "kv": 1000},
            "control_voltage_v": {
                "": 1,
                "v": 1,
                "vac": 1,
                "vdc": 1,
                "volt": 1,
                "volts": 1,
                "kv": 1000,
            },
            "interrupting_ka": {
                "": 1,
                "ka": 1,
                "kaic": 1,
                "a": 0.001,
                "amp": 0.001,
                "amps": 0.001,
            },
            "quantity": {"": 1, "ea": 1, "each": 1, "pc": 1, "pcs": 1},
        }[field]
        if suffix not in units:
            return None, None, "unsupported_or_wrong_unit"
        value *= units[suffix]
        if value <= 0 or field == "quantity" and value != int(value):
            return None, None, "invalid_numeric_value"
        # AC/DC is a separate engineering property, not silently equivalent.
        unit = {
            "frame_a": "A",
            "trip_a": "A",
            "control_voltage_v": "V",
            "voltage_v": "V",
            "interrupting_ka": "kA",
            "quantity": "each",
        }[field]
        if field == "control_voltage_v" and suffix in {"vac", "vdc"}:
            unit = "V AC" if suffix == "vac" else "V DC"
        return int(value) if value.is_integer() else value, unit, None
    if len(text) > 500 or ASSERTION.search(text):
        return None, None, "narrative_or_ambiguous_value"
    # Keep exact identifiers and nameplate spelling, allowing harmless case.
    return text.upper(), None, None


class Collector:
    def __init__(self, source, request_mode=False):
        self.source = source
        self.facts = []
        self.warnings = []
        self.tags = set()
        self.uncertain = []
        self.pages = 0
        self.batches = []
        self.page_coverage = []
        self.observed_bom_rows = []
        self.partial = False
        self.request_mode = request_mode
        self.source_local_identities = {}
        self.model_local_identities = {}
        self.model_local_bindings = set()

    def warning(self, code, message, **location):
        item = {
            "code": code,
            "message": message,
            "source_id": self.source.id,
            **location,
        }
        if item not in self.warnings:
            self.warnings.append(item)

    def observe_rows(self, rows):
        remaining = max(0, MAX_ROWS - len(self.observed_bom_rows))
        self.observed_bom_rows.extend(rows[:remaining])
        if len(rows) > remaining:
            self.partial = True
            self.warning(
                "document_bom_row_limit",
                "Only the first bounded set of raw table rows was retained. This is not complete inventory coverage.",
            )

    def fact(self, tag, field, raw, evidence, *, source_raw=None, identity=None):
        if len(self.facts) >= 10000:
            raise ValueError("page_fact_limit")
        self.tags.add(tag)
        if identity:
            evidence = {**evidence, **identity}
            if identity.get("identity_scope") == "source_local":
                self.source_local_identities[tag] = identity
        value, unit, error = normalize_value(field, raw)
        if error:
            self.uncertain.append(
                {
                    "device_tag": tag,
                    "field": field,
                    "reason": error,
                    "raw_value": str(raw if source_raw is None else source_raw),
                    "evidence": evidence,
                }
            )
            self.warning(
                error, f"{tag}: {field} could not be interpreted safely.", **evidence
            )
            return
        fact = {
            "id": stable_id(
                self.source.id,
                self.source.sha256,
                tag,
                field,
                value,
                unit,
                sorted(evidence.items()),
            ),
            "source_id": self.source.id,
            "source_name": self.source.name,
            "role": self.source.role,
            "revision": self.source.revision,
            "sha256": self.source.sha256.lower(),
            "lifecycle": self.source.lifecycle,
            "device_tag": tag,
            "field": field,
            "value": value,
            "unit": unit,
            "raw_value": str(raw if source_raw is None else source_raw),
            "evidence": evidence,
        }
        if identity:
            fact.update(
                identity_scope=identity["identity_scope"],
                source_local_identity=identity.get("source_local_identity", False),
                model_id=identity["model_id"],
                model_version=identity["model_version"],
                model_device_id=identity["model_device_id"],
            )
            if identity.get("model_local_identity"):
                fact["model_local_identity"] = True
        if not any(existing["id"] == fact["id"] for existing in self.facts):
            self.facts.append(fact)


def _tabular(rows, col, sheet="BOM", *, report_missing=True, merges=()):
    header = None
    units = {}
    labels = {}
    for rownum, values in rows:
        if header is None:
            candidates = []
            candidate_units, candidate_labels = {}, {}
            for i, value in enumerate(values):
                label = str(value or "").strip()
                requested = re.match(
                    r"^(?:target|requested|desired)[ _]+(.+)$", label, re.IGNORECASE
                )
                name = requested[1] if requested else label
                declared = re.fullmatch(r"(.*?)\s*[\[(]\s*([^\])]{1,16})\s*[\])]", name)
                field = HEADER_MAP.get(_key(name))
                if declared:
                    field = HEADER_MAP.get(_key(declared[1])) or field
                    if field in {
                        "frame_a",
                        "trip_a",
                        "control_voltage_v",
                        "rated_current_a",
                        "bus_current_a",
                        "sensor_a",
                        "frequency_hz",
                        "voltage_v",
                        "interrupting_ka",
                        "quantity",
                    }:
                        candidate_units[i] = declared[2].strip()
                candidate_labels[i] = label
                if col.request_mode and field != "device_tag" and not requested:
                    field = None
                candidates.append((i, field))
            recognized = {field for _, field in candidates if field}
            if "device_tag" in recognized and len(recognized) >= 2:
                units, labels = candidate_units, candidate_labels
                header = [(index, field) for index, field in candidates if field]
                duplicate = [
                    field
                    for field in recognized
                    if sum(f == field for _, f in header) > 1
                ]
                if duplicate:
                    col.partial = True
                    col.warning(
                        "duplicate_columns",
                        "Repeated recognized columns are retained and checked for contradictions.",
                        sheet=sheet,
                        row=rownum,
                    )
            continue
        if any(
            first_row <= rownum <= last_row
            and any(first_col <= i + 1 <= last_col for i, _ in header)
            and (first_row != last_row or first_col != last_col)
            for first_col, first_row, last_col, last_row in merges
        ):
            col.partial = True
            col.warning(
                "merged_equipment_row_ambiguous",
                "A merged cell spans an equipment field or device row. No values or device tags were forward-filled from another row.",
                sheet=sheet,
                row=rownum,
            )
            continue
        cells = {field: [] for _, field in header}
        for index, field in header:
            cells[field].append((index, values[index] if index < len(values) else None))
        raw_tags = [value for _, value in cells.get("device_tag", [])]
        metadata = {}
        for index, label in labels.items():
            metadata.setdefault(_key(label), []).append(
                values[index] if index < len(values) else None
            )
        local = (
            (
                _source_local_identity(raw_tags[0], metadata)
                or _model_local_identity(raw_tags[0], metadata)
            )
            if raw_tags and all(value == raw_tags[0] for value in raw_tags)
            else None
        )
        if (
            any(str(value or "").startswith("drawing:") for value in raw_tags)
            and local is None
        ):
            col.partial = True
            col.warning(
                "unbound_source_local_identity",
                "An opaque drawing ID lacks one exact model/version/device binding; it was not treated as an enterprise tag.",
                sheet=sheet,
                row=rownum,
            )
            continue
        if (
            any(str(value or "").casefold().startswith("model:") for value in raw_tags)
            or "model_local" in metadata.get("identityscope", [])
        ) and local is None:
            col.partial = True
            col.warning(
                "unbound_model_local_identity",
                "An opaque model ID lacks one exact model/version/device binding; no source tag was inferred.",
                sheet=sheet,
                row=rownum,
            )
            continue
        tags = {
            clean_tag(value)
            for _, value in cells.get("device_tag", [])
            if clean_tag(value)
        }
        if local:
            tags.add(raw_tags[0])
        if not tags:
            if any(str(value or "").strip() for value in values):
                col.warning(
                    "row_without_explicit_device_tag",
                    "A row has no supported explicit device tag and was not assigned to a device.",
                    sheet=sheet,
                    row=rownum,
                )
            continue
        if len(tags) != 1:
            col.partial = True
            col.warning(
                "ambiguous_device_tag",
                "A row has multiple different device tags.",
                sheet=sheet,
                row=rownum,
            )
            continue
        tag = next(iter(tags))
        _register_model_local(col, tag, local)
        col.tags.add(tag)
        for field, entries in cells.items():
            if field == "device_tag":
                continue
            for index, value in entries:
                from openpyxl.utils import get_column_letter

                evidence = {
                    "sheet": sheet,
                    "row": rownum,
                    "cell": f"{get_column_letter(index + 1)}{rownum}",
                    "column": index + 1,
                }
                raw = value
                if index in units:
                    evidence.update(header=labels[index], declared_unit=units[index])
                    if re.fullmatch(r"\d+(?:\.\d+)?", str(value or "").strip()):
                        raw = f"{value} {units[index]}"
                    elif not str(value or "").startswith("=") and re.search(
                        r"[A-Za-z]", str(value or "")
                    ):
                        suffix = re.search(r"([A-Za-z /]+)$", str(value))
                        if suffix and _key(suffix[1]) != _key(units[index]):
                            col.uncertain.append(
                                {
                                    "device_tag": tag,
                                    "field": field,
                                    "reason": "header_cell_unit_conflict",
                                    "evidence": evidence,
                                }
                            )
                            col.partial = True
                            col.warning(
                                "header_cell_unit_conflict",
                                "The cell's explicit unit conflicts with its column header; no unit was selected.",
                                sheet=sheet,
                                row=rownum,
                            )
                            continue
                col.fact(tag, field, raw, evidence, source_raw=value, identity=local)
    if header is None and report_missing:
        col.partial = True
        col.warning(
            "no_equipment_table",
            "No table containing an explicit tag and supported equipment field was found.",
            sheet=sheet,
        )
    return header is not None


def _read_csv(data, col):
    content = data.decode("utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(content), dialect)
    bounded = []
    for index, values in enumerate(reader, start=1):
        if index > MAX_ROWS or len(values) > MAX_COLUMNS:
            col.partial = True
            col.warning(
                "table_limit", "The table exceeded the supported inspection size."
            )
            break
        bounded.append((index, values))
    if col.source.role == "bom" and not col.request_mode:
        from .bom_rows import tabular_rows

        col.observe_rows(tabular_rows(col.source, bounded, sheet="BOM"))
    _tabular(bounded, col)


def _read_xlsx(data, col):
    from openpyxl import load_workbook

    with ZipFile(io.BytesIO(data)) as archive:
        if (
            len(archive.infolist()) > 2000
            or sum(entry.file_size for entry in archive.infolist()) > 64 * 1024 * 1024
        ):
            raise ValueError("expanded_workbook_limit")
    from .workbook_geometry import merged_ranges

    merges = merged_ranges(data)
    workbook = load_workbook(
        io.BytesIO(data), read_only=True, data_only=False, keep_links=False
    )
    try:
        found_table = False
        for sheet in workbook:
            # Valid streaming XLSX writers can omit the optional dimension
            # element. Bound the streamed cells too; None is not an error.
            if (sheet.max_row or 0) > MAX_ROWS or (sheet.max_column or 0) > MAX_COLUMNS:
                col.partial = True
                col.warning(
                    "table_limit",
                    "The worksheet exceeded the supported inspection size.",
                    sheet=sheet.title,
                )
                continue
            rows = []
            for rownum, values in enumerate(sheet.values, start=1):
                if rownum > MAX_ROWS or len(values) > MAX_COLUMNS:
                    col.partial = True
                    col.warning(
                        "table_limit",
                        "The worksheet exceeded the supported inspection size.",
                        sheet=sheet.title,
                    )
                    break
                rows.append((rownum, values))
            if col.source.role == "bom" and not col.request_mode:
                from .bom_rows import tabular_rows

                col.observe_rows(
                    tabular_rows(
                        col.source,
                        rows,
                        sheet=sheet.title,
                        merges=merges.get(sheet.title, ()),
                    )
                )
            found = _tabular(
                rows,
                col,
                sheet.title,
                report_missing=False,
                merges=merges.get(sheet.title, ()),
            )
            found_table |= found
            if not found:
                col.warning(
                    "non_equipment_sheet_excluded",
                    "This worksheet has no supported equipment table and was excluded from device facts.",
                    sheet=sheet.title,
                )
        if not found_table:
            col.partial = True
            col.warning(
                "no_equipment_table",
                "No worksheet contains an explicit tag and supported equipment field.",
            )
    finally:
        workbook.close()


def _read_dxf(data, col):
    import ezdxf

    drawing = ezdxf.read(io.StringIO(data.decode("utf-8-sig")))
    entities = drawing.modelspace()
    if len(entities) > MAX_CAD_ENTITIES:
        raise ValueError("cad_entity_limit")
    # Native INSERT attributes describe components; loose text in a change
    # note or title block cannot establish a device's revised state.
    for entity in entities.query("INSERT"):
        attributes = []
        metadata = {}
        for attr in entity.attribs:
            label = attr.dxf.tag
            metadata.setdefault(_key(label), []).append(attr.dxf.text)
            requested = re.match(
                r"^(?:target|requested|desired)[ _]+(.+)$", label, re.IGNORECASE
            )
            field = HEADER_MAP.get(_key(requested[1] if requested else label))
            if col.request_mode and field != "device_tag" and not requested:
                field = None
            attributes.append((field, attr))
        raw_tags = [
            attr.dxf.text for field, attr in attributes if field == "device_tag"
        ]
        local = (
            (
                _source_local_identity(raw_tags[0], metadata)
                or _model_local_identity(raw_tags[0], metadata)
            )
            if raw_tags and all(value == raw_tags[0] for value in raw_tags)
            else None
        )
        if any(value.startswith("drawing:") for value in raw_tags) and local is None:
            col.partial = True
            col.warning(
                "unbound_source_local_identity",
                "An opaque drawing ID lacks one exact model/version/device binding; it was not treated as an enterprise tag.",
                cad_handle=entity.dxf.handle,
            )
            continue
        if (
            any(value.casefold().startswith("model:") for value in raw_tags)
            or "model_local" in metadata.get("identityscope", [])
        ) and local is None:
            col.partial = True
            col.warning(
                "unbound_model_local_identity",
                "An opaque model ID lacks one exact model/version/device binding; no source tag was inferred.",
                cad_handle=entity.dxf.handle,
            )
            continue
        tags = {
            clean_tag(attr.dxf.text)
            for field, attr in attributes
            if field == "device_tag" and clean_tag(attr.dxf.text)
        }
        if local:
            tags.add(raw_tags[0])
        if not tags:
            continue
        if len(tags) > 1:
            col.partial = True
            col.warning(
                "ambiguous_device_tag",
                "A native block contains contradictory device tags.",
                cad_handle=entity.dxf.handle,
            )
            continue
        if SKIP_REGION.search(entity.dxf.name):
            continue
        tag = next(iter(tags))
        _register_model_local(col, tag, local)
        col.tags.add(tag)
        for field, attribute in attributes:
            if not field or field == "device_tag":
                continue
            anchor = entity.dxf.insert
            col.fact(
                tag,
                field,
                attribute.dxf.text,
                {
                    "cad_handle": entity.dxf.handle,
                    "attribute_handle": attribute.dxf.handle,
                    "attribute": attribute.dxf.tag,
                    "block_name": entity.dxf.name,
                    "anchor_xyz": [float(anchor.x), float(anchor.y), float(anchor.z)],
                },
                identity=local,
            )
    col.warning(
        "cad_geometry_not_checked",
        "Native component attributes were inspected; wiring connectivity, clearance, geometry and engineering correctness were not checked.",
    )
    if not col.tags:
        col.partial = True
        col.warning(
            "no_equipment_attributes",
            "No supported tagged equipment blocks were found. Loose drawing text was not treated as component evidence.",
        )


LABEL_OPTIONS = sorted(
    (
        (alias, field)
        for field, aliases in ALIASES.items()
        if field != "device_tag"
        for alias in aliases
    ),
    key=lambda x: len(x[0]),
    reverse=True,
)
LABEL_RE = re.compile(
    r"(?<![\w])("
    + "|".join(re.escape(alias).replace(r"\ ", r"[ _]+") for alias, _ in LABEL_OPTIONS)
    + r")\s*(?::|=)\s*",
    re.IGNORECASE,
)
REQUEST_LABEL_RE = re.compile(
    r"\b(?:target|requested|desired)[ _]+("
    + "|".join(re.escape(alias).replace(r"\ ", r"[ _]+") for alias, _ in LABEL_OPTIONS)
    + r")\s*(?::|=)\s*",
    re.IGNORECASE,
)


def _pdf_bbox(rect, page):
    import pymupdf

    # Text is crop-relative and unrotated; evidence is displayed in the
    # viewer's rotated visible page coordinate frame.
    visual = pymupdf.Rect(rect) * page.rotation_matrix
    return [
        round(max(0, min(1, value)), 6)
        for value in (
            (visual.x0 - page.rect.x0) / page.rect.width,
            (visual.y0 - page.rect.y0) / page.rect.height,
            (visual.x1 - page.rect.x0) / page.rect.width,
            (visual.y1 - page.rect.y0) / page.rect.height,
        )
    ]


def _read_pdf_page(page, col):
    import pymupdf

    from .pdf_batches import PDF_MAX_PAGE_CONTENT

    pagenum = page._rivet_original_page - 1
    if len(page.read_contents()) > PDF_MAX_PAGE_CONTENT:
        raise ValueError("page_content_limit")
    from .pdf_model import visible_equipment_words

    visible, visibility_issues = visible_equipment_words(
        page, col.source.sha256.lower()
    )
    if visibility_issues:
        col.partial = True
        col.warning(
            "unverified_text_visibility",
            "Hidden, clipped or covered content was excluded; remaining supported visible values do not establish complete page coverage.",
            page=pagenum + 1,
        )
    if col.source.role == "bom" and not col.request_mode:
        from .bom_rows import pdf_rows

        try:
            col.observe_rows(pdf_rows(col.source, page, visible))
        except ValueError as exc:
            col.partial = True
            col.warning(
                str(exc),
                "The printed table exceeds bounded raw-row extraction. No rows were reconstructed from images or equipment facts.",
                page=pagenum + 1,
            )
    blocks = page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE).get(
        "blocks", []
    )
    lines = []
    skip_boxes = []
    for block in blocks:
        if block.get("type") != 0:
            continue

        def actual_text(line):
            b = line["bbox"]
            words = [
                w
                for w in visible
                if b[0] <= w["center"][0] <= b[2] and b[1] <= w["center"][1] <= b[3]
            ]
            words.sort(key=lambda w: w["bbox"][0])
            return " ".join(w["text"] for w in words)

        text = " ".join(actual_text(line) for line in block.get("lines", []))
        if SKIP_REGION.search(text):
            skip_boxes.append(block["bbox"])
            continue
        for line in block.get("lines", []):
            text = actual_text(line).strip()
            if text:
                lines.append(
                    {
                        "text": text,
                        "bbox": line["bbox"],
                        "block": block["bbox"],
                    }
                )
    if skip_boxes:
        # Review rows can be separate blocks from their heading.
        # Excluding only the heading would promote those rows to
        # actual device state. Unknown layouts fail conservatively.
        cutoff = min(box[1] for box in skip_boxes)
        lines = [line for line in lines if line["bbox"][1] < cutoff]
        col.warning(
            "review_or_revision_region_excluded",
            "Content beneath a review/revision-notes heading was excluded from equipment facts; narrative completion claims are not design evidence.",
            page=pagenum + 1,
        )
    if sum(len(line["text"]) for line in lines) < 30:
        col.partial = True
        col.warning(
            "unreadable_page",
            "This page has insufficient selectable text. Scans, handwriting and image-only symbols require separate review and were not silently inferred.",
            page=pagenum + 1,
        )
        return
    anchors = []
    for line in lines:
        text = line["text"].strip()
        if re.match(r"^CHANGE\s+[A-Za-z0-9_-]+\s*:", text, re.IGNORECASE):
            # Native change instructions have a separate cited compiler;
            # their proposed attributes are not current equipment facts.
            continue
        if (
            col.source.role == "drawing"
            and line["bbox"][1] >= page.cropbox.height * 0.88
        ):
            # Standard bottom title/revision strips are deliberately
            # excluded. Unusual layouts need native data or review.
            continue
        # A narrative tag mention is not a component instance.
        match = re.fullmatch(
            r"(?:(?:device|equipment|breaker|component)(?:[ _]+tag)?\s*[:#]?\s*)?("
            + TAG_FULL.pattern
            + r")\s*",
            text,
            re.IGNORECASE,
        )
        if match:
            tag = clean_tag(match[1])
            anchors.append((tag, line))
        elif TAG_RE.match(text) and LABEL_RE.search(text):
            match = TAG_RE.match(text)
            anchors.append((clean_tag(match[1]), line))
    extracted = 0
    label_pattern = REQUEST_LABEL_RE if col.request_mode else LABEL_RE
    for line in lines:
        text, bbox = line["text"], line["bbox"]
        if re.match(r"^CHANGE\s+[A-Za-z0-9_-]+\s*:", text, re.IGNORECASE):
            continue
        if col.source.role == "drawing" and bbox[1] >= page.cropbox.height * 0.88:
            continue
        matches = list(label_pattern.finditer(text))
        if not matches or ASSERTION.search(text[: matches[0].start()]):
            continue
        # Prefer an anchor sharing the native text block, otherwise a
        # spatially preceding component heading in the same column.
        candidates = []
        for tag, anchor in anchors:
            ab = anchor["bbox"]
            dy = bbox[1] - ab[1]
            if -2 <= dy <= 230 and abs(bbox[0] - ab[0]) < max(
                100, page.cropbox.width * 0.19
            ):
                score = dy + abs(bbox[0] - ab[0]) * 0.6
                if line["block"] == anchor["block"]:
                    score -= 30
                candidates.append((score, tag, anchor))
        if not candidates:
            continue
        candidates.sort(key=lambda candidate: candidate[0])
        if (
            len(candidates) > 1
            and candidates[0][1] != candidates[1][1]
            and abs(candidates[0][0] - candidates[1][0]) < 5
        ):
            col.partial = True
            col.warning(
                "ambiguous_pdf_association",
                "A labelled value could not be assigned uniquely to a nearby device.",
                page=pagenum + 1,
                bbox=_pdf_bbox(bbox, page),
            )
            continue
        tag = candidates[0][1]
        for index, match in enumerate(matches):
            field = HEADER_MAP[_key(match[1])]
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            raw = text[match.end() : end].strip(" ;|\t")
            if ASSERTION.search(raw):
                col.warning(
                    "narrative_not_equipment_fact",
                    "A change instruction or revision assertion was not treated as the current equipment value.",
                    page=pagenum + 1,
                    bbox=_pdf_bbox(bbox, page),
                )
                continue
            col.fact(
                tag,
                field,
                raw,
                {
                    "page": pagenum + 1,
                    "bbox": _pdf_bbox(bbox, page),
                    "coordinate_system": "normalized_top_left",
                    "text": text,
                },
            )
            extracted += 1
    if not extracted:
        col.partial = True
        col.warning(
            "no_supported_equipment_on_page",
            "No supported explicitly tagged equipment values were found on this page. Any untagged table rows are separate source observations; symbol meaning and device identity remain unverified.",
            page=pagenum + 1,
        )
    if page.annots() is not None:
        # Annotations are intentionally not consumed as design state.
        annotations = list(page.annots())
        if annotations:
            col.warning(
                "annotations_excluded",
                "PDF annotations were excluded from equipment facts; the underlying issued drawing must reflect a change.",
                page=pagenum + 1,
            )


def _read_pdf(data, col):
    import pymupdf

    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        if document.needs_pass:
            raise ValueError("encrypted_pdf")
        col.pages = len(document)
        if len(document) > MAX_PAGES:
            col.partial = True
            col.warning(
                "page_limit", f"Only the first {MAX_PAGES} pages were inspected."
            )
        col.batches = [
            {**batch.public(), "original_sha256": col.source.sha256.lower()}
            for batch in batch_plan(len(document))
        ]
        for batch, chunk in document_batches(document):
            for page in original_pages(batch, chunk):
                pagenum = page._rivet_original_page - 1
                try:
                    local = Collector(col.source, request_mode=col.request_mode)
                    _read_pdf_page(page, local)
                    if len(col.facts) + len(local.facts) > 100000:
                        raise ValueError("document_fact_limit")
                    col.facts.extend(local.facts)
                    col.tags.update(local.tags)
                    col.uncertain.extend(local.uncertain)
                    col.observe_rows(local.observed_bom_rows)
                    col.warnings.extend(local.warnings)
                    col.partial = col.partial or local.partial
                    col.page_coverage.append(
                        {
                            "page": pagenum + 1,
                            "status": "unreadable"
                            if any(
                                w["code"] == "unreadable_page" for w in local.warnings
                            )
                            else "partial"
                            if local.partial
                            else "processed",
                            "equipment_readable": bool(local.facts),
                            "fact_count": len(local.facts),
                            "observed_bom_row_count": len(local.observed_bom_rows),
                        }
                    )
                except Exception:  # noqa: BLE001 - isolate a malformed original page, retain other pages.
                    col.partial = True
                    col.warning(
                        "unreadable_page",
                        "This original page failed bounded extraction; other pages remain available.",
                        page=pagenum + 1,
                    )
                    col.page_coverage.append(
                        {"page": pagenum + 1, "status": "unreadable", "fact_count": 0}
                    )
        col.warning(
            "drawing_semantics_not_checked",
            "Extracted labels do not establish wiring connectivity, physical layout, complete device inventory or engineering correctness.",
        )
    finally:
        document.close()


def read_source(source, *, requested_changes=False):
    source = (
        source
        if isinstance(source, SourceInput)
        else SourceInput.model_validate(source)
    )
    suffix = Path(source.name).suffix.casefold() or Path(source.path).suffix.casefold()
    result = {
        key: getattr(source, key)
        for key in ("id", "name", "role", "revision", "sha256", "lifecycle")
    }
    result.update(
        format=suffix.lstrip("."),
        status="parsed",
        warnings=[],
        observed_device_tags=[],
        uncertain_fields=[],
        page_count=None,
        observed_bom_rows=[],
    )
    col = Collector(source, request_mode=requested_changes)
    if source.role == "markup" and not requested_changes:
        result.update(
            status="excluded",
            warnings=[
                {
                    "code": "markup_excluded",
                    "source_id": source.id,
                    "message": "Markup/comment sources cannot establish current drawing or BOM equipment state.",
                }
            ],
        )
        return result, []
    try:
        path = Path(source.path)
        if not path.is_file() or path.stat().st_size > (
            PDF_MAX_BYTES if suffix == ".pdf" else MAX_BYTES
        ):
            raise ValueError("missing_or_oversized_source")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != source.sha256.casefold():
            raise ValueError("source_hash_mismatch")
        if (
            suffix in {".xml", ".csv", ".xlsx", ".tsv", ".txt"}
            and source.role != "markup"
        ):
            from .cad_reports import read_cad_report

            imported = read_cad_report(source)
            if imported is not None:
                cad_report, cad_facts = imported
                result.update(
                    status="partial" if cad_report["status"] == "partial" else "parsed",
                    warnings=cad_report["warnings"],
                    observed_device_tags=sorted(
                        {
                            item["tag"]
                            for item in cad_report["components"]
                            if item.get("tag")
                        }
                    ),
                    uncertain_fields=cad_report["uncertain"],
                    observed_bom_rows=cad_report["observed_bom_rows"],
                    cad_report=cad_report,
                )
                return result, cad_facts
        readers = {
            ".pdf": _read_pdf,
            ".dxf": _read_dxf,
            ".csv": _read_csv,
            ".xlsx": _read_xlsx,
        }
        reader = readers.get(suffix)
        if reader is None:
            result["status"] = "unsupported"
            col.warning(
                "unsupported_format",
                "Supported formats are PDF, native DXF attributes, CSV/XLSX and recognized electrical CAD report XML.",
            )
        else:
            reader(data, col)
            if col.partial or not col.facts:
                result["status"] = "partial"
    except Exception as exc:  # noqa: BLE001 - third-party parsers fail closed without exposing paths.
        result["status"] = "unreadable"
        code = (
            str(exc)
            if isinstance(exc, ValueError) and re.fullmatch(r"[a-z_]+", str(exc))
            else "source_parse_failed"
        )
        col.warning(
            code,
            "The source could not be inspected safely; no equipment facts from it were used.",
        )
        col.facts = []
        col.tags = set()
        col.observed_bom_rows = []
        col.model_local_identities = {}
        col.model_local_bindings = set()
    for variants in ambiguous_tag_styles(col.tags):
        col.warning(
            "ambiguous_tag_styles",
            "These exact labels have similar spelling but are not presumed aliases: "
            + ", ".join(variants),
        )
    result.update(
        warnings=col.warnings,
        observed_device_tags=sorted(col.tags),
        uncertain_fields=col.uncertain,
        page_count=col.pages or None,
        processing_batches=col.batches,
        page_coverage=col.page_coverage,
        observed_bom_rows=col.observed_bom_rows,
        source_local_identities=col.source_local_identities,
        model_local_identities=col.model_local_identities,
    )
    if (
        suffix == ".pdf"
        and source.role in {"specification", "markup"}
        and result["status"] == "partial"
    ):
        from .design_requests import extract_requests

        native = extract_requests([source])
        # A readable native-only change schedule is intentionally not a table
        # of build attributes. Do not count this supported source as unreadable.
        consumed_annotations = bool(native["requests"]) and not native["blockers"]
        other_warnings = [
            w
            for w in col.warnings
            if w["code"]
            not in {
                "no_supported_equipment_on_page",
                "review_or_revision_region_excluded",
                "drawing_semantics_not_checked",
            }
            and not (consumed_annotations and w["code"] == "annotations_excluded")
        ]
        if native["requests"] and not native["blockers"] and not other_warnings:
            result["status"] = "parsed"
            result["native_request_count"] = len(native["requests"])
    return result, sorted(
        col.facts, key=lambda fact: (fact["device_tag"], fact["field"], fact["id"])
    )
