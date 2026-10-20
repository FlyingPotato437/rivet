"""Explicit customer identity and source-backed historical memory.

Names never resolve identity. Background work quotes reviewed history into candidates;
only a human approval makes a candidate available to the next relevant generation.
"""

from sqlalchemy import func

from backend.domain.service import digest, fail, get, scoped
from backend.identity import current_actor
from backend.storage.models import Document, Order, OrderRecord, Project, Span, now

from . import service as company
from .models import (
    CompanyContextEvent,
    CustomerMemory,
    CustomerProfile,
    OrderCustomerBinding,
)
from .schemas import CustomerNoteWrite

GUARDRAILS = (
    "Customer memory is historical guidance from an explicitly linked customer, not a "
    "current project specification, engineering solution, authorization, or approval. "
    "Check each applicability condition and the current order's evidence. Quote its "
    "original order/revision when explaining it. Current requirements take precedence; "
    "conflicting or unclear preferences need a human decision, never silent selection. "
    "Standing preferences are human-approved guidance, not automatic technical commitments."
)


def _audit(row, action, reason, details=None):
    kind = (
        "customer_note"
        if isinstance(row, CustomerMemory)
        else "customer_binding"
        if isinstance(row, OrderCustomerBinding)
        else "customer"
    )
    return CompanyContextEvent(
        subject_type=kind,
        subject_id=row.id,
        action=action,
        actor=current_actor(),
        reason=reason,
        details=details or {},
    )


def _binding(s, order_id):
    return s.scalar(
        scoped(OrderCustomerBinding).where(OrderCustomerBinding.order_id == order_id)
    )


def profile_view(s, row):
    counts = dict(
        s.execute(
            scoped(CustomerMemory)
            .with_only_columns(CustomerMemory.status, func.count(CustomerMemory.id))
            .where(CustomerMemory.customer_id == row.id)
            .group_by(CustomerMemory.status)
        ).all()
    )
    return {
        "id": row.id,
        "key": row.key,
        "name": row.name,
        "description": row.description,
        "created_by": row.created_by,
        "order_count": s.scalar(
            scoped(OrderCustomerBinding)
            .with_only_columns(func.count(OrderCustomerBinding.id))
            .where(OrderCustomerBinding.customer_id == row.id)
        ),
        "candidate_count": counts.get("candidate", 0),
        "approved_count": counts.get("approved", 0),
        "count_note": "Approval counts include expired or changed evidence; inspect each note before reuse.",
    }


def list_customers(s):
    rows = list(
        s.scalars(
            scoped(CustomerProfile)
            .order_by(CustomerProfile.name, CustomerProfile.id)
            .limit(201)
        )
    )
    return {
        "customers": [profile_view(s, row) for row in rows[:200]],
        "has_more": len(rows) > 200,
    }


def create_customer(s, body):
    company.require_writer()
    company._lock(s, "customer-key:" + body.key)
    if s.scalar(scoped(CustomerProfile).where(CustomerProfile.key == body.key)):
        fail(
            "This customer code is already used. Select that customer or choose another code.",
            409,
        )
    row = CustomerProfile(
        key=body.key,
        name=body.name.strip(),
        description=body.description.strip(),
        created_by=current_actor(),
    )
    if len(row.name) < 2:
        fail("Enter the customer's name.")
    s.add(row)
    s.flush()
    s.add(
        _audit(
            row, "created", "Explicit customer profile created; no name-based matching."
        )
    )
    return profile_view(s, row)


def binding_view(s, order_id):
    get(s, Order, order_id)
    row = _binding(s, order_id)
    customer = (
        get(s, CustomerProfile, row.customer_id) if row and row.customer_id else None
    )
    return {
        "order_id": order_id,
        "version": row.version if row else 0,
        "customer": profile_view(s, customer) if customer else None,
        "confirmed_by": row.confirmed_by if row else "",
        "confirmed_at": row.confirmed_at.isoformat() if row else None,
    }


