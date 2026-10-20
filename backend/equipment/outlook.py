"""Draft-only Outlook handoff of a current, rechecked correction package.

The reservation is committed before the first provider write. Unknown outcomes
stop for reconciliation; retries never replace a person's draft or blindly
repeat attachment writes. This module has no send or delete operation.
"""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from uuid import uuid4

from fastapi import HTTPException

from backend.autopilot import outlook as graph_mail
from backend.autopilot.service import _client
from backend.company_context.service import require_writer
from backend.domain.service import fail, get, scoped
from backend.identity import current_org
from backend.mailbox.graph import GraphError
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.storage import db
from backend.storage.models import Order, OrderEvent, RecordIntake, now

MAX_TOTAL_BYTES = 7 * 1024 * 1024
MAX_THREAD_CHECK_PAGES = 4
MAX_THREAD_CHECK_ITEMS = 400
PUBLIC_KEYS = {
    "id",
    "status",
    "connection_id",
    "web_link",
    "rest_item_id",
    "attachments",
    "notes",
    "error",
    "drafted_at",
    "threaded",
    "mailbox",
    "bundle_id",
    "checked_at",
    "sent_at",
    "attachments_verified",
    "outcome",
}


def _provider(s, order, bundle_id):
    from .workflow import outlook_bundle

    return outlook_bundle(s, order, bundle_id)


def metadata(s, order):
    """Public delivery metadata only; local paths and message IDs stay private."""
    owned = get(s, Order, order if isinstance(order, str) else order.id)
    return {
        key: {k: deepcopy(v) for k, v in row.items() if k in PUBLIC_KEYS}
        for key, row in owned.state.get("equipment_outlook", {}).items()
        if isinstance(row, dict)
    }


def _put(order, bundle_id, row):
    deliveries = deepcopy(order.state.get("equipment_outlook", {}))
    deliveries[bundle_id] = deepcopy(row)
    order.state = {**order.state, "equipment_outlook": deliveries}


def _checkpoint(order_id, bundle_id, expected_lease, **values):
    with db.Session.begin() as s:
        order = get(s, Order, order_id, True)
        row = deepcopy(order.state.get("equipment_outlook", {}).get(bundle_id, {}))
        if row.get("lease") != expected_lease:
            fail("Another request owns this Outlook handoff. Open Outlook Drafts.", 409)
        row.update(values)
        _put(order, bundle_id, row)
        return row


def _files(bundle, order_id, bundle_id):
    if (
        bundle.get("id") != bundle_id
        or bundle.get("order_id") != order_id
        or bundle.get("status") != "draft"
        or bundle.get("current") is not True
        or bundle.get("engineering_approved") is not False
        or bundle.get("production_released") is not False
        or bundle.get("recheck", {}).get("summary", {}).get("outcome")
        != "supported_fields_match"
        or not bundle.get("fingerprint")
    ):
        fail(
            "Only this order's current, rechecked draft copies can be saved to Outlook.",
            409,
        )
    outputs = bundle.get("outputs", [])
    if not isinstance(outputs, list) or not 3 <= len(outputs) <= 8:
        fail(
            "The correction package needs its native drawing, BOM and checked PDF.", 409
        )
    files, names, ids, suffixes = [], set(), set(), set()
    owned_root = (
        db.BLOBS / "equipment-corrections" / current_org() / order_id / bundle_id
    ).resolve()
    total = 0
    for item in outputs:
        name = item.get("name", "")
        # The server accessor supplies these paths; clients cannot supply one.
        path = Path(item.get("path", ""))
        resolved = path.resolve()
        if (
            not name
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or name in names
            or not item.get("id")
            or item["id"] in ids
            or not resolved.is_relative_to(db.BLOBS.resolve())
            or not resolved.is_relative_to(owned_root)
            or not resolved.is_file()
            or path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents)
        ):
            fail(
                "A correction attachment is unavailable or outside this order's owned storage.",
                409,
            )
        suffix = Path(name).suffix.lower()
        if suffix not in {".dxf", ".xlsx", ".csv", ".pdf"}:
            # Manifests are internal evidence, never incidental mail attachments.
            continue
        data = resolved.read_bytes()
        checksum = sha256(data).hexdigest()
        if not data or checksum != item.get("sha256"):
            fail(
                "A corrected file changed after verification. Recheck the package before drafting.",
                409,
            )
        if len(data) > graph_mail.MAX_INLINE_ATTACHMENT:
            fail(
                "A corrected file exceeds Outlook's inline attachment limit. No partial package was drafted.",
                409,
            )
        total += len(data)
        names.add(name)
        ids.add(item["id"])
        suffixes.add(suffix)
        files.append(
            {
                "id": item["id"],
                "name": name,
                "sha256": checksum,
                "size_bytes": len(data),
                "content_type": item.get("content_type") or "application/octet-stream",
                "base64": base64.b64encode(data).decode("ascii"),
            }
        )
    if (
        ".dxf" not in suffixes
        or ".pdf" not in suffixes
        or not ({".xlsx", ".csv"} & suffixes)
        or total > MAX_TOTAL_BYTES
    ):
        fail(
            "Save requires a complete native drawing, BOM and checked PDF package within Outlook's limits.",
            409,
        )
    return files


