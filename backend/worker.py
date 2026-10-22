"""Separate durable worker. No request-scoped background tasks."""

import logging
import threading
import time
from datetime import timedelta

from sqlalchemy import and_, or_, select, update

from backend.agents.runner import run_agent
from backend.domain.service import get
from backend.identity import Identity, identity_scope
from backend.ingestion.parser import parse
from backend.storage.db import Session
from backend.storage.models import Document, Job, Run, now, uid

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
# Graph delta links and SharePoint downloads can contain bearer-like query
# credentials. Keep HTTP transport URLs out of the worker's routine logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
COORDINATION_KINDS = ("agent", "record_agent", "comment_prepare", "vision_page")


def claim(*, kinds=None, exclude_kinds=()):
    with Session.begin() as s:
        exhausted = list(
            s.scalars(
                select(Job)
                .where(
                    Job.status == "running", Job.lease_until < now(), Job.attempts >= 3
                )
                .with_for_update(skip_locked=True)
            )
        )
        for dead in exhausted:
            with identity_scope(Identity(dead.organization_id, "Rivet worker")):
                dead.status = "failed"
                dead.error = "Worker lease expired after three attempts. Review saved progress before retrying."
                if dead.kind == "parse":
                    failed_document = get(s, Document, dead.payload["document_id"])
                    failed_document.state = "failed"
                    from backend.orders.service import refresh_project_orders

                    refresh_project_orders(s, failed_document.project_id)
                elif dead.kind in {"agent", "record_agent"}:
                    from backend.storage.models import RecordAgentRun

                    model = RecordAgentRun if dead.kind == "record_agent" else Run
                    run = get(s, model, dead.payload["run_id"])
                    if dead.kind != "record_agent" or run.state.get(
                        "generation", 0
                    ) == dead.payload.get("generation", 0):
                        run.status = "failed"
                        run.result = {"error": dead.error}
                elif dead.kind in {"inbound_email", "send_notice"}:
                    from backend.storage.models import InboundEmail, NoticeDelivery

                    model, key = (
                        (InboundEmail, "email_id")
                        if dead.kind == "inbound_email"
                        else (NoticeDelivery, "delivery_id")
                    )
                    row = get(s, model, dead.payload[key])
                    row.status = "failed"
                    row.error = dead.error
                elif dead.kind == "vision_page":
                    from backend.ingestion.vision import mark_failed
                    mark_failed(s,dead.payload)
                elif dead.kind == "comment_prepare":
                    from backend.records.agent_tasks import mark_failed

                    mark_failed(s, dead.payload["task_id"], dead.error)
                elif dead.kind == "reply_draft":
                    from backend.reply_assistant.service import mark_exhausted

                    mark_exhausted(s, dead.payload["draft_id"], dead.error)
                elif dead.kind == "engineering_redesign":
                    from backend.engineering.redesign_service import mark_failed

                    mark_failed(s, dead.payload, dead.error)
                s.flush()
        eligible = select(Job).where(
            or_(
                Job.status == "queued",
                and_(Job.status == "running", Job.lease_until < now()),
            ),
            Job.attempts < 3,
        )
        if kinds is not None:
            eligible = eligible.where(Job.kind.in_(kinds))
        if exclude_kinds:
            eligible = eligible.where(Job.kind.not_in(exclude_kinds))
        j = s.scalar(
            eligible.order_by(Job.created_at).with_for_update(skip_locked=True).limit(1)
        )
        if not j:
            return None
        j.status = "running"
        j.lease = uid()
        j.lease_until = now() + timedelta(seconds=120)
        j.attempts += 1
        return j.id, j.lease, j.kind, j.payload, j.organization_id


def process(task):
    with identity_scope(Identity(task[4], "Rivet worker")):
        return process_scoped(task)