def bind_order(s, order_id, body):
    company.require_writer()
    order = get(s, Order, order_id, True)
    customer = get(s, CustomerProfile, body.customer_id) if body.customer_id else None
    row = _binding(s, order.id)
    if body.expected_version != (row.version if row else 0):
        fail("The customer link changed. Refresh before confirming it.", 409)
    previous = row.customer_id if row else None
    if row is None:
        row = OrderCustomerBinding(order_id=order.id, version=0)
        s.add(row)
    row.customer_id = customer.id if customer else None
    row.version += 1
    row.confirmed_by, row.confirmed_at, row.reason = current_actor(), now(), body.reason
    s.flush()
    s.add(
        _audit(
            row,
            "customer_confirmed" if customer else "customer_unlinked",
            body.reason,
            {
                "previous_customer_id": previous,
                "customer_id": row.customer_id,
                "version": row.version,
            },
        )
    )
    # Immediately prepare historical candidates; approval is a separate operation.
    if customer:
        refresh_order_customer_candidates(s, order)
    return binding_view(s, order.id)


def _comment_fingerprint(comment):
    return digest(
        {
            key: comment.get(key)
            for key in (
                "id",
                "text",
                "original_text",
                "response",
                "responder",
                "reviewed",
                "status",
                "revision",
                "source_ids",
            )
        }
    )


def _comment(s, order_id, comment_id):
    record = s.scalar(scoped(OrderRecord).where(OrderRecord.order_id == order_id))
    return next(
        (
            c
            for c in (record.data.get("comments", []) if record else [])
            if c["id"] == comment_id
        ),
        None,
    )


def fresh(s, row):
    if row.expires_at and row.expires_at <= now():
        return False
    if row.provenance.get("kind") == "lifecycle_decision_candidate":
        from backend.lifecycle.feedback import candidate_current

        if not candidate_current(s, row):
            return False
    order = s.scalar(scoped(Order).where(Order.id == row.source_order_id))
    binding = _binding(s, row.source_order_id)
    if not order or not binding or binding.customer_id != row.customer_id:
        return False
    if row.provenance.get("binding_version") != binding.version:
        return False
    for evidence in row.provenance.get("evidence", []):
        source = s.scalar(scoped(Span).where(Span.id == evidence["source_id"]))
        doc = s.scalar(scoped(Document).where(Document.id == evidence["document_id"]))
        if (
            not source
            or not doc
            or source.document_id != doc.id
            or doc.project_id != order.project_id
        ):
            return False
        if (
            doc.sha256 != evidence["document_sha256"]
            or digest([source.text, source.location]) != evidence["source_hash"]
        ):
            return False
    if not row.provenance.get("evidence"):
        return False
    if row.provenance.get("comment_id"):
        comment = _comment(s, order.id, row.provenance["comment_id"])
        if not comment or _comment_fingerprint(comment) != row.provenance.get(
            "comment_hash"
        ):
            return False
    # New documents do not erase history. Fingerprints prove historical source
    # integrity; they deliberately do not claim the old specification is current.
    return True


def note_view(s, row):
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "key": row.key,
        "version": row.version,
        "title": row.title,
        "text": row.text,
        "kind": row.kind,
        "topics": row.topics,
        "category": row.category,
        "applicability": row.applicability,
        "applicability_note": row.applicability_note,
        "source_order_id": row.source_order_id,
        "provenance": row.provenance,
        "conflicts_with": row.conflicts_with,
        "status": row.status,
        "author": row.author,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_reason": row.review_reason,
        "evidence_current": fresh(s, row),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "source_url": f"#order/{row.source_order_id}?tab=work&source={row.provenance['evidence'][0]['source_id']}",
    }


def _last(s, customer_id, key):
    return s.scalar(
        scoped(CustomerMemory)
        .where(CustomerMemory.customer_id == customer_id, CustomerMemory.key == key)
        .order_by(CustomerMemory.version.desc())
        .limit(1)
    )


