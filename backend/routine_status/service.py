"""Deterministic routine status replies. No model call, no Graph send permission.

A captured, unambiguous status-only question can use exact, human-approved facts.
Everything else is a draft or an explicit escalation. Optional sending currently
executes only against a synthetic-order local simulator, never a customer mailbox.
"""

import json
import re
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from email import policy as email_policy
from email.parser import BytesParser
from email.utils import getaddresses
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import func, select

from backend.company_context.models import CompanyRule
from backend.domain.service import digest, fail, get, scoped
from backend.identity import current_actor, current_identity
from backend.lifecycle.service import _sources
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.mailbox.service import find_receipt_by_message_id
from backend.orders import service as orders
from backend.records.delivery import workspace_url
from backend.records.intake_preview import email_preview
from backend.reply_assistant import automatic
from backend.reply_assistant import service as replies
from backend.reply_assistant.models import ReplyDraft
from backend.storage import db
from backend.storage.models import (
    Document,
    Job,
    OrderEvent,
    Project,
    RecordIntake,
    Span,
    now,
)

from .schemas import exact_address

POLICY_KEY = "routine_status_policy"
INSTRUCTION = "Reply only with the exact current approved order-status facts."


def policy(s):
    row = s.scalar(
        scoped(CompanyRule)
        .where(CompanyRule.key == POLICY_KEY, CompanyRule.status == "approved")
        .order_by(CompanyRule.version.desc())
        .limit(1)
    )
    return {
        "auto_send": False,
        "recipients": [],
        "status_approvers": [],
        "max_fact_age_hours": 24,
        **(row.provenance.get("policy", {}) if row else {}),
        "version": row.version if row else 0,
        "transport": "local_simulation_only",
        "customer_mail_sending_supported": False,
    }


def set_policy(s, body):
    if current_identity().role != "org:admin":
        fail("A team administrator must configure routine status replies.", 403)
    if body.auto_send and not body.recipients:
        fail(
            "Choose exact allowed recipient addresses before enabling routine status sending."
        )
    s.execute(
        select(
            func.pg_advisory_xact_lock(
                int(digest([current_identity().organization_id, POLICY_KEY])[:15], 16)
            )
        )
    )
    value = body.model_dump(exclude={"reason"})
    row = CompanyRule(
        key=POLICY_KEY,
        version=policy(s)["version"] + 1,
        title="Routine status policy",
        text=json.dumps(value, sort_keys=True),
        topics=["routine_status_policy"],
        provenance={"kind": POLICY_KEY, "policy": value},
        author=current_actor(),
        status="approved",
        reviewed_by=current_actor(),
        reviewed_at=now(),
        review_reason=body.reason,
    )
    s.add(row)
    s.flush()
    return policy(s)


def _time(raw):
    value = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
    if not isinstance(value, datetime) or value.tzinfo is None:
        fail("Use an explicit timezone for a current status fact.", 409)
    return value.astimezone(UTC)


def _bytes(document):
    path = db.BLOBS / document.blob
    if (
        Path(document.blob).name != document.blob
        or not path.resolve().is_relative_to(db.BLOBS.resolve())
        or not path.is_file()
    ):
        fail("The saved source file is unavailable.", 409)
    content = path.read_bytes()
    if (
        len(content) > 32 * 1024 * 1024
        or sha256(content).hexdigest() != document.sha256
    ):
        fail("The saved source bytes changed. Review the current source.", 409)
    return content


def approve_fact(s, order, body):
    orders.check_version(order, body.expected_version)
    p = policy(s)
    local_admin = (
        get(s, Project, order.project_id).synthetic
        and current_identity().role == "org:admin"
    )
    if not local_admin and current_actor() not in p["status_approvers"]:
        fail("A named company status approver must review this exact fact.", 403)
    expires = _time(body.valid_until)
    if not now() < expires <= now() + timedelta(days=7):
        fail("Set a status fact expiry within the next seven days.")
    sources = _sources(s, order, body.source_ids)
    expected = body.value.isoformat()
    for sid in body.source_ids:
        span = get(s, Span, sid)
        document = get(s, Document, span.document_id)
        _bytes(document)
        if document.kind in {"email", "markup"} or document.name.lower().endswith(
            ".eml"
        ):
            fail(
                "An incoming customer request cannot establish an approved schedule fact.",
                409,
            )
        try:
            value = json.loads(span.text)
        except (ValueError, TypeError):
            fail(
                "Select the structured schedule or material record containing this exact date.",
                409,
            )
        if (
            not isinstance(value, dict)
            or value.get("job_number") != order.number
            or value.get(body.kind) != expected
            or value.get("status") != "confirmed"
        ):
            fail(
                "The cited confirmed status record must match this job, date and fact type exactly.",
                409,
            )
    fact = {
        "kind": body.kind,
        "value": expected,
        "sources": sources,
        "approved_at": now().isoformat(),
        "valid_until": expires.isoformat(),
        "approved_by": current_actor(),
        "reason": body.reason,
    }
    fact["fingerprint"] = digest(fact)
    facts = deepcopy(order.state.get("routine_status_facts", {}))
    facts[body.kind] = fact
    order.state = {**order.state, "routine_status_facts": facts}
    orders.commit(
        s,
        order,
        "Reviewed a source-bound routine status fact",
        "routine_status_fact",
        {"kind": body.kind, "fingerprint": fact["fingerprint"]},
    )
    return fact


