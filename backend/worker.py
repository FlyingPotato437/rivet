"""Separate durable worker. No request-scoped background tasks."""

import time, threading, logging
from datetime import timedelta
from sqlalchemy import or_, and_, update
from backend.storage.db import Session
from backend.storage.models import Job, Document, Run, now, uid
from backend.domain.service import get, scoped
from backend.ingestion.parser import parse
from backend.agents.runner import run_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


def claim():
    with Session.begin() as s:
        exhausted = list(
            s.scalars(
                scoped(Job)
                .where(
                    Job.status == "running", Job.lease_until < now(), Job.attempts >= 3
                )
                .with_for_update(skip_locked=True)
            )
        )
        for dead in exhausted:
            dead.status = "failed"
            dead.error = "Worker lease expired after three attempts. Review saved progress before retrying."
            if dead.kind == "parse":
                get(s, Document, dead.payload["document_id"]).state = "failed"
            elif dead.kind == "agent":
                run = get(s, Run, dead.payload["run_id"])
                run.status = "failed"
                run.result = {"error": dead.error}
        j = s.scalar(
            scoped(Job)
            .where(
                or_(
                    Job.status == "queued",
                    and_(Job.status == "running", Job.lease_until < now()),
                ),
                Job.attempts < 3,
            )
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not j:
            return None
        j.status = "running"
        j.lease = uid()
        j.lease_until = now() + timedelta(seconds=120)
        j.attempts += 1
        return j.id, j.lease, j.kind, j.payload


def process(task):
    id, token, kind, payload = task
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
                    .where(Job.id == id, Job.lease == token, Job.status == "running")
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
        elif kind == "agent":
            run_agent(payload["run_id"], leased)
        if leased():
            with Session.begin() as s:
                j = get(s, Job, id, True)
                if j.lease == token:
                    j.status = "succeeded"
                    j.lease_until = None
                    if kind == "agent":
                        state = get(s, Run, payload["run_id"])
                        if state.status in ("failed", "cancelled"):
                            j.status = state.status
                            j.error = state.result.get("error", "")
    except Exception as exc:
        with Session.begin() as s:
            j = get(s, Job, id, True)
            if j.lease == token:
                j.status = "failed"
                j.error = str(exc)[:1000]
                j.lease_until = None
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
        logging.warning("Job %s failed (%s)", id, type(exc).__name__)
    finally:
        stop.set()
        thread.join(timeout=1)


def main():
    logging.info("Rivet worker ready")
    while True:
        task = claim()
        if task:
            process(task)
        else:
            time.sleep(1)


if __name__ == "__main__":
    main()
