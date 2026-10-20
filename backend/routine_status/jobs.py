"""Durable local outbox with a committed send intent and reconciliation-only retry."""

from fastapi import HTTPException

from backend.domain.service import digest, get, scoped
from backend.orders.service import lock_order
from backend.reply_assistant.models import ReplyDraft
from backend.storage.db import Session
from backend.storage.models import Job

from . import service
from .sender import configured_sender


def _receipt_matches(value, key, payload):
    return (
        isinstance(value, dict)
        and value.get("id") == key
        and value.get("mode") == "local_simulation"
        and value.get("status") == "accepted_simulation"
        and value.get("fingerprint") == digest(payload)
        and value.get("recipient") == payload["recipient"]
    )


def process_job(payload, leased, *, sender=None):
    sender = sender or configured_sender()
    key = payload["send_key"]
    with Session.begin() as s:
        order = lock_order(s, payload["order_id"])
        job = s.scalar(scoped(Job).where(Job.key == key).with_for_update())
        if not job or job.payload.get("reply_id") != payload["reply_id"]:
            raise ValueError("The status outbox identity changed.")
        saved = dict(job.payload)
        phase = saved.get("phase", "queued")
        if phase in {"accepted_simulation", "blocked", "disabled"}:
            return {"status": phase}
        if not leased():
            return {"status": "lease_lost"}
        if sender.mode != "local_simulation":
            status = "uncertain" if phase in {"attempted", "uncertain"} else "disabled"
            job.payload = {**saved, "phase": status}
            service._event(
                s,
                order,
                status,
                {
                    "reply_id": payload["reply_id"],
                    "detail": "The prior simulated attempt still needs reconciliation; disabling its adapter does not prove it was unsent."
                    if status == "uncertain"
                    else "No customer sender is configured. The prepared response remains unsent.",
                },
            )
            return {"status": status}
        if phase in {"attempted", "uncertain"}:
            frozen = saved["frozen"]
        else:
            try:
                reply = get(s, ReplyDraft, payload["reply_id"])
                if reply.order_id != order.id or reply.status != "ready":
                    raise HTTPException(
                        409,
                        "Only this order's unchanged ready reply can enter the status outbox.",
                    )
                from backend.autopilot.models import OutlookDraft

                if s.scalar(
                    scoped(OutlookDraft).where(OutlookDraft.reply_draft_id == reply.id)
                ):
                    raise HTTPException(
                        409,
                        "An Outlook draft already owns this reply; a person must send it.",
                    )
                frozen = service.validate(s, order, reply, for_send=True)
            except HTTPException as exc:
                job.payload = {**saved, "phase": "blocked", "error": str(exc.detail)}
                service._event(
                    s,
                    order,
                    "blocked",
                    {"reply_id": payload["reply_id"], "detail": str(exc.detail)},
                )
                return {"status": "blocked"}
            # Commit before any transport side effect. Worker death now makes the
            # next attempt reconciliation-only, even if no receipt is found.
            job.payload = {**saved, "phase": "attempted", "frozen": frozen}
    result = None
    try:
        result = sender.lookup(key)
        if result is None and phase == "queued":
            if not leased():
                return {"status": "lease_lost"}
            # Recheck changes in the small window since the durable intent. The
            # order remains locked for the bounded simulator call.
            with Session.begin() as s:
                order = lock_order(s, payload["order_id"])
                reply = get(s, ReplyDraft, payload["reply_id"])
                current = service.validate(s, order, reply, for_send=True)
                if current != frozen:
                    raise HTTPException(
                        409, "Status evidence changed before the send attempt."
                    )
                result = sender.send(key, frozen)
    except Exception:  # noqa: BLE001 - any post-intent failure has an uncertain send outcome
        # An exception cannot establish that a provider did not accept a send.
        try:
            result = sender.lookup(key)
        except Exception:  # noqa: BLE001 - failed reconciliation must never trigger a resend
            result = None
    status = (
        "accepted_simulation" if _receipt_matches(result, key, frozen) else "uncertain"
    )
    with Session.begin() as s:
        order = lock_order(s, payload["order_id"])
        job = s.scalar(scoped(Job).where(Job.key == key).with_for_update())
        prior = job.payload.get("phase")
        if prior == "accepted_simulation":
            return {"status": prior}
        job.payload = {
            **job.payload,
            "phase": status,
            "receipt": result if status == "accepted_simulation" else None,
        }
        if prior != status:
            service._event(
                s,
                order,
                status,
                {
                    "reply_id": payload["reply_id"],
                    "send_key": key,
                    "mode": "local_simulation",
                    "recipient": frozen["recipient"],
                    "receipt": result if status == "accepted_simulation" else None,
                },
            )
    return {"status": status, "customer_mail_sent": False}
