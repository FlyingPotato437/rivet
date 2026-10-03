"""Conservative, citation-preserving reads for the order replay workflow.

This module extracts assertions. It does not decide contractual precedence,
compliance, equivalence between equipment, or release authority.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


def _attribute(label, kind="text", unit=None, aliases=()):
    return {"label": label, "type": kind, "unit": unit, "aliases": list(aliases)}


ATTRIBUTE_REGISTRY = {
    "rated_voltage_v": _attribute(
        "Rated voltage", "number", "V", ("rated voltage", "system voltage", "voltage")
    ),
    "frequency_hz": _attribute("Frequency", "number", "Hz", ("frequency",)),
    "phases": _attribute("Phases", "integer", None, ("phase count", "phases")),
    "wires": _attribute("Wires", "integer", None, ("wire count", "wires")),
    "main_bus_a": _attribute(
        "Main bus rating",
        "number",
        "A",
        ("main bus", "main bus rating", "bus ampacity"),
    ),
    "neutral_bus_percent": _attribute(
        "Neutral bus", "number", "%", ("neutral bus", "neutral bus rating")
    ),
    "short_circuit_ka": _attribute(
        "Short-circuit rating",
        "number",
        "kA",
        ("short circuit rating", "short-circuit rating", "sccr"),
    ),
    "short_time_ka": _attribute(
        "Short-time withstand",
        "number",
        "kA",
        ("short time withstand", "short-time withstand"),
    ),
    "short_time_duration_s": _attribute(
        "Short-time duration",
        "number",
        "s",
        ("short time duration", "short-time duration"),
    ),
    "main_breaker_frame_a": _attribute(
        "Main breaker frame", "number", "A", ("main breaker frame",)
    ),
    "main_breaker_trip_a": _attribute(
        "Main breaker trip", "number", "A", ("main breaker trip",)
    ),
    "main_breaker_poles": _attribute(
        "Main breaker poles", "integer", None, ("main breaker poles",)
    ),
    "main_breaker_interrupting_ka": _attribute(
        "Main breaker interrupting rating",
        "number",
        "kA",
        ("main breaker interrupting rating", "main breaker interrupting"),
    ),
    "feeder_breaker_frame_a": _attribute(
        "Feeder breaker frame", "number", "A", ("feeder breaker frame",)
    ),
    "feeder_breaker_trip_a": _attribute(
        "Feeder breaker trip", "number", "A", ("feeder breaker trip",)
    ),
    "enclosure_type": _attribute(
        "Enclosure type", aliases=("enclosure type", "enclosure", "nema enclosure")
    ),
    "bus_material": _attribute("Bus material", aliases=("bus material",)),
    "bus_plating": _attribute("Bus plating", aliases=("bus plating",)),
    "cable_entry": _attribute("Cable entry", aliases=("cable entry",)),
    "control_voltage_v": _attribute(
        "Control voltage", "number", "V", ("control voltage",)
    ),
    "metering": _attribute("Metering", aliases=("metering", "meter type")),
    "communications_protocol": _attribute(
        "Communications protocol",
        aliases=("communications protocol", "communication protocol", "protocol"),
    ),
    "surge_protection": _attribute(
        "Surge protection", "boolean", aliases=("surge protection", "spd included")
    ),
    "ground_fault_protection": _attribute(
        "Ground-fault protection",
        "boolean",
        aliases=("ground fault protection", "ground-fault protection"),
    ),
    "arc_resistant": _attribute(
        "Arc-resistant construction",
        "boolean",
        aliases=("arc resistant", "arc-resistant", "arc resistant construction"),
    ),
    "seismic_qualification": _attribute(
        "Seismic qualification", aliases=("seismic qualification",)
    ),
    "enclosure_width_mm": _attribute(
        "Enclosure width", "number", "mm", ("enclosure width",)
    ),
    "enclosure_height_mm": _attribute(
        "Enclosure height", "number", "mm", ("enclosure height",)
    ),
    "enclosure_depth_mm": _attribute(
        "Enclosure depth", "number", "mm", ("enclosure depth",)
    ),
    "quantity": _attribute("Quantity", "integer", None, ("quantity", "qty")),
    "manufacturer": _attribute("Manufacturer", aliases=("manufacturer",)),
    "catalog_number": _attribute(
        "Catalog number",
        aliases=("catalog number", "catalog no", "catalog-number", "model number"),
    ),
    "nameplate_text": _attribute("Nameplate text", aliases=("nameplate text",)),
    "lead_time_weeks": _attribute("Lead time", "number", "weeks", ("lead time",)),
}

ROLES = {
    "purchase_order",
    "accepted_exception",
    "specification",
    "drawing",
    "bom",
    "supplier_po",
    "nameplate",
    "markup",
    "quote",
}
ROLE_ALIASES = {
    "po": "purchase_order",
    "spec": "specification",
    "approved_exception": "accepted_exception",
    "markups": "markup",
    "approval_drawing": "drawing",
    "approval_drawings": "drawing",
    "one_line": "drawing",
}
ATTRIBUTES = {
    re.sub(r"[\s_-]+", " ", label.strip().lower()): key
    for key, definition in ATTRIBUTE_REGISTRY.items()
    for label in [key, *definition["aliases"]]
}
TAG = r"[A-Z][A-Z0-9]{1,10}[-–]\d{1,5}(?:[A-Z])?"
DEVICE_RE = re.compile(rf"(?<!\w)({TAG})(?!\w)")
COMMENT_RE = re.compile(
    r"^\s*(?:(?:comment|note|item)\s*#?\s*(\d+)|\[(\d+)\]|(\d+)\s*(?:\.\)|[.)])(?=\s|$))\s*[:.\-–|]?\s*(.*)$",
    re.IGNORECASE,
)


def _get(obj, key, default=None):
    return (
        obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    )


def canonical_attribute(label: str) -> str | None:
    return ATTRIBUTES.get(re.sub(r"[\s_-]+", " ", label.strip().lower()))


def normalize_value(attribute: str, raw: Any) -> tuple[Any, str | None]:
    """Normalize only declared units and unambiguous scalar values.

    Ranges, qualifiers, ambiguous compound units and unknown Boolean words fail
    closed instead of becoming a false numerical assertion.
    """
    definition = ATTRIBUTE_REGISTRY[attribute]
    unit = definition["unit"]
    value = str(raw).strip().rstrip(";.")
    if not value or value.lower() in {
        "unknown",
        "tbd",
        "tbc",
        "provisional",
        "not stated",
        "?",
    }:
        raise ValueError("Value is not established")
    if definition["type"] == "boolean":
        if value.lower() in {"yes", "true", "included", "required", "provided"}:
            return True, None
        if value.lower() in {"no", "false", "not included", "not required", "none"}:
            return False, None
        raise ValueError("Boolean value needs an explicit yes/no")
    if definition["type"] == "text":
        # Case and whitespace are presentation differences. No semantic equivalence
        # such as copper=Cu or Modbus=Modbus TCP is inferred here.
        return re.sub(r"\s+", " ", value).strip(), None
    match = re.fullmatch(
        r"([+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*([A-Za-z%\"°-]*)", value
    )
    if not match:
        raise ValueError(
            "Expected one explicit numeric value, not a range or qualifier"
        )
    try:
        number = Decimal(match[1].replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("Invalid number") from exc
    suffix = match[2].lower()
    units = {
        "V": {"": 1, "v": 1, "volts": 1, "kv": 1000},
        "A": {"": 1, "a": 1, "amps": 1, "ka": 1000},
        "kA": {"": 1, "ka": 1, "a": Decimal("0.001")},
        "Hz": {"": 1, "hz": 1},
        "s": {"": 1, "s": 1, "sec": 1, "seconds": 1, "ms": Decimal("0.001")},
        "mm": {
            "": 1,
            "mm": 1,
            "cm": 10,
            "m": 1000,
            "in": Decimal("25.4"),
            '"': Decimal("25.4"),
        },
        "%": {"": 1, "%": 1},
        "weeks": {"": 1, "week": 1, "weeks": 1, "wk": 1},
        None: {"": 1, "each": 1, "ea": 1, "poles": 1, "phases": 1, "wires": 1},
    }
    if suffix not in units[unit]:
        raise ValueError(f"Unsupported unit {suffix!r} for {attribute}")
    number *= Decimal(str(units[unit][suffix]))
    if definition["type"] == "integer" and number != number.to_integral_value():
        raise ValueError("Expected a whole number")
    return int(number) if number == number.to_integral_value() else float(number), unit


def _role(document, text):
    meta = _get(document, "meta", {}) or {}
    explicit = meta.get("order_role") or meta.get("role") or meta.get("document_role")
    if explicit:
        role = ROLE_ALIASES.get(str(explicit).lower(), str(explicit).lower())
        return role if role in ROLES else "unknown", "metadata"
    name = str(_get(document, "name", "")).lower().replace("_", " ").replace("-", " ")
    choices = [
        ("accepted_exception", r"accepted\s+exceptions?"),
        ("supplier_po", r"supplier\s+(?:purchase\s+order|po)"),
        ("purchase_order", r"purchase\s+order|(?:^|\s)po(?:\s|\.)"),
        ("markup", r"markups?|review\s+comments?"),
        ("nameplate", r"nameplates?"),
        ("bom", r"(?:^|\s)bom(?:\s|\.)|bill\s+of\s+materials"),
        ("specification", r"specifications?|(?:^|\s)spec(?:\s|\.)"),
        ("drawing", r"drawings?|one\s+line|approval\s+package"),
        ("quote", r"quotation|quote"),
    ]
    for role, pattern in choices:
        if re.search(pattern, name):
            return role, "filename"
    header = re.search(r"(?im)^\s*Document role\s*:\s*([\w ]+)$", text)
    if header:
        role = header[1].strip().lower().replace(" ", "_")
        role = ROLE_ALIASES.get(role, role)
        if role in ROLES:
            return role, "declared_header"
    kind = ROLE_ALIASES.get(
        str(_get(document, "kind", "")), str(_get(document, "kind", ""))
    )
    return (kind, "kind") if kind in ROLES else ("unknown", "unclassified")


def _revision(document, text):
    meta = _get(document, "meta", {}) or {}
    explicit = meta.get("revision_label") or meta.get("revision")
    if explicit:
        return re.sub(
            r"^rev(?:ision)?\s*[.:_-]?\s*", "", str(explicit), flags=re.IGNORECASE
        ).strip()
    for candidate in [str(_get(document, "name", "")), text]:
        match = re.search(
            r"\brev(?:ision)?[\s_.:-]*([A-Z]|\d{1,3})(?=[\s_.-]|$)",
            candidate,
            re.IGNORECASE,
        )
        if match:
            return match[1].upper()
    return None


def _pairs(text):
    for segment in re.split(r"\s*[|;]\s*", text):
        pair = re.match(r"^\s*([^:=]{2,65})\s*[:=]\s*(.+?)\s*$", segment)
        if pair:
            attribute = canonical_attribute(pair[1])
            if attribute:
                yield attribute, pair[2]


def _device(text, context, aliases):
    explicit = re.search(
        r"(?:^|[|;])\s*(?:device|tag|equipment tag)\s*[:=]\s*([^|;]+)",
        text,
        re.IGNORECASE,
    )
    if explicit:
        return explicit[1].strip()
    first = text.split("|", 1)[0].strip().rstrip(":")
    if first in aliases:
        return first
    if "|" in text and first and len(first) <= 80 and not re.search(r"[:=]", first):
        # A leading cell in the documented device | attribute: value format is
        # an explicit identifier, including numeric aliases such as 52-M1.
        return first
    match = DEVICE_RE.search(text)
    return match[1].replace("–", "-") if match else context


def _assertions(text, device):
    # Permit natural labels immediately after a device tag, but do not extract
    # arbitrary prose statements without a recognized attribute delimiter.
    cleaned = (
        re.sub(rf"^\s*{re.escape(device)}\s*[:\-]?\s+(?=[A-Za-z])", "", text)
        if device
        else text
    )
    return list(_pairs(cleaned))


def extract_order_document(document, spans) -> dict:
    # Annotation Contents and spreadsheet cells can contain several numbered
    # comments in a single persisted span. Split the text, not the evidence ID.
    spans = [
        {
            "id": _get(span, "id"),
            "text": line,
            "location": _get(span, "location", {}) or {},
        }
        for span in spans
        for line in str(_get(span, "text", "")).splitlines()
        if line.strip()
    ]
    full_text = "\n".join(str(_get(span, "text", "")) for span in spans)
    role, role_source = _role(document, full_text)
    result = {
        "role": role,
        "role_source": role_source,
        "revision_label": _revision(document, full_text),
        "facts": [],
        "aliases": [],
        "comments": [],
        "warnings": [],
    }
    if role == "unknown":
        result["warnings"].append(
            "Document role is unknown. Classify this document before using it as an obligation."
        )
    for part in _get(document, "coverage", []) or []:
        if part.get("state") in {"scanned", "empty", "unsupported", "failed"}:
            result["warnings"].append(
                f"{part.get('label', 'Source')}: {part.get('detail') or 'No reliable text extraction; review required.'}"
            )
    aliases = set()
    for span in spans:
        text = str(_get(span, "text", "")).strip()
        match = re.match(
            r"^\s*Alias\s*:\s*(.+?)\s*(?:->|→|=)\s*(\S.*?)\s*$", text, re.IGNORECASE
        )
        if match and _get(span, "id"):
            alias, device = match[1].strip(), match[2].strip()
            result["aliases"].append(
                {"alias": alias, "device": device, "source_ids": [_get(span, "id")]}
            )
            aliases.add(alias)
    context = None
    current_comment = None
    for span in spans:
        text = str(_get(span, "text", "")).strip()
        source_id = _get(span, "id")
        location = _get(span, "location", {}) or {}
        if current_comment and current_comment.get("location", {}).get(
            "page"
        ) != location.get("page"):
            # A following sheet's title block or specification is not a
            # continuation of a review comment on the previous sheet.
            current_comment = None
        if not source_id:
            result["warnings"].append(
                "A text segment has no persisted source ID and was excluded."
            )
            continue
        if (
            not text
            or re.match(r"^(Alias|Document role|Revision)\s*:", text, re.IGNORECASE)
            or text.startswith("Rivet replay /")
        ):
            continue
        is_annotation = bool(
            location.get("annotation_id") or location.get("annotation_type")
        )
        comment_match = (
            COMMENT_RE.match(text)
            if (
                role == "markup"
                or is_annotation
                or re.match(r"^Comment\s+", text, re.IGNORECASE)
            )
            else None
        )
        new_annotation = is_annotation and (
            current_comment is None or source_id not in current_comment["source_ids"]
        )
        if comment_match or (role == "markup" and new_annotation):
            number = (
                next((x for x in comment_match.groups()[:3] if x), None)
                if comment_match
                else "A" + str(len(result["comments"]) + 1)
            )
            content = comment_match[4] if comment_match else text
            device = _device(content, context, aliases)
            item = {
                "number": str(number),
                "text": content,
                "device": device,
                "attribute": None,
                "source_ids": [source_id],
                "location": location,
            }
            pairs = _assertions(content, device)
            if pairs:
                item["attribute"] = pairs[0][0]
                try:
                    item["required_value"], item["unit"] = normalize_value(*pairs[0])
                except ValueError as exc:
                    result["warnings"].append(f"Comment {number}: {exc}.")
            result["comments"].append(item)
            current_comment = item
            continue
        # Unnumbered lines immediately following a numbered review comment are
        # part of that comment, not independent obligations or closure evidence.
        if (
            role == "markup"
            and current_comment
            and not re.match(
                r"^(?:Document|Page|FICTIONAL|CUSTOMER|END COMMENTS|Revision|Device)\b",
                text,
                re.IGNORECASE,
            )
        ):
            current_comment["text"] += "\n" + text
            if source_id not in current_comment["source_ids"]:
                current_comment["source_ids"].append(source_id)
            continue
        explicit_context = re.fullmatch(
            r"\s*(?:Device|Equipment tag)\s*[:=]\s*(.+?)\s*", text, re.IGNORECASE
        )
        if explicit_context:
            context = explicit_context[1].strip()
            continue
        device = _device(text, context, aliases)
        pairs = _assertions(text, device)
        if not pairs:
            continue
        if not device:
            result["warnings"].append(
                f"Source {source_id}: attribute has no explicit equipment tag; excluded."
            )
            continue
        for attribute, raw in pairs:
            try:
                value, unit = normalize_value(attribute, raw)
            except ValueError as exc:
                result["warnings"].append(
                    f"{device} · {ATTRIBUTE_REGISTRY[attribute]['label']}: {exc}."
                )
                continue
            result["facts"].append(
                {
                    "device": device,
                    "attribute": attribute,
                    "value": value,
                    "unit": unit,
                    "source_ids": [source_id],
                    "location": location,
                    "confidence": "explicit",
                }
            )
    if not spans:
        result["warnings"].append(
            "No readable evidence was extracted. Scanned documents need transcription or visual review."
        )
    result["comments"].sort(
        key=lambda item: (
            int(item["number"]) if str(item["number"]).isdigit() else 100000,
            str(item["number"]),
        )
    )
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    return result


def _pdf_text(value):
    if isinstance(value, bytes):
        if value.startswith((b"\xfe\xff", b"\xff\xfe")):
            return value.decode("utf-16", errors="replace")
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def parse_pdf_annotations(path: str | Path) -> list[dict]:
    """Read annotation objects, including callouts/clouds; never infer cloud text.

    Bounding boxes use the same normalized, top-left-origin coordinates as
    parser text spans. Empty geometric annotations remain visible for review.
    """
    import pdfplumber

    output = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            for index, annotation in enumerate(page.annots or [], 1):
                data = annotation.get("data", {})
                subtype = str(data.get("Subtype", "Annotation")).strip("/'")
                if subtype == "Link":
                    continue
                text = annotation.get("contents") or ""
                if isinstance(text, bytes):
                    text = text.decode("utf-8", errors="replace")
                name = data.get("NM") or f"page-{page_number}-annotation-{index}"
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                bbox = [
                    annotation.get("x0", 0) / page.width,
                    annotation.get("top", 0) / page.height,
                    annotation.get("x1", page.width) / page.width,
                    annotation.get("bottom", page.height) / page.height,
                ]
                output.append(
                    {
                        "id": str(name),
                        "text": str(text).strip(),
                        "page": page_number,
                        "bbox": [round(max(0, min(1, float(v))), 6) for v in bbox],
                        "kind": subtype,
                        "author": _pdf_text(annotation.get("title") or data.get("T")),
                        "authored_at": _pdf_text(
                            data.get("M") or data.get("CreationDate")
                        ),
                        "needs_review": not bool(str(text).strip()),
                    }
                )
    return output