def current_facts(s, order):
    p, facts = policy(s), deepcopy(order.state.get("routine_status_facts", {}))
    if not facts:
        fail("No current human-approved schedule or material status is available.", 409)
    for fact in facts.values():
        signed = {k: v for k, v in fact.items() if k != "fingerprint"}
        if (
            digest(signed) != fact["fingerprint"]
            or _sources(s, order, list(fact["sources"])) != fact["sources"]
        ):
            fail(
                "Approved status evidence changed. A person must review the new record.",
                409,
            )
        if _time(fact["valid_until"]) <= now() or _time(
            fact["approved_at"]
        ) < now() - timedelta(hours=p["max_fact_age_hours"]):
            fail("The approved status record is too old to answer automatically.", 409)
        for source in fact["sources"].values():
            _bytes(get(s, Document, source["document_id"]))
    return facts


def _mail(s, order, receipt):
    if (
        receipt.status != "imported"
        or not receipt.intake_id
        or not receipt.internet_message_id
    ):
        fail("Confirm the captured email and its order first.", 409)
    unique = find_receipt_by_message_id(s, receipt.internet_message_id)
    if not unique or unique.id != receipt.id:
        fail("This email does not have one unambiguous captured identity.", 409)
    connection = get(s, MailboxConnection, receipt.connection_id)
    intake = get(s, RecordIntake, receipt.intake_id)
    if (
        not connection.enabled
        or intake.order_id != order.id
        or intake.status not in {"imported", "matched"}
    ):
        fail("This email is not currently linked to this order.", 409)
    latest = automatic.latest_in_thread(s, order, receipt)
    if automatic.historical(receipt) or not latest or latest.id != receipt.id:
        fail("A newer email or an old thread needs a fresh status review.", 409)
    original = db.BLOBS / intake.blob
    if (
        Path(intake.blob).name != intake.blob
        or not original.resolve().is_relative_to(db.BLOBS.resolve())
        or not original.is_file()
    ):
        fail("The captured email is unavailable.", 409)
    raw = original.read_bytes()
    if sha256(raw).hexdigest() != intake.sha256:
        fail("The captured email bytes changed.", 409)
    message = BytesParser(policy=email_policy.default).parsebytes(raw)
    parsed = email_preview(raw)
    senders = getaddresses(message.get_all("From", []))
    reply_to = getaddresses(message.get_all("Reply-To", []))
    try:
        recipient = exact_address(senders[0][1]) if len(senders) == 1 else ""
        if (
            not recipient
            or recipient != exact_address(receipt.data.get("sender", ""))
            or (
                reply_to
                and (len(reply_to) != 1 or exact_address(reply_to[0][1]) != recipient)
            )
        ):
            fail("The sender or reply-to address needs confirmation.", 409)
    except ValueError:
        fail("The sender address cannot be verified.", 409)
    if str(message.get("Message-ID", "")) != receipt.internet_message_id:
        fail("The saved email identity does not match this captured thread.", 409)
    if (
        message.get("Auto-Submitted", "no").lower() != "no"
        or message.get("List-Id")
        or message.get("Precedence", "").lower() in {"bulk", "list", "junk"}
    ):
        fail("Automated or list email requires no routine customer reply.", 409)
    return connection, intake, parsed, recipient


