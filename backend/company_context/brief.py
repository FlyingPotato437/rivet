"""Customer continuity at kickoff and review. Retrieval is not design validation."""

import re

from sqlalchemy import func

from backend.domain.service import digest, fail, get, scoped
from backend.storage.models import Document, Order, Project, Span, now

from . import customers, service
from .models import CustomerMemory, CustomerProfile, OrderCustomerBinding


def order_brief(s, order_id):
    order = get(s, Order, order_id)
    binding = customers.binding_view(s, order_id)
    result = {
        **binding,
        "preferences": [],
        "history": [],
        "withheld": [],
        "partial": False,
        "documents": [],
        "method": "approved_customer_history",
    }
    documents = list(
        s.scalars(
            scoped(Document)
            .where(
                Document.project_id == order.project_id,
            )
            .order_by(Document.created_at.desc(), Document.id)
            .limit(201)
        )
    )
    result["documents"] = [
        {
            "id": doc.id,
            "name": doc.name,
            "state": doc.state,
            "revision": doc.meta.get("revision_label", ""),
            "evidence_version": digest([doc.sha256, doc.meta, doc.coverage]),
        }
        for doc in documents[:200]
    ]
    result["partial"] = len(documents) > 200
    if not binding["customer"]:
        result["snapshot"] = digest(result)
        return result
    category = get(s, Project, order.project_id).category
    rows = list(
        s.scalars(
            scoped(CustomerMemory)
            .where(
                CustomerMemory.customer_id == binding["customer"]["id"],
                CustomerMemory.status != "revoked",
            )
            .order_by(CustomerMemory.created_at.desc(), CustomerMemory.id)
            .limit(201)
        )
    )
    result["partial"] |= len(rows) > 200
    # A declared conflict blocks either side, even if only one side names it.
    conflict_ids = {row.id for row in rows if row.conflicts_with}
    conflict_ids.update(other for row in rows for other in row.conflicts_with)
    for row in rows[:200]:
        if row.category and row.category != category:
            continue
        view = customers.note_view(s, row)
        reason = (
            "Needs approval"
            if row.status != "approved"
            else "Expired"
            if row.expires_at and row.expires_at <= now()
            else "Source or customer link changed"
            if not view["evidence_current"]
            else "Conflicting customer context"
            if row.id in conflict_ids
            else ""
        )
        if reason:
            result["withheld"].append(
                {"id": row.id, "title": row.title, "reason": reason}
            )
        elif row.applicability == "standing_preference":
            result["preferences"].append(view)
        else:
            result["history"].append(view)
    result["snapshot"] = digest(result)
    return result


def _excerpt(text, terms, size=600):
    lower = text.lower()
    positions = [lower.find(term) for term in terms if term in lower]
    start = max(0, min(positions, default=0) - 100)
    return (
        ("…" if start else "")
        + text[start : start + size]
        + ("…" if len(text) > start + size else "")
    )


def search_history(s, customer_id, query):
    customer = get(s, CustomerProfile, customer_id)
    # Preserve drawing/equipment identifiers such as E-10 and CB-12. The broader
    # context retriever drops their short pieces, which is unsuitable for lookup.
    terms = sorted(
        {
            word
            for word in re.findall(r"[a-z0-9]+(?:[-._/][a-z0-9]+)*", query.lower())
            if len(word) >= 3 and word not in service.STOP_WORDS
        }
    )[:12]
    if not terms:
        fail("Enter a drawing number, equipment tag, or a few specific words.")
    # Every joined entity is explicitly tenant scoped. An identical customer name
    # elsewhere is never enough to include its sources.
    org = customer.organization_id
    base = (
        scoped(Span)
        .join(Document, Span.document_id == Document.id)
        .join(
            Order,
            Document.project_id == Order.project_id,
        )
        .join(OrderCustomerBinding, OrderCustomerBinding.order_id == Order.id)
        .where(
            Document.organization_id == org,
            Order.organization_id == org,
            OrderCustomerBinding.organization_id == org,
            OrderCustomerBinding.customer_id == customer_id,
        )
    )
    # AND tokens yields a precise, explainable search. Escape identifier characters
    # which also have meaning in SQL LIKE patterns.
    for term in terms:
        base = base.where(func.lower(Span.text).contains(term, autoescape=True))
    rows = list(
        s.execute(
            base.with_only_columns(Span, Document, Order)
            .order_by(
                Document.created_at.desc(),
                Span.id,
            )
            .limit(31)
        )
    )
    return {
        "query": query,
        "method": "source_text_search",
        "has_more": len(rows) > 30,
        "customer": {"id": customer.id, "name": customer.name},
        "results": [
            {
                "source_id": sp.id,
                "order_id": order.id,
                "order_number": order.number,
                "document_id": doc.id,
                "filename": doc.name,
                "revision": doc.meta.get("revision_label", ""),
                "location": sp.location,
                "excerpt": _excerpt(sp.text, terms),
                "source_url": f"#order/{order.id}?tab=work&source={sp.id}",
                "use": "Historical source. Confirm the order and revision before relying on it.",
            }
            for sp, doc, order in rows[:30]
        ],
    }


def check_documents(s, order_id, body):
    order = get(s, Order, order_id)
    brief = order_brief(s, order_id)
    if not brief["customer"]:
        fail("Link this order to a customer before checking their preferences.", 409)
    ids = list(dict.fromkeys(body.document_ids))
    docs = [get(s, Document, id) for id in ids]
    if any(doc.project_id != order.project_id for doc in docs):
        fail("Choose documents from this order only.", 404)
    spans = list(
        s.scalars(
            scoped(Span)
            .where(Span.document_id.in_(ids))
            .order_by(
                Span.document_id,
                Span.id,
            )
            .limit(2001)
        )
    )
    incomplete = len(spans) > 2000 or any(doc.state != "ready" for doc in docs)
    text_ids = {sp.document_id for sp in spans if sp.text.strip()}
    incomplete |= any(doc.id not in text_ids for doc in docs)
    names = {doc.id: doc.name for doc in docs}
    checks = []
    for note in brief["preferences"]:
        # Prefer curator-assigned topics over long prose to avoid matching boilerplate.
        terms = service._tokens(" ".join(note["topics"]) or note["title"])
        ranked = sorted(
            ((len(terms & service._tokens(sp.text)), sp) for sp in spans[:2000]),
            key=lambda pair: (-pair[0], pair[1].id),
        )
        hits = [sp for score, sp in ranked if score >= min(2, max(1, len(terms)))][:3]
        checks.append(
            {
                "note": note,
                "status": "passages_found" if hits else "not_found_in_text",
                "matches": [
                    {
                        "source_id": sp.id,
                        "filename": names[sp.document_id],
                        "location": sp.location,
                        "excerpt": _excerpt(sp.text, terms),
                        "source_url": f"#order/{order.id}?tab=work&source={sp.id}",
                    }
                    for sp in hits
                ],
            }
        )
    return {
        "method": "topic_text_check",
        "order_id": order_id,
        "checked_at": now().isoformat(),
        "checks": checks,
        "document_ids": ids,
        "partial": incomplete or brief["partial"],
        "withheld_count": len(brief["withheld"]),
        "brief_snapshot": brief["snapshot"],
        "snapshot": digest(
            [
                brief["snapshot"],
                [(d.id, d.sha256, d.meta) for d in docs],
                [(sp.id, sp.text, sp.location) for sp in spans],
            ]
        ),
        "notice": "Text matches help locate a note; they do not confirm its meaning or drawing compliance. Inspect the selected revision before sending. Scanned handwriting and visual-only details can be missed.",
    }
