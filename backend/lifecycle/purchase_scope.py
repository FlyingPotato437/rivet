"""Compare explicitly stated PO requirements with the quoted trusted offer.

Numeric equality never implies equipment scope or exception acceptance. This
bounded comparison is deterministic and keeps the original requirement sources.
"""

from backend.records.commercial_intake import REQUIREMENTS, extract_rows, normalized
from backend.records.service import source_data


def compare(s, order, quote, lines, purchase_order_number):
    from . import service

    documents, sources = source_data(s, order)
    parsed, _ = extract_rows({"documents": documents, "sources": sources})
    quoted = {line["tag"]: line for line in quote["payload"]["lines"]}
    differences, evidence = [], set()
    for line in lines:
        tag, ids = line["tag"], set(line["source_ids"])
        requests = [
            row
            for row in parsed
            if row["role"] == "purchase_order"
            and row["value"]["tag"] == tag
            and ids.intersection(row["source_ids"])
        ]
        # Existing typed ERP/PO inputs retain their original structured sources.
        for sid in sorted(ids):
            for value in service._structured(s, [sid]):
                if value.get("tag") == tag:
                    requests.append(
                        {
                            "value": value,
                            "source_ids": [sid],
                            "field_source_ids": {field: [sid] for field in value},
                        }
                    )
        required = (
            ("quantity", line["quantity"]),
            ("unit_price", line["unit_price"]),
            ("purchase_order_number", purchase_order_number),
        )

        def matches(row, field, expected, source_ids=ids):
            return (
                str(
                    row["value"].get(
                        "purchase_order_number",
                        row["value"].get("purchase_order", ""),
                    )
                )
                == str(expected)
                if field == "purchase_order_number"
                else service.orders.equivalent(row["value"].get(field), expected)
            ) and bool(
                source_ids.intersection(
                    row["field_source_ids"].get(field, row["source_ids"])
                )
            )

        for field, expected in required:
            if not any(matches(row, field, expected) for row in requests):
                service.fail(
                    f"{tag}: cite this purchase order's exact {field.replace('_', ' ')} before preparing kickoff. Unrelated evidence cannot establish the requested value.",
                    409,
                )
        coherent = [
            row
            for row in requests
            if all(matches(row, field, expected) for field, expected in required)
        ]
        if not coherent or any(
            str(
                row["value"].get(
                    "purchase_order_number", row["value"].get("purchase_order", "")
                )
            )
            not in {"", str(purchase_order_number)}
            for row in requests
        ):
            service.fail(
                f"{tag}: cite this purchase order's exact identity, quantity and unit price from one original row. Values from different purchase orders or revisions cannot be combined.",
                409,
            )
        requests = [
            row
            for row in requests
            if str(
                row["value"].get(
                    "purchase_order_number", row["value"].get("purchase_order", "")
                )
            )
            == str(purchase_order_number)
        ]
        offer_ids = quoted.get(tag, {}).get("source_ids", [])
        offers = [
            (sid, value)
            for sid in offer_ids
            if service._trusted_commercial_source(s, order, sid, "pricing")
            for value in service._structured(s, [sid])
            if value.get("tag") == tag
        ]
        for field in (
            *REQUIREMENTS,
            "special_requirement",
            "model",
            "currency",
            "equipment_category",
        ):
            specified = [row for row in requests if field in row["value"]]
            if not specified:
                continue
            requested = {normalized(row["value"][field]) for row in specified}
            offered = {
                normalized(value[field]) for _, value in offers if field in value
            }
            refs = {
                sid
                for row in specified
                for sid in row["field_source_ids"].get(field, row["source_ids"])
            } | {sid for sid, value in offers if field in value}
            evidence.update(refs)
            if len(requested) == len(offered) == 1 and requested == offered:
                continue
            requested_text = "; ".join(
                sorted({str(row["value"][field]) for row in specified})
            )
            offered_text = "; ".join(
                sorted({str(value[field]) for _, value in offers if field in value})
            )
            differences.append(
                {
                    "tag": tag,
                    "field": field,
                    "purchase_order_value": requested_text,
                    "quoted_value": offered_text or None,
                    "source_ids": sorted(refs),
                    "detail": f"{tag}: purchase order states {field.replace('_', ' ')} {requested_text}; "
                    + (
                        f"the quoted catalog offer records {offered_text}."
                        if offered_text
                        else "the quoted catalog does not establish that requirement."
                    )
                    + " Confirm the scope before accepting the order.",
                }
            )
    return differences, sorted(evidence)
