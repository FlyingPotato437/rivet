"""Approved lifecycle proposals enter the existing unsent Outlook delivery lane.

No caller supplies recipients or file paths. Approval, source and artifact identity
are rechecked by the worker immediately before writing the bound customer reply.
"""

import base64
import re
from urllib.parse import urlencode

from fastapi import HTTPException

from backend.company_context.service import require_writer
from backend.domain.service import digest, fail, get, scoped
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.records.delivery import workspace_url
from backend.records.email_intake import email_document_ids
from backend.reply_assistant import service as replies
from backend.reply_assistant.models import ReplyDraft
from backend.storage.models import Document, RecordIntake, Span

from . import adapters, service

KIND = "lifecycle_proposal"
INSTRUCTION = "Prepare the approved lifecycle proposal as an unsent customer reply."


def validate_handover_request(s, order, source_ids):
    """Optional closeout thread evidence must be an actual captured customer request."""
    from backend.reply_assistant import automatic

    for source_id in source_ids:
        source = get(s, Span, source_id)
        document = get(s, Document, source.document_id)
        if document.project_id != order.project_id or not re.search(
            r"\b(handover|closeout|as[- ]built|test reports?)\b",
            source.text,
            re.IGNORECASE,
        ):
            fail(
                "Cite this order's captured handover request, not an unrelated source.",
                409,
            )
        candidates = list(
            s.scalars(
                scoped(MailboxReceipt)
                .join(RecordIntake, MailboxReceipt.intake_id == RecordIntake.id)
                .where(
                    MailboxReceipt.status == "imported",
                    RecordIntake.order_id == order.id,
                    RecordIntake.data["document_id"].astext == document.id,
                )
                .limit(2)
            )
        )
        if len(candidates) != 1 or automatic.historical(candidates[0]):
            fail(
                "The handover request must identify one current captured customer email.",
                409,
            )
        _receipt(s, order, candidates[0].id)


def _revision(value):
    value = re.sub(
        r"^rev(?:ision)?\.?\s*", "", str(value or "").strip(), flags=re.IGNORECASE
    )
    value = value.casefold()
    return "" if value in {"", "unspecified", "unknown", "not supplied"} else value


def _matches_intake(s, order, package, intake):
    """Match real captured provenance, or the confirmed order/returned revision.

    A quote has no revision and therefore always needs source provenance. A
    revision match uses the saved confirmed intake, never subject guesses or a
    most-recent-thread fallback.
    """
    if intake.order_id != order.id or intake.status not in {"matched", "imported"}:
        return False
    if package["operation"] == "revision_review":
        revision = _revision(intake.data.get("revision_label"))
        if revision:
            return revision == _revision(package["payload"].get("revision"))
    root_id = intake.data.get("document_id")
    if not root_id:
        return False
    try:
        root = get(s, Document, root_id)
    except HTTPException:
        return False
    documents = {source["document_id"] for source in package["sources"].values()}
    if package["operation"] == "closeout":
        documents.update(document["id"] for document in package["payload"]["documents"])
    return bool(documents.intersection(email_document_ids(s, order, root)))


def commercial_work_for_receipt(s, order, receipt, *, preserve_existing=True):
    """Current commercial work owns its matching reply until a person decides.

    This does not pause the mailbox or unrelated source-only replies. It also
    does not alter an Outlook draft that already exists, including human edits.
    """
    from backend.autopilot.models import OutlookDraft

    if not order.state.get("lifecycle", {}).get("packages") or not receipt.intake_id:
        return []
    intake = get(s, RecordIntake, receipt.intake_id)
    if receipt.status != "imported" or intake.order_id != order.id:
        return []
    existing = s.scalar(
        scoped(OutlookDraft)
        .where(
            OutlookDraft.receipt_id == receipt.id,
            OutlookDraft.graph_draft_id != "",
        )
        .limit(1)
    )
    if preserve_existing and existing:
        return []
    return [
        package
        for package in service.view(s, order)["packages"]
        if package["status"] not in {"rejected", "superseded", "stale"}
        and package["evidence_current"]
        and (
            package["operation"] in {"quote", "closeout"}
            or (
                package["operation"] == "revision_review"
                and package["payload"].get("change_order", {}).get("lines")
            )
        )
        and _matches_intake(s, order, package, intake)
    ]