def _receipt(s, order, connection_id):
    return s.scalar(
        scoped(MailboxReceipt)
        .join(RecordIntake, RecordIntake.id == MailboxReceipt.intake_id)
        .where(
            RecordIntake.organization_id == order.organization_id,
            RecordIntake.order_id == order.id,
            MailboxReceipt.connection_id == connection_id,
            MailboxReceipt.status == "imported",
        )
        .order_by(MailboxReceipt.last_seen_at.desc(), MailboxReceipt.id.desc())
        .limit(1)
    )


def _received_time(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            raise ValueError()
        return stamp.astimezone(UTC)
    except (AttributeError, TypeError, ValueError):
        raise GraphError(
            "provider_error",
            "Microsoft did not return a reliable received time. Check inbox now before preparing this reply.",
        ) from None


def _assert_thread_current(graph, original_id):
    """Read only this conversation before writes, including uncaptured mail.

    GET /users/{authorized mailbox}/messages supports a conversation filter.
    We deliberately avoid orderby (Graph's combined-filter ordering constraints)
    and inspect every bounded page. No bodies or unrelated conversations are read.
    https://learn.microsoft.com/en-us/graph/api/user-list-messages
    """
    selected = "id,isDraft,conversationId,receivedDateTime,parentFolderId"
    original = graph_mail._call(
        graph,
        "GET",
        graph_mail._url(graph, "messages", original_id)
        + "?"
        + urlencode({"$select": selected}),
    )
    conversation = (
        original.get("conversationId") if isinstance(original, dict) else None
    )
    if (
        not isinstance(original, dict)
        or original.get("id") != original_id
        or original.get("isDraft") is not False
        or not isinstance(conversation, str)
        or not 0 < len(conversation) <= 1000
        or any(ord(character) < 32 for character in conversation)
        or not isinstance(original.get("parentFolderId"), str)
        or not original["parentFolderId"]
    ):
        raise GraphError(
            "provider_error",
            "Microsoft did not identify the captured customer conversation completely. Check inbox now before preparing this reply.",
        )
    received = _received_time(original.get("receivedDateTime"))
    sent = graph_mail._call(
        graph, "GET", graph_mail._url(graph, "mailFolders", "sentitems") + "?$select=id"
    )
    sent_folder = sent.get("id") if isinstance(sent, dict) else None
    if (
        not isinstance(sent_folder, str)
        or not sent_folder
        or original["parentFolderId"] == sent_folder
    ):
        raise GraphError(
            "provider_error",
            "Rivet could not establish the received customer email. Check inbox now before preparing this reply.",
        )
    expression = "conversationId eq '" + conversation.replace("'", "''") + "'"
    base = graph_mail._url(graph, "messages")
    link = (
        base
        + "?"
        + urlencode({"$filter": expression, "$select": selected, "$top": "100"})
    )
    cursors, identities, found, count = set(), set(), False, 0
    for _ in range(MAX_THREAD_CHECK_PAGES):
        if link in cursors:
            raise GraphError(
                "provider_error",
                "Microsoft repeated the conversation cursor. Check inbox now; Rivet has not confirmed that the reply is current.",
            )
        cursors.add(link)
        result = graph_mail._call(graph, "GET", link)
        values = result.get("value") if isinstance(result, dict) else None
        if not isinstance(values, list):
            raise GraphError(
                "provider_error",
                "Microsoft did not return the conversation check. Check inbox now before preparing this reply.",
            )
        count += len(values)
        if count > MAX_THREAD_CHECK_ITEMS:
            raise GraphError(
                "provider_error",
                "This conversation is too large to check completely. Refresh and review the customer thread before preparing a reply.",
            )
        for item in values:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("id"), str)
                or not item["id"]
                or item["id"] in identities
                or item.get("conversationId") != conversation
                or type(item.get("isDraft")) is not bool
            ):
                raise GraphError(
                    "provider_error",
                    "Microsoft returned incomplete or inconsistent conversation metadata. Check inbox now before preparing this reply.",
                )
            identities.add(item["id"])
            if item["isDraft"]:
                continue
            parent = item.get("parentFolderId")
            if not isinstance(parent, str) or not parent:
                raise GraphError(
                    "provider_error",
                    "Microsoft did not identify a conversation message's folder. Check inbox now before preparing this reply.",
                )
            if parent == sent_folder:
                continue
            timestamp = _received_time(item.get("receivedDateTime"))
            if item["id"] == original_id:
                if timestamp != received or parent != original["parentFolderId"]:
                    raise GraphError(
                        "provider_error",
                        "The captured email changed during the conversation check. Check inbox now before preparing this reply.",
                    )
                found = True
            elif timestamp >= received:
                fail(
                    "A newer customer email arrived in this Outlook conversation. Check inbox now so Rivet can refresh the order and prepare a reply to the latest email. Your existing draft is preserved.",
                    409,
                )
        following = result.get("@odata.nextLink")
        if not following:
            if not found:
                raise GraphError(
                    "provider_error",
                    "Microsoft did not include the captured customer email in the complete thread check. Check inbox now before preparing this reply.",
                )
            return {
                "conversation_id": conversation,
                "original_message_id": original_id,
                "received_at": received.isoformat(),
                "checked_messages": count,
            }
        try:
            target, expected = urlsplit(following), urlsplit(base)
            query = parse_qs(target.query)
            safe = (
                target.scheme == "https"
                and target.netloc == expected.netloc
                and unquote(target.path) == unquote(expected.path)
                and not target.fragment
                and not target.username
                and not target.password
                and query.get("$filter") == [expression]
                and query.get("$select") == [selected]
            )
        except (TypeError, ValueError):
            safe = False
        if not safe:
            raise GraphError(
                "invalid_cursor",
                "Microsoft returned an unexpected conversation cursor. Rivet stopped before further writes; check inbox now.",
            )
        link = following
    raise GraphError(
        "provider_error",
        "The full customer conversation could not be checked within the read limit. Check inbox now and review the latest email before preparing this reply.",
    )


