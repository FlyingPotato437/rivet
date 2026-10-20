"""Bounded lifecycle preparation in the normal event-agent workflow.

This module grants no approval or delivery authority. Connected-system receipts
remain local proposals; native Outlook reply delivery is a separate capability.
"""

import json
from copy import deepcopy

from pydantic import TypeAdapter, ValidationError

from backend.domain.service import digest, fail, scoped
from backend.lifecycle import service as lifecycle
from backend.lifecycle.schemas import (
    CloseoutPrepare,
    KickoffPrepare,
    Prepare,
    PurchasingPrepare,
    QuotePrepare,
    ReleasePrepare,
    RevisionCheckPrepare,
    RevisionPrepare,
)
from backend.storage.models import RecordAgentRun

from . import commercial_intake

MODELS = {
    "quote": QuotePrepare,
    "kickoff": KickoffPrepare,
    "revision_review": RevisionPrepare,
    "purchasing": PurchasingPrepare,
    "revision_check": RevisionCheckPrepare,
    "release": ReleasePrepare,
    "closeout": CloseoutPrepare,
}
MAX_ROWS = 50
MAX_INPUT = 24000


def _structured_rows(w):
    docs = {d["id"]: d for d in w["documents"]}
    result, chars = [], 0
    for source in w["sources"]:
        document = docs.get(source["document_id"], {})
        if document.get("state") not in {
            "ready",
            "parsed",
            "mapped",
        } or not document.get("active", True):
            continue
        try:
            value = json.loads(source["text"])
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        chars += len(source["text"])
        if len(result) >= MAX_ROWS or chars > MAX_INPUT:
            return result, False
        result.append(
            {
                "source_id": source["id"],
                "document_id": source["document_id"],
                "role": document.get("role", ""),
                "value": value,
            }
        )
    extracted, _ = commercial_intake.extract_rows(w)
    if len(result) + len(extracted) > MAX_ROWS:
        return result, False
    return [*result, *extracted], True


def inspect(s, order, w, *, schemas=True):
    from .cad_preparation import saved_bundles

    rows, complete = _structured_rows(w)
    workspace = lifecycle.view(s, order)
    return {
        "order_version": order.version,
        "mode": "local_demo",
        "allowed_operations": list(MODELS),
        "protected_gates": workspace["policy"]["protected_gates"],
        "customer_sending_supported": False,
        "external_writes_enabled": False,
        "packages": [
            {
                k: p[k]
                for k in (
                    "id",
                    "operation",
                    "title",
                    "status",
                    "evidence_current",
                    "pending_gates",
                    "blockers",
                )
            }
            for p in workspace["packages"][:12]
        ],
        "structured_evidence": rows,
        "existing_revision_work": [
            {
                "package_id": p["id"],
                "revision": p["payload"]["revision"],
                "comment_id": item["comment_id"],
                "kind": item["kind"],
                "response": item.get("response", "")[:2000],
                "source_ids": item.get("source_ids", []),
                "status": item["status"],
            }
            for p in workspace["packages"]
            if p["operation"] == "revision_review"
            and p["evidence_current"]
            and p["status"] not in {"superseded", "rejected"}
            for item in p["payload"]["comments"]
        ][:MAX_ROWS],
        "saved_cad_bundles": saved_bundles(s, order),
        "structured_coverage_complete": complete,
        "commercial_input_questions": commercial_intake.extract_rows(w)[1],
        "input_contract": "payload_json must match one operation schema. Omit expected_version: Rivet binds the current locked version. Read every cited source first. All outputs remain proposals. Missing quantities, prices, dates and approvals must not be invented.",
        **(
            {
                "schemas": {
                    name: model.model_json_schema() for name, model in MODELS.items()
                }
            }
            if schemas
            else {}
        ),
    }


def _source_ids(body):
    return lifecycle._all_source_ids(body)