def classify(subject, body, order_number, attachments=False):
    """Closed grammar, not an LLM confidence score. Unknown wording escalates."""
    text = re.split(r"(?im)^On .{1,300}wrote:|^From:\s|^>", body, maxsplit=1)[0]
    combined = subject + " " + text
    cue = bool(
        re.search(
            r"\b(status|update|ship(?:ping)?|deliver(?:y)?|material availability)\b",
            combined,
            re.IGNORECASE,
        )
    )
    if not cue:
        return None
    if attachments or re.search(
        r"\b(late|delay|expedite|earlier|change|cost|price|quote|approve|approval|release|rating|voltage|breaker|trip unit|drawing|design|test|certif\w*|guarantee|promise|commit)\b",
        combined,
        re.IGNORECASE,
    ):
        return "This status question includes a delay, commitment, technical or commercial decision. A person must respond."
    text = re.sub(re.escape(order_number), "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?im)^(?:hi|hello|dear)(?: team| rivet)?[,!]?\s*$", "", text)
    text = re.sub(
        r"(?is)\n(?:thanks|thank you|regards|best regards)[,! .]*\s*(?:\n[^\n]{1,60})?$",
        "",
        text,
    ).strip()
    text = " ".join(text.lower().split()).strip(" .?!")
    text = re.sub(r"\b(?:for|of|on) (?:the |our )?(?:job|order)\s*$", "", text).strip()
    patterns = [
        r"(?:please )?(?:send|share|provide)(?: us| me)? (?:a |the |an )?(?:current )?(?:(?:order|shipping|delivery|material) )?(?:status|update)",
        r"(?:can|could) you (?:please )?(?:send|share|provide|confirm)(?: us| me)? (?:a |the |an )?(?:current )?(?:(?:order|shipping|delivery|material) )?(?:status|update)",
        r"(?:what is|what's) (?:the |our )?(?:current )?(?:(?:order|shipping|delivery|material) )?status",
        r"(?:any |a )?(?:status )?update",
        r"when (?:will|does) (?:the |our )?order ship",
        r"when (?:will|does) (?:the |our )?material (?:arrive|be available)",
    ]
    return (
        "eligible"
        if any(re.fullmatch(pattern, text) for pattern in patterns)
        else "Rivet cannot safely reduce this email to a routine status question. A person must review the full request."
    )


def _references(s, order, facts):
    ids = sorted({sid for fact in facts.values() for sid in fact["sources"]})
    refs = []
    for sid in ids:
        span = get(s, Span, sid)
        document = get(s, Document, span.document_id)
        location = span.location
        suffix = (
            f" · page {location['page']}"
            if location.get("page")
            else f" · line {location['line']}"
            if location.get("line")
            else ""
        )
        refs.append(
            {
                "id": sid,
                "label": document.name + suffix,
                "url": workspace_url()
                + "#order/"
                + order.id
                + "?"
                + urlencode({"tab": "work", "source": sid}),
            }
        )
    return refs


def _render(s, order, facts, question):
    ship = facts.get("confirmed_ship_date")
    material = facts.get("material_available_date")
    if ship and date.fromisoformat(ship["value"]) < now().date():
        fail(
            "The recorded ship date has passed. Check for a delay or shipment evidence before replying.",
            409,
        )
    if ship and material and material["value"] > ship["value"]:
        fail(
            "Material availability is later than the recorded ship date. A person must resolve the delay.",
            409,
        )
    if not ship and re.search(r"ship|deliver", question, re.IGNORECASE):
        fail(
            "Material availability does not establish a promised ship date. A person must confirm the schedule.",
            409,
        )
    refs = _references(s, order, facts)
    indexes = {ref["id"]: index + 1 for index, ref in enumerate(refs)}

    def cite(fact):
        return " ".join(f"[{indexes[sid]}]" for sid in sorted(fact["sources"]))

    lines = [f"Status for order {order.number}:"]
    if ship:
        lines.append(
            f"The current approved schedule records a confirmed ship date of {ship['value']}. {cite(ship)}"
        )
    if material:
        lines.append(
            f"The current approved material record lists availability on {material['value']}. {cite(material)} This material date is not a shipment commitment."
        )
    lines.append(
        "These are the recorded dates; no schedule change is proposed in this reply."
    )
    lines.append(
        "Sources:\n"
        + "\n".join(f"[{index + 1}] {ref['label']}" for index, ref in enumerate(refs))
    )
    return "\n\n".join(lines)