def _fresh(
    order_id, bundle_id, fingerprint, connection_id, receipt_id, provider, *, graph=None
):
    with db.Session() as s:
        order = get(s, Order, order_id)
        bundle = provider(s, order, bundle_id)
        files = _files(bundle, order.id, bundle_id)
        latest = _receipt(s, order, connection_id)
        if (
            bundle.get("fingerprint") != fingerprint
            or (latest.id if latest else None) != receipt_id
        ):
            fail(
                "The order or customer email changed while preparing Outlook. The earlier draft was preserved.",
                409,
            )
        connection = get(s, MailboxConnection, connection_id)
        if not connection.enabled:
            fail("The selected Outlook connection is no longer enabled.", 409)
        original_id = latest.provider_message_id if latest else None
    if graph is not None and original_id:
        try:
            _assert_thread_current(graph, original_id)
        except GraphError:
            fail(
                "Rivet could not verify the latest Outlook conversation. Check inbox now and refresh the order before retrying. Rivet stopped before further changes and preserved any existing draft.",
                409,
            )
    return bundle, files


def _public(row):
    return {k: deepcopy(v) for k, v in row.items() if k in PUBLIC_KEYS}


def _open_target(graph, draft_id):
    message, target = graph_mail.open_target(graph, draft_id)
    return message, {
        "web_link": target.get("outlook_url", ""),
        "rest_item_id": target.get("rest_item_id", ""),
    }


