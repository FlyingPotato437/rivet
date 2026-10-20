"""Source synchronization precedes generation; every result has saved evidence."""

from datetime import timedelta

from sqlalchemy import text

from backend.domain.service import digest, get, scoped
from backend.identity import Identity, identity_scope
from backend.orders.service import lock_order
from backend.storage.db import Session, engine
from backend.storage.models import Document, Job, Order, Span, now, uid

from . import providers
from .models import ConnectedContext


def row_for(s, order):
    return s.scalar(
        scoped(ConnectedContext).where(ConnectedContext.order_id == order.id)
    )


def enqueue(s, order):
    bindings = providers.bindings(order.number)
    if not bindings:
        return False
    row = row_for(s, order)
    if not row:
        row = ConnectedContext(order_id=order.id)
        s.add(row)
        s.flush()
    active = s.scalar(
        scoped(Job).where(
            Job.project_id == order.project_id,
            Job.kind == "connected_context",
            Job.status.in_(["queued", "running"]),
        )
    )
    if active:
        return False
    # Keep the last successful snapshot visible while refreshing, but prohibit
    # generation against it until the check completes.
    row.status, row.error = "pending", ""
    s.add(
        Job(
            project_id=order.project_id,
            kind="connected_context",
            key="connected-context:" + uid(),
            payload={"order_id": order.id},
        )
    )
    s.flush()
    return True


def summary(s, order):
    bindings = providers.bindings(order.number)
    if not bindings:
        return {
            "status": "not_configured",
            "systems": [],
            "findings": [],
            "resources": [],
            "fingerprint": "",
        }
    row = row_for(s, order)
    if not row:
        return {
            "status": "pending",
            "systems": [
                {
                    "key": b["key"],
                    "label": b["label"],
                    "kind": b["kind"],
                    "mode": b.get("mode", "live"),
                    "status": "pending",
                }
                for b in bindings
            ],
            "findings": [],
            "resources": [],
            "fingerprint": "pending",
        }
    status = row.status
    if (
        row.configuration_hash != digest(bindings)
        or row.checked_at
        and now() - row.checked_at > timedelta(minutes=5)
    ):
        status = "stale"
    return {
        "systems": [
            {
                "key": b["key"],
                "label": b.get("label", b["key"]),
                "kind": b["kind"],
                "mode": b.get("mode", "live"),
                "status": "pending",
                "documents": 0,
            }
            for b in bindings
        ],
        "findings": [],
        "resources": [],
        **row.report,
        "status": status,
        "error": row.error,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        "fingerprint": digest([row.content_hash, digest(bindings), status]),
    }


def _findings(resources):
    """Compare explicit normalized facts, never infer engineering from a filename."""
    facts, findings = {}, []
    for resource in resources:
        if resource["lifecycle"] in {"approved", "released", "accepted"}:
            for key, value in resource["facts"].items():
                facts.setdefault(key, []).append((value, resource))
        if resource["kind"] == "vault" and resource["lifecycle"] == "draft":
            findings.append(
                {
                    "kind": "unreleased_revision",
                    "title": f"{resource['family'] or resource['name']} · Rev {resource['revision']} is a draft",
                    "detail": "Available for context; it does not supersede the released drawing and cannot be attached as a released file.",
                    "document_ids": [resource["document_id"]],
                }
            )
    for key, values in facts.items():
        if len({v for v, _ in values}) > 1:
            findings.append(
                {
                    "kind": "source_conflict",
                    "title": key.replace(".", " · ").replace("_", " "),
                    "detail": "; ".join(
                        f"{r['label']} ({r['revision'] or r['lifecycle']}): {v}"
                        for v, r in values
                    )
                    + ". Resolve before making a commitment.",
                    "document_ids": list(
                        dict.fromkeys(r["document_id"] for _, r in values)
                    ),
                }
            )
    return findings


def synchronize(order_id, leased=lambda: True):
    # Serialize competing refreshes without holding an order row transaction
    # open during provider IO. Always release the session advisory lock.
    from backend.identity import current_org

    key = int(digest([current_org(), order_id, "connected-sources"])[:15], 16)
    with engine.connect() as connection:
        acquired = connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
        )
        connection.commit()
        if not acquired:
            return
        try:
            return _synchronize(order_id, leased)
        except Exception:
            with Session.begin() as s:
                row = row_for(s, get(s, Order, order_id))
                if row:
                    row.status, row.error = (
                        "attention_required",
                        "Source processing failed. Check the originals and retry synchronization.",
                    )
                    row.checked_at = now()
            raise
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            connection.commit()


