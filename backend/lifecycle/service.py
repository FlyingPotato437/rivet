"""Order-scoped deterministic coordination with source and approval dependencies.

The agent prepares typed work. A human decision is bound to its exact content
and source hashes. Increasing autonomy never grants money, scope, engineering,
or customer commitment authority to the agent.
"""

import json
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

from backend.company_context.brief import check_documents, order_brief
from backend.company_context.models import CompanyRule
from backend.domain.pricing import gross_margin, money
from backend.domain.service import digest, fail, get, scoped
from backend.identity import current_actor, current_identity
from backend.orders import service as orders
from backend.storage.models import (
    Document,
    OrderEvent,
    OrderRecord,
    Project,
    Span,
    now,
    uid,
)

from . import adapters

POLICY_KEY = "lifecycle-autonomy"
DEPENDENCIES = {
    "quote": [],
    "kickoff": ["quote"],
    "revision_review": [],
    "revision_check": ["revision_review"],
    "purchasing": ["revision_review"],
    "release": ["revision_check"],
    "closeout": ["release"],
}


def _state(order):
    return deepcopy(order.state.get("lifecycle", {"schema_version": 1, "packages": []}))


def policy(s):
    row = s.scalar(
        scoped(CompanyRule)
        .where(CompanyRule.key == POLICY_KEY, CompanyRule.status == "approved")
        .order_by(CompanyRule.version.desc())
    )
    value = row.provenance.get("policy", {}) if row else {}
    return {
        "level": value.get("level", 1),
        "allowed_routine_work": value.get("allowed_routine_work", []),
        "gate_approvers": value.get("gate_approvers", {}),
        "version": row.version if row else 0,
        "protected_gates": ["money", "scope", "engineering", "customer_commitment"],
        "customer_sending_supported": False,
        "external_writes_enabled": False,
        "local_http_actions_supported": True,
        "native_outlook_drafts_supported": True,
        "external_write_scope": "Real SAP, SharePoint, vault and Teams writes are disabled. Explicitly configured synthetic orders can execute against the local HTTP simulator. Approved documents may be placed in scoped, unsent Outlook drafts.",
    }


def set_policy(s, body):
    if current_identity().role != "org:admin":
        fail("A team administrator must configure autonomy.", 403)
    old = policy(s)
    value = body.model_dump(exclude={"reason"})
    row = CompanyRule(
        key=POLICY_KEY,
        version=old["version"] + 1,
        title="Order coordination autonomy",
        text=json.dumps(value, sort_keys=True),
        topics=["lifecycle_policy"],
        provenance={"kind": "lifecycle_policy", "policy": value},
        author=current_actor(),
        status="approved",
        reviewed_by=current_actor(),
        reviewed_at=now(),
        review_reason=body.reason,
    )
    s.add(row)
    s.flush()
    return policy(s)


def _latest(state, operation):
    return next(
        (
            p
            for p in reversed(state["packages"])
            if p["operation"] == operation and p["status"] != "superseded"
        ),
        None,
    )


def _sources(s, order, ids):
    result = {}
    for source_id in orders.valid_sources(s, order, ids, required=True):
        span = get(s, Span, source_id)
        document = get(s, Document, span.document_id)
        if document.state not in {"ready", "parsed"}:
            fail("Wait for the cited documents to finish processing.", 409)
        result[source_id] = {
            "hash": digest([document.sha256, document.meta, span.text, span.location]),
            "document_id": document.id,
            "document_sha256": document.sha256,
        }
    return result