def _verify_attachments(graph, draft_id, files):
    """Confirm actual provider bytes before claiming a complete handoff."""
    result = graph_mail._call(
        graph,
        "GET",
        graph_mail._url(graph, "messages", draft_id, "attachments") + "?$top=20",
    )
    values = result.get("value")
    if not isinstance(values, list) or result.get("@odata.nextLink"):
        fail(
            "Outlook did not return a complete attachment inventory. Check the saved draft.",
            409,
        )
    for expected in files:
        matches = [
            v
            for v in values
            if isinstance(v, dict) and v.get("name") == expected["name"]
        ]
        if len(matches) != 1:
            fail(
                "Outlook's saved attachments do not match the correction package. The draft is preserved.",
                409,
            )
        actual = matches[0]
        if not actual.get("contentBytes") and actual.get("id"):
            actual = graph_mail._call(
                graph,
                "GET",
                graph_mail._url(
                    graph, "messages", draft_id, "attachments", actual["id"]
                ),
            )
        try:
            data = base64.b64decode(actual.get("contentBytes", ""), validate=True)
        except (ValueError, TypeError):
            data = b""
        if sha256(data).hexdigest() != expected["sha256"]:
            fail(
                "The actual Outlook attachment differs from the checked file. The draft is preserved for review.",
                409,
            )