def _create_note(s, customer_id, body, extra_provenance=None):
    get(s, CustomerProfile, customer_id)
    company._lock(s, "customer:" + customer_id)
    previous = _last(s, customer_id, body.key)
    if body.expected_version != (previous.version if previous else 0):
        fail("This customer note has a newer version. Refresh before editing.", 409)
    order = get(s, Order, body.source_order_id)
    binding = _binding(s, order.id)
    if not binding or binding.customer_id != customer_id:
        fail(
            "Confirm the source order belongs to this customer before proposing a note.",
            409,
        )
    evidence = company._source_snapshot(s, order, body.source_ids)
    if not evidence:
        fail("A customer note needs an actual source location.")
    for item in evidence:
        doc, source = (
            get(s, Document, item["document_id"]),
            get(s, Span, item["source_id"]),
        )
        item.update(
            filename=doc.name,
            revision=doc.meta.get("revision_label", ""),
            location=source.location,
        )
    for conflict_id in set(body.conflicts_with):
        other = get(s, CustomerMemory, conflict_id)
        if other.customer_id != customer_id:
            fail("Conflicting notes must belong to the same customer.", 422)
    if body.expires_at and body.expires_at <= now():
        fail("Choose a future expiration date.")
    if len(body.title.strip()) < 2 or len(body.text.strip()) < 3:
        fail("Enter a descriptive title and a sourced customer note.")
    row = CustomerMemory(
        customer_id=customer_id,
        key=body.key,
        version=body.expected_version + 1,
        title=body.title.strip(),
        text=body.text.strip(),
        kind=body.kind,
        topics=company.normalize_topics(body.topics),
        category=body.category.strip(),
        applicability=body.applicability,
        applicability_note=body.applicability_note,
        source_order_id=order.id,
        conflicts_with=sorted(set(body.conflicts_with)),
        expires_at=body.expires_at,
        author=current_actor(),
        status="candidate",
        provenance={
            "kind": "human_proposed",
            "note": body.provenance_note,
            "source_order_number": order.number,
            "binding_version": binding.version,
            "evidence": evidence,
            **(extra_provenance or {}),
        },
    )
    s.add(row)
    s.flush()
    s.add(_audit(row, "candidate_created", body.provenance_note))
    return row


def create_note(s, customer_id, body):
    company.require_writer()
    return note_view(s, _create_note(s, customer_id, body))


def review_note(s, note_id, body, approve):
    company.require_admin()
    row = get(s, CustomerMemory, note_id)
    company._lock(s, "customer:" + row.customer_id)
    s.refresh(row)
    latest = _last(s, row.customer_id, row.key)
    if body.expected_version != row.version or (approve and latest.id != row.id):
        fail("This note has a newer version. Review the latest candidate.", 409)
    if approve:
        if row.status != "candidate":
            fail(
                "Only a candidate can be approved. Propose a new version for changes.",
                409,
            )
        if not fresh(s, row):
            fail(
                "The note's source or confirmed customer changed. Propose a fresh candidate.",
                409,
            )
        for previous in s.scalars(
            scoped(CustomerMemory).where(
                CustomerMemory.customer_id == row.customer_id,
                CustomerMemory.key == row.key,
                CustomerMemory.status == "approved",
            )
        ):
            previous.status = "revoked"
            previous.review_reason = "Replaced by explicitly approved version " + str(
                row.version
            )
            s.add(
                _audit(previous, "superseded", body.reason, {"replacement_id": row.id})
            )
    row.status = "approved" if approve else "revoked"
    row.reviewed_by, row.reviewed_at, row.review_reason = (
        current_actor(),
        now(),
        body.reason,
    )
    s.add(_audit(row, row.status, body.reason))
    s.flush()
    return note_view(s, row)


def approval_pointers(s, order_id):
    record = s.scalar(scoped(OrderRecord).where(OrderRecord.order_id == order_id))
    return [
        {key: approval.get(key) for key in ("id", "label", "actor", "at", "version")}
        for approval in (record.data.get("approvals", []) if record else [])[-3:]
    ]


def customer_detail(s, customer_id):
    customer = get(s, CustomerProfile, customer_id)
    orders = []
    for binding in s.scalars(
        scoped(OrderCustomerBinding)
        .where(OrderCustomerBinding.customer_id == customer.id)
        .order_by(OrderCustomerBinding.confirmed_at.desc())
        .limit(200)
    ):
        order = get(s, Order, binding.order_id)
        project = get(s, Project, order.project_id)
        orders.append(
            {
                "order_id": order.id,
                "number": order.number,
                "title": project.title,
                "category": project.category,
                "binding_version": binding.version,
                "approvals": approval_pointers(s, order.id),
            }
        )
    notes = list(
        s.scalars(
            scoped(CustomerMemory)
            .where(CustomerMemory.customer_id == customer.id)
            .order_by(CustomerMemory.created_at.desc(), CustomerMemory.id)
            .limit(201)
        )
    )
    return {
        "customer": profile_view(s, customer),
        "orders": orders,
        "notes": [note_view(s, row) for row in notes[:200]],
        "has_more_notes": len(notes) > 200,
        "guardrails": GUARDRAILS,
    }