def _require_numeric_evidence(body, rows):
    """A real citation ID alone must not launder fabricated commercial values."""
    by_id = {
        sid: row for row in rows for sid in row.get("source_ids", [row["source_id"]])
    }
    if body.operation in {"quote", "kickoff"}:
        for line in body.lines:
            cited = [by_id[sid] for sid in line.source_ids if sid in by_id]
            fields = ["quantity"] + (
                ["unit_price"] if body.operation == "kickoff" else []
            )
            for field in fields:
                if not any(
                    str(row["value"].get("tag")) == line.tag
                    and lifecycle.orders.equivalent(
                        row["value"].get(field), getattr(line, field)
                    )
                    and (
                        not row.get("field_source_ids")
                        or set(row["field_source_ids"].get(field, [])).intersection(
                            line.source_ids
                        )
                    )
                    for row in cited
                ):
                    fail(
                        f"{line.tag}: {field} requires an explicit structured source value. No price or quantity will be inferred.",
                        409,
                    )
            if body.operation == "kickoff" and not any(
                row["value"].get(
                    "purchase_order_number", row["value"].get("purchase_order")
                )
                == body.purchase_order_number
                for row in cited
            ):
                fail(
                    "Cite the customer purchase order number for every compared line.",
                    409,
                )
    if body.operation == "purchasing":
        cited = [by_id[sid]["value"] for sid in body.source_ids if sid in by_id]
        for field in ("order_date", "current_ship_date"):
            if not any(
                str(row.get(field)) == getattr(body, field).isoformat() for row in cited
            ):
                fail(
                    f"{field.replace('_', ' ')} must be recorded explicitly before preparing schedule impact.",
                    409,
                )


