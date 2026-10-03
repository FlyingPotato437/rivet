"""Deterministic, evidence-backed engineering order coordination.

Originals and spans remain immutable. Every accepted change creates an append-only
snapshot. Language models can propose language/actions; only these functions can
reconcile obligations, compare values, verify propagation, or authorize release.
"""

from __future__ import annotations

import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from backend.agents.gateway import configured
from backend.domain.service import digest, fail, get, new_project, scoped
from backend.identity import current_actor, auth_mode
from backend.storage.models import (
    Document,
    Order,
    OrderAction,
    OrderEvent,
    OrderRelease,
    OrderRevision,
    OrderWaiver,
    Project,
    Span,
    now,
    uid,
)

PRECEDENCE = {
    "purchase_order": 400,
    "accepted_exception": 300,
    "specification": 200,
    "drawing": 100,
}
TARGETS = ("drawing", "bom", "supplier_po", "nameplate")
MINIMUM_ATTRIBUTES = {
    "short_circuit_ka",
    "short_time_ka",
    "short_time_duration_s",
    "main_bus_a",
    "main_breaker_interrupting_ka",
}
ROLE_LABELS = {
    "purchase_order": "Purchase order",
    "accepted_exception": "Accepted exception",
    "specification": "Specification",
    "drawing": "Approval drawing",
    "bom": "Bill of materials",
    "supplier_po": "Supplier purchase order",
    "nameplate": "Nameplates",
    "markup": "Review markups",
    "quote": "Quote",
}


def label(attribute):
    try:
        from .extraction import ATTRIBUTE_REGISTRY

        entry = ATTRIBUTE_REGISTRY.get(attribute, {})
        return (
            entry.get("label", attribute.replace("_", " ").capitalize())
            if isinstance(entry, dict)
            else attribute.replace("_", " ").capitalize()
        )
    except ImportError:
        return attribute.replace("_", " ").capitalize()


def check_version(order, expected):
    if order.version != expected:
        fail(
            "This order has changed. Refresh to review the current revision before continuing.",
            409,
        )


def lock_order(s, id):
    initial = get(s, Order, id)
    get(s, Project, initial.project_id, True)
    order = get(s, Order, id, True)
    s.refresh(order)
    return order


def valid_sources(s, order, source_ids, required=False):
    ids = list(dict.fromkeys(source_ids or []))
    if required and not ids:
        fail("Evidence is required. Select a source from this order.")
    for id in ids:
        span = get(s, Span, id)
        if get(s, Document, span.document_id).project_id != order.project_id:
            fail("Evidence belongs to another order.", 403)
    return ids


def revision_label(state, version):
    labels = [
        d.get("revision_label")
        for d in state.get("documents", [])
        if d.get("role") == "drawing" and d.get("active")
    ]
    if not any(labels):
        # A newly queued package can supersede the previous drawing family
        # before its label is read. Retain the last known package label during
        # processing rather than presenting a spurious return to Rev A.
        labels = [
            d.get("revision_label")
            for d in state.get("documents", [])
            if d.get("role") == "drawing"
        ]
    value = str(next((v for v in reversed(labels) if v), "A"))
    return value if value.lower().startswith("rev ") else "Rev " + value


def document_family(name):
    # Revision designators only; arbitrary document names remain distinct.
    stem = re.sub(r"\.[^.]+$", "", name.lower())
    stem = re.sub(r"(?:[_\- ]+rev(?:ision)?[ _\-]*[a-z0-9]+)\b", "", stem)
    return re.sub(r"[ _\-]+", " ", stem).strip()


def canonical_device(device, aliases):
    device = str(device or "PACKAGE").strip().upper()
    # Only explicit, reviewed aliases enter this map. Never fuzzy-match devices.
    return aliases.get(device, device)