def _synchronize(order_id, leased):
    with Session.begin() as s:
        order = lock_order(s, order_id)
        job_number = order.number
        bindings = providers.bindings(job_number)
        expected_config = digest(bindings)
        row = row_for(s, order)
        if not row:
            row = ConnectedContext(order_id=order.id)
            s.add(row)
        row.status = "syncing"
    # Provider IO takes place outside order transactions.
    results = []
    for binding in bindings:
        if not leased():
            return
        try:
            results.append((binding, providers.fetch(binding, job_number), ""))
        except providers.ConnectorError as exc:
            results.append((binding, [], str(exc)))
    if not leased():
        return
    with Session.begin() as s:
        order = lock_order(s, order_id)
        row = row_for(s, order)
        if (
            digest(providers.bindings(order.number)) != expected_config
            or order.number != job_number
        ):
            row.status, row.error = (
                "stale",
                "Source bindings changed during retrieval. Synchronization will retry.",
            )
            return
        systems, resources = [], []
        for binding, originals, error in results:
            system = {
                "key": binding["key"],
                "label": binding["label"],
                "kind": binding["kind"],
                "mode": binding.get("mode", "live"),
                "status": "unavailable" if error else "current",
                "error": error,
                "documents": len(originals),
            }
            systems.append(system)
            if error:
                continue
            previous = [
                d
                for d in s.scalars(
                    scoped(Document).where(Document.project_id == order.project_id)
                )
                if d.meta.get("connected_source", {}).get("key") == binding["key"]
            ]
            current_ids = set()
            for original in originals:
                provenance = {
                    "key": binding["key"],
                    "kind": binding["kind"],
                    "label": binding["label"],
                    "mode": binding.get("mode", "live"),
                    "external_id": original.external_id,
                    "revision": original.revision,
                    "lifecycle": original.lifecycle,
                    "family": original.family,
                    "facts": original.facts,
                    "source_url": original.source_url,
                }
                identity = digest([provenance, original.sha256])
                doc = next(
                    (
                        d
                        for d in previous
                        if d.meta.get("connected_identity") == identity
                    ),
                    None,
                )
                if not doc:
                    from backend.ingestion.parser import parse, persist_blob

                    blob, sha = persist_blob(
                        original.content(),
                        ".pdf" if original.name.lower().endswith(".pdf") else ".txt",
                    )
                    role = {
                        "erp": "order_scope",
                        "sharepoint": "specification",
                        "vault": "drawing",
                    }[binding["kind"]]
                    doc = Document(
                        project_id=order.project_id,
                        name=original.name,
                        kind=role,
                        blob=blob,
                        sha256=sha,
                        meta={
                            "order_role": role,
                            "revision_label": original.revision,
                            "connected_source": provenance,
                            "connected_identity": identity,
                            "active": True,
                        },
                    )
                    s.add(doc)
                    s.flush()
                    parse(s, doc)
                    s.flush()
                else:
                    doc.meta = {**doc.meta, "active": True}
                current_ids.add(doc.id)
                source_ids = list(
                    s.scalars(
                        scoped(Span)
                        .where(Span.document_id == doc.id)
                        .with_only_columns(Span.id)
                    )
                )
                resources.append(
                    {
                        **provenance,
                        "document_id": doc.id,
                        "name": doc.name,
                        "sha256": doc.sha256,
                        "source_ids": source_ids,
                    }
                )
            for old in previous:
                if old.id not in current_ids:
                    old.meta = {**old.meta, "active": False}
        # Removed bindings must also withdraw prior retrieved sources.
        active_keys = {b["key"] for b in bindings}
        for doc in s.scalars(
            scoped(Document).where(Document.project_id == order.project_id)
        ):
            if (
                doc.meta.get("connected_source")
                and doc.meta["connected_source"]["key"] not in active_keys
            ):
                doc.meta = {**doc.meta, "active": False}
        findings = _findings(resources)
        row.report = {
            "systems": systems,
            "resources": resources,
            "findings": findings,
            "read_only": True,
            "scope": "Explicit order bindings; PDF/text evidence, no CAD geometry verification.",
        }
        row.status = (
            "current"
            if systems and all(x["status"] == "current" for x in systems)
            else "attention_required"
        )
        row.error = (
            "One or more required sources could not be checked. Reply preparation is paused."
            if row.status != "current"
            else ""
        )
        row.configuration_hash = expected_config
        row.checked_at = now()
        row.content_hash = digest(row.report)
        from backend.orders.service import refresh_project_orders

        refresh_project_orders(s, order.project_id)
        s.flush()
    # Capture and notification processing lock receipt before order. Start the
    # reply follow-up only after releasing the source-sync order transaction.
    with Session.begin() as s:
        prepare_background_reply(s, get(s, Order, order_id))


def prepare_background_reply(s, order):
    """Reconcile captured email work after connected sources become current."""
    from backend.reply_assistant.automatic import schedule_order

    schedule_order(s, order)


def schedule():
    # Local adapter configurations also declare their tenant, so the scheduler
    # cannot inadvertently import fictional records into every workspace.
    configured = providers.configuration().get("organizations", {})
    for organization_id, config in configured.items():
        with (
            identity_scope(Identity(organization_id, "Rivet source coordinator")),
            Session.begin() as s,
        ):
            for order in s.scalars(
                scoped(Order).where(Order.number.in_(list(config.get("jobs", {}))))
            ):
                row = row_for(s, order)
                if (
                    not row
                    or not row.checked_at
                    or now() - row.checked_at >= timedelta(seconds=120)
                    or row.configuration_hash
                    != digest(providers.bindings(order.number))
                ):
                    enqueue(s, order)
                else:
                    # Parsing mail attachments can finish after source retrieval.
                    prepare_background_reply(s, order)


def prepare_before_reply(order_id):
    """User's generation request gets a fresh provider check before its snapshot."""
    with Session() as s:
        order = get(s, Order, order_id)
        configured = bool(providers.bindings(order.number))
    if configured:
        synchronize(order_id)