def _all_source_ids(body):
    value = body.model_dump()
    ids = []

    def walk(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key.endswith("source_ids"):
                    ids.extend(child)
                else:
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return list(dict.fromkeys(ids))


def _structured(s, ids):
    for source_id in ids:
        try:
            value = json.loads(get(s, Span, source_id).text)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            yield value


def _trusted_commercial_source(s, order, source_id, purpose):
    span = get(s, Span, source_id)
    document = get(s, Document, span.document_id)
    local = document.meta.get("lifecycle_trust", {})
    if (
        get(s, Project, order.project_id).synthetic
        and local.get("origin") == "backend_local_demo"
        and local.get("order_id") == order.id
        and local.get("organization_id") == order.organization_id
        and local.get("document_sha256") == document.sha256
        and purpose in local.get("purposes", [])
    ):
        return True
    # Only administrator-controlled, still-current ERP bindings may supply live
    # commercial facts. An uploaded JSON paragraph cannot declare its own trust.
    connected = document.meta.get("connected_source", {})
    if (
        connected.get("kind") != "erp"
        or connected.get("mode") != "live"
        or document.meta.get("active") is not True
    ):
        return False
    from backend.connected_context import providers

    binding = next(
        (
            b
            for b in providers.bindings(order.number)
            if b.get("key") == connected.get("key")
            and b.get("kind") == "erp"
            and b.get("mode", "live") == "live"
        ),
        None,
    )
    return bool(
        binding
        and document.meta.get("connected_identity")
        == digest([connected, document.sha256])
        and purpose in binding.get("commercial_fact_purposes", [])
    )


def _trusted_values(s, order, ids, purpose):
    for source_id in ids:
        if _trusted_commercial_source(s, order, source_id, purpose):
            yield from _structured(s, [source_id])


def _price(s, order, item, currency):
    # Numeric prices cannot be invented and laundered through an unrelated cite.
    matching = any(
        str(v.get("tag")) == item.tag
        and str(v.get("currency")) == currency
        and orders.equivalent(v.get("unit_cost"), item.unit_cost)
        and orders.equivalent(v.get("gross_margin_percent"), item.gross_margin_percent)
        for v in _trusted_values(s, order, item.source_ids, "pricing")
    )
    if not matching:
        fail(
            f"{item.tag}: cite a trusted structured ERP price containing this tag, currency, unit cost and margin rule. Uploaded customer JSON cannot establish your cost or margin.",
            409,
        )
    price = gross_margin(item.unit_cost, item.gross_margin_percent)
    quantity = (
        item.quantity
        if hasattr(item, "quantity")
        else item.requested_quantity - item.baseline_quantity
    )
    return {
        "unit_cost": str(money(item.unit_cost)),
        "gross_margin_percent": str(item.gross_margin_percent),
        "unit_price": str(price),
        "quantity": str(quantity),
        "extended_price": str(money(price * quantity)),
        "currency": currency,
    }


def _documents(s, order, ids):
    docs = [get(s, Document, id) for id in dict.fromkeys(ids)]
    if any(d.project_id != order.project_id for d in docs):
        fail("Choose documents from this order only.", 404)
    if any(d.state not in {"ready", "parsed"} for d in docs):
        fail("A selected document has not finished processing.", 409)
    return docs


def _comment_map(s, order):
    record = s.scalar(scoped(OrderRecord).where(OrderRecord.order_id == order.id))
    comments = (
        record.data.get("comments", []) if record else order.state.get("comments", [])
    )
    return {c["id"]: c for c in comments}


def _unique(items, field):
    values = [getattr(i, field) for i in items]
    if len(set(values)) != len(values):
        fail(f"Each {field.replace('_', ' ')} must appear once.")


def _prepare_quote(s, order, state, body):
    _unique(body.lines, "tag")
    lines = [
        {
            "tag": line.tag,
            "description": line.description,
            "source_ids": line.source_ids,
            **_price(s, order, line, body.currency),
        }
        for line in body.lines
    ]
    total = sum((Decimal(line["extended_price"]) for line in lines), Decimal(0))
    payload = {
        "currency": body.currency,
        "lines": lines,
        "total": str(money(total)),
        "exceptions": [x.model_dump() for x in body.exceptions],
        "email_draft": {
            "subject": f"{order.number} - quotation for review",
            "body": f"Prepared quotation total: {body.currency} {money(total)}.\n"
            + "\n".join("Exception: " + x.text for x in body.exceptions),
            "status": "unsent_artifact",
        },
    }
    return (
        "Quote and specification exceptions prepared",
        payload,
        ["money", "scope", "customer_commitment"],
        [],
        [adapters.prepare_receipt("Outlook", "quote_draft", payload["email_draft"])],
        {"quote.json": payload},
    )


def _prepare_kickoff(s, order, state, body):
    from .purchase_scope import compare

    _unique(body.lines, "tag")
    quote = _latest(state, "quote")
    if not quote:
        fail("Prepare the quote before comparing the purchase order.", 409)
    quoted = {line["tag"]: line for line in quote["payload"]["lines"]}
    po = {line.tag: line for line in body.lines}
    mismatches = []
    for tag in sorted(set(quoted) | set(po)):
        before, after = quoted.get(tag), po.get(tag)
        if (
            not before
            or not after
            or Decimal(before["quantity"]) != after.quantity
            or Decimal(before["unit_price"]) != after.unit_price
        ):
            mismatches.append(
                {
                    "tag": tag,
                    "quote": before,
                    "purchase_order": after.model_dump(mode="json") if after else None,
                }
            )
    scope_mismatches, scope_source_ids = compare(
        s,
        order,
        quote,
        [line.model_dump(mode="json") for line in body.lines],
        body.purchase_order_number,
    )
    brief = order_brief(s, order.id)
    preflight = (
        check_documents(
            s,
            order.id,
            type("Check", (), {"document_ids": body.approval_document_ids})(),
        )
        if brief["customer"] and body.approval_document_ids
        else None
    )
    blockers = (
        ["Resolve the purchase-order differences before accepting scope."]
        if mismatches
        else []
    )
    blockers.extend(item["detail"] for item in scope_mismatches)
    if quote["status"] != "approved":
        blockers.append("Sales has not approved the prepared quote.")
    payload = {
        "purchase_order_number": body.purchase_order_number,
        "lines": [x.model_dump(mode="json") for x in body.lines],
        "mismatches": mismatches,
        "scope_mismatches": scope_mismatches,
        "scope_source_ids": scope_source_ids,
        "quoted_exceptions": quote["payload"].get("exceptions", []),
        "exception_acceptance": "Not established by numeric or feature matching. Review the original customer evidence.",
        "customer_brief": brief,
        "drawing_preflight": preflight,
        "clarification": "Please confirm the differences between your purchase order and our quotation."
        if mismatches or scope_mismatches
        else "",
        "job_folder": order.number,
    }
    receipts = [
        adapters.prepare_receipt(
            "SAP",
            "draft_sales_order",
            {
                "number": order.number,
                "purchase_order": body.purchase_order_number,
                "lines": payload["lines"],
            },
        ),
        adapters.prepare_receipt(
            "SharePoint", "create_job_folder", {"name": order.number}
        ),
    ]
    return (
        "Purchase order checked and kickoff prepared",
        payload,
        ["scope", "engineering"],
        blockers,
        receipts,
        {},
    )


def _draft_bundle(s, order, bundle_id):
    from uuid import UUID

    from backend.drafting import DraftingError, load_bundle
    from backend.storage import db

    try:
        UUID(bundle_id)
    except ValueError:
        fail("Choose a saved draft revision bundle.")
    report = (
        db.DATA
        / "drafting"
        / order.organization_id
        / order.id
        / bundle_id
        / "report.json"
    )
    try:
        bundle = load_bundle(
            report, organization_id=order.organization_id, order_id=order.id
        )
        data = json.loads(report.read_text())
    except (DraftingError, OSError, ValueError) as exc:
        fail(f"The saved drawing revision is unavailable or changed: {exc}", 409)
    hashes = {
        doc.sha256
        for doc in s.scalars(
            scoped(Document).where(Document.project_id == order.project_id)
        )
    }
    if any(source["sha256"] not in hashes for source in data["sources"]):
        fail("The CAD bundle's originals are not registered to this order.", 409)
    result = bundle.model_dump(mode="json")
    result["drawing_number"] = data["drawing_number"]
    result.pop("report_path", None)
    for artifact in result["artifacts"]:
        artifact["url"] = (
            f"/api/orders/{order.id}/lifecycle/drafting/{bundle_id}/{artifact['name']}"
        )
    return result


def _prepare_revision(s, order, state, body):
    _unique(body.comments, "comment_id")
    known = _comment_map(s, order)
    unknown = [c.comment_id for c in body.comments if c.comment_id not in known]
    if unknown:
        fail("The review contains comments outside this order.", 404)
    revision_ids = {
        id
        for id, comment in known.items()
        if str(comment.get("revision", "")).removeprefix("Rev ").casefold()
        == body.revision.removeprefix("Rev ").casefold()
    }
    if revision_ids and revision_ids != {c.comment_id for c in body.comments}:
        fail(
            "Process every comment in this returned revision; do not omit the difficult ones.",
            409,
        )
    work = []
    used_edits = set()
    for item in body.comments:
        entry = item.model_dump(mode="json")
        entry["text"] = known[item.comment_id].get("text", "")
        if item.kind == "answer" and not item.response:
            fail("Each answer needs a prepared response with its source references.")
        if item.kind == "text_edit":
            if not item.draft_bundle_id or not item.draft_change_id:
                fail("A drawing edit needs an actual prepared CAD revision bundle.")
            entry["draft_bundle"] = _draft_bundle(s, order, item.draft_bundle_id)
            change = next(
                (
                    change
                    for change in entry["draft_bundle"]["changes"]
                    if change["id"] == item.draft_change_id
                ),
                None,
            )
            if not change or change["evidence_id"] not in item.source_ids:
                fail(
                    "Each draft edit must identify its actual change and cited markup.",
                    409,
                )
            key = (item.draft_bundle_id, item.draft_change_id)
            if key in used_edits:
                fail("The same CAD edit cannot resolve two separate comments.", 409)
            used_edits.add(key)
        if item.kind == "layout_task":
            if not item.draft_bundle_id or not item.layout_task_id:
                fail(
                    "A layout task needs a saved CAD bundle and actual entity task ID.",
                    409,
                )
            entry["draft_bundle"] = _draft_bundle(s, order, item.draft_bundle_id)
            task = next(
                (
                    task
                    for task in entry["draft_bundle"]["layout_tasks"]
                    if task["id"] == item.layout_task_id
                ),
                None,
            )
            if not task or task["evidence_id"] not in item.source_ids:
                fail("The layout task must reference its actual cited markup.", 409)
            location = {
                "sheet": entry["draft_bundle"]["drawing_number"],
                "x": task["anchor_xyz"][0],
                "y": task["anchor_xyz"][1],
                "entity_handle": task["entity_handle"],
                "bbox": task["bbox"],
            }
            if any(
                key in item.layout_location
                and not orders.equivalent(item.layout_location[key], location[key])
                for key in ("sheet", "x", "y", "entity_handle")
            ):
                fail(
                    "The proposed layout location disagrees with the validated CAD entity anchor.",
                    409,
                )
            entry["layout_location"] = location
        entry["status"] = (
            "prepared" if item.kind == "answer" else "requires_engineering"
        )
        work.append(entry)
    change_lines = []
    blockers = []
    for addition in body.scope_additions:
        if addition.requested_quantity <= addition.baseline_quantity:
            fail("A scope addition must exceed the cited baseline quantity.")
        billable = addition.billability == "contract_confirmed" and any(
            v.get("billable_scope_tag") == addition.tag and v.get("billable") is True
            for v in _trusted_values(
                s, order, addition.billability_source_ids, "commercial_scope"
            )
        )
        if not billable:
            blockers.append(
                f"{addition.tag}: a contract owner must confirm whether the extra scope is billable."
            )
        change_lines.append(
            {
                "tag": addition.tag,
                "description": addition.description,
                "billability": "contract_confirmed" if billable else "unconfirmed",
                "source_ids": addition.source_ids,
                "billability_source_ids": addition.billability_source_ids,
                **_price(s, order, addition, "USD"),
            }
        )
    counts = {
        kind: sum(c.kind == kind for c in body.comments)
        for kind in ("answer", "text_edit", "layout_task")
    }
    reply = {
        "subject": f"{order.number} - Rev {body.revision} comments",
        "body": "\n\n".join(
            f"Comment {known[c.comment_id].get('number', c.comment_id)}: {c.response}"
            for c in body.comments
            if c.response
        ),
        "source_ids": sorted(
            {source for c in body.comments for source in c.source_ids}
        ),
        "status": "unsent_artifact",
    }
    payload = {
        "revision": body.revision,
        "comments": work,
        "counts": counts,
        "change_order": {
            "lines": change_lines,
            "total": str(
                money(
                    sum(
                        (Decimal(x["extended_price"]) for x in change_lines), Decimal(0)
                    )
                )
            ),
            "status": "draft",
        },
        "email_draft": reply,
    }
    gates = ["engineering", "customer_commitment"] + (
        ["money", "scope"] if change_lines else []
    )
    artifacts = {
        "reply.json": reply,
        "drafting-list.json": [c for c in work if c["kind"] != "answer"],
    }
    if change_lines:
        artifacts["change-order.json"] = payload["change_order"]
    return (
        f"{len(work)} comments processed: {counts['answer']} answers, {counts['text_edit']} draft edits, {counts['layout_task']} drafting tasks",
        payload,
        gates,
        blockers,
        [adapters.prepare_receipt("Outlook", "customer_reply", reply)],
        artifacts,
    )


def _bom(rows):
    _unique(rows, "tag")
    return {
        row.tag: {
            "quantity": str(row.quantity.normalize()),
            "part_number": row.part_number,
        }
        for row in rows
    }


def _verify_bom_evidence(s, order, rows, approved):
    for row in rows:
        matches = any(
            value.get("tag") == row.tag
            and value.get("part_number") == row.part_number
            and orders.equivalent(value.get("quantity"), row.quantity)
            for value in _structured(s, row.source_ids)
        )
        if not matches:
            fail(
                f"{row.tag}: cite the structured bill-of-materials row with this part and quantity.",
                409,
            )
        if approved:
            docs = [
                get(s, Document, get(s, Span, id).document_id) for id in row.source_ids
            ]
            if not any(
                str(d.meta.get("release_status", "")).lower()
                in {"approved", "released"}
                for d in docs
            ):
                fail("The baseline bill of materials needs a recorded approval.", 409)


def _prepare_check(s, order, state, body):
    _unique(body.resolutions, "comment_id")
    review = _latest(state, "revision_review")
    if not review:
        fail("There is no returned revision to check.", 409)
    expected = {c["comment_id"]: c for c in review["payload"]["comments"]}
    resolutions = {c.comment_id: c for c in body.resolutions}
    if set(resolutions) - set(expected):
        fail("The resolution check includes comments outside this revision.", 404)
    missing = sorted(set(expected) - set(resolutions))
    incorrect = [
        id
        for id, row in resolutions.items()
        if row.disposition
        != {
            "answer": "answered",
            "text_edit": "edit_verified",
            "layout_task": "layout_verified",
        }[expected[id]["kind"]]
    ]
    from .verification import resolution_failure

    recorded = _comment_map(s, order)
    proof_failures = {
        id: reason
        for id, resolution in resolutions.items()
        if (
            reason := resolution_failure(
                s, order, expected[id], resolution, body.revision, recorded.get(id)
            )
        )
    }
    blockers = []
    if missing or incorrect:
        blockers.append(
            f"{len(missing) + len(incorrect)} comments still need the correct evidence-backed resolution."
        )
    blockers.extend(f"Comment {id}: {reason}" for id, reason in proof_failures.items())
    _verify_bom_evidence(s, order, body.approved_bom, approved=True)
    _verify_bom_evidence(s, order, body.revision_bom, approved=False)
    bom_matches = _bom(body.approved_bom) == _bom(body.revision_bom)
    if not bom_matches:
        blockers.append(
            "The revision bill of materials differs from the approved order bill of materials."
        )
    docs = _documents(s, order, body.outbound_document_ids)
    unreleased = [
        d.name
        for d in docs
        if str(d.meta.get("release_status", d.meta.get("review_status", ""))).lower()
        not in {"released", "approved_for_customer"}
    ]
    if unreleased:
        blockers.append("Unreleased outbound files: " + ", ".join(unreleased))
    document_hashes = {d.id: digest([d.sha256, d.meta]) for d in docs}
    payload = {
        "revision": body.revision,
        "total_comments": len(expected),
        "documented_resolutions": len(expected)
        - len(set(missing) | set(incorrect) | set(proof_failures)),
        "resolution_failures": proof_failures,
        "addressed_comments": 0,
        "resolutions": [x.model_dump(mode="json") for x in body.resolutions],
        "bom_matches": bom_matches,
        "outbound_document_ids": body.outbound_document_ids,
        "document_hashes": document_hashes,
        "unreleased_files": unreleased,
        "ready_for_human_signoff": not blockers,
    }
    return (
        f"Rev {body.revision}: {payload['documented_resolutions']} documented resolutions prepared for engineering sign-off",
        payload,
        ["engineering"],
        blockers,
        [],
        {"revision-check.json": payload},
    )


def _prepare_purchasing(s, order, state, body):
    if not any(
        value.get("part_number") == body.part_number
        and value.get("lead_time_days") == body.lead_time_days
        for value in _trusted_values(s, order, body.source_ids, "lead_time")
    ):
        fail(
            "Cite the structured supplier or ERP record containing this part and lead time.",
            409,
        )
    arrival = body.order_date + timedelta(days=body.lead_time_days)
    proposed = max(arrival, body.current_ship_date)
    payload = {
        "part_number": body.part_number,
        "lead_time_days": body.lead_time_days,
        "order_date": body.order_date.isoformat(),
        "current_ship_date": body.current_ship_date.isoformat(),
        "proposed_ship_date": proposed.isoformat(),
        "schedule_status": "proposal",
        "basis": "Earliest material availability only; production, test and transit durations still need confirmation.",
        "source_ids": body.source_ids,
    }
    receipts = [
        adapters.prepare_receipt("Teams", "buyer_change_draft", payload),
        adapters.prepare_receipt("Schedule", "proposed_ship_date", payload),
    ]
    return (
        "Purchasing change and schedule impact prepared",
        payload,
        ["money", "customer_commitment"],
        [],
        receipts,
        {},
    )


def _prepare_release(s, order, state, body):
    check = _latest(state, "revision_check")
    if not check or check["status"] != "approved":
        fail(
            "A clean, engineer-approved revision check is required before release.", 409
        )
    if check["blockers"]:
        fail("The revision still has release blockers.", 409)
    review = _latest(state, "revision_review")
    if not review or review["status"] != "approved":
        fail(
            "The returned-comment responses, engineering edits and any scope/price changes need their recorded decisions before release.",
            409,
        )
    for package in state["packages"]:
        if package["status"] in {"superseded", "rejected"} or package["operation"] in {
            "release",
            "closeout",
        }:
            continue
        if package["blockers"]:
            fail("Resolve the outstanding order-work blockers before release.", 409)
        if any(
            gate in {"money", "scope"}
            and not any(
                d["gate"] == gate and d["decision"] == "approve"
                for d in package["decisions"]
            )
            for gate in package["gates"]
        ):
            fail("A money or scope decision is still outstanding on this order.", 409)
    for ids, role in (
        (body.customer_approval_source_ids, "customer_approval"),
        (body.engineering_approval_source_ids, "engineering_approval"),
    ):
        docs = [get(s, Document, get(s, Span, id).document_id) for id in ids]
        matching = [
            d for d in docs if d.kind == role or d.meta.get("order_role") == role
        ]
        if not matching:
            fail(
                f"Release needs a document classified as {role.replace('_', ' ')}.", 409
            )
        outbound_hashes = {
            get(s, Document, id).sha256
            for id in check["payload"]["outbound_document_ids"]
        }
        if not any(
            d.meta.get("approved_revision") == check["payload"]["revision"]
            and set(d.meta.get("approved_document_sha256", [])) == outbound_hashes
            for d in matching
        ):
            fail(
                f"The {role.replace('_', ' ')} must identify this exact revision and its outbound document hashes.",
                409,
            )
    payload = {
        "revision_check_id": check["id"],
        "customer_approval_source_ids": body.customer_approval_source_ids,
        "engineering_approval_source_ids": body.engineering_approval_source_ids,
        "outbound_document_ids": check["payload"]["outbound_document_ids"],
        "release_status": "prepared_local_demo",
    }
    receipts = [
        adapters.prepare_receipt("SAP", "release_order", payload),
        adapters.prepare_receipt("Drawing vault", "release_to_production", payload),
    ]
    return (
        "Production release package prepared",
        payload,
        ["engineering", "scope", "customer_commitment"],
        [],
        receipts,
        {},
    )


def _prepare_closeout(s, order, state, body):
    from .closeout import document_evidence, prepare_payload
    from .outlook_handoff import validate_handover_request

    validate_handover_request(s, order, body.handover_request_source_ids)

    release = _latest(state, "release")
    if not release or release["status"] != "approved":
        fail("Approve the release package before preparing closeout.", 409)
    reports = _documents(s, order, body.test_report_document_ids)
    as_built = _documents(s, order, body.as_built_document_ids)
    other = _documents(s, order, body.other_document_ids)
    if any(
        d.kind != "test_report" and d.meta.get("order_role") != "test_report"
        for d in reports
    ):
        fail("Closeout needs documents classified as test reports.")
    if any(
        d.kind != "as_built" and d.meta.get("order_role") != "as_built"
        for d in as_built
    ):
        fail("Closeout needs documents classified as as-built drawings.")
    check = _latest(state, "revision_check")
    if not check or check["id"] != release["payload"]["revision_check_id"]:
        fail("Closeout needs the current released revision check.", 409)
    payload = prepare_payload(
        order, reports, as_built, other, check["payload"]["revision"]
    )
    payload["source_ids"] = document_evidence(s, order, payload["documents"])
    return (
        "Closeout documents gathered and handover prepared",
        payload,
        ["engineering", "customer_commitment"],
        [],
        [],
        {"closeout.json": payload},
    )


def _current(s, order, state, package):
    try:
        if (
            "synthetic" in package
            and package["synthetic"] != get(s, Project, order.project_id).synthetic
        ):
            return False
        current = (
            _sources(s, order, list(package["sources"])) if package["sources"] else {}
        )
        if current != package["sources"]:
            return False
        if package["operation"] == "quote" and any(
            not any(
                _trusted_commercial_source(s, order, id, "pricing")
                for id in line["source_ids"]
            )
            for line in package["payload"]["lines"]
        ):
            return False
        if package["operation"] == "purchasing" and not any(
            _trusted_commercial_source(s, order, id, "lead_time")
            for id in package["payload"]["source_ids"]
        ):
            return False
        if package["operation"] == "revision_review":
            for line in package["payload"]["change_order"]["lines"]:
                if not any(
                    _trusted_commercial_source(s, order, id, "pricing")
                    for id in line["source_ids"]
                ):
                    return False
                if line["billability"] == "contract_confirmed" and not any(
                    _trusted_commercial_source(s, order, id, "commercial_scope")
                    for id in line["billability_source_ids"]
                ):
                    return False
        for operation, id in package["dependencies"].items():
            dependency = _latest(state, operation)
            if (
                not dependency
                or dependency["id"] != id
                or not _current(s, order, state, dependency)
            ):
                return False
        if package["operation"] == "kickoff":
            from .purchase_scope import compare

            quote = _latest(state, "quote")
            differences, source_ids = compare(
                s,
                order,
                quote,
                package["payload"]["lines"],
                package["payload"]["purchase_order_number"],
            )
            if differences != package["payload"].get("scope_mismatches", []) or not set(
                source_ids
            ) <= set(package["sources"]):
                return False
        for doc_id, expected in package["payload"].get("document_hashes", {}).items():
            doc = get(s, Document, doc_id)
            if digest([doc.sha256, doc.meta]) != expected:
                return False
        if package["operation"] == "closeout":
            from .closeout import current

            if not current(s, order, package["payload"]):
                return False
        for comment in package["payload"].get("comments", []):
            if comment.get("draft_bundle_id"):
                current_bundle = _draft_bundle(s, order, comment["draft_bundle_id"])
                if (
                    current_bundle["report_sha256"]
                    != comment["draft_bundle"]["report_sha256"]
                ):
                    return False
        return True
    except Exception as exc:
        from fastapi import HTTPException

        if isinstance(exc, HTTPException) and exc.status_code in {403, 404, 409}:
            return False
        raise


def prepare(s, order, body):
    orders.check_version(order, body.expected_version)
    state = _state(order)
    sources = _sources(s, order, _all_source_ids(body)) if _all_source_ids(body) else {}
    operation = body.operation
    title, payload, gates, blockers, receipts, artifact_payloads = {
        "quote": _prepare_quote,
        "kickoff": _prepare_kickoff,
        "revision_review": _prepare_revision,
        "revision_check": _prepare_check,
        "purchasing": _prepare_purchasing,
        "release": _prepare_release,
        "closeout": _prepare_closeout,
    }[operation](s, order, state, body)
    if operation == "closeout":
        sources.update(_sources(s, order, payload["source_ids"]))
    if operation == "kickoff" and payload.get("scope_source_ids"):
        sources.update(_sources(s, order, payload["scope_source_ids"]))
    dependencies = {}
    for name in DEPENDENCIES[operation]:
        prior = _latest(state, name)
        if not prior:
            fail(f"Prepare {name.replace('_', ' ')} first.", 409)
        if not _current(s, order, state, prior):
            fail(
                f"The {name.replace('_', ' ')} evidence changed. Prepare it again.", 409
            )
        dependencies[name] = prior["id"]
    previous = _latest(state, operation)
    if previous:
        previous["status"] = "superseded"
    package_id = uid()
    package = {
        "id": package_id,
        "operation": operation,
        "title": title,
        "mode": "local_demo",
        "synthetic": get(s, Project, order.project_id).synthetic,
        "status": "blocked" if blockers else "awaiting_decision",
        "payload": payload,
        "gates": gates,
        "decisions": [],
        "blockers": blockers,
        "sources": sources,
        "dependencies": dependencies,
        "receipts": receipts,
        "prepared_at": now().isoformat(),
        "prepared_by": "Rivet",
        "artifacts": [],
    }
    package["fingerprint"] = digest(
        {
            key: package[key]
            for key in (
                "operation",
                "payload",
                "gates",
                "sources",
                "dependencies",
                "synthetic",
            )
        }
    )
    artifact_payloads["work.json"] = {
        key: package[key]
        for key in (
            "id",
            "operation",
            "title",
            "mode",
            "payload",
            "fingerprint",
            "sources",
            "receipts",
        )
    }
    for name, value in artifact_payloads.items():
        package["artifacts"].append(
            adapters.write_json(
                order.organization_id, order.id, package_id, name, value
            )
        )
    from .exports import prepare_artifacts

    package["artifacts"].extend(prepare_artifacts(s, order, package))
    state["packages"].append(package)
    order.state = {**order.state, "lifecycle": state}
    orders.commit(
        s,
        order,
        title,
        "lifecycle_prepared",
        {
            "package_id": package_id,
            "operation": operation,
            "fingerprint": package["fingerprint"],
            "mode": "local_demo",
        },
        actor="AI · Rivet",
    )
    return view(s, order)


def decide(s, order, package_id, body):
    orders.check_version(order, body.expected_version)
    state = _state(order)
    package = next((p for p in state["packages"] if p["id"] == package_id), None)
    if not package:
        fail("Prepared work not found.", 404)
    identity = current_identity()
    local_admin = (
        get(s, Project, order.project_id).synthetic and identity.role == "org:admin"
    )
    named_approvers = policy(s)["gate_approvers"].get(body.gate, [])
    if not local_admin and current_actor() not in named_approvers:
        fail(
            "This approval requires a named company approver for the selected decision. A general workspace role is not engineering or commercial authority.",
            403,
        )
    if package["status"] in {"approved", "rejected", "superseded"}:
        fail("This prepared work already has a final decision.", 409)
    if package["blockers"]:
        fail(
            {
                "message": "Resolve the source blockers and prepare a new package.",
                "blockers": package["blockers"],
            },
            409,
        )
    if body.gate not in package["gates"]:
        fail("That decision is not required by this package.")
    if not _current(s, order, state, package):
        fail(
            "The work or its evidence changed. Prepare it again before approving.", 409
        )
    evidence = _sources(s, order, body.source_ids)
    if any(d["gate"] == body.gate for d in package["decisions"]):
        fail("This approval gate already has a recorded decision.", 409)
    package["decisions"].append(
        {
            "gate": body.gate,
            "decision": body.decision,
            "reason": body.reason,
            "source_ids": body.source_ids,
            "evidence_hashes": evidence,
            "actor": current_actor(),
            "at": now().isoformat(),
            "fingerprint": package["fingerprint"],
        }
    )
    if body.decision == "approve":
        from .feedback import capture_candidates

        with s.begin_nested():
            package["feedback"] = capture_candidates(s, order, package)
    if body.decision == "reject":
        package["status"] = "rejected"
    elif {d["gate"] for d in package["decisions"] if d["decision"] == "approve"} == set(
        package["gates"]
    ):
        package["status"] = "approved"
        package["receipts"] = [
            {**r, "status": "approved_local_demo"} for r in package["receipts"]
        ]
        package["artifacts"].append(
            adapters.write_json(
                order.organization_id,
                order.id,
                package_id,
                "approved.json",
                {
                    "fingerprint": package["fingerprint"],
                    "decisions": package["decisions"],
                    "receipts": [
                        {**r, "status": "approved_local_demo"}
                        for r in package["receipts"]
                    ],
                },
            )
        )
    order.state = {**order.state, "lifecycle": state}
    orders.commit(
        s,
        order,
        f"{body.gate.replace('_', ' ').capitalize()} decision: {body.decision}",
        "lifecycle_decision",
        {
            "package_id": package_id,
            "gate": body.gate,
            "decision": body.decision,
            "fingerprint": package["fingerprint"],
        },
    )
    if body.decision == "approve" and package["status"] == "approved":
        from backend.workflow_actions.jobs import enqueue as enqueue_local_actions

        enqueue_local_actions(s, order, package)
        from backend.workflow_actions.publication import enqueue_approved

        enqueue_approved(s, order, package)
        from .outlook_handoff import enqueue_if_unambiguous

        handoff = enqueue_if_unambiguous(s, order, package)
        if handoff["status"] != "not_applicable":
            s.add(
                OrderEvent(
                    order_id=order.id,
                    version=order.version,
                    kind="lifecycle_handoff",
                    summary="Rivet checked the approved customer-document handoff",
                    actor="AI · Rivet",
                    data={"package_id": package_id, "handoff": handoff},
                )
            )
            s.flush()
    return view(s, order)


def view(s, order):
    state = _state(order)
    handoffs = {}
    if state["packages"]:
        from .outlook_handoff import statuses_for_order

        handoffs = statuses_for_order(s, order)
        for event in s.scalars(
            scoped(OrderEvent)
            .where(
                OrderEvent.order_id == order.id, OrderEvent.kind == "lifecycle_handoff"
            )
            .order_by(OrderEvent.created_at.desc())
            .limit(100)
        ):
            handoffs.setdefault(
                event.data.get("package_id"), event.data.get("handoff", {})
            )
    packages = []
    for package in reversed(state["packages"]):
        item = deepcopy(package)
        item["outlook_handoff"] = handoffs.get(
            package["id"], {"status": "not_requested"}
        )
        item["evidence_current"] = _current(s, order, state, package)
        if not item["evidence_current"] and item["status"] not in {
            "rejected",
            "superseded",
        }:
            item["status"] = "stale"
        item["pending_gates"] = [
            gate
            for gate in item["gates"]
            if not any(
                d["gate"] == gate and d["decision"] == "approve"
                for d in item["decisions"]
            )
        ]
        if item["operation"] == "revision_check" and item["status"] == "approved":
            item["payload"]["addressed_comments"] = item["payload"][
                "documented_resolutions"
            ]
            item["title"] = (
                f"Rev {item['payload']['revision']}: {item['payload']['addressed_comments']} of {item['payload']['total_comments']} comments verified by engineering"
            )
        packages.append(item)
    active = [p for p in packages if p["status"] not in {"superseded", "rejected"}]
    return {
        "order_id": order.id,
        "version": order.version,
        "mode": "local_demo",
        "policy": policy(s),
        "completed_work": [
            {
                "id": p["id"],
                "title": p["title"],
                "operation": p["operation"],
                "at": p["prepared_at"],
                "status": p["status"],
            }
            for p in active
        ],
        "decisions": [
            {"package_id": p["id"], "title": p["title"], "gates": p["pending_gates"]}
            for p in active
            if p["status"] == "awaiting_decision"
        ],
        "blockers": [
            {"package_id": p["id"], "message": message}
            for p in active
            for message in (
                p["blockers"]
                + (
                    ["The source evidence changed. Prepare this work again."]
                    if p["status"] == "stale"
                    else []
                )
            )
        ],
        "packages": packages,
        "external_writes": "ERP, SharePoint, vault and Teams receipts are local demo work. Approved customer PDFs may be queued as unsent Outlook drafts when a captured email establishes the thread.",
    }