def prepare(s, order, run, w, payload, *, read_source_ids, automatic=False):
    inspected_version = run.state.get("lifecycle_order_version")
    if inspected_version is not None and inspected_version != order.version:
        fail(
            "The order lifecycle changed after inspection. Read its current packages before preparing more work.",
            409,
        )
    if isinstance(payload, str):
        if len(payload) > MAX_INPUT:
            fail("Narrow this lifecycle preparation to at most 24,000 characters.")
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            fail(
                "Lifecycle preparation requires a JSON object matching the inspected schema."
            )
    if not isinstance(payload, dict) or payload.get("operation") not in MODELS:
        fail(
            "Choose an inspected lifecycle preparation operation. Approval, production release and sending are unavailable to the agent.",
            403,
        )
    if "expected_version" in payload and payload["expected_version"] != order.version:
        fail(
            "The order changed. Inspect the current lifecycle before preparing work.",
            409,
        )
    try:
        body = TypeAdapter(Prepare).validate_python(
            {**payload, "expected_version": order.version}
        )
    except ValidationError as exc:
        fail(
            {
                "message": "Lifecycle input is incomplete or invalid.",
                "errors": exc.errors(include_url=False, include_context=False),
            },
            422,
        )
    raw = body.model_dump(mode="json", exclude={"expected_version"})
    if len(json.dumps(raw)) > MAX_INPUT or any(
        isinstance(value, list) and len(value) > MAX_ROWS for value in raw.values()
    ):
        fail("Prepare at most 50 rows within the bounded lifecycle request.")
    ids = _source_ids(body)
    owned = {source["id"]: source for source in w["sources"]}
    if body.operation == "closeout":
        documents = [
            *body.test_report_document_ids,
            *body.as_built_document_ids,
            *body.other_document_ids,
        ]
        for document_id in documents:
            inspected = [
                sid
                for sid in read_source_ids
                if sid in owned and owned[sid]["document_id"] == document_id
            ]
            if not inspected:
                fail(
                    "Read each selected handover file's evidence before preparing closeout.",
                    409,
                )
            ids.extend(inspected)
        ids = list(dict.fromkeys(ids))
    if not ids or set(ids) - owned.keys():
        fail("Every cited source must belong to the current order.", 404)
    if set(ids) - set(read_source_ids):
        fail("Read every cited source before preparing lifecycle work.", 409)
    rows, _ = _structured_rows(w)
    _require_numeric_evidence(body, rows)
    request_hash = digest(
        [
            raw,
            [
                {k: owned[sid].get(k) for k in ("id", "text", "location")}
                for sid in sorted(ids)
            ],
        ]
    )
    current_packages = lifecycle.view(s, order)["packages"]
    if body.operation == "kickoff":
        quote = next(
            (
                p
                for p in current_packages
                if p["operation"] == "quote"
                and p["status"] not in {"superseded", "rejected"}
            ),
            None,
        )
        quoted_models = {
            row["value"].get("model")
            for row in rows
            if quote
            and set(row.get("source_ids", [row["source_id"]])).intersection(
                quote["sources"]
            )
            and row["value"].get("model")
        }
        for row in rows:
            if (
                row.get("extraction") != "explicit_breaker_fields_v1"
                or row["role"] != "purchase_order"
                or not set(row["source_ids"]).intersection(ids)
            ):
                continue
            if (
                not quote
                or row["value"]["currency"] != quote["payload"]["currency"]
                or row["value"]["model"] not in quoted_models
            ):
                fail(
                    "The purchase-order currency or equipment model does not match the current quote. Confirm the commercial change before preparing kickoff.",
                    409,
                )
    by_id = {p["id"]: p for p in current_packages}
    previous_runs = list(
        s.scalars(
            scoped(RecordAgentRun)
            .where(RecordAgentRun.order_id == order.id)
            .order_by(RecordAgentRun.created_at.desc())
            .limit(50)
        )
    )
    for candidate in [run, *previous_runs]:
        for saved in candidate.state.get("lifecycle_preparations", []):
            package = by_id.get(saved["package_id"])
            if (
                saved["request_hash"] == request_hash
                and package
                and package["evidence_current"]
                and package["status"] not in {"superseded", "rejected"}
            ):
                return _result(package, replayed=True), ids
    # Event scans never replace a person's live commercial package because an
    # unrelated document arrived. Changes need an explicitly scoped agent task.
    if automatic and body.operation in {"quote", "kickoff", "purchasing"}:
        existing = next(
            (
                p
                for p in current_packages
                if p["operation"] == body.operation
                and p["evidence_current"]
                and p["status"] not in {"superseded", "rejected"}
            ),
            None,
        )
        replace_po = (
            existing
            and body.operation == "kickoff"
            and _corrected_po(body, existing, rows)
        )
        if existing and not replace_po:
            return _result(existing, replayed=True), ids
    if len(run.state.get("lifecycle_preparations", [])) >= 4:
        fail(
            "This run has reached its four-package preparation limit. Saved work is retained.",
            409,
        )
    result = lifecycle.prepare(s, order, body)
    package = result["packages"][0]
    state = deepcopy(run.state)
    state["lifecycle_order_version"] = order.version
    state["lifecycle_preparations"] = [
        *state.get("lifecycle_preparations", []),
        {
            "request_hash": request_hash,
            "package_id": package["id"],
            "operation": body.operation,
        },
    ]
    run.state = state
    return _result(package), ids


def _result(package, replayed=False):
    return {
        "package_id": package["id"],
        "operation": package["operation"],
        "summary": package["title"],
        "status": package["status"],
        "mode": package["mode"],
        "replayed": replayed,
        "pending_gates": package["pending_gates"],
        "blockers": package["blockers"],
        "artifacts": package["artifacts"],
        "external_writes_enabled": False,
        "customer_sending_supported": False,
    }


