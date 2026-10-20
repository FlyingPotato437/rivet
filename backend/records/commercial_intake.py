"""Bounded, source-preserving RFQ/PO extraction for low-voltage breakers.

Prices and margin rules never come from this parser. It reads explicit labelled
customer fields, not equipment selection, drawing compliance or hidden terms.
No derived fact is saved as if it were an original source paragraph.
"""

import re
from collections import defaultdict
from decimal import Decimal

from .commercial_cover import is_complete_attachment_cover

FIELDS = {
    "equipment category": "equipment_category",
    "equipment tag": "tag",
    "tag": "tag",
    "catalog model": "model",
    "quantity": "quantity",
    "currency": "currency",
    "purchase order": "purchase_order_number",
    "po number": "purchase_order_number",
    "po revision": "po_revision",
    "unit price": "unit_price",
    "trip unit": "trip_unit",
    "remote indication": "remote_indication",
    "rated current": "rated_current",
    "voltage": "voltage",
    "poles": "poles",
    "special requirement": "special_requirement",
}
CATEGORY = "low_voltage_circuit_breaker"
REQUIREMENTS = ("trip_unit", "remote_indication", "rated_current", "voltage", "poles")
NUMBER = r"\d+(?:\.\d+)?"


def normalized(value):
    return re.sub(r"[\s_-]+", " ", str(value).strip()).casefold()


def extract_rows(workspace):
    documents = {d["id"]: d for d in workspace["documents"]}
    by_document = defaultdict(list)
    for source in workspace["sources"]:
        document = documents.get(source["document_id"], {})
        if document.get("state") not in {
            "ready",
            "parsed",
            "mapped",
        } or not document.get("active", True):
            continue
        if source.get("location", {}).get("annotation_id"):
            continue
        by_document[source["document_id"]].append(source)
    rows, issues, possible_covers = [], [], {}
    for document_id, sources in by_document.items():
        text = "\n".join(source["text"] for source in sources)
        rfq = bool(
            re.search(
                r"\b(request for quot(?:ation|e)|quotation request|RFQ)\b",
                text,
                re.IGNORECASE,
            )
        )
        po = bool(
            re.search(
                r"(?:^|\n)\s*(?:purchase order|po number)\s*:", text, re.IGNORECASE
            )
        )
        if not (rfq or po):
            continue
        if len(text) > 24000:
            issues.append(
                {
                    "document_id": document_id,
                    "detail": "This commercial document exceeds the supported automatic extraction size. Confirm its current equipment schedule before quoting.",
                }
            )
            continue
        # Quoted historical correspondence must not become a new commercial input.
        if any(
            marker in text
            for marker in ("-----Original Message-----", "\nFrom:", "\nOn ")
        ):
            continue
        facts, evidence, ambiguous = {}, defaultdict(list), set()
        for source in sources:
            for line in source["text"].splitlines():
                key, separator, value = line.partition(":")
                field = FIELDS.get(key.strip().casefold())
                if not separator or not field or not value.strip():
                    continue
                value = value.strip()
                if field in facts and facts[field] != value:
                    ambiguous.add(field)
                facts[field] = value
                evidence[field].append(source["id"])
        if not facts:
            if all(source.get("location", {}).get("email") for source in sources):
                possible_covers[document_id] = text
            issues.append(
                {
                    "document_id": document_id,
                    "detail": "Confirm the equipment category, catalog model and quantity in this commercial request.",
                }
            )
            continue
        category = normalized(facts.get("equipment_category", ""))
        if category not in {"low voltage circuit breaker", "low voltage breaker"}:
            issues.append(
                {
                    "document_id": document_id,
                    "detail": "Confirm the equipment category. Automatic quoting currently supports explicit low-voltage circuit-breaker requests only.",
                }
            )
            continue
        required = {"tag", "model", "quantity"} | (
            {"purchase_order_number", "po_revision", "unit_price"} if po else set()
        )
        missing = required - facts.keys()
        if missing or ambiguous:
            issues.append(
                {
                    "document_id": document_id,
                    "detail": "Confirm "
                    + ", ".join(sorted(missing | ambiguous))
                    + " in this commercial request.",
                }
            )
            continue
        quantity = re.fullmatch(
            rf"({NUMBER})(?:\s*(?:each|ea|units?))?", facts["quantity"], re.IGNORECASE
        )
        if (
            not quantity
            or Decimal(quantity[1]) <= 0
            or len(quantity[1].replace(".", "")) > 14
        ):
            issues.append(
                {
                    "document_id": document_id,
                    "detail": "Confirm an explicit numeric equipment quantity.",
                }
            )
            continue
        facts["quantity"] = quantity[1]
        facts["equipment_category"] = CATEGORY
        if po:
            price = re.fullmatch(
                rf"(?:(USD|CAD|EUR|GBP)\s+)?({NUMBER})", facts["unit_price"]
            )
            if not price or not re.fullmatch(r"\d{1,4}", facts["po_revision"]):
                issues.append(
                    {
                        "document_id": document_id,
                        "detail": "Confirm the purchase-order revision, unit price and currency.",
                    }
                )
                continue
            facts["unit_price"] = price[2]
            if price[1]:
                if facts.get("currency", price[1]) != price[1]:
                    issues.append(
                        {
                            "document_id": document_id,
                            "detail": "The purchase order contains conflicting currencies.",
                        }
                    )
                    continue
                facts["currency"] = price[1]
                evidence["currency"].extend(evidence["unit_price"])
            if facts.get("currency") not in {"USD", "CAD", "EUR", "GBP"}:
                issues.append(
                    {
                        "document_id": document_id,
                        "detail": "Confirm the purchase-order currency.",
                    }
                )
                continue
            facts["po_revision"] = int(facts["po_revision"])
        ids = list(dict.fromkeys(id for values in evidence.values() for id in values))
        rows.append(
            {
                "source_id": ids[0],
                "source_ids": ids,
                "field_source_ids": dict(evidence),
                "document_id": document_id,
                "role": "purchase_order" if po else "quote_request",
                "extraction": "explicit_breaker_fields_v1",
                "value": facts,
            }
        )
    covered = {
        document_id
        for document_id, text in possible_covers.items()
        if is_complete_attachment_cover(text, documents[document_id], rows)
    }
    return rows, [issue for issue in issues if issue["document_id"] not in covered]


def latest_purchase_rows(rows):
    """A corrected PO replaces its own earlier explicit revision, never another PO."""
    purchase = [row for row in rows if row["role"] == "purchase_order"]
    versioned = [
        row for row in purchase if isinstance(row["value"].get("po_revision"), int)
    ]
    if not versioned:
        return purchase
    numbers = {row["value"].get("purchase_order_number") for row in purchase}
    if len(numbers) != 1 or any(row not in versioned for row in purchase):
        return []
    revision = max(row["value"]["po_revision"] for row in versioned)
    selected = [row for row in versioned if row["value"]["po_revision"] == revision]
    by_tag = {}
    for row in selected:
        tag = row["value"]["tag"]
        if tag in by_tag and by_tag[tag]["value"] != row["value"]:
            return []
        if tag not in by_tag:
            by_tag[tag] = row
        else:
            by_tag[tag] = {
                **row,
                "source_ids": list(
                    dict.fromkeys([*row["source_ids"], *by_tag[tag]["source_ids"]])
                ),
            }
    return list(by_tag.values())
