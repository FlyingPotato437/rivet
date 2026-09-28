"""Only explicit, synthetic demonstration records. Safe to rerun."""

from datetime import date, timedelta
from decimal import Decimal
from backend.storage.db import Session, ROOT
from backend.storage.models import *
from backend.domain.service import *
from backend.domain.schemas import Operation, LineInput
from backend.ingestion.parser import persist_blob, parse
from backend.domain.pricing import gross_margin

ITEMS = [
    (
        "PDU-01",
        "Power distribution unit",
        "VD-PDU-300",
        8,
        13500,
        18000,
        "18–20 weeks after approved submittal",
    ),
    (
        "UPS-01",
        "Modular uninterruptible power supply",
        "VD-UPS-500",
        2,
        64000,
        80000,
        "22 weeks after approved submittal",
    ),
    (
        "ATS-01",
        "Automatic transfer switch",
        "VD-ATS-1200",
        4,
        9500,
        12500,
        "12–14 weeks after release",
    ),
    (
        "RPP-01",
        "Remote power panel",
        "VD-RPP-225",
        6,
        6200,
        8250,
        "10 weeks after approved submittal",
    ),
    (
        "TX-01",
        "Dry-type transformer",
        "VD-TX-300",
        2,
        18500,
        24000,
        "16–18 weeks after approved submittal",
    ),
    (
        "MBP-01",
        "Maintenance bypass panel",
        "VD-MBP-500",
        2,
        11600,
        14500,
        "14 weeks after approved submittal",
    ),
]