def save(
    order_id,
    bundle_id,
    connection_id=None,
    *,
    client_factory=None,
    bundle_provider=None,
):
    """Save real corrected files as an unsent reply or unaddressed native draft.

    Owns its short transactions. Call outside a caller's locked order transaction
    so the durable create reservation exists before Graph can accept a write.
    ``bundle_provider`` is a server-only test seam, never an API argument.
    """
    require_writer()
    provider = bundle_provider or _provider
    lease = str(uuid4())
    interrupted = False
    with db.Session.begin() as s:
        order = get(s, Order, order_id, True)
        bundle = provider(s, order, bundle_id)
        files = _files(bundle, order.id, bundle_id)
        previous = order.state.get("equipment_outlook", {})
        row = deepcopy(previous.get(bundle_id, {}))
        if row.get("status") in {
            "manual_attention",
            "sent",
            "sent_for_review",
            "discarded",
        }:
            fail(
                "The earlier draft needs a check in Outlook. Rivet will not replace it or create a duplicate.",
                409,
            )
        if row.get("status") == "creating":
            deadline = datetime.fromisoformat(
                row.get("lease_until", "1970-01-01T00:00:00+00:00")
            )
            if deadline > now():
                fail("This correction package is already being saved to Outlook.", 409)
            row.update(
                status="manual_attention",
                error="The prior save was interrupted. Check Outlook Drafts; no duplicate was created.",
                lease="",
            )
            _put(order, bundle_id, row)
            interrupted = True
        if not interrupted:
            enabled = list(
                s.scalars(
                    scoped(MailboxConnection).where(MailboxConnection.enabled.is_(True))
                )
            )
            chosen = (
                row.get("connection_id")
                or connection_id
                or (enabled[0].id if len(enabled) == 1 else None)
            )
            connection = next((c for c in enabled if c.id == chosen), None)
            if not connection:
                fail(
                    "Choose a connected Outlook mailbox."
                    if enabled
                    else "Connect Outlook before saving this draft.",
                    409,
                )
            receipt = _receipt(s, order, connection.id)
            # A file-only draft has no conversation to deduplicate through Graph.
            # Preserve any earlier correction draft for the order as well.
            other = next(
                (
                    v
                    for k, v in previous.items()
                    if k != bundle_id
                    and v.get("graph_draft_id")
                    and v.get("status")
                    in {"created", "creating", "existing_draft", "manual_attention"}
                ),
                None,
            )
            if other and not row.get("graph_draft_id"):
                fail(
                    "An earlier correction draft is preserved in Outlook. Review it before requesting a different package.",
                    409,
                )
            row = {
                **row,
                "id": row.get("id") or str(uuid4()),
                "bundle_id": bundle_id,
                "connection_id": connection.id,
                "mailbox": connection.mailbox,
                "fingerprint": bundle["fingerprint"],
                "source_fingerprint": bundle.get(
                    "source_fingerprint", bundle["fingerprint"]
                ),
                "receipt_id": receipt.id if receipt else None,
                "threaded": bool(receipt),
                "attachments": row.get("attachments", []),
                "expected_attachments": [
                    {k: f[k] for k in ("id", "name", "sha256", "size_bytes")}
                    for f in files
                ],
                "notes": row.get("notes", []),
                "lease": lease,
                "lease_until": (now() + timedelta(minutes=5)).isoformat(),
            }
            existing = row.get("graph_draft_id", "")
            if not existing:
                row.update(status="creating", phase="reserved", error="")
            _put(order, bundle_id, row)
            snapshot = {
                "organization_id": connection.organization_id,
                "mailbox": connection.mailbox,
                "tenant": connection.microsoft_tenant_id,
            }
            selected_id = connection.id
            original_id = receipt.provider_message_id if receipt else None
            subject = (
                bundle.get("subject")
                or f"{order.number} - revised drawing and BOM - draft"
            )
            body = (
                (
                    bundle.get("body")
                    or "Corrected drawing and BOM draft copies are attached for review."
                )
                + "\n\nDRAFT COPIES - not an engineering approval or authorization for production."
            )
    if interrupted:
        fail(
            "The earlier save was interrupted. Check Outlook Drafts; Rivet has not created another draft.",
            409,
        )

    graph, uncertain = None, False
    try:
        graph = (client_factory or _client)(snapshot)
        graph.headers()
        if existing:
            message, target = _open_target(graph, existing)
            if message.get("isDraft") is not True or graph_mail.is_deleted(
                graph, message
            ):
                _checkpoint(
                    order_id,
                    bundle_id,
                    lease,
                    status="sent" if message.get("isDraft") is False else "discarded",
                    lease="",
                )
                fail(
                    "The saved draft was sent or removed. Its history is preserved.",
                    409,
                )
            row = _checkpoint(order_id, bundle_id, lease, **target, lease="", error="")
            return {"bundle_id": bundle_id, "draft": _public(row)}

        if original_id:
            prior = graph_mail.existing_reply(graph, original_id)
            if prior:
                prior_message, target = _open_target(graph, prior["id"])
                if prior_message.get("isDraft") is not True or graph_mail.is_deleted(
                    graph, prior_message
                ):
                    fail(
                        "The earlier reply changed in Outlook. Check that conversation before drafting again.",
                        409,
                    )
                row = _checkpoint(
                    order_id,
                    bundle_id,
                    lease,
                    graph_draft_id=prior["id"],
                    status="existing_draft",
                    attachments=[],
                    **target,
                    notes=[
                        "Your existing reply was preserved. Rivet did not change its text or attach this correction package."
                    ],
                    lease="",
                    error="",
                )
                return {"bundle_id": bundle_id, "draft": _public(row)}
        _, files = _fresh(
            order_id,
            bundle_id,
            bundle["fingerprint"],
            selected_id,
            row["receipt_id"],
            provider,
            graph=graph,
        )
        _checkpoint(order_id, bundle_id, lease, phase="creating_message")
        uncertain = True
        if original_id:
            message = graph_mail._call(
                graph,
                "POST",
                graph_mail._url(graph, "messages", original_id, "createReply"),
                {
                    "message": {
                        "body": {
                            "contentType": "html",
                            "content": graph_mail.reply_html(body),
                        }
                    }
                },
            )
        else:
            message = graph_mail._call(
                graph,
                "POST",
                graph_mail._url(graph, "messages"),
                {
                    "subject": subject,
                    "toRecipients": [],
                    "body": {
                        "contentType": "html",
                        "content": graph_mail.reply_html(body),
                    },
                    "attachments": [
                        {
                            "@odata.type": "#microsoft.graph.fileAttachment",
                            "name": f["name"],
                            "contentType": f["content_type"],
                            "contentBytes": f["base64"],
                        }
                        for f in files
                    ],
                },
            )
        draft_id = message.get("id")
        if (
            not isinstance(draft_id, str)
            or not draft_id
            or message.get("isDraft") is not True
        ):
            raise GraphError(
                "unknown_outcome", "Microsoft did not return an unsent draft identity."
            )
        _checkpoint(
            order_id, bundle_id, lease, graph_draft_id=draft_id, phase="message_created"
        )
        uncertain = False
        attached = []
        if original_id:
            for file in files:
                _fresh(
                    order_id,
                    bundle_id,
                    bundle["fingerprint"],
                    selected_id,
                    row["receipt_id"],
                    provider,
                    graph=graph,
                )
                _checkpoint(
                    order_id,
                    bundle_id,
                    lease,
                    phase="attaching",
                    pending_attachment={k: file[k] for k in ("id", "name", "sha256")},
                )
                uncertain = True
                graph_mail.attach(
                    graph, draft_id, file["name"], file["content_type"], file["base64"]
                )
                uncertain = False
                attached.append(
                    {k: file[k] for k in ("id", "name", "sha256", "size_bytes")}
                )
                _checkpoint(
                    order_id,
                    bundle_id,
                    lease,
                    phase="attachment_saved",
                    attachments=attached,
                    pending_attachment=None,
                )
        else:
            attached = [
                {k: f[k] for k in ("id", "name", "sha256", "size_bytes")} for f in files
            ]
        _fresh(
            order_id,
            bundle_id,
            bundle["fingerprint"],
            selected_id,
            row["receipt_id"],
            provider,
            graph=graph,
        )
        _verify_attachments(graph, draft_id, files)
        message, target = _open_target(graph, draft_id)
        if message.get("isDraft") is not True or graph_mail.is_deleted(graph, message):
            fail(
                "The draft changed in Outlook while Rivet was saving it. Check Outlook Drafts.",
                409,
            )
        row = _checkpoint(
            order_id,
            bundle_id,
            lease,
            status="created",
            phase="complete",
            attachments=attached,
            drafted_at=now().isoformat(),
            **target,
            notes=[
                "Corrected draft DXF, BOM and checked PDF attached. No message was sent. Engineering approval and production release remain separate."
            ],
            error="",
            lease="",
        )
        return {"bundle_id": bundle_id, "draft": _public(row)}
    except (GraphError, HTTPException) as exc:
        with db.Session.begin() as s:
            order = get(s, Order, order_id, True)
            saved = deepcopy(
                order.state.get("equipment_outlook", {}).get(bundle_id, {})
            )
            if saved.get("lease") == lease:
                known_rejected = isinstance(exc, GraphError) and exc.status in {
                    400,
                    401,
                    403,
                    404,
                    413,
                    422,
                    429,
                }
                safe_retry = not saved.get("graph_draft_id") and (
                    not uncertain or known_rejected
                )
                saved.update(
                    status=saved.get("status")
                    if existing
                    else "permission_required"
                    if safe_retry and isinstance(exc, GraphError) and exc.status == 403
                    else "failed"
                    if safe_retry
                    else "manual_attention",
                    error=exc.detail
                    if isinstance(exc, HTTPException)
                    and isinstance(exc.detail, str)
                    and (
                        "newer customer email" in exc.detail
                        or "latest Outlook conversation" in exc.detail
                    )
                    else "Microsoft could not open the existing draft. Its text and files were preserved; try opening it again."
                    if existing
                    else "Microsoft could not save the draft. Check mailbox permissions."
                    if safe_retry
                    else "The draft or attachment outcome needs a check in Outlook. Rivet will not replace edits or create duplicate files.",
                    lease="",
                )
                _put(order, bundle_id, saved)
        if isinstance(exc, HTTPException):
            raise
        fail(
            "Outlook could not complete the draft handoff. Its saved state is preserved; check Outlook Drafts before retrying.",
            502,
        )
    finally:
        if graph:
            graph.close()