def process_scoped(task):
    id, token, kind, payload, organization_id = task
    stop = threading.Event()

    def leased():
        with Session() as s:
            j = get(s, Job, id)
            return j.lease == token and j.status == "running"

    def heartbeat():
        while not stop.wait(25):
            with Session.begin() as s:
                s.execute(
                    update(Job)
                    .where(
                        Job.id == id,
                        Job.organization_id == organization_id,
                        Job.lease == token,
                        Job.status == "running",
                    )
                    .values(lease_until=now() + timedelta(seconds=120))
                )

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        if kind == "parse":
            with Session.begin() as s:
                j = get(s, Job, id, True)
                if j.lease != token:
                    return
                d = get(s, Document, payload["document_id"])
                if d.state == "queued":
                    parse(s, d)
                    from backend.orders.service import refresh_project_orders

                    refresh_project_orders(s, d.project_id)
        elif kind == "agent":
            run_agent(payload["run_id"], leased)
        elif kind == "record_agent":
            from backend.records.agent_service import process_run

            process_run(payload["run_id"], leased, payload.get("generation", 0))
        elif kind == "vision_page":
            from backend.ingestion.vision import process as process_vision

            process_vision(payload, leased)
        elif kind == "comment_prepare":
            from backend.records.agent_tasks import process as process_comment

            process_comment(payload["task_id"], leased)
        elif kind == "reply_draft":
            from backend.reply_assistant.service import process_draft

            process_draft(payload["draft_id"], leased)
        elif kind == "equipment_corrections":
            from backend.equipment.workflow import process as correct_equipment

            correct_equipment(payload, leased)
        elif kind == "engineering_redesign":
            from backend.engineering.redesign_service import process as redesign

            redesign(payload, leased)
        elif kind == "enterprise_publication":
            from backend.workflow_actions.publication import process as publish
            publish(payload["publication_id"],leased)
        elif kind == "workflow_actions":
            from backend.workflow_actions.jobs import process_job

            outcome = process_job(payload, leased)
            if outcome["status"] in {"partial", "incomplete"}:
                with Session.begin() as s:
                    job = get(s, Job, id, True)
                    if job.lease == token:
                        job.error = "Local enterprise actions are incomplete; prior receipts are retained for reconciliation."
                        job.status = "failed" if job.attempts >= 3 else "running"
                        job.lease_until = (
                            None
                            if job.status == "failed"
                            else now() + timedelta(seconds=10)
                        )
                return
        elif kind == "routine_status":
            from backend.routine_status.jobs import process_job

            outcome = process_job(payload, leased)
            if outcome["status"] == "uncertain":
                with Session.begin() as s:
                    job = get(s, Job, id, True)
                    if job.lease == token:
                        job.error = "Routine status simulation has an uncertain receipt. Reconciliation only; no send retry."
                        job.status = "failed" if job.attempts >= 3 else "running"
                        job.lease_until = (
                            None
                            if job.status == "failed"
                            else now() + timedelta(seconds=30)
                        )
                return
        elif kind == "connected_context":
            from backend.connected_context.service import synchronize

            synchronize(payload["order_id"], leased)
        elif kind in {"inbound_email", "send_notice"}:
            from backend.email_jobs import receive, send

            if leased():
                if kind == "inbound_email":
                    receive(payload["email_id"])
                else:
                    send(payload["delivery_id"])
        else:
            raise ValueError("Unsupported durable job type")
        if leased():
            with Session.begin() as s:
                j = get(s, Job, id, True)
                if j.lease == token:
                    j.status = "succeeded"
                    j.lease_until = None
                    if kind == "workflow_actions":
                        j.error = ""
                    if kind in {"agent", "record_agent"}:
                        from backend.storage.models import RecordAgentRun

                        model = RecordAgentRun if kind == "record_agent" else Run
                        state = get(s, model, payload["run_id"])
                        if state.status in ("failed", "cancelled"):
                            j.status = state.status
                            j.error = state.result.get("error", "")
                    elif kind == "reply_draft":
                        from backend.reply_assistant.models import ReplyDraft

                        draft = get(s, ReplyDraft, payload["draft_id"])
                        if draft.status == "failed":
                            j.status, j.error = "failed", draft.error
    except Exception as exc:  # noqa: BLE001 - record failure and isolate the durable work lane
        if kind in {"inbound_email", "send_notice"} and leased():
            from backend.email_jobs import record_failure

            record_failure(
                kind,
                payload,
                str(
                    getattr(
                        exc,
                        "detail",
                        "Email processing failed. Try again after checking configuration.",
                    )
                ),
            )
        with Session.begin() as s:
            j = get(s, Job, id, True)
            if j.lease == token:
                j.status = "failed"
                j.error = str(exc)[:1000]
                j.lease_until = None
                if kind == "vision_page":
                    from backend.ingestion.vision import mark_failed
                    mark_failed(s,payload)
                if kind == "comment_prepare":
                    from backend.records.agent_tasks import mark_failed

                    mark_failed(s, payload["task_id"], "Comment preparation stopped unexpectedly; saved evidence is retained.")
                if kind == "engineering_redesign":
                    from backend.engineering.redesign_service import mark_failed

                    mark_failed(s, payload, "Drawing preparation stopped; saved files and original documents are retained.")
                if kind == "record_agent":
                    from backend.storage.models import RecordAgentRun

                    run = get(s, RecordAgentRun, payload["run_id"], True)
                    if run.state.get("generation", 0) == payload.get(
                        "generation", 0
                    ) and run.status in {"queued", "running"}:
                        run.status = "failed"
                        run.result = {
                            **run.result,
                            "error": "Processing failed. Saved steps are retained; retry the run.",
                        }
                if kind == "parse":
                    d = get(s, Document, payload["document_id"])
                    d.state = "failed"
                    d.coverage = [
                        {
                            "label": "Document",
                            "state": "failed",
                            "detail": str(exc)[:300],
                        }
                    ]
                    from backend.orders.service import refresh_project_orders

                    refresh_project_orders(s, d.project_id)
                elif kind == "reply_draft":
                    from backend.reply_assistant.service import mark_exhausted

                    mark_exhausted(
                        s,
                        payload["draft_id"],
                        "Reply preparation was interrupted. Generate a fresh draft to retry.",
                    )
        logger.warning("Job %s failed (%s)", id, type(exc).__name__)
    finally:
        stop.set()
        thread.join(timeout=1)