def _binding(receipt, connection, intake, recipient):
    return {
        "receipt_id": receipt.id,
        "connection_id": connection.id,
        "mailbox": connection.mailbox,
        "provider_message_id": receipt.provider_message_id,
        "internet_message_id": receipt.internet_message_id,
        "conversation_id": receipt.data.get("conversation_id", ""),
        "recipient": recipient,
        "intake_id": intake.id,
        "intake_sha256": intake.sha256,
    }


def _event(s, order, status, data):
    s.add(
        OrderEvent(
            order_id=order.id,
            version=order.version,
            kind="routine_status",
            actor="Rivet · deterministic status",
            summary={
                "draft_ready": "Prepared a cited routine status draft",
                "needs_review": "Routine status needs a person",
                "accepted_simulation": "Routine status accepted in the local simulator",
                "uncertain": "Status send outcome is uncertain; no retry sent",
            }.get(status, "Routine status " + status.replace("_", " ")),
            data={"status": status, "customer_mail_sent": False, **data},
        )
    )


def prepare_for_receipt(s, order, receipt):
    # Inspect the incoming body before acquiring status authority. Unrelated mail
    # stays in its normal preparation lane.
    intake = get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
    if not intake:
        return None
    if intake.order_id != order.id:
        fail("This captured email belongs to a different order.", 404)
    path = db.BLOBS / intake.blob
    if (
        Path(intake.blob).name != intake.blob
        or not path.resolve().is_relative_to(db.BLOBS.resolve())
        or not path.is_file()
    ):
        return None
    parsed = email_preview(path.read_bytes())
    intent = classify(
        parsed["subject"], parsed["body"], order.number, bool(parsed["attachments"])
    )
    if intent is None:
        return None
    key = "auto-mail:" + digest([order.organization_id, "routine-status", receipt.id])
    existing = s.scalar(scoped(ReplyDraft).where(ReplyDraft.request_key == key))
    if existing:
        try:
            reviewed_hash = replies.context(
                s,
                order,
                existing.context["instruction"],
                existing.context.get("focus_document_ids", []),
            )[-1]
            validate(
                s,
                order,
                existing,
                for_send=existing.context["routine_status"].get("auto_send_requested"),
                reviewed_hash=reviewed_hash,
            )
        except HTTPException as exc:
            return {
                "status": "needs_input",
                "detail": str(exc.detail),
                "draft_id": existing.id,
                "automatic": True,
            }
        return {
            "status": "ready" if existing.status == "ready" else existing.status,
            "detail": "The routine status draft is saved for this exact email.",
            "draft_id": existing.id,
            "automatic": True,
        }
    try:
        if intent != "eligible":
            fail(intent, 409)
        connection, intake, parsed, recipient = _mail(s, order, receipt)
        facts = current_facts(s, order)
        body = _render(s, order, facts, parsed["subject"] + " " + parsed["body"])
        focused = automatic.focus_documents(s, order, receipt)
        if not focused:
            fail("The captured email has no source record in this order.", 409)
        workspace, data, company, evidence_hash = replies.context(
            s, order, INSTRUCTION, focused
        )
    except HTTPException as exc:
        result = {
            "status": "needs_input",
            "detail": str(exc.detail),
            "reason": "routine_status_escalation",
            "automatic": True,
        }
        if receipt.data.get("routine_status") != result:
            _event(
                s,
                order,
                "needs_review",
                {"receipt_id": receipt.id, "detail": result["detail"]},
            )
            receipt.data = {**receipt.data, "routine_status": result}
        return result
    sources = {
        sid: source
        for fact in facts.values()
        for sid, source in fact["sources"].items()
    }
    refs = _references(s, order, facts)
    p = policy(s)
    binding = _binding(receipt, connection, intake, recipient)
    auto_requested = p["auto_send"] and recipient in p["recipients"]
    snapshot = {
        "order_version": order.version,
        "facts": facts,
        "binding": binding,
        "policy_version": p["version"],
        "auto_send_requested": auto_requested,
    }
    subject = "Re: " + parsed["subject"].removeprefix("Re: ")
    row = ReplyDraft(
        order_id=order.id,
        request_key=key,
        request_hash=digest(snapshot),
        internet_message_id=receipt.internet_message_id,
        status="ready",
        evidence_hash=evidence_hash,
        model="deterministic-approved-status",
        subject=subject,
        body=body,
        original_body=body,
        source_refs=refs,
        context={
            "instruction": INSTRUCTION,
            "focus_document_ids": focused,
            "current_order": data,
            "company": company,
            "routine_status": snapshot,
        },
        coverage={
            "origin": "deterministic",
            "kind": "routine_status",
            "source_ids": list(sources),
            "documents_read": len(
                set(focused) | {source["document_id"] for source in sources.values()}
            ),
            "documents_total": len(workspace["documents"]),
            "comments_considered": 0,
        },
    )
    snapshot["reply_hash"] = digest([subject, body, refs])
    s.add(row)
    s.flush()
    _event(
        s,
        order,
        "draft_ready",
        {"reply_id": row.id, "receipt_id": receipt.id, "source_ids": list(sources)},
    )
    if auto_requested:
        job_key = "routine-status-send:" + digest([order.organization_id, receipt.id])
        if not s.scalar(scoped(Job).where(Job.key == job_key)):
            s.add(
                Job(
                    kind="routine_status",
                    project_id=order.project_id,
                    key=job_key,
                    payload={
                        "order_id": order.id,
                        "reply_id": row.id,
                        "send_key": job_key,
                        "phase": "queued",
                        "mode": "local_simulation",
                    },
                )
            )
    return {
        "status": "ready",
        "detail": "A cited routine status reply is prepared. Sending remains disabled unless the exact recipient and local simulator are explicitly enabled.",
        "draft_id": row.id,
        "automatic": True,
    }