def comparable(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return str(value).strip().casefold()


def equivalent(a, b):
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return comparable(a) == comparable(b)


def satisfies(attribute, actual, expected):
    a, e = comparable(actual), comparable(expected)
    if a is None or e is None:
        return False
    if (
        attribute in MINIMUM_ATTRIBUTES
        and isinstance(a, Decimal)
        and isinstance(e, Decimal)
    ):
        return a >= e
    return a == e


def active_facts(state):
    aliases = state.get("aliases", {})
    facts = []
    for original in state.get("facts", []):
        if not original.get("active", True):
            continue
        fact = deepcopy(original)
        fact["device"] = canonical_device(fact["device"], aliases)
        facts.append(fact)
    return facts


def fact_groups(state):
    groups = {}
    for f in active_facts(state):
        groups.setdefault((f["device"], f["attribute"]), []).append(f)
    return groups


def latest_actual(facts, role="drawing"):
    candidates = [f for f in facts if f["role"] == role]
    if not candidates:
        return None, [], [], ""
    # Several currently-active documents can describe a device. Disagreement is
    # retained as a conflict, rather than being hidden behind arrival order.
    values = []
    for f in candidates:
        if not any(equivalent(f["value"], v) for v in values):
            values.append(f["value"])
    return (
        candidates[-1]["value"] if len(values) == 1 else None,
        sorted({i for f in candidates for i in f["source_ids"]}),
        values,
        candidates[-1].get("unit", ""),
    )


def build_ledger(state):
    rows = []
    accepted_documents = set(state.get("accepted_documents", []))
    for (device, attribute), facts in sorted(fact_groups(state).items()):
        requirements = [
            f
            for f in facts
            if f["role"] in PRECEDENCE
            and (
                f["role"] != "accepted_exception"
                or f["document_id"] in accepted_documents
            )
        ]
        if not requirements:
            continue
        priority = max(PRECEDENCE[f["role"]] for f in requirements)
        winning = [f for f in requirements if PRECEDENCE[f["role"]] == priority]
        values = []
        for f in winning:
            if not any(equivalent(f["value"], v) for v in values):
                values.append(f["value"])
        expected = values[0] if len(values) == 1 else None
        actual, actual_sources, actual_values, actual_unit = latest_actual(facts)
        unit = winning[0].get("unit", "")
        conflict = len(values) > 1 or len(actual_values) > 1
        if conflict:
            status = "conflict"
            detail = "Current evidence disagrees. Resolve the source conflict before accepting a value."
        elif priority == PRECEDENCE["drawing"]:
            status = "unknown"
            detail = "This drawing value has no PO, accepted exception, or specification requirement to check against."
        elif actual is None:
            status = "unknown"
            detail = "No current approval-drawing evidence confirms this requirement."
        elif unit and actual_unit and unit.casefold() != actual_unit.casefold():
            status = "unknown"
            detail = "The source units differ. A verified conversion is required."
        elif satisfies(attribute, actual, expected):
            status = "pass"
            detail = "Current drawing evidence satisfies the effective requirement."
        else:
            status = "fail"
            detail = "The current drawing does not satisfy the effective requirement."
        rows.append(
            {
                "id": digest([device, attribute])[:24],
                "device": device,
                "attribute": attribute,
                "label": label(attribute),
                "expected": expected,
                "actual": actual,
                "unit": unit,
                "status": status,
                "source_ids": sorted({i for f in winning for i in f["source_ids"]}),
                "actual_source_ids": actual_sources,
                "precedence": winning[0]["role"],
                "conflict_values": values
                if len(values) > 1
                else actual_values
                if len(actual_values) > 1
                else [],
                "detail": detail,
            }
        )
    return rows


def raw_checks(state, ledger):
    checks = []
    for row in ledger:
        item = {
            k: deepcopy(row[k])
            for k in (
                "device",
                "attribute",
                "expected",
                "actual",
                "unit",
                "status",
                "detail",
                "source_ids",
                "actual_source_ids",
            )
        }
        item.update(
            id="obligation:" + row["id"], title=row["device"] + " · " + row["label"]
        )
        item["fingerprint"] = digest(item)
        checks.append(item)
    documents = [d for d in state.get("documents", []) if d.get("active", True)]
    coverage = []
    if not documents:
        coverage.append(
            "Add the purchase order, specifications, and current approval drawings."
        )
    if not any(
        d.get("role") in ("purchase_order", "specification") and d.get("fact_count", 0)
        for d in documents
    ):
        coverage.append(
            "No readable contractual requirement facts have been extracted."
        )
    if not any(
        d.get("role") == "drawing" and d.get("fact_count", 0) for d in documents
    ):
        coverage.append(
            "No readable approval-drawing configuration has been extracted."
        )
    for d in documents:
        if d.get("state") in ("queued", "processing", "parsing"):
            coverage.append(d["name"] + " is still being read.")
        elif d.get("state") in ("failed", "unsupported", "error"):
            coverage.append(d["name"] + " could not be fully read.")
        elif d.get("role") not in ("markup",) and not d.get("fact_count", 0):
            coverage.append(
                d["name"]
                + " has no supported engineering facts. Confirm or supply readable evidence."
            )
    item = {
        "id": "coverage",
        "device": "ORDER",
        "attribute": "source_coverage",
        "title": "Source coverage",
        "status": "unknown" if coverage else "pass",
        "expected": "Readable current requirement and drawing evidence",
        "actual": "Incomplete" if coverage else "Complete",
        "unit": "",
        "detail": " ".join(coverage)
        if coverage
        else "All active files were read and supported facts were extracted.",
        "source_ids": [],
        "actual_source_ids": [],
    }
    # Bind coverage to document hashes and warnings, including otherwise empty files.
    item["fingerprint"] = digest(
        [
            item,
            [
                {k: d.get(k) for k in ("id", "sha256", "state", "warnings")}
                for d in documents
            ],
        ]
    )
    checks.append(item)
    for document in documents:
        if (
            not document.get("warnings")
            or not document.get("fact_count", 0)
            and document.get("role") != "markup"
        ):
            continue
        ids = sorted(
            {
                i
                for f in state.get("facts", [])
                if f["document_id"] == document["id"]
                for i in f["source_ids"]
            }
            | {
                i
                for c in state.get("comments", [])
                if c["document_id"] == document["id"]
                for i in c["source_ids"]
            }
        )
        item = {
            "id": "source-review:" + document["id"],
            "device": "ORDER",
            "attribute": "source_review",
            "title": "Review source regions · " + document["name"],
            "status": "unknown",
            "expected": "Named review of unresolved regions",
            "actual": "; ".join(str(w) for w in document["warnings"]),
            "unit": "",
            "detail": "Readable source content is available, but these regions need manual review. Open the original, record the exact review and disposition, then sign a finding-specific waiver if appropriate.",
            "source_ids": ids,
            "actual_source_ids": [],
        }
        item["fingerprint"] = digest(
            [item, document["sha256"], document.get("coverage")]
        )
        checks.append(item)
    for task in state.get("tasks", []):
        item = {
            "id": "propagation:" + task["id"],
            "device": task["device"],
            "attribute": task["attribute"],
            "title": task["title"],
            "status": "pass" if task["status"] == "verified" else "unknown",
            "expected": task["expected"],
            "actual": task.get("actual"),
            "unit": task.get("unit", ""),
            "detail": "Verified against current downstream evidence."
            if task["status"] == "verified"
            else "The downstream artifact must contain the accepted value before this task can close.",
            "source_ids": task.get("source_ids", []),
            "actual_source_ids": task.get("actual_source_ids", []),
        }
        item["fingerprint"] = digest(item)
        checks.append(item)
    for comment in state.get("comments", []):
        matching = next(
            (
                r
                for r in ledger
                if r["device"]
                == canonical_device(comment.get("device"), state.get("aliases", {}))
                and r["attribute"] == comment.get("attribute")
            ),
            None,
        )
        requested = comment.get("required_value")
        if not matching or requested is None:
            status, detail = (
                "unknown",
                "This review comment has no machine-verifiable engineering assertion. A named reviewer must resolve it or sign a finding-specific waiver.",
            )
        elif not equivalent(requested, matching["expected"]):
            status, detail = (
                "conflict",
                "The reviewer request conflicts with the effective contractual obligation. A response alone cannot change precedence.",
            )
        elif satisfies(comment["attribute"], matching["actual"], requested):
            status, detail = "pass", "The current drawing verifies the requested value."
        else:
            status, detail = (
                "fail",
                "The requested value is not verified in the current drawing. A comment saying fixed does not close this finding.",
            )
        item = {
            "id": "review:" + comment["id"],
            "device": comment.get("device") or "ORDER",
            "attribute": comment.get("attribute") or "review_comment",
            "title": "Comment "
            + comment["number"]
            + " · "
            + (
                label(comment["attribute"])
                if comment.get("attribute")
                else "Review needed"
            ),
            "status": status,
            "expected": requested,
            "actual": matching["actual"] if matching else None,
            "unit": matching["unit"] if matching else "",
            "detail": detail,
            "source_ids": comment["source_ids"]
            + (matching["source_ids"] if matching else []),
            "actual_source_ids": matching["actual_source_ids"] if matching else [],
        }
        item["fingerprint"] = digest(item)
        checks.append(item)
    # Basic arithmetic engineering invariants are deterministic and deliberately
    # narrow; these are not a substitute for licensed engineering approval.
    grouped = fact_groups(state)
    for device in sorted({k[0] for k in grouped}):
        for frame_attr, trip_attr in (
            ("main_breaker_frame_a", "main_breaker_trip_a"),
            ("feeder_breaker_frame_a", "feeder_breaker_trip_a"),
        ):
            frame, fs, _, _ = latest_actual(grouped.get((device, frame_attr), []))
            trip, ts, _, _ = latest_actual(grouped.get((device, trip_attr), []))
            if frame is None and trip is None:
                continue
            a, b = comparable(frame), comparable(trip)
            status = (
                "unknown"
                if not isinstance(a, Decimal) or not isinstance(b, Decimal)
                else "pass"
                if b <= a
                else "fail"
            )
            item = {
                "id": "rule:" + digest([device, frame_attr])[:24],
                "device": device,
                "attribute": trip_attr,
                "title": device + " · Trip must not exceed frame",
                "status": status,
                "expected": frame,
                "actual": trip,
                "unit": "A",
                "detail": "Breaker trip must be at or below the documented breaker frame rating.",
                "source_ids": fs,
                "actual_source_ids": ts,
            }
            item["fingerprint"] = digest(item)
            checks.append(item)
    return checks


def checks_for(s, order, state=None):
    state = state if state is not None else order.state
    ledger = build_ledger(state)
    checks = raw_checks(state, ledger)
    waivers = list(
        s.scalars(scoped(OrderWaiver).where(OrderWaiver.order_id == order.id))
    )
    for check in checks:
        waiver = next(
            (
                w
                for w in reversed(waivers)
                if w.check_id == check["id"] and w.fingerprint == check["fingerprint"]
            ),
            None,
        )
        if waiver and check["status"] != "pass" and check["id"] != "coverage":
            check["original_status"] = check["status"]
            check["status"] = "waived"
            check["waiver"] = {
                "signer": waiver.signer,
                "reason": waiver.reason,
                "at": waiver.created_at.isoformat(),
                "version": waiver.version,
            }
    return ledger, checks


def action_view(action):
    return {
        k: getattr(action, k)
        for k in (
            "id",
            "type",
            "title",
            "summary",
            "status",
            "base_version",
            "before",
            "after",
            "source_ids",
            "reason",
        )
    } | {"created_at": action.created_at.isoformat()}


def order_actions(s, order):
    return list(
        s.scalars(
            scoped(OrderAction)
            .where(OrderAction.order_id == order.id)
            .order_by(OrderAction.created_at)
        )
    )


def evidence_hash(state):
    return digest(
        {
            "facts": state.get("facts", []),
            "documents": state.get("documents", []),
            "aliases": state.get("aliases", {}),
            "accepted_documents": state.get("accepted_documents", []),
        }
    )


def commit(s, order, summary, kind="change", data=None, initial=False, actor=None):
    actor = actor or current_actor()
    if not initial:
        order.version += 1
    order.updated_at = now()
    ledger, checks = checks_for(s, order)
    actions = [action_view(a) for a in order_actions(s, order)]
    snapshot = {
        "state": deepcopy(order.state),
        "ledger": ledger,
        "checks": checks,
        "actions": actions,
        "version": order.version,
    }
    checksum = digest(snapshot)
    order.snapshot_hash = checksum
    blocked = any(c["status"] not in ("pass", "waived") for c in checks)
    pending = any(a["status"] == "pending" for a in actions) or any(
        t["status"] != "verified" for t in order.state.get("tasks", [])
    )
    unanswered = any(
        c["id"] not in order.state.get("responses", {})
        for c in order.state.get("comments", [])
    )
    order.status = "review" if blocked or pending or unanswered else "ready"
    if not order.state.get("documents"):
        order.status = "draft"
    s.add(
        OrderRevision(
            order_id=order.id,
            version=order.version,
            revision_label=revision_label(order.state, order.version),
            snapshot=snapshot,
            checksum=checksum,
            summary=summary,
            actor=actor,
        )
    )
    s.add(
        OrderEvent(
            order_id=order.id,
            version=order.version,
            summary=summary,
            kind=kind,
            actor=actor,
            data=data or {},
        )
    )
    s.flush()


def new_order(s, body):
    p, _ = new_project(
        s,
        {
            "title": body["title"],
            "customer": body["customer"],
            "category": body.get("category", "Low-voltage switchgear"),
            "due_date": None,
        },
    )
    p.synthetic = body.get("synthetic", False)
    order = Order(
        project_id=p.id,
        number=body.get("number") or "ORD-" + uid()[:8].upper(),
        state={
            "facts": [],
            "documents": [],
            "comments": [],
            "aliases": {},
            "accepted_documents": [],
            "configuration": {},
            "tasks": [],
            "responses": {},
            "source_signature": "",
        },
    )
    s.add(order)
    s.flush()
    commit(s, order, "Order opened", "create", initial=True)
    return order


def source_signature(documents, spans):
    return digest(
        [
            [
                d.id,
                d.sha256,
                d.state,
                d.meta,
                d.coverage,
                [
                    (sp.id, digest([sp.text, sp.location]))
                    for sp in spans
                    if sp.document_id == d.id
                ],
            ]
            for d in documents
        ]
    )


def synchronize_tasks(state):
    grouped = fact_groups(state)
    for task in state.get("tasks", []):
        value, ids, values, _ = latest_actual(
            grouped.get((task["device"], task["attribute"]), []), task["target"]
        )
        task["actual"] = value
        task["actual_source_ids"] = ids
        task["status"] = (
            "verified"
            if len(values) == 1 and equivalent(value, task["expected"])
            else "pending"
        )


def make_action(
    s, order, type, title, summary, after, source_ids, before=None, auto=False
):
    ids = valid_sources(
        s, order, source_ids, required=type in ("configuration", "alias")
    )
    validate_action(order, type, after, ids)
    fp = digest([type, after, ids, evidence_hash(order.state)])
    previous = s.scalar(
        scoped(OrderAction).where(
            OrderAction.order_id == order.id,
            OrderAction.fingerprint == fp,
            OrderAction.status.in_(["pending", "accepted", "rejected"]),
        )
    )
    if previous:
        return previous
    action = OrderAction(
        order_id=order.id,
        type=type,
        title=title[:240],
        summary=summary,
        before=before or {},
        after=deepcopy(after),
        source_ids=ids,
        base_version=order.version,
        evidence_hash=evidence_hash(order.state),
        fingerprint=fp,
    )
    s.add(action)
    s.flush()
    return action


def validate_action(order, type, after, ids):
    if type == "configuration":
        if "accept_exception_document" in after:
            document = next(
                (
                    d
                    for d in order.state.get("documents", [])
                    if d["id"] == after["accept_exception_document"]
                    and d["role"] == "accepted_exception"
                ),
                None,
            )
            if not document:
                fail("Select an accepted-exception document from this order.")
            facts = [
                f
                for f in order.state.get("facts", [])
                if f["document_id"] == document["id"]
            ]
            if not facts or not set(ids).intersection(
                i for f in facts for i in f["source_ids"]
            ):
                fail("The exception decision must cite its extracted evidence.")
            return
        needed = {"device", "attribute", "value"}
        if not needed <= after.keys():
            fail("A configuration proposal needs a device, attribute, and value.")
        evidence = [
            f
            for f in active_facts(order.state)
            if f["device"] == after["device"]
            and f["attribute"] == after["attribute"]
            and equivalent(f["value"], after["value"])
            and set(f["source_ids"]) & set(ids)
        ]
        if not evidence:
            fail(
                "This configuration value is not supported by the cited order evidence."
            )
    elif type == "alias":
        if not after.get("alias") or not after.get("device"):
            fail(
                "An alias needs the source identifier and canonical device identifier."
            )
        if after["alias"].strip().upper() == after["device"].strip().upper():
            fail("These identifiers are already the same.")
        proven = any(
            str(item["alias"]).strip().upper() == after["alias"].strip().upper()
            and str(item["device"]).strip().upper() == after["device"].strip().upper()
            and set(item["source_ids"]).intersection(ids)
            for item in order.state.get("proposed_aliases", [])
        )
        if not proven:
            fail(
                "This identifier mapping is not explicitly stated in the cited source. Add an authoritative alias declaration first."
            )
        known = {f["device"] for f in order.state.get("facts", [])}
        if after["device"].strip().upper() not in known:
            fail("The canonical device is not present in the extracted order facts.")
    elif type == "response":
        if not after.get("text", "").strip() or not after.get("comment_id"):
            fail("A response needs a current review comment and nonempty draft text.")
        if after["comment_id"] not in {
            c["id"] for c in order.state.get("comments", [])
        }:
            fail("The review comment is no longer current.")
    elif type in ("rfi", "task"):
        if not after.get("text", "").strip():
            fail("A draft needs nonempty text.")
    else:
        fail("This action type is not supported.")


def refresh(s, order, force=False, summary=None, event_kind="ingest", event_data=None):
    from .extraction import extract_order_document

    documents = list(
        s.scalars(
            scoped(Document)
            .where(Document.project_id == order.project_id)
            .order_by(Document.created_at)
        )
    )
    spans = (
        list(
            s.scalars(
                scoped(Span)
                .where(Span.document_id.in_([d.id for d in documents]))
                .order_by(Span.created_at)
            )
        )
        if documents
        else []
    )
    signature = source_signature(documents, spans)
    if signature == order.state.get("source_signature") and not force:
        return False
    state = deepcopy(order.state)
    facts, comments, records, proposed_aliases = [], [], [], []
    for document in documents:
        docspans = [sp for sp in spans if sp.document_id == document.id]
        if document.state in ("queued", "processing", "parsing", "failed", "error"):
            result = {
                "role": (document.meta or {}).get("order_role", "unknown"),
                "facts": [],
                "comments": [],
                "warnings": [],
            }
        else:
            result = extract_order_document(document, docspans)
        role = result.get("role", "unknown")
        record = {
            "id": document.id,
            "name": document.name,
            "kind": document.kind,
            "state": document.state,
            "role": role,
            "revision_label": result.get("revision_label", ""),
            "coverage": document.coverage,
            "warnings": result.get("warnings", []),
            "sha256": document.sha256,
            "family": document_family(document.name),
            "fact_count": len(result.get("facts", [])),
            "active": True,
        }
        records.append(record)
        allowed_ids = {sp.id for sp in docspans}
        for raw in result.get("facts", []):
            if not raw.get("source_ids") or not set(raw["source_ids"]) <= allowed_ids:
                continue
            f = {
                k: deepcopy(raw.get(k))
                for k in ("device", "attribute", "value", "unit", "source_ids")
            }
            f.update(
                id=digest([document.id, f])[:24],
                document_id=document.id,
                role=role,
                active=True,
            )
            f["device"] = str(f["device"] or "PACKAGE").upper().strip()
            f["unit"] = f["unit"] or ""
            facts.append(f)
        for raw in result.get("comments", []):
            if not raw.get("source_ids") or not set(raw["source_ids"]) <= allowed_ids:
                continue
            comments.append(
                {
                    "id": digest(
                        [document.id, str(raw.get("number")), raw.get("text")]
                    )[:24],
                    "number": str(raw.get("number", len(comments) + 1)),
                    "text": raw["text"],
                    "device": raw.get("device") or "",
                    "attribute": raw.get("attribute") or "",
                    "required_value": raw.get("required_value"),
                    "source_ids": raw["source_ids"],
                    "document_id": document.id,
                    "status": "open",
                    "response": "",
                }
            )
        proposed_aliases.extend(result.get("aliases", []))
    # A later revision of the same named drawing replaces that drawing fully;
    # missing values remain unknown instead of inheriting stale facts.
    families = {}
    for d in records:
        key = (d["role"], d["family"])
        if key in families and d["role"] in (
            "drawing",
            "bom",
            "supplier_po",
            "nameplate",
        ):
            families[key]["active"] = False
        families[key] = d
    active_ids = {d["id"] for d in records if d["active"]}
    for f in facts:
        f["active"] = f["document_id"] in active_ids
    comments = [c for c in comments if c["document_id"] in active_ids]
    state.update(
        facts=facts,
        documents=records,
        comments=comments,
        source_signature=signature,
        proposed_aliases=proposed_aliases,
    )
    synchronize_tasks(state)
    order.state = state
    current_hash = evidence_hash(state)
    for action in order_actions(s, order):
        if action.status == "pending" and action.evidence_hash != current_hash:
            action.status = "stale"
            action.reason = (
                "New evidence arrived. Review a proposal against the current sources."
            )
    s.flush()
    stage_identity_decisions(s, order)
    stage_findings(s, order)
    commit(
        s,
        order,
        summary or "Read current documents and reran order checks",
        event_kind,
        {"document_ids": [d.id for d in documents], **(event_data or {})},
    )
    return True


def stage_identity_decisions(s, order):
    state = order.state
    facts = state.get("facts", [])
    for doc in state.get("documents", []):
        if doc["role"] == "accepted_exception" and doc["id"] not in state.get(
            "accepted_documents", []
        ):
            ids = sorted(
                {
                    i
                    for f in facts
                    if f["document_id"] == doc["id"]
                    for i in f["source_ids"]
                }
            )
            if ids:
                make_action(
                    s,
                    order,
                    "configuration",
                    "Review accepted exceptions",
                    "Confirm the contractual authority of "
                    + doc["name"]
                    + ". A purchase order still takes precedence.",
                    {"accept_exception_document": doc["id"]},
                    ids,
                    {"accepted": False},
                )
    for alias in state.get("proposed_aliases", []):
        if (
            state.get("aliases", {}).get(str(alias["alias"]).upper())
            != str(alias["device"]).upper()
        ):
            try:
                make_action(
                    s,
                    order,
                    "alias",
                    "Connect " + alias["alias"] + " to " + alias["device"],
                    "Review the source identifiers before merging their evidence.",
                    {"alias": alias["alias"], "device": alias["device"]},
                    alias["source_ids"],
                )
            except Exception as exc:
                from fastapi import HTTPException

                if not isinstance(exc, HTTPException):
                    raise


def stage_findings(s, order):
    ledger = build_ledger(order.state)
    for row in ledger:
        if row["status"] == "fail":
            config_key = row["device"] + ":" + row["attribute"]
            accepted = order.state.get("configuration", {}).get(config_key)
            if accepted and equivalent(accepted["value"], row["expected"]):
                continue
            make_action(
                s,
                order,
                "configuration",
                "Update " + row["device"] + " · " + row["label"],
                row["detail"]
                + " Accepting opens evidence-verified propagation tasks; it does not edit drawings or authorize customer commitments.",
                {
                    "device": row["device"],
                    "attribute": row["attribute"],
                    "value": row["expected"],
                    "unit": row["unit"],
                },
                row["source_ids"],
                {"value": row["actual"], "source_ids": row["actual_source_ids"]},
            )


def classify_document(s, order, document_id, body):
    document = get(s, Document, document_id)
    if document.project_id != order.project_id:
        fail("This document belongs to another order.", 403)
    if document.state in ("queued", "processing", "parsing"):
        fail(
            "Wait until this document finishes reading before updating its details.",
            409,
        )
    # The project/order lock already serializes order decisions. Reading the
    # source state before locking avoids racing a worker's metadata write.
    document = get(s, Document, document_id, True)
    before = {
        "order_role": document.meta.get("order_role"),
        "revision_label": document.meta.get("revision_label"),
    }
    metadata = {**document.meta, "order_role": body["role"]}
    if "revision_label" in body:
        value = body.get("revision_label")
        if value:
            metadata["revision_label"] = value.strip()
        else:
            metadata.pop("revision_label", None)
    document.meta = metadata
    s.flush()
    refresh(
        s,
        order,
        summary="Updated source details: " + document.name,
        event_kind="classify",
        event_data={
            "document_id": document.id,
            "before": before,
            "after": {
                "order_role": metadata.get("order_role"),
                "revision_label": metadata.get("revision_label"),
            },
        },
    )


def refresh_project_orders(s, project_id):
    # Project-row lock is the common lock order for uploads, worker reads, and
    # order decisions. Call this inside the ingestion transaction.
    get(s, Project, project_id, True)
    for order in s.scalars(
        scoped(Order).where(Order.project_id == project_id).with_for_update()
    ):
        refresh(s, order)
        from backend.records.service import ensure

        ensure(s, order)


def accept_action(s, order, action_id, reason, edited_text=None):
    action = get(s, OrderAction, action_id, True)
    if action.order_id != order.id:
        fail("This action belongs to another order.", 403)
    if action.status != "pending":
        fail("This action is no longer pending. Review the current order.", 409)
    if action.evidence_hash != evidence_hash(order.state):
        fail(
            "The evidence behind this action changed. Rerun checks and review a current proposal.",
            409,
        )
    after = deepcopy(action.after)
    if edited_text is not None:
        if action.type not in ("response", "rfi", "task") or not edited_text.strip():
            fail("Only draft text can be edited during acceptance.")
        after["text"] = edited_text.strip()
    validate_action(order, action.type, after, action.source_ids)
    state = deepcopy(order.state)
    if action.type == "configuration" and "accept_exception_document" in after:
        state["accepted_documents"] = list(
            dict.fromkeys(
                [
                    *state.get("accepted_documents", []),
                    after["accept_exception_document"],
                ]
            )
        )
    elif action.type == "configuration":
        key = after["device"] + ":" + after["attribute"]
        state.setdefault("configuration", {})[key] = after | {
            "source_ids": action.source_ids,
            "decision_id": action.id,
        }
        # A replacement decision supersedes obsolete propagation obligations,
        # preserved in prior immutable revisions and decision events.
        state["tasks"] = [
            t
            for t in state.get("tasks", [])
            if not (
                t["device"] == after["device"] and t["attribute"] == after["attribute"]
            )
        ]
        for target in TARGETS:
            state["tasks"].append(
                {
                    "id": uid(),
                    "title": "Update "
                    + ROLE_LABELS[target].lower()
                    + " · "
                    + after["device"]
                    + " "
                    + label(after["attribute"]),
                    "target": target,
                    "device": after["device"],
                    "attribute": after["attribute"],
                    "expected": after["value"],
                    "actual": None,
                    "unit": after.get("unit", ""),
                    "status": "pending",
                    "source_ids": action.source_ids,
                    "actual_source_ids": [],
                    "decision_id": action.id,
                }
            )
    elif action.type == "alias":
        alias, device = after["alias"].strip().upper(), after["device"].strip().upper()
        if device in state.get("aliases", {}) or alias in set(
            state.get("aliases", {}).values()
        ):
            fail(
                "Alias chains are not supported. Link identifiers directly to the canonical device."
            )
        state.setdefault("aliases", {})[alias] = device
    elif action.type == "response":
        state.setdefault("responses", {})[after["comment_id"]] = {
            "text": after["text"],
            "source_ids": action.source_ids,
            "action_id": action.id,
            "accepted_at": now().isoformat(),
            "actor": current_actor(),
        }
    else:
        state.setdefault("drafts", []).append(
            {
                "id": action.id,
                "type": action.type,
                "title": action.title,
                "text": after["text"],
                "source_ids": action.source_ids,
                "status": "approved_draft",
            }
        )
    action.after = after
    action.status = "accepted"
    action.reason = reason
    synchronize_tasks(state)
    order.state = state
    if action.type == "alias" or "accept_exception_document" in after:
        for other in order_actions(s, order):
            if other.status == "pending":
                other.status = "stale"
                other.reason = "The effective obligation graph changed. Review the refreshed proposal."
        stage_identity_decisions(s, order)
        stage_findings(s, order)
    commit(
        s,
        order,
        "Accepted: " + action.title,
        "accept",
        {"action_id": action.id, "reason": reason, "edited": edited_text is not None},
    )


def reject_action(s, order, action_id, reason):
    action = get(s, OrderAction, action_id, True)
    if action.order_id != order.id:
        fail("This action belongs to another order.", 403)
    if action.status != "pending":
        fail("This action is no longer pending.", 409)
    action.status, action.reason = "rejected", reason
    commit(
        s,
        order,
        "Rejected: " + action.title,
        "reject",
        {"action_id": action.id, "reason": reason},
    )


def verify_task(s, order, task_id, source_ids):
    ids = valid_sources(s, order, source_ids)
    state = deepcopy(order.state)
    task = next((t for t in state.get("tasks", []) if t["id"] == task_id), None)
    if not task:
        fail("Propagation task not found.", 404)
    synchronize_tasks(state)
    if task["status"] != "verified":
        fail(
            "The current "
            + ROLE_LABELS[task["target"]].lower()
            + " still does not contain the accepted value. Upload the revised artifact; marking a task done is not evidence."
        )
    if ids and not set(ids).intersection(task["actual_source_ids"]):
        fail("The selected evidence does not verify this downstream value.")
    order.state = state
    commit(
        s,
        order,
        "Verified: " + task["title"],
        "verification",
        {"task_id": task_id, "source_ids": task["actual_source_ids"]},
    )


def waive(s, order, body):
    if auth_mode() == "clerk":
        body = {**body, "signer": current_actor()}
    _, checks = checks_for(s, order)
    check = next((c for c in checks if c["id"] == body["check_id"]), None)
    if not check or check["fingerprint"] != body["fingerprint"]:
        fail(
            "This finding changed. Review its current evidence before signing a waiver.",
            409,
        )
    if check["id"] == "coverage" or check["id"].startswith("propagation:"):
        fail(
            "Source coverage and propagation verification must be completed with actual evidence; they cannot be waived."
        )
    if check["status"] in ("pass", "waived"):
        fail("This check does not need a waiver.")
    s.add(
        OrderWaiver(
            order_id=order.id,
            check_id=check["id"],
            fingerprint=check["fingerprint"],
            signer=body["signer"],
            reason=body["reason"],
            version=order.version,
        )
    )
    s.flush()
    commit(
        s,
        order,
        "Signed waiver: " + check["title"],
        "waiver",
        {
            "fingerprint": check["fingerprint"],
            "signer": body["signer"],
            "reason": body["reason"],
        },
        actor=body["signer"],
    )


def release_state(s, order, checks, actions):
    blockers = []
    unresolved = [c for c in checks if c["status"] not in ("pass", "waived")]
    if unresolved:
        blockers.append(
            f"{len(unresolved)} checks still need evidence or a signed decision."
        )
    pending = [a for a in actions if a.status == "pending"]
    if pending:
        blockers.append(f"{len(pending)} proposed actions still need a decision.")
    tasks = [t for t in order.state.get("tasks", []) if t["status"] != "verified"]
    if tasks:
        blockers.append(f"{len(tasks)} downstream artifacts still need verification.")
    unanswered = [
        c
        for c in order.state.get("comments", [])
        if c["id"] not in order.state.get("responses", {})
    ]
    if unanswered:
        blockers.append(
            f"{len(unanswered)} review comments still need an approved response."
        )
    approval = s.scalar(
        scoped(OrderRelease)
        .where(
            OrderRelease.order_id == order.id,
            OrderRelease.version == order.version,
            OrderRelease.snapshot_hash == order.snapshot_hash,
        )
        .order_by(OrderRelease.created_at.desc())
    )
    return {
        "ready": not blockers,
        "blockers": blockers,
        "approval": {
            "signer": approval.signer,
            "reason": approval.reason,
            "version": approval.version,
            "snapshot_hash": approval.snapshot_hash,
            "at": approval.created_at.isoformat(),
        }
        if approval and not blockers
        else None,
    }


def release(s, order, body):
    if auth_mode() == "clerk":
        body = {**body, "signer": current_actor()}
    if order.snapshot_hash != body["snapshot_hash"]:
        fail(
            "The order snapshot changed. Review the exact current package before signing release.",
            409,
        )
    _, checks = checks_for(s, order)
    gate = release_state(s, order, checks, order_actions(s, order))
    if not gate["ready"]:
        fail(
            {
                "message": "Release is blocked until the order checks and decisions are complete.",
                "blockers": gate["blockers"],
            }
        )
    if gate["approval"]:
        fail("This exact revision has already been released.", 409)
    s.add(
        OrderRelease(
            order_id=order.id,
            version=order.version,
            snapshot_hash=order.snapshot_hash,
            signer=body["signer"],
            reason=body["reason"],
        )
    )
    order.status = "released"
    order.updated_at = now()
    s.add(
        OrderEvent(
            order_id=order.id,
            version=order.version,
            kind="release",
            summary="Released exact approval package "
            + revision_label(order.state, order.version),
            actor=body["signer"],
            data={"snapshot_hash": order.snapshot_hash, "reason": body["reason"]},
        )
    )
    s.flush()


def summary(s, order):
    p = get(s, Project, order.project_id)
    _, checks = checks_for(s, order)
    actions = order_actions(s, order)
    return {
        "id": order.id,
        "project_id": p.id,
        "title": p.title,
        "customer": p.customer,
        "number": order.number,
        "category": p.category,
        "version": order.version,
        "revision_label": revision_label(order.state, order.version),
        "status": order.status,
        "updated_at": order.updated_at.isoformat(),
        "synthetic": p.synthetic,
        "snapshot_hash": order.snapshot_hash,
        "counts": {
            "checks": len(checks),
            "passing": sum(c["status"] == "pass" for c in checks),
            "failing": sum(c["status"] in ("fail", "conflict") for c in checks),
            "unknown": sum(c["status"] == "unknown" for c in checks),
            "waived": sum(c["status"] == "waived" for c in checks),
            "decisions": sum(a.status == "pending" for a in actions),
            "tasks": sum(
                t["status"] != "verified" for t in order.state.get("tasks", [])
            ),
        },
    }


def workspace(s, order):
    ledger, checks = checks_for(s, order)
    actions = order_actions(s, order)
    sources = list(
        s.scalars(
            scoped(Span)
            .join(Document, Span.document_id == Document.id)
            .where(Document.project_id == order.project_id)
        )
    )
    comments = deepcopy(order.state.get("comments", []))
    for c in comments:
        response = order.state.get("responses", {}).get(c["id"])
        c["response"] = response["text"] if response else ""
        comment_check = next(
            (x for x in checks if x["id"] == "review:" + c["id"]), None
        )
        c["status"] = (
            "addressed"
            if response
            and comment_check
            and comment_check["status"] in ("pass", "waived")
            else "responded"
            if response
            else "open"
        )
    return {
        "order": summary(s, order),
        "documents": deepcopy(order.state.get("documents", [])),
        "sources": [
            {
                "id": sp.id,
                "document_id": sp.document_id,
                "text": sp.text,
                "location": sp.location,
            }
            for sp in sources
        ],
        "ledger": ledger,
        "checks": checks,
        "actions": [action_view(a) for a in reversed(actions)],
        "tasks": deepcopy(order.state.get("tasks", [])),
        "comments": comments,
        "drafts": deepcopy(order.state.get("drafts", [])),
        "history": [
            {
                "version": r.version,
                "revision_label": r.revision_label,
                "summary": r.summary,
                "actor": r.actor,
                "at": r.created_at.isoformat(),
                "checksum": r.checksum,
                "is_processing": any(
                    document.get("active", True)
                    and document.get("state") in ("queued", "processing", "parsing")
                    for document in r.snapshot.get("state", {}).get("documents", [])
                ),
            }
            for r in s.scalars(
                scoped(OrderRevision)
                .where(OrderRevision.order_id == order.id)
                .order_by(OrderRevision.version.desc())
            )
        ],
        "events": [
            {
                "id": e.id,
                "kind": e.kind,
                "summary": e.summary,
                "actor": e.actor,
                "at": e.created_at.isoformat(),
                "version": e.version,
                "data": e.data,
            }
            for e in s.scalars(
                scoped(OrderEvent)
                .where(OrderEvent.order_id == order.id)
                .order_by(OrderEvent.created_at.desc())
                .limit(150)
            )
        ],
        "release": release_state(s, order, checks, actions),
        "capabilities": {
            "ai_configured": configured(),
            "inbox_connected": False,
            "cad_supported": False,
            "mode": "local_replay",
            "pricing_rules_configured": False,
        },
    }


def get_revision(s, order, version):
    revision = s.scalar(
        scoped(OrderRevision).where(
            OrderRevision.order_id == order.id, OrderRevision.version == version
        )
    )
    if not revision:
        fail("Order revision not found.", 404)
    return revision


def diff(s, order, from_version, to_version=None):
    before, after = (
        get_revision(s, order, from_version),
        get_revision(s, order, to_version or order.version),
    )
    old = {(r["device"], r["attribute"]): r for r in before.snapshot["ledger"]}
    new = {(r["device"], r["attribute"]): r for r in after.snapshot["ledger"]}
    changes = []
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key, {}), new.get(key, {})
        if any(
            a.get(k) != b.get(k)
            for k in ("expected", "actual", "status", "source_ids", "actual_source_ids")
        ):
            changes.append(
                {
                    "device": key[0],
                    "attribute": key[1],
                    "before": a.get("actual"),
                    "after": b.get("actual"),
                    "expected_before": a.get("expected"),
                    "expected_after": b.get("expected"),
                    "status_before": a.get("status", "absent"),
                    "status_after": b.get("status", "absent"),
                    "unit": b.get("unit", a.get("unit", "")),
                    "source_ids": b.get("actual_source_ids", [])
                    + b.get("source_ids", []),
                    "before_source_ids": a.get("actual_source_ids", [])
                    + a.get("source_ids", []),
                }
            )
    return {
        "from_version": before.version,
        "to_version": after.version,
        "from_label": before.revision_label,
        "to_label": after.revision_label,
        "changes": changes,
        "checks_before": before.snapshot["checks"],
        "checks_after": after.snapshot["checks"],
    }