def mailbox_loop(stop):
    # A long model run must not prevent polling the mailbox. Each poll owns its
    # own database sessions and durable leases; multiple workers are safe.
    while not stop.is_set():
        try:
            from backend.connected_context.service import schedule

            schedule()
        except Exception as exc:  # noqa: BLE001 - record failure and isolate the durable work lane
            logger.warning("Connected sources need attention (%s)", type(exc).__name__)
        try:
            from backend.mailbox.service import poll_due_mailboxes

            poll_due_mailboxes(limit=2, receipt_limit=5)
        except Exception as exc:  # noqa: BLE001 - record failure and isolate the durable work lane
            logger.warning("Mailbox reconciliation failed (%s)", type(exc).__name__)
        try:
            from backend.reply_assistant.automatic import schedule as schedule_replies

            schedule_replies(limit=10)
            from backend.reply_assistant.order_packet import (
                schedule as schedule_packets,
            )

            schedule_packets(limit=5)
        except Exception as exc:  # noqa: BLE001 - record failure and isolate the durable work lane
            logger.warning(
                "Automatic reply preparation needs attention (%s)", type(exc).__name__
            )
        try:
            from backend.autopilot.service import deliver, sync_sent

            deliver(limit=5)
            sync_sent(limit=10)
        except Exception as exc:  # noqa: BLE001 - record failure and isolate the durable work lane
            logger.warning("Autopilot needs attention (%s)", type(exc).__name__)
        try:
            from backend.mailbox.updates import schedule_and_deliver_updates

            schedule_and_deliver_updates(limit=5)
        except Exception as exc:  # noqa: BLE001 - record failure and isolate the durable work lane
            logger.warning("Internal mailbox updates failed (%s)", type(exc).__name__)
        stop.wait(15)


def learning_loop(stop):
    """Fit local task models without occupying the durable document/job lane."""
    while not stop.is_set():
        try:
            from backend.improvement.adaptive import scheduled_pass

            scheduled_pass()
        except Exception as exc:  # noqa: BLE001 - learning cannot stop inbox work
            logger.warning("Learning refresh needs attention (%s)", type(exc).__name__)
        stop.wait(300)


def communication_loop(stop):
    """Slow Sent Items reads cannot block incoming mail capture or preparation."""
    while not stop.is_set():
        try:
            from backend.project_flow.service import sync

            sync(limit=5)
            from backend.equipment.workflow import sync_outcomes

            sync_outcomes(limit=10)
            from backend.project_flow.followups import scheduled_pass as follow_up
            follow_up()
            from backend.notifications.delivery import scheduled_pass as notify
            notify()
        except Exception as exc:  # noqa: BLE001 - isolate the communication lane
            logger.warning(
                "Project communication sync needs attention (%s)", type(exc).__name__
            )
        stop.wait(15)


def main():
    logger.info("Rivet worker ready")
    mail_stop = threading.Event()
    threading.Thread(target=mailbox_loop, args=(mail_stop,), daemon=True).start()
    for _ in range(4):
        threading.Thread(
            target=coordination_loop, args=(mail_stop,), daemon=True
        ).start()
    threading.Thread(target=learning_loop, args=(mail_stop,), daemon=True).start()
    threading.Thread(target=communication_loop, args=(mail_stop,), daemon=True).start()
    next_coordination = 0.0
    while True:
        if time.monotonic() >= next_coordination:
            try:
                from backend.records.agent_service import (
                    scheduled_pass as schedule_agents,
                )
                from backend.records.automation import scheduled_pass

                scheduled_pass()
                schedule_agents()
                from backend.mailbox.notifications import renew_due
                renew_due()
            except Exception as exc:  # noqa: BLE001 - record failure and isolate the durable work lane
                logger.warning("Scheduled coordination failed (%s)", type(exc).__name__)
            next_coordination = time.monotonic() + 60
        task = claim(exclude_kinds=COORDINATION_KINDS)
        if task:
            process(task)
        else:
            time.sleep(1)


def coordination_loop(stop):
    """Long research tasks never occupy the email/PDF/reply processing lane."""
    while not stop.is_set():
        try:
            task = claim(kinds=COORDINATION_KINDS)
            if task:
                process(task)
                continue
        except Exception as exc:  # noqa: BLE001 - isolate a durable worker lane
            logger.warning("Coordination worker failed (%s)", type(exc).__name__)
        stop.wait(1)


def equipment_main():
    """Run only native-file corrections and their known Outlook outcomes."""
    logger.info("Rivet equipment correction worker ready")
    next_outcomes = 0.0
    while True:
        if time.monotonic() >= next_outcomes:
            try:
                from backend.equipment.workflow import sync_outcomes

                sync_outcomes(limit=10)
            except Exception as exc:  # noqa: BLE001 - isolate provider outages
                logger.warning("Correction outcome sync needs attention (%s)", type(exc).__name__)
            next_outcomes = time.monotonic() + 60
        task = claim(kinds=("equipment_corrections",))
        if task:
            process(task)
        else:
            time.sleep(2)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=("all", "equipment"), default="all")
    arguments = parser.parse_args()
    equipment_main() if arguments.lane == "equipment" else main()