def waiting_state(packages):
    gates = sorted({gate for package in packages for gate in package["pending_gates"]})
    labels = {
        "money": "price",
        "scope": "scope",
        "engineering": "engineering",
        "customer_commitment": "customer commitment",
    }
    return {
        "status": "waiting_for_approval" if gates else "needs_thread",
        "detail": (
            "The response and commercial proposal are prepared. Check the "
            + ", ".join(labels.get(gate, gate.replace("_", " ")) for gate in gates)
            + " decisions before Rivet places the proposal in Outlook."
            if gates
            else "The commercial proposal is prepared. Choose its captured customer email before placing the approved PDFs in Outlook."
        ),
        "automatic": True,
        "package_ids": [package["id"] for package in packages],
        "pending_gates": gates,
    }


def _package(s, order, package_id):
    package = next(
        (p for p in service.view(s, order)["packages"] if p["id"] == package_id), None
    )
    if not package:
        fail("Prepared work not found.", 404)
    if package["operation"] not in {"quote", "revision_review", "closeout"}:
        fail(
            "Only a quotation, customer revision response or approved handover can be drafted in Outlook.",
            409,
        )
    required = (
        {"customer_commitment", "money", "scope"}
        if package["operation"] == "quote"
        else {"engineering", "customer_commitment"}
    )
    if (
        package["operation"] == "revision_review"
        and package["payload"]["change_order"]["lines"]
    ):
        required |= {"money", "scope"}
    if (
        package["status"] != "approved"
        or not package["evidence_current"]
        or package["pending_gates"]
        or package["blockers"]
        or not required <= set(package["gates"])
    ):
        fail(
            "Complete the current engineering and commercial decisions before placing this proposal in Outlook.",
            409,
        )
    fingerprint = digest(
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
    if fingerprint != package["fingerprint"]:
        fail("The approved package changed. Prepare and review it again.", 409)
    decisions = {
        d["gate"]: d for d in package["decisions"] if d["decision"] == "approve"
    }
    for gate in required | set(package["gates"]):
        decision = decisions.get(gate)
        if (
            not decision
            or decision["fingerprint"] != fingerprint
            or service._sources(s, order, decision["source_ids"])
            != decision["evidence_hashes"]
        ):
            fail("A required approval or its evidence is no longer current.", 409)
    return package


def _manifests(package):
    names = {
        "quote": ["quote.pdf"],
        "revision_review": ["response-letter.pdf"],
        "closeout": ["handover-index.pdf", "handover-documents.zip"],
    }[package["operation"]]
    if (
        package["operation"] == "revision_review"
        and package["payload"]["change_order"]["lines"]
    ):
        names.append("change-order.pdf")
    result = []
    for name in names:
        manifest = next(
            (
                a
                for a in package["artifacts"]
                if a["name"] == name
                and a["media_type"]
                == ("application/zip" if name.endswith(".zip") else "application/pdf")
            ),
            None,
        )
        if not manifest:
            fail(
                "The customer PDF is not available. Prepare a new package before creating the Outlook draft.",
                409,
            )
        result.append(manifest)
    return result


def _files(s, order, package):
    from backend.autopilot.outlook import MAX_INLINE_ATTACHMENT

    files, raw_files = [], {}
    for manifest in _manifests(package):
        try:
            raw = adapters.read_artifact(
                order.organization_id, order.id, package["id"], manifest
            )
        except OSError:
            fail(
                "The approved customer PDF could not be read. Check the stored file before creating a draft.",
                409,
            )
        signature = (
            b"PK\x03\x04" if manifest["media_type"] == "application/zip" else b"%PDF-"
        )
        if not raw.startswith(signature) or len(raw) > MAX_INLINE_ATTACHMENT:
            fail(
                "A customer file cannot be attached safely within Outlook's supported size. Review the file before continuing.",
                409,
            )
        files.append(
            {
                "name": manifest["name"],
                "sha256": manifest["sha256"],
                "size_bytes": len(raw),
                "content_type": manifest["media_type"],
                "base64": base64.b64encode(raw).decode(),
                "package_id": package["id"],
            }
        )
        raw_files[manifest["name"]] = raw
    if package["operation"] == "closeout":
        from .closeout import render_bundle

        # Rebuild from the currently approved physical bytes. A modified archive
        # cannot smuggle internal records or draft drawings into a handover.
        expected = render_bundle(s, order, package, raw_files["handover-index.pdf"])
        if expected != raw_files["handover-documents.zip"]:
            fail("The handover archive differs from the approved source files.", 409)
    return files


def _receipt(s, order, receipt_id):
    from backend.autopilot import service as autopilot

    receipt = get(s, MailboxReceipt, receipt_id)
    connection = get(s, MailboxConnection, receipt.connection_id)
    intake = get(s, RecordIntake, receipt.intake_id) if receipt.intake_id else None
    if (
        not connection.enabled
        or not receipt.provider_message_id
        or not receipt.internet_message_id
        or receipt.status != "imported"
        or not intake
        or intake.order_id != order.id
        or intake.status not in {"matched", "imported"}
        or autopilot._newer_in_thread(s, receipt)
    ):
        fail(
            "Choose the current captured customer email for this order and an enabled mailbox.",
            409,
        )
    return receipt, connection, intake


def _binding(receipt, connection):
    return {
        "receipt_id": receipt.id,
        "connection_id": connection.id,
        "provider_message_id": receipt.provider_message_id,
        "internet_message_id": receipt.internet_message_id,
        "conversation_id": receipt.data.get("conversation_id", ""),
        "mailbox": connection.mailbox,
        "microsoft_tenant_id": connection.microsoft_tenant_id,
    }


def _approval_hash(package):
    return digest([package["fingerprint"], package["decisions"], _manifests(package)])


def validate(s, order, reply):
    """Recheck the full commercial handoff, independent of general reply context."""
    saved = reply.context["lifecycle_handoff"]
    package = _package(s, order, saved["package_id"])
    receipt, connection, intake = _receipt(s, order, saved["receipt_id"])
    if (
        _approval_hash(package) != saved["approval_hash"]
        or _binding(receipt, connection) != saved["mail_binding"]
        or reply.internet_message_id != receipt.internet_message_id
        or package["operation"] == "closeout"
        and not _matches_intake(s, order, package, intake)
        or digest([reply.subject, reply.body, reply.source_refs]) != saved["reply_hash"]
    ):
        fail(
            "The approved work or its customer email changed before Outlook delivery.",
            409,
        )
    return _files(s, order, package)


def enqueue(s, order, package_id, receipt_id):
    from backend.autopilot import service as autopilot

    require_writer()
    package = _package(s, order, package_id)
    receipt, connection, intake = _receipt(s, order, receipt_id)
    if package["operation"] == "closeout" and not _matches_intake(
        s, order, package, intake
    ):
        fail(
            "Choose the captured request or handover files cited by this approved handover.",
            409,
        )
    _files(s, order, package)  # Fail before queuing if an immutable artifact changed.
    key = "lifecycle-mail:" + digest(
        [order.id, package_id, receipt.id, _approval_hash(package)]
    )
    previous = s.scalar(scoped(ReplyDraft).where(ReplyDraft.request_key == key))
    if previous:
        return {
            "status": "queued",
            "reply_id": previous.id,
            "package_id": package_id,
            "draft": autopilot.draft_for_message(s, receipt.internet_message_id),
        }
    if not autopilot.settings(s)["draft_in_outlook"]:
        fail("Automatic Outlook drafts are switched off for this workspace.", 409)
    root = get(s, Document, intake.data.get("document_id", ""))
    focused = email_document_ids(s, order, root)
    if not focused:
        fail("The captured email source is outside this order.", 409)
    w, data, company, evidence_hash = replies.context(s, order, INSTRUCTION, focused)
    documents = {d["id"]: d for d in w["documents"]}
    sources = {source["id"]: source for source in w["sources"]}
    refs = [
        {
            "id": sid,
            "label": documents[sources[sid]["document_id"]]["name"],
            "url": workspace_url()
            + "#order/"
            + order.id
            + "?"
            + urlencode({"tab": "work", "source": sid}),
        }
        for sid in sorted(package["sources"])
    ]
    body = (
        "DRAFT HANDOVER — for your review.\n\n"
        if package["operation"] == "closeout"
        else "DRAFT PROPOSAL — for your review.\n\n"
    ) + package["payload"]["email_draft"]["body"]
    if (
        package["operation"] == "revision_review"
        and package["payload"]["change_order"]["lines"]
    ):
        body += (
            "\n\nProposed change-order amount: USD "
            + package["payload"]["change_order"]["total"]
            + "."
        )
    if package["operation"] != "closeout":
        body += "\n\nThis proposal does not authorize production or change an accepted order without the required written acceptance."
    row = ReplyDraft(
        order_id=order.id,
        request_key=key,
        request_hash=digest([package_id, receipt_id]),
        internet_message_id=receipt.internet_message_id,
        status="ready",
        evidence_hash=evidence_hash,
        model="deterministic-approved-package",
        subject=package["payload"]["email_draft"]["subject"],
        body=body,
        original_body=body,
        source_refs=refs,
        context={
            "instruction": INSTRUCTION,
            "focus_document_ids": focused,
            "current_order": data,
            "company": company,
            "email": {
                k: receipt.data.get(k, "")
                for k in ("subject", "sender", "conversation_id")
            },
            "lifecycle_handoff": {
                "kind": KIND,
                "package_id": package_id,
                "receipt_id": receipt.id,
                "approval_hash": _approval_hash(package),
                "mail_binding": _binding(receipt, connection),
                "reply_hash": digest(
                    [package["payload"]["email_draft"]["subject"], body, refs]
                ),
            },
        },
        coverage={
            "origin": KIND,
            "package_id": package_id,
            "required_artifacts": [a["name"] for a in _manifests(package)],
        },
    )
    s.add(row)
    s.flush()
    return {
        "status": "queued",
        "reply_id": row.id,
        "package_id": package_id,
        "draft": None,
    }


def enqueue_if_unambiguous(s, order, package):
    """Use source provenance or confirmed revision; never choose a latest thread."""
    from backend.reply_assistant import automatic

    if package["operation"] not in {"quote", "revision_review", "closeout"}:
        return {"status": "not_applicable"}
    candidates = []
    for intake in s.scalars(
        scoped(RecordIntake).where(
            RecordIntake.order_id == order.id,
            RecordIntake.status.in_(["matched", "imported"]),
        )
    ):
        if not _matches_intake(s, order, package, intake):
            continue
        for receipt in s.scalars(
            scoped(MailboxReceipt).where(
                MailboxReceipt.intake_id == intake.id,
                MailboxReceipt.status == "imported",
            )
        ):
            try:
                _receipt(s, order, receipt.id)
                if automatic.historical(receipt) or automatic.acknowledgement_only(
                    s, intake
                ):
                    continue
                candidates.append(receipt.id)
            except HTTPException:
                continue
    if len(candidates) != 1:
        return {"status": "needs_thread", "candidate_count": len(candidates)}
    try:
        with s.begin_nested():
            return enqueue(s, order, package["id"], candidates[0])
    except HTTPException as exc:
        return {"status": "blocked", "message": str(exc.detail)}


def statuses_for_order(s, order):
    """One bounded projection; the approved package snapshot remains immutable."""
    from backend.autopilot.models import OutlookDraft
    from backend.autopilot.service import view

    rows = s.execute(
        scoped(ReplyDraft)
        .add_columns(OutlookDraft)
        .outerjoin(
            OutlookDraft,
            (OutlookDraft.reply_draft_id == ReplyDraft.id)
            & (OutlookDraft.organization_id == order.organization_id),
        )
        .where(
            ReplyDraft.order_id == order.id,
            ReplyDraft.request_key.like("lifecycle-mail:%"),
        )
        .order_by(ReplyDraft.created_at.desc())
        .limit(100)
    )
    result = {}
    for reply, native in rows:
        handoff = reply.context.get("lifecycle_handoff", {})
        package_id = handoff.get("package_id")
        if package_id and package_id not in result:
            result[package_id] = {
                "status": native.status if native else "queued",
                "reply_id": reply.id,
                "receipt_id": handoff["receipt_id"],
                "internet_message_id": reply.internet_message_id,
                "draft": view(native) if native else None,
            }
    return result
