import hashlib, json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select
from backend.storage.db import ORG, ACTOR
from backend.storage.models import (
    Project,
    Quote,
    Line,
    Version,
    Event,
    Approval,
    Proposal,
    Document,
    Requirement,
    Span,
    Catalog,
    Offer,
    Idempotency,
    uid,
)
from .pricing import dec, money, extended, gross_margin, markup
from .schemas import Operation, LineInput


def fail(detail, status=422):
    raise HTTPException(status, detail)


def digest(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


def scoped(cls):
    return select(cls).where(cls.organization_id == ORG)


def get(s, cls, id, lock=False):
    q = scoped(cls).where(cls.id == id)
    row = s.scalar(q.with_for_update() if lock else q)
    if row is None:
        fail("Record not found.", 404)
    return row


def line_dict(l):
    return {
        "id": l.id,
        "tag": l.tag,
        "description": l.description,
        "quantity": format(l.quantity.normalize(), "f"),
        "unit": l.unit,
        "model": l.model,
        "cost": str(l.cost) if l.cost is not None else None,
        "price": str(l.price) if l.price is not None else None,
        "extended": str(extended(l)) if l.price is not None else None,
        "lead_time": l.lead_time,
        "review": l.review,
        "meta": l.meta,
        "position": l.position,
    }


def lines(s, q):
    return list(
        s.scalars(scoped(Line).where(Line.quote_id == q.id).order_by(Line.position))
    )


def docs(s, p):
    return list(
        s.scalars(
            scoped(Document)
            .where(Document.project_id == p.id)
            .order_by(Document.created_at)
        )
    )


def snapshot(s, q, p):
    return {
        "id": q.id,
        "number": q.number,
        "version": q.version,
        "input_revision": p.input_revision,
        "currency": q.currency,
        "terms": q.terms,
        "notes": q.notes,
        "project": p.title,
        "customer": p.customer,
        "synthetic": p.synthetic,
        "lines": [line_dict(l) for l in lines(s, q)],
    }


def save_version(s, q, p, summary, actor=ACTOR):
    s.flush()
    data = snapshot(s, q, p)
    s.add(
        Version(
            quote_id=q.id,
            version=q.version,
            input_revision=p.input_revision,
            snapshot=data,
            checksum=digest(data),
            summary=summary,
            actor=actor,
        )
    )
    s.add(
        Event(
            project_id=p.id,
            summary=summary,
            actor=actor,
            meta={"quote_version": q.version},
        )
    )


def invalidate(s, q):
    q.status = "draft"
    for pr in s.scalars(
        scoped(Proposal).where(Proposal.quote_id == q.id, Proposal.status == "pending")
    ):
        pr.status = "stale"


def lock_quote(s, id):
    initial = get(s, Quote, id)
    p = get(s, Project, initial.project_id, True)
    q = get(s, Quote, id, True)
    s.refresh(q)
    return q, p


def check_version(q, p, v, i):
    if q.version != v or p.input_revision != i:
        fail(
            "This quote or its sources changed. Refresh and review the latest revision.",
            409,
        )


def checks(s, q, p):
    result = []
    ls = lines(s, q)

    def add(code, message, line_id=None, severity="blocker"):
        result.append(
            {
                "code": code,
                "message": message,
                "line_id": line_id,
                "severity": severity,
                "result": "unknown"
                if code in ["missing_cost", "missing_price", "coverage", "review"]
                else "fail",
            }
        )

    if not ls:
        add("empty", "Add at least one line to this quote.")
    if not q.terms.strip():
        add("terms", "Add customer-facing terms and exclusions before approval.")
    if q.reconciled_input != p.input_revision:
        add(
            "reconcile",
            "Review new source documents and reconcile the quote with this input revision.",
        )
    for d in docs(s, p):
        if d.state not in ("ready", "mapped"):
            add("coverage", f"{d.name}: {d.state.replace('_', ' ')}.")
        if any(
            c.get("state") in ("scanned", "unsupported", "failed") for c in d.coverage
        ) and not d.meta.get("reviewed_gap"):
            add(
                "coverage",
                f"{d.name} has unreadable or unsupported sections. Transcribe or record a reviewed exception.",
            )
    for l in ls:
        if l.cost is None:
            add("missing_cost", f"{l.tag}: supplier cost is missing.", l.id)
        if l.price is None:
            add("missing_price", f"{l.tag}: selling price is missing.", l.id)
        if not l.model.strip():
            add(
                "selection",
                f"{l.tag}: choose a product or enter a reviewed configuration.",
                l.id,
            )
        if l.review != "approved":
            add("review", f"{l.tag}: line needs review.", l.id)
        if l.price is not None and l.cost is not None and l.price < l.cost:
            add("below_cost", f"{l.tag}: selling price is below cost.", l.id)
        if l.meta.get("offer_id"):
            o = get(s, Offer, l.meta["offer_id"])
            if o.currency != q.currency or o.unit != l.unit:
                add(
                    "offer_basis",
                    f"{l.tag}: supplier offer currency or unit does not match.",
                    l.id,
                )
            if o.valid_until and date.fromisoformat(o.valid_until) < date.today():
                add("offer_expired", f"{l.tag}: supplier offer has expired.", l.id)
            if o.max_quantity is not None and l.quantity > o.max_quantity:
                add(
                    "offer_scope",
                    f"{l.tag}: quantity exceeds supplier offer coverage.",
                    l.id,
                )
    tags = {l.tag for l in ls}
    for r in s.scalars(
        scoped(Requirement).where(
            Requirement.project_id == p.id, Requirement.active == True
        )
    ):
        if r.tag not in tags:
            add("uncovered", f"{r.tag}: requirement has no quote line.")
        elif r.attribute == "quantity":
            line = next(l for l in ls if l.tag == r.tag)
            if dec(r.value["quantity"]) != line.quantity:
                add(
                    "requirement_quantity",
                    f"{r.tag}: quoted quantity does not match the active source requirement.",
                    line.id,
                )
    return result


def quote_view(s, q, p):
    data = snapshot(s, q, p)
    ls = lines(s, q)
    total = sum((extended(l) or Decimal(0) for l in ls), Decimal(0))
    cost = sum(
        (money(l.cost * l.quantity) if l.cost is not None else Decimal(0) for l in ls),
        Decimal(0),
    )
    data.update(
        status=q.status,
        total=str(money(total)),
        total_complete=all(l.price is not None for l in ls),
        total_cost=str(money(cost)),
        margin=str(((total - cost) / total * 100).quantize(Decimal(".1")))
        if total and all(l.cost is not None for l in ls)
        else None,
        checks=checks(s, q, p),
        reconciled_input=q.reconciled_input,
    )
    return data


def validate_sources(s, p, ids):
    for id in ids:
        sp = get(s, Span, id)
        d = get(s, Document, sp.document_id)
        if d.project_id != p.id:
            fail("Evidence must belong to this project.", 403)


def apply_operation(s, q, p, op):
    validate_sources(s, p, op.source_ids)
    typ = op.type
    if typ == "add_line":
        if op.line is None:
            fail("Line values are required.")
        values = op.line.model_dump(exclude={"source_ids"})
        validate_sources(s, p, op.line.source_ids)
        if s.scalar(scoped(Line).where(Line.quote_id == q.id, Line.tag == op.line.tag)):
            fail(f"Tag {op.line.tag} already exists. Update the existing line instead.")
        ls = lines(s, q)
        s.add(
            Line(
                id=uid(),
                quote_id=q.id,
                position=max([x.position for x in ls], default=0) + 1,
                **values,
                meta={
                    "source_ids": op.line.source_ids,
                    "origin": "document extraction"
                    if op.line.source_ids
                    else "human entered",
                    "price_basis": op.line.unit,
                    "field_evidence": {
                        f: {
                            "origin": "document extraction"
                            if op.line.source_ids
                            else "human entered",
                            "source_ids": op.line.source_ids,
                            "review": "unreviewed",
                        }
                        for f in (
                            "quantity",
                            "description",
                            "cost",
                            "price",
                            "lead_time",
                            "model",
                        )
                    },
                },
            )
        )
        s.flush()
        return
    if typ == "set_terms":
        q.terms = op.value or ""
        return
    if typ == "reconcile_inputs":
        if not op.reason.strip():
            fail("Describe how you reconciled the sources.")
        if any(d.state not in ("ready", "mapped") for d in docs(s, p)):
            fail("Finish document processing and column mapping before reconciliation.")
        q.reconciled_input = p.input_revision
        return
    if op.line_id is None:
        fail("Select a quote line.")
    l = get(s, Line, op.line_id)
    if l.quote_id != q.id:
        fail("Line does not belong to this quote.", 403)
    fields = {
        "set_quantity": "quantity",
        "update_description": "description",
        "override_selling_price": "price",
        "set_cost": "cost",
        "set_lead_time": "lead_time",
    }
    if typ in fields and op.expected_before is not None:
        current = getattr(l, fields[typ])
        try:
            matches = (
                dec(current) == dec(op.expected_before)
                if fields[typ] in ("quantity", "price", "cost")
                else str(current) == op.expected_before
            )
        except ValueError:
            matches = str(current) == op.expected_before
        if not matches:
            fail("The original value changed. Refresh this proposal.", 409)
    meta = deepcopy(l.meta or {})
    if typ == "remove_line":
        s.delete(l)
        s.flush()
        return
    elif typ == "set_quantity":
        v = dec(op.value)
        if not 0 < v <= 1000000 or v.as_tuple().exponent < -4:
            fail("Quantity must be positive with at most four decimal places.")
        l.quantity = v
        if op.source_ids:
            for requirement in s.scalars(
                scoped(Requirement).where(
                    Requirement.project_id == p.id,
                    Requirement.tag == l.tag,
                    Requirement.attribute == "quantity",
                    Requirement.active == True,
                )
            ):
                requirement.active = False
            s.add(
                Requirement(
                    project_id=p.id,
                    tag=l.tag,
                    attribute="quantity",
                    value={"quantity": str(v), "unit": l.unit},
                    sources=op.source_ids,
                    input_revision=p.input_revision,
                )
            )
    elif typ in ("set_gross_margin", "set_markup"):
        l.price = (gross_margin if typ == "set_gross_margin" else markup)(
            l.cost, op.value
        )
        meta["calculation"] = {
            "type": typ,
            "percent": op.value,
            "cost": str(l.cost),
            "rounding": "unit cents, half up",
            "policy": "buy-sell-v1",
        }
    elif typ in ("set_cost", "override_selling_price"):
        v = dec(op.value)
        if not 0 <= v <= 100000000 or v != money(v):
            fail("Money must be nonnegative and have at most two decimal places.")
        if not op.reason.strip():
            fail("Record a reason for the commercial override.")
        setattr(l, "cost" if typ == "set_cost" else "price", v)
        if typ == "set_cost":
            meta.pop("offer_id", None)
            meta.pop("calculation", None)
        else:
            meta.pop("calculation", None)
        meta["override"] = {"actor": ACTOR, "reason": op.reason}
    elif typ == "update_description":
        if not (op.value or "").strip():
            fail("Description is required.")
        l.description = op.value
    elif typ == "set_lead_time":
        l.lead_time = op.value or "Needs confirmation"
    elif typ == "select_catalog_item":
        item = get(s, Catalog, op.value)
        l.model = item.model
        meta["catalog_id"] = item.id
        offers = list(s.scalars(scoped(Offer).where(Offer.catalog_id == item.id)))
        valid = [
            o
            for o in offers
            if o.currency == q.currency
            and o.unit == l.unit
            and (not o.valid_until or date.fromisoformat(o.valid_until) >= date.today())
            and (o.max_quantity is None or o.max_quantity >= l.quantity)
        ]
        if valid:
            o = valid[0]
            l.cost = o.cost
            l.lead_time = o.lead_time
            meta["offer_id"] = o.id
        else:
            l.cost = None
            meta.pop("offer_id", None)
    elif typ == "review_line":
        if l.price is None or l.cost is None:
            fail("Add supplier cost and selling price before approving this line.")
        l.review = "approved"
        meta["review_actor"] = ACTOR
        meta["review_reason"] = op.reason
    elif typ == "attach_evidence":
        pass
    elif typ == "record_assumption":
        if not op.reason.strip():
            fail("Record the assumption explicitly.")
        meta["assumptions"] = [*meta.get("assumptions", []), op.reason]
    if typ != "review_line":
        l.review = "unreviewed"
    if op.source_ids:
        meta["source_ids"] = list(
            dict.fromkeys([*meta.get("source_ids", []), *op.source_ids])
        )
        meta["origin"] = "document extraction"
    else:
        meta["origin"] = (
            "human entered"
            if typ not in ("review_line", "set_gross_margin", "set_markup")
            else meta.get("origin", "human entered")
        )
    field = fields.get(
        typ,
        "price"
        if typ in ("set_gross_margin", "set_markup")
        else "model"
        if typ == "select_catalog_item"
        else None,
    )
    if field:
        field_evidence = meta.get("field_evidence", {})
        field_evidence[field] = {
            "origin": "calculated"
            if typ in ("set_gross_margin", "set_markup")
            else "document extraction"
            if op.source_ids
            else "human entered",
            "source_ids": op.source_ids,
            "review": "unreviewed",
            "actor": ACTOR,
        }
        meta["field_evidence"] = field_evidence
    if typ == "review_line":
        meta["field_evidence"] = {
            k: {**v, "review": "approved"}
            for k, v in meta.get("field_evidence", {}).items()
        }
    l.meta = meta
    s.flush()


def command(s, q, p, operations, summary="Updated quote"):
    for op in operations:
        apply_operation(s, q, p, op)
    invalidate(s, q)
    q.version += 1
    save_version(s, q, p, summary)


def idempotent(s, key, body, action):
    if not key or len(key) > 160:
        fail("A valid idempotency key is required.")
    prior = s.scalar(scoped(Idempotency).where(Idempotency.key == key))
    fingerprint = digest(body)
    if prior:
        if prior.fingerprint != fingerprint:
            fail("This retry key was already used for a different request.", 409)
        return prior.result
    result = action()
    s.add(Idempotency(key=key, fingerprint=fingerprint, result=result))
    s.flush()
    return result


def new_project(s, data, synthetic=False):
    p = Project(**data, synthetic=synthetic)
    s.add(p)
    s.flush()
    q = Quote(project_id=p.id, number=f"RV-{date.today().year}-{p.id[:4].upper()}")
    s.add(q)
    s.flush()
    save_version(s, q, p, "Created quote")
    return p, q


def proposal_dict(pr):
    return {
        k: getattr(pr, k)
        for k in (
            "id",
            "quote_id",
            "base_version",
            "input_revision",
            "title",
            "summary",
            "operations",
            "sources",
            "status",
            "reason",
        )
    }


def document_dict(d):
    return {
        "id": d.id,
        "name": d.name,
        "kind": d.kind,
        "state": d.state,
        "coverage": d.coverage,
        "meta": d.meta,
        "created_at": d.created_at.isoformat(),
    }