def main():
    with Session.begin() as s:
        if s.scalar(scoped(Project)):
            return
        today = date.today()
        p, q = new_project(
            s,
            {
                "title": "Westfield Data Center",
                "customer": "Atlas Electrical Group",
                "category": "Power distribution",
                "due_date": str(today + timedelta(days=12)),
            },
            True,
        )
        source = "SYNTHETIC DEMO — All products, suppliers, prices and delivery conditions are fictional.\n"
        source += "\n".join(
            f"{tag}: {qty} each; {desc}; Voltwell {model}; supplier cost USD {cost:.2f} per each; selling price USD {price:.2f} per each; {lead}."
            for tag, desc, model, qty, cost, price, lead in ITEMS
        )
        blob, sha = persist_blob(source.encode(), ".txt")
        doc = Document(
            project_id=p.id,
            name="Westfield — equipment schedule.txt",
            kind="schedule",
            blob=blob,
            sha256=sha,
        )
        s.add(doc)
        s.flush()
        parse(s, doc)
        spans = list(s.scalars(scoped(Span).where(Span.document_id == doc.id)))
        for n, (tag, desc, model, qty, cost, price, lead) in enumerate(ITEMS):
            sp = next(x for x in spans if x.text.startswith(tag + ":"))
            cat = Catalog(
                manufacturer="Voltwell · fictional",
                model=model,
                description=desc,
                attributes={
                    "voltage": "480 V",
                    "category": "Power distribution",
                    "source_ids": [sp.id],
                },
                synthetic=True,
            )
            s.add(cat)
            s.flush()
            offer = Offer(
                catalog_id=cat.id,
                supplier="Voltwell Supply · fictional",
                cost=Decimal(cost),
                lead_time=lead,
                valid_until=str(today + timedelta(days=30)),
                max_quantity=qty if n == 0 else None,
                sources=[sp.id],
            )
            s.add(offer)
            s.flush()
            s.add(
                Line(
                    quote_id=q.id,
                    position=n + 1,
                    tag=tag,
                    description=desc,
                    quantity=qty,
                    model=model,
                    cost=cost,
                    price=price,
                    lead_time=lead,
                    review="approved" if n < 4 else "unreviewed",
                    meta={
                        "source_ids": [sp.id],
                        "origin": "imported value",
                        "catalog_id": cat.id,
                        "offer_id": offer.id,
                        "price_basis": "each",
                        "review_actor": "Synthetic fixture" if n < 4 else None,
                    },
                )
            )
            s.add(
                Requirement(
                    project_id=p.id,
                    tag=tag,
                    attribute="quantity",
                    value={"quantity": str(qty), "unit": "each"},
                    sources=[sp.id],
                    input_revision=1,
                )
            )
        q.version = 2
        save_version(
            s,
            q,
            p,
            "Imported six equipment lines from the sample schedule",
            "Synthetic fixture",
        )
        q.version = 3
        save_version(
            s, q, p, "Reviewed PDU, UPS, ATS and RPP lines", "Synthetic fixture"
        )
        addon = "SYNTHETIC DEMO — Addendum 02\nPDU-01 quantity changes from 8 to 10 each. All other quantities remain unchanged.\nTX-01 delivery requirement: 14 weeks after approved submittal. Existing supplier offer remains 16–18 weeks; confirmation required.\n"
        blob, sha = persist_blob(addon.encode(), ".txt")
        ad = Document(
            project_id=p.id,
            name="Addendum 02 — electrical revisions.txt",
            kind="addendum",
            blob=blob,
            sha256=sha,
        )
        s.add(ad)
        s.flush()
        parse(s, ad)
        asp = list(s.scalars(scoped(Span).where(Span.document_id == ad.id)))
        p.input_revision = 2
        ls = lines(s, q)
        pdu = next(l for l in ls if l.tag == "PDU-01")
        tx = next(l for l in ls if l.tag == "TX-01")
        a = next(x for x in asp if x.text.startswith("PDU"))
        b = next(x for x in asp if x.text.startswith("TX"))
        ops = [
            Operation(
                type="set_quantity",
                line_id=pdu.id,
                value="10",
                expected_before="8",
                source_ids=[a.id],
            ),
            Operation(
                type="set_lead_time",
                line_id=tx.id,
                value="14 weeks requested; supplier offer 16–18 weeks. Needs confirmation.",
                expected_before=tx.lead_time,
                source_ids=[b.id],
            ),
            Operation(
                type="reconcile_inputs",
                reason="Reviewed explicit Addendum 02 changes; PDU offer covers only eight units and TX delivery remains unconfirmed.",
            ),
        ]
        s.add(
            Proposal(
                quote_id=q.id,
                base_version=q.version,
                input_revision=2,
                title="Addendum 02 · quantity & delivery",
                summary="Two equipment lines affected. PDU quantity increases to 10; the existing offer covers 8. Transformer delivery needs supplier confirmation. Review the source before accepting.",
                operations=[op.model_dump(mode="json") for op in ops],
                sources=[a.id, b.id],
            )
        )
        s.add(
            Event(
                project_id=p.id,
                summary="Addendum 02 is ready for review",
                kind="proposal",
                actor="Synthetic fixture",
            )
        )
        for title, customer, days, amt, color in [
            ("Redwood Office Campus", "Stonebridge MEP", 18, 129400, "amber"),
            ("Pacific Logistics Hub", "Meridian Contractors", 24, 86250, "green"),
        ]:
            pp, qq = new_project(
                s,
                {
                    "title": title,
                    "customer": customer,
                    "category": "Power distribution",
                    "due_date": str(today + timedelta(days=days)),
                },
                True,
            )
            pp.color = color
            for n, (tag, desc, model, qty, cost, price, lead) in enumerate(ITEMS[:3]):
                unitprice = (
                    money(Decimal(amt) / 3)
                    if n < 2
                    else money(Decimal(amt) - money(Decimal(amt) / 3) * 2)
                )
                s.add(
                    Line(
                        quote_id=qq.id,
                        position=n + 1,
                        tag=tag,
                        description=desc,
                        quantity=1,
                        model=model,
                        cost=money(unitprice * Decimal(".8")),
                        price=unitprice,
                        lead_time="Subject to supplier confirmation",
                        review="unreviewed",
                        meta={"origin": "human entered", "synthetic": True},
                    )
                )
            qq.version = 2
            save_version(
                s, qq, pp, "Prepared synthetic equipment package", "Synthetic fixture"
            )
    print("Synthetic workspace seeded.")


if __name__ == "__main__":
    main()
