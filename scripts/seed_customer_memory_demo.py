"""Opt-in fictional returning-customer fixture; preserves existing orders and notes."""

import argparse
import json

from backend.company_context import customers
from backend.company_context.models import CustomerProfile
from backend.company_context.schemas import (
    CustomerBindingWrite,
    CustomerNoteReview,
    CustomerNoteWrite,
    CustomerWrite,
)
from backend.domain.service import scoped
from backend.identity import Identity, identity_scope
from backend.ingestion.parser import parse, persist_blob
from backend.orders.service import new_order
from backend.storage.db import Session
from backend.storage.models import Document, Span


def build(organization_id):
    with (
        identity_scope(Identity(organization_id, "Fictional demo setup", "org:admin")),
        Session.begin() as s,
    ):
        existing = s.scalar(
            scoped(CustomerProfile).where(
                CustomerProfile.key == "demo-returning-customer"
            )
        )
        if existing:
            return {
                "customer_id": existing.id,
                "created": False,
                "message": "Existing demo preserved.",
            }
        c = customers.create_customer(
            s,
            CustomerWrite(
                key="demo-returning-customer",
                name="Northline Facilities · fictional",
                description="Fictional returning-customer demo. No real customer requirements or engineering approvals.",
            ),
        )
        ids = []
        sources = []
        documents = []
        for number, title, text in [
            (
                "DEMO-HISTORY-101",
                "Previous build · customer requests",
                "FICTIONAL CUSTOMER HISTORY DEMO\nJob DEMO-HISTORY-101 / Revision B\nCustomer: Northline Facilities (fictional)\nCustomer request: Show the cable entry location on the general arrangement for each indoor switchgear order.\nCustomer request: Include the customer equipment tag in the nameplate text on future drawings.\nRecorded response: Both documentation requests were recorded for this fictional build.\nThis is sample correspondence, not an engineering design or actual approval.\n",
            ),
            (
                "DEMO-HISTORY-102",
                "New build · customer kickoff",
                "FICTIONAL DRAWING NOTES EXTRACT\nJob DEMO-HISTORY-102 / Revision A\nCustomer: Northline Facilities (fictional)\nSheet GA-01. Indoor low-voltage switchgear.\nNameplate text includes customer equipment tag NF-SW-02.\nOverall enclosure dimensions are shown for demonstration only.\nNot an engineering drawing or production release.\n",
            ),
        ]:
            order = new_order(
                s,
                {
                    "number": number,
                    "title": title,
                    "customer": c["name"],
                    "category": "Low-voltage switchgear",
                    "synthetic": True,
                },
            )
            blob, sha = persist_blob(text.encode(), ".txt")
            doc = Document(
                project_id=order.project_id,
                name=f"{number}-{'customer-history' if not ids else 'drawing-notes'}.txt",
                kind="drawing",
                blob=blob,
                sha256=sha,
                meta={
                    "revision_label": "B" if not ids else "A",
                    "order_role": "drawing",
                    "fixture": True,
                },
            )
            s.add(doc)
            s.flush()
            parse(s, doc)
            customers.bind_order(
                s,
                order.id,
                CustomerBindingWrite(
                    customer_id=c["id"],
                    expected_version=0,
                    reason="Fictional fixture: both orders explicitly belong to the same sample customer.",
                ),
            )
            ids.append(order.id)
            documents.append(doc.id)
            sources.append(
                list(s.scalars(scoped(Span).where(Span.document_id == doc.id)))
            )
        for key, title, text, topic in [
            (
                "cable-entry",
                "Show the cable entry location",
                "Show the cable entry location on the general arrangement.",
                "cable entry",
            ),
            (
                "nameplate",
                "Include the customer equipment tag",
                "Include the customer equipment tag in the nameplate text.",
                "nameplate equipment tag",
            ),
        ]:
            source = next(
                sp
                for sp in sources[0]
                if sp.text.startswith("Customer request:")
                and ("cable entry" if key == "cable-entry" else "nameplate") in sp.text
            )
            n = customers.create_note(
                s,
                c["id"],
                CustomerNoteWrite(
                    key=key,
                    title=title,
                    text=text,
                    kind="preference",
                    topics=[topic],
                    category="Low-voltage switchgear",
                    applicability="standing_preference",
                    applicability_note="For this fictional customer’s indoor switchgear. Confirm the current job and site requirements before use.",
                    source_order_id=ids[0],
                    source_ids=[source.id],
                    provenance_note="Fictional demo setup; exact source passage retained. This is not real customer consent.",
                ),
            )
            customers.review_note(
                s,
                n["id"],
                CustomerNoteReview(
                    expected_version=1,
                    reason="Fictional fixture approval for testing only; not a real customer or engineering decision.",
                ),
                approve=True,
            )
        return {
            "customer_id": c["id"],
            "source_order_id": ids[0],
            "order_id": ids[1],
            "document_id": documents[1],
            "created": True,
        }


if __name__ == "__main__":
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--organization-id", required=True)
    print(json.dumps(build(args.parse_args().organization_id)))
