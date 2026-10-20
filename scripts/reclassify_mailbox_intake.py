"""Reclassify only untouched, unmatched mailbox captures; dry run by default.

Never imports a document, changes an order, deletes mail, or marks mail read.
An explicitly restored/set-aside item is a human decision and is left alone.
Run from the repository root with `python -m scripts.reclassify_mailbox_intake`.
"""

import argparse
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import exists

from backend.domain.service import scoped
from backend.identity import Identity, current_org, identity_scope
from backend.mailbox.models import MailboxReceipt
from backend.records.intake import inspect_file, match_orders
from backend.records.mail_relevance import classify
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import RecordIntake, now


def reclassify_pending(s, *, apply=False, limit=1000):
    """Caller owns identity/transaction; returns identifiers and reasons only."""
    mailbox_capture = exists().where(
        MailboxReceipt.intake_id == RecordIntake.id,
        MailboxReceipt.organization_id == current_org(),
    )
    query = (
        scoped(RecordIntake)
        .where(
            RecordIntake.status == "needs_confirmation",
            RecordIntake.order_id.is_(None),
            mailbox_capture,
        )
        .order_by(RecordIntake.created_at, RecordIntake.id)
        .limit(limit)
    )
    if apply:
        query = query.with_for_update()
    report = {
        "mode": "apply" if apply else "dry_run",
        "examined": 0,
        "changes": [],
        "skipped": 0,
    }
    for row in s.scalars(query):
        report["examined"] += 1
        if (
            not row.name.lower().endswith(".eml")
            or row.data.get("disposition_history")
            or row.data.get("original_detected")
            or row.data.get("document_id")
        ):
            report["skipped"] += 1
            continue
        try:
            payload = (db.BLOBS / row.blob).read_bytes()
            detected, email = inspect_file(row.name, payload)
            relevance = classify(payload, detected, match_orders(s, detected, email))
        except (OSError, ValueError, HTTPException):
            report["skipped"] += 1
            continue
        if relevance["relevant"]:
            continue
        report["changes"].append({"intake_id": row.id, "reason": relevance["reason"]})
        if apply:
            row.status = "not_order_related"
            row.data = {
                **row.data,
                "email": email,
                "mail_relevance": relevance,
                "reason": relevance["reason"],
                "reclassified_at": now().isoformat(),
            }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True, type=UUID)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10000:
        parser.error("--limit must be between 1 and 10000")
    with (
        identity_scope(
            Identity(str(args.organization_id), "Mailbox intake reclassification")
        ),
        Session.begin() as s,
    ):
        print(
            json.dumps(
                reclassify_pending(s, apply=args.apply, limit=args.limit), indent=2
            )
        )


if __name__ == "__main__":
    main()