def _sent_time(value):
    try:
        stamp = datetime.fromisoformat(value)
        if (
            stamp.tzinfo is None
            or stamp.year < 2000
            or stamp > now() + timedelta(minutes=5)
        ):
            return None
        return stamp.astimezone(UTC)
    except (AttributeError, TypeError, ValueError):
        return None


def sync_one(order_id, bundle_id, *, client_factory=None):
    """Observe a person's send outcome; never interpret it as design approval."""
    with db.Session() as s:
        order = get(s, Order, order_id)
        saved = deepcopy(order.state.get("equipment_outlook", {}).get(bundle_id, {}))
        if not saved.get("graph_draft_id") or saved.get("status") not in {
            "created",
            "existing_draft",
            "manual_attention",
        }:
            return {"bundle_id": bundle_id, "draft": _public(saved)}
        connection = get(s, MailboxConnection, saved["connection_id"])
        if not connection.enabled:
            return {"bundle_id": bundle_id, "draft": _public(saved)}
        snapshot = {
            "organization_id": connection.organization_id,
            "mailbox": connection.mailbox,
            "tenant": connection.microsoft_tenant_id,
        }

    graph = None
    state, attachment_proof, sent_at = "unknown", None, None
    try:
        graph = (client_factory or _client)(snapshot)
        message = graph_mail.get_message(graph, saved["graph_draft_id"])
        sent_at = _sent_time(message.get("sentDateTime"))
        if message.get("isDraft") is False and sent_at:
            state = "sent_for_review"
            expected = saved.get("expected_attachments") or saved.get("attachments")
            if saved.get("status") != "existing_draft" and expected:
                try:
                    _verify_attachments(graph, saved["graph_draft_id"], expected)
                    attachment_proof = True
                except (GraphError, HTTPException):
                    attachment_proof = False
        elif graph_mail.is_deleted(graph, message):
            state = "discarded"
        elif message.get("isDraft") is True:
            state = "draft"
            expected = saved.get("expected_attachments") or saved.get("attachments")
            if saved.get("status") != "existing_draft" and expected:
                try:
                    _verify_attachments(graph, saved["graph_draft_id"], expected)
                    attachment_proof = True
                except HTTPException:
                    state, attachment_proof = "draft_changed", False
                except GraphError:
                    # A read failure does not establish that a person changed
                    # a file. Keep it unconfirmed until a later complete read.
                    state = "unknown"
    except GraphError as exc:
        state = "missing" if exc.code == "not_found" or exc.status == 404 else "unknown"
    finally:
        if graph:
            graph.close()

    with db.Session.begin() as s:
        order = get(s, Order, order_id, True)
        row = deepcopy(order.state.get("equipment_outlook", {}).get(bundle_id, {}))
        if row.get("graph_draft_id") != saved["graph_draft_id"] or row.get(
            "status"
        ) not in {"created", "existing_draft", "manual_attention"}:
            return {"bundle_id": bundle_id, "draft": _public(row)}
        row["checked_at"] = now().isoformat()
        if state == "missing":
            first = _sent_time(row.get("missing_since")) or now()
            row["missing_since"] = first.isoformat()
            if now() - first >= timedelta(minutes=10):
                row.update(
                    status="discarded",
                    outcome="removed",
                    error="The saved draft remained unavailable for at least ten minutes. No send was established.",
                )
        else:
            row["missing_since"] = None
            if state == "sent_for_review":
                row.update(
                    status="sent_for_review",
                    outcome="sent_for_review",
                    sent_at=sent_at.isoformat(),
                    attachments_verified=attachment_proof,
                    error=""
                    if attachment_proof is True
                    else "The message was sent, but its attachments were not verified against the prepared copies.",
                )
                event = OrderEvent(
                    order_id=order.id,
                    version=order.version,
                    kind="equipment_correction_outcome",
                    summary="Revised drawing package sent for review"
                    if attachment_proof
                    else "Revised package sent; attachment evidence needs checking",
                    actor="Rivet Outlook observation",
                    data={
                        "bundle_id": bundle_id,
                        "source_fingerprint": row.get(
                            "source_fingerprint", row.get("fingerprint")
                        ),
                        "delivery_fingerprint": row.get("fingerprint"),
                        "outcome": "sent_for_review",
                        "sent_at": row["sent_at"],
                        "attachments_verified": attachment_proof,
                        "outputs": deepcopy(
                            row.get("expected_attachments")
                            or row.get("attachments", [])
                        ),
                        "engineering_approved": False,
                        "production_released": False,
                        "learning_status": "outcome_recorded_only",
                    },
                )
                s.add(event)
                s.flush()
                row["outcome_event_id"] = event.id
            elif state == "discarded":
                row.update(
                    status="discarded",
                    outcome="removed",
                    error="The draft was removed in Outlook. No send was established.",
                )
            elif state == "draft" and attachment_proof is True:
                row.update(
                    status="created",
                    attachments_verified=True,
                    error="",
                )
            elif state == "draft_changed":
                row.update(
                    status="manual_attention",
                    attachments_verified=False,
                    error="Review changed attachments in Outlook. They no longer match Rivet's checked draft copies; your draft and edits were preserved.",
                )
            elif state == "unknown":
                row["error"] = (
                    "Outlook's current message state could not be confirmed. No send or deletion was inferred."
                )
        _put(order, bundle_id, row)
        return {"bundle_id": bundle_id, "draft": _public(row)}


def sync_due(limit=20, *, client_factory=None):
    """Poll only the current identity's tenant; the worker supplies that scope."""
    candidates = []
    with db.Session() as s:
        for order in s.scalars(
            scoped(Order).order_by(Order.created_at.desc()).limit(200)
        ):
            for bundle_id, row in order.state.get("equipment_outlook", {}).items():
                checked = _sent_time(row.get("checked_at"))
                if (
                    row.get("status")
                    in {"created", "existing_draft", "manual_attention"}
                    and row.get("graph_draft_id")
                    and (not checked or now() - checked >= timedelta(minutes=1))
                ):
                    candidates.append((order.id, bundle_id))
                    if len(candidates) >= max(1, min(limit, 50)):
                        break
            if len(candidates) >= max(1, min(limit, 50)):
                break
    return [
        sync_one(oid, bid, client_factory=client_factory) for oid, bid in candidates
    ]