def covered_comments(s, order, workspace=None, *, include_documents=False):
    from . import service as records
    from .commercial_cover import is_complete_attachment_cover

    retained = [
        package
        for package in lifecycle.view(s, order)["packages"]
        if package["evidence_current"] and package["status"] != "rejected"
    ]
    packages = [package for package in retained if package["status"] != "superseded"]
    covered_documents = {
        source["document_id"]
        for package in retained
        for source in package["sources"].values()
    }
    covered = {
        item["comment_id"]
        for package in packages
        if package["operation"] == "revision_review"
        for item in package["payload"]["comments"]
    }
    if not workspace:
        documents, sources = records.source_data(s, order)
        workspace = {
            "documents": documents,
            "sources": sources,
            "comments": records.ensure(s, order).data.get("comments", []),
        }
    rows, _ = commercial_intake.extract_rows(workspace)
    for document in workspace["documents"]:
        original = [
            source
            for source in workspace["sources"]
            if source["document_id"] == document["id"]
            and source["location"].get("email")
        ]
        if not original or not is_complete_attachment_cover(
            "\n".join(source["text"] for source in original), document, rows
        ):
            continue
        attached = [
            row
            for row in rows
            if row["document_id"] in document.get("attachment_document_ids", [])
        ]
        if not attached or any(
            not any(
                package["operation"]
                == ("quote" if row["role"] == "quote_request" else "kickoff")
                and set(row["source_ids"]) <= set(package["sources"])
                for package in retained
            )
            for row in attached
        ):
            continue
        source_ids = {source["id"] for source in original}
        covered_documents.add(document["id"])
        covered.update(
            comment["id"]
            for comment in workspace["comments"]
            if comment.get("source_ids") and set(comment["source_ids"]) <= source_ids
        )
    return (covered, covered_documents) if include_documents else covered


def _corrected_po(body, existing, rows):
    if existing["payload"]["purchase_order_number"] != body.purchase_order_number:
        return False
    old_ids, new_ids = set(existing["sources"]), set(_source_ids(body))

    def versions(ids):
        return {
            row["value"]["po_revision"]
            for row in rows
            if row["role"] == "purchase_order"
            and isinstance(row["value"].get("po_revision"), int)
            and ids.intersection(row.get("source_ids", [row["source_id"]]))
        }

    before, after = versions(old_ids), versions(new_ids)
    return len(before) == len(after) == 1 and next(iter(after)) > next(iter(before))


def _requested_quote(s, order, rows):
    requests = [row for row in rows if row["role"] == "quote_request"]
    if not requests:
        return None
    lines, exceptions, currencies, seen = [], [], set(), {}
    for row in requests:
        request = row["value"]
        tag = request["tag"]
        if tag in seen:
            if seen[tag] != request:
                return None
            continue
        seen[tag] = request
        candidates = [
            source
            for source in rows
            if source["role"] in {"erp_price", "quote", "supplier_offer", "order_scope"}
            and source["value"].get("equipment_category") == commercial_intake.CATEGORY
            and source["value"].get("tag") == tag
            and source["value"].get("model") == request["model"]
            and lifecycle._trusted_commercial_source(
                s, order, source["source_id"], "pricing"
            )
            and all(
                key in source["value"]
                for key in (
                    "description",
                    "unit_cost",
                    "gross_margin_percent",
                    "currency",
                    "exceptions",
                )
            )
            and isinstance(source["value"]["exceptions"], list)
            and all(
                isinstance(value, str) and value.strip()
                for value in source["value"]["exceptions"]
            )
        ]
        if len(candidates) != 1:
            return None
        catalog = candidates[0]
        terms, sid = catalog["value"], catalog["source_id"]
        if request.get("currency", terms["currency"]) != terms["currency"]:
            return None
        currencies.add(terms["currency"])
        lines.append(
            {
                "tag": tag,
                "quantity": request["quantity"],
                **{
                    key: terms[key]
                    for key in ("description", "unit_cost", "gross_margin_percent")
                },
                "source_ids": [*row["source_ids"], sid],
            }
        )
        exceptions.extend(
            {"text": text, "source_ids": [sid]} for text in terms["exceptions"]
        )
        for field in commercial_intake.REQUIREMENTS:
            if field not in request:
                continue
            offered = terms.get(field)
            if offered is None or commercial_intake.normalized(
                offered
            ) != commercial_intake.normalized(request[field]):
                exceptions.append(
                    {
                        "text": f"{tag}: requested {field.replace('_', ' ')} {request[field]}; "
                        + (
                            f"catalog offer records {offered}."
                            if offered is not None
                            else "the catalog does not establish this requirement."
                        )
                        + " Engineering and customer acceptance are required before commitment.",
                        "source_ids": [*row["field_source_ids"][field], sid],
                    }
                )
        if request.get("special_requirement"):
            exceptions.append(
                {
                    "text": "Customer requirement to confirm: "
                    + request["special_requirement"],
                    "source_ids": row["field_source_ids"]["special_requirement"],
                }
            )
    if len(currencies) != 1:
        return None
    return {
        "operation": "quote",
        "currency": next(iter(currencies)),
        "lines": lines,
        "exceptions": exceptions,
    }