def validate(s, order, reply, *, for_send=False, reviewed_hash=None):
    saved = reply.context["routine_status"]
    receipt = get(s, MailboxReceipt, saved["binding"]["receipt_id"])
    connection, intake, parsed, recipient = _mail(s, order, receipt)
    if (
        _binding(receipt, connection, intake, recipient) != saved["binding"]
        or order.version != saved["order_version"]
    ):
        fail(
            "The order, recipient or email thread changed after status preparation.",
            409,
        )
    facts = current_facts(s, order)
    # A current, explicit human review may edit the response for manual Outlook
    # use. It never extends source expiry or authorizes the routine sender to use
    # an edited body. Subject, citations and the original generation stay bound.
    reviewed = bool(
        not for_send
        and reviewed_hash
        and reply.status == "approved"
        and reply.reviewed_at
        and reply.review.get("actor")
        and reply.review.get("request_hash")
        and reply.review.get("reviewed_evidence_hash") == reviewed_hash
    )
    generated_body = reply.original_body if reviewed else reply.body
    if (
        facts != saved["facts"]
        or digest([reply.subject, generated_body, reply.source_refs])
        != saved["reply_hash"]
    ):
        fail("The approved status facts or prepared response changed.", 409)
    if (
        classify(
            parsed["subject"], parsed["body"], order.number, bool(parsed["attachments"])
        )
        != "eligible"
        or _render(s, order, facts, parsed["subject"] + " " + parsed["body"])
        != generated_body
    ):
        fail("This request no longer qualifies for a routine response.", 409)
    p = policy(s)
    if for_send:
        from backend.autopilot.models import OutlookDraft

        if s.scalar(scoped(OutlookDraft).where(OutlookDraft.receipt_id == receipt.id)):
            fail(
                "An Outlook reply already exists for this email. Preserve it for the person to send.",
                409,
            )
        if (
            not p["auto_send"]
            or recipient not in p["recipients"]
            or p["version"] != saved["policy_version"]
            or not saved["auto_send_requested"]
        ):
            fail(
                "Routine sending is off, the recipient is not allowed, or its policy changed.",
                409,
            )
        if not get(s, Project, order.project_id).synthetic:
            fail(
                "Routine sending supports isolated synthetic orders only; production sending is disabled.",
                409,
            )
    elif saved["auto_send_requested"]:
        fail(
            "This response belongs to the isolated status simulator; it must not create a live Outlook draft.",
            409,
        )
    return {
        "organization_id": order.organization_id,
        "order_id": order.id,
        **saved["binding"],
        "subject": reply.subject,
        "body": reply.body,
        "source_refs": reply.source_refs,
        "evidence_hash": digest(facts),
    }


def view(s, order):
    events = s.scalars(
        scoped(OrderEvent)
        .where(OrderEvent.order_id == order.id, OrderEvent.kind == "routine_status")
        .order_by(OrderEvent.created_at.desc())
        .limit(100)
    )
    return {
        "order_id": order.id,
        "policy": policy(s),
        "facts": order.state.get("routine_status_facts", {}),
        "activity": [
            {"id": e.id, "at": e.created_at.isoformat(), **e.data} for e in events
        ],
        "live_sending_enabled": False,
    }