def _topics(text):
    words = company._tokens(text)
    concepts = {
        "cable-entry": {"cable", "entry"},
        "enclosure-finish": {"paint", "finish", "coating", "ral"},
        "drawing-dimensions": {
            "dimension",
            "dimensions",
            "height",
            "width",
            "clearance",
        },
        "identification": {"label", "labels", "nameplate", "marking"},
        "accessories": {"accessory", "accessories", "spare", "spares"},
        "documentation": {"manual", "submittal", "certificate", "certificates"},
        "delivery": {"delivery", "shipping", "packing"},
    }
    return [topic for topic, terms in concepts.items() if words & terms] or [
        "reviewed-comment"
    ]


def refresh_order_customer_candidates(s, order, *, limit=50):
    """Trusted hook after a human review or explicit customer binding, in its transaction.

    The exact reviewed comment and response are copied, never model-inferred. Revoked
    keys remain suppressed. An edited comment creates a new candidate version; stale
    approval never propagates to it. Reads are capped and report incomplete coverage.
    """
    order = company._order(s, order)
    binding = _binding(s, order.id)
    counts = {"created": 0, "unchanged": 0, "skipped": 0, "notes": [], "partial": False}
    if not binding or not binding.customer_id:
        return counts
    get(s, CustomerProfile, binding.customer_id)
    company._lock(s, "customer:" + binding.customer_id)
    record = s.scalar(scoped(OrderRecord).where(OrderRecord.order_id == order.id))
    comments = record.data.get("comments", []) if record else []
    counts["partial"] = len(comments) > 200
    category = get(s, Project, order.project_id).category
    for comment in comments[:200]:
        if counts["created"] >= max(0, min(limit, 50)):
            counts["partial"] = True
            break
        if not (
            comment.get("reviewed")
            and str(comment.get("response", "")).strip()
            and str(comment.get("responder", "")).strip()
            and comment.get("source_ids")
        ):
            counts["skipped"] += 1
            continue
        key = "comment-" + digest([order.id, comment["id"]])[:32]
        previous = _last(s, binding.customer_id, key)
        fingerprint = _comment_fingerprint(comment)
        if previous and (
            previous.status == "revoked"
            or (
                previous.provenance.get("comment_hash") == fingerprint
                and fresh(s, previous)
            )
        ):
            counts["unchanged"] += 1
            continue
        text = (
            "Reviewed customer comment:\n"
            + str(comment.get("text", ""))
            + "\n\nRecorded team response:\n"
            + comment["response"]
        )
        source_ids = sorted(set(comment["source_ids"]))
        # Fail closed for stale/misassigned source metadata; do not break a valid PM save.
        sources = list(s.scalars(scoped(Span).where(Span.id.in_(source_ids))))
        docs = {
            d.id
            for d in s.scalars(
                scoped(Document).where(Document.project_id == order.project_id)
            )
        }
        if (
            len(text) > 6000
            or len(source_ids) > 20
            or len(sources) != len(source_ids)
            or any(sp.document_id not in docs for sp in sources)
        ):
            counts["skipped"] += 1
            continue
        body = CustomerNoteWrite(
            key=key,
            expected_version=previous.version if previous else 0,
            title=(f"Reviewed comment {comment.get('number', '')} · {order.number}")[
                :180
            ],
            text=text,
            kind="requirement",
            topics=_topics(text),
            category=category,
            applicability="historical_reference",
            applicability_note="Historical request on this order only. Confirm relevance and current specifications before using it on another order.",
            source_order_id=order.id,
            source_ids=source_ids,
            provenance_note="Prepared from a human-reviewed comment and its recorded team response. Not yet approved for customer memory.",
        )
        row = _create_note(
            s,
            binding.customer_id,
            body,
            {
                "kind": "reviewed_comment_candidate",
                "comment_id": comment["id"],
                "comment_hash": fingerprint,
                "responder": comment["responder"],
                "source_revision": comment.get("revision", ""),
            },
        )
        counts["created"] += 1
        counts["notes"].append(note_view(s, row))
    return counts