def automatic_inputs(s, order, w):
    """Only complete, typed commercial records qualify for no-model preparation."""
    rows, complete = _structured_rows(w)
    if not complete:
        return [], rows
    quote, purchase, exceptions = [], [], []
    currencies, po_numbers = set(), set()
    purchase_rows = commercial_intake.latest_purchase_rows(rows)
    for row in rows:
        data, sid = row["value"], row["source_id"]
        if (
            row["role"] in {"quote", "supplier_offer", "erp_price", "order_scope"}
            and lifecycle._trusted_commercial_source(s, order, sid, "pricing")
            and all(
                key in data
                for key in (
                    "tag",
                    "description",
                    "quantity",
                    "unit_cost",
                    "gross_margin_percent",
                    "currency",
                    "exceptions",
                )
            )
            and isinstance(data["exceptions"], list)
            and all(
                isinstance(value, str) and value.strip() for value in data["exceptions"]
            )
        ):
            currencies.add(str(data["currency"]))
            quote.append(
                {
                    key: data[key]
                    for key in (
                        "tag",
                        "description",
                        "quantity",
                        "unit_cost",
                        "gross_margin_percent",
                    )
                }
                | {"source_ids": [sid]}
            )
            exceptions.extend(
                {"text": text, "source_ids": [sid]} for text in data["exceptions"]
            )
        if (
            row in purchase_rows
            and all(key in data for key in ("tag", "quantity", "unit_price"))
            and (data.get("purchase_order_number") or data.get("purchase_order"))
        ):
            po_numbers.add(
                data.get("purchase_order_number", data.get("purchase_order"))
            )
            purchase.append(
                {key: data[key] for key in ("tag", "quantity", "unit_price")}
                | {"source_ids": row.get("source_ids", [sid])}
            )
    requests = []
    incoming_quote = _requested_quote(s, order, rows)
    if (
        quote
        and not any(row["role"] == "quote_request" for row in rows)
        and len(currencies) == 1
        and len({line["tag"] for line in quote}) == len(quote)
    ):
        requests.append(
            {
                "operation": "quote",
                "currency": next(iter(currencies)),
                "lines": quote,
                "exceptions": exceptions,
            }
        )
    if incoming_quote:
        requests.append(incoming_quote)
    quote_package = next(
        (
            p
            for p in lifecycle.view(s, order)["packages"]
            if p["operation"] == "quote"
            and p["evidence_current"]
            and p["status"] not in {"superseded", "rejected"}
        ),
        None,
    )
    if quote_package:
        quoted_sources = set(quote_package["sources"])
        for row in purchase_rows:
            value = row["value"]
            quoted_models = {
                other["value"].get("model")
                for other in rows
                if other["source_id"] in quoted_sources
                and other["value"].get("tag") == value["tag"]
                and other["value"].get("model")
            }
            if (
                value.get("currency", quote_package["payload"]["currency"])
                != quote_package["payload"]["currency"]
                or value.get("model")
                and quoted_models != {value["model"]}
            ):
                purchase = []
                break
    if (
        purchase
        and len(po_numbers) == 1
        and len({line["tag"] for line in purchase}) == len(purchase)
    ):
        requests.append(
            {
                "operation": "kickoff",
                "purchase_order_number": next(iter(po_numbers)),
                "lines": purchase,
                "approval_document_ids": [],
            }
        )
    return requests, rows