def refresh_customer_candidates(s, customer_id):
    company.require_writer()
    get(s, CustomerProfile, customer_id)
    bindings = list(
        s.scalars(
            scoped(OrderCustomerBinding)
            .where(OrderCustomerBinding.customer_id == customer_id)
            .order_by(OrderCustomerBinding.order_id)
            .limit(101)
        )
    )
    result = {
        "created": 0,
        "unchanged": 0,
        "skipped": 0,
        "notes": [],
        "partial": len(bindings) > 100,
    }
    for binding in bindings[:100]:
        if result["created"] >= 50:
            result["partial"] = True
            break
        counts = refresh_order_customer_candidates(
            s, binding.order_id, limit=50 - result["created"]
        )
        for key in ("created", "unchanged", "skipped"):
            result[key] += counts[key]
        result["notes"].extend(counts["notes"])
        result["partial"] |= counts["partial"]
    return result


def retrieve(s, order, query, topics=(), *, exclude_order_ids=()):
    """All historical input honors tenant, customer binding, review and holdout filters."""
    binding = _binding(s, order.id)
    if not binding or not binding.customer_id:
        return {
            "customer": None,
            "notes": [],
            "conflicts": [],
            "guardrails": GUARDRAILS,
        }
    customer = get(s, CustomerProfile, binding.customer_id)
    category = get(s, Project, order.project_id).category
    query_tokens, topic_set = (
        company._tokens(query),
        set(company.normalize_topics(topics)),
    )
    eligible, selected = {}, set()
    excluded_notes = (
        set(
            s.scalars(
                scoped(CustomerMemory)
                .with_only_columns(CustomerMemory.id)
                .where(
                    CustomerMemory.customer_id == customer.id,
                    CustomerMemory.source_order_id.in_(exclude_order_ids),
                )
            )
        )
        if exclude_order_ids
        else set()
    )
    for row in s.scalars(
        scoped(CustomerMemory)
        .where(
            CustomerMemory.customer_id == customer.id,
            CustomerMemory.status == "approved",
        )
        .order_by(CustomerMemory.key, CustomerMemory.version)
    ):
        if (
            row.source_order_id in exclude_order_ids
            or bool(set(row.conflicts_with) & excluded_notes)
            or (row.category and row.category != category)
            or not fresh(s, row)
        ):
            continue
        eligible[row.id] = row
        relevant = (
            bool(set(row.topics) & topic_set)
            or len(query_tokens & company._tokens(" ".join(row.topics))) >= 1
        )
        relevant |= len(query_tokens & company._tokens(row.title + " " + row.text)) >= 2
        if relevant:
            selected.add(row.id)
    # Include both sides of a declared conflict, including the less lexically
    # similar side. Never resolve it by relevance ranking or latest upload.
    changed = True
    while changed:
        changed = False
        for row in eligible.values():
            linked = set(row.conflicts_with) & set(eligible)
            if row.id in selected or linked & selected:
                before = len(selected)
                selected |= linked | ({row.id} if linked else set())
                changed |= len(selected) != before
    notes, conflicts = [], []
    for row in eligible.values():
        if row.id not in selected:
            continue
        notes.append(
            {
                **note_view(s, row),
                "use": "Historical guidance; verify applicability against the current order.",
                "source_approvals": approval_pointers(s, row.source_order_id),
            }
        )
        for other in row.conflicts_with:
            conflicts.append(
                {
                    "note_id": row.id,
                    "other_note_id": other,
                    "other_available": other in selected,
                    "status": "requires_human_resolution",
                }
            )
    return {
        "customer": {
            "id": customer.id,
            "key": customer.key,
            "name": customer.name,
            "binding_version": binding.version,
            "confirmed_by": binding.confirmed_by,
        },
        "notes": notes,
        "conflicts": conflicts,
        "guardrails": GUARDRAILS,
        "current_order_approvals": []
        if order.id in exclude_order_ids
        else approval_pointers(s, order.id),
    }
