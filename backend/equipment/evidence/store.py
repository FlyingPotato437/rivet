"""Append equipment claims and versioned decisions without rewriting evidence.

The database function checks current tenant/order ownership and leaves commit to
its caller. The disk store is a private, symlink-resistant append-only archive
for DB-free evaluation and first-day package processing.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.identity import current_org
from backend.storage.models import Order

from .models import EquipmentEvidenceClaim, EquipmentEvidenceRun
from .schema import SCHEMA_VERSION, STATES, source_ref


class EvidenceCollisionError(ValueError):
    """An existing evidence ID was reused for a different payload."""


class EvidenceScopeError(ValueError):
    """Evidence crossed a tenant/order or archive scope boundary."""


def canonical_bytes(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def content_hash(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _validated(graph):
    if graph.get("schema_version") != SCHEMA_VERSION or not graph.get("scope_id"):
        raise ValueError("A versioned, scoped evidence graph is required")
    canonical_bytes(graph)  # Reject non-JSON and non-finite values before writes.
    subjects = {node["id"] for node in graph.get("nodes", [])}
    seen = {}
    for claim in graph.get("claims", []):
        if (
            not isinstance(claim.get("id"), str)
            or not claim["id"]
            or len(claim["id"]) > 160
        ):
            raise ValueError("Claim IDs must contain 1–160 characters")
        if claim.get("subject_id") not in subjects:
            raise ValueError("Claim subject is not in the persisted node graph")
        if not claim.get("feature") or claim.get("state") not in STATES:
            raise ValueError("A typed claim feature and lifecycle state are required")
        source_ref(claim["source"])
        digest = content_hash(claim)
        if claim["id"] in seen and seen[claim["id"]][1] != digest:
            raise EvidenceCollisionError(
                "One graph reuses a claim ID with different source data"
            )
        seen[claim["id"]] = (deepcopy(claim), digest)
    policy = graph.get(
        "policy", graph.get("metadata", {}).get("reconciliation_policy", {})
    )
    calibration = graph.get(
        "calibration", graph.get("metadata", {}).get("calibration", {})
    )
    if not policy:
        policy = graph.get("reconciliation", {})
    return (
        list(seen.values()),
        content_hash(graph),
        content_hash(policy),
        content_hash(calibration),
    )


def append_graph(session, order, graph):
    """Append a run and new claims; validate repeated IDs; flush, never commit.

    A fresh run is recorded even when every claim already exists. Its complete
    node graph, resolutions, policy and calibration are retained verbatim.
    """
    claims, graph_hash, policy_hash, calibration_hash = _validated(graph)
    tenant = current_org()
    order_id = str(order.id)
    if order.organization_id != tenant:
        raise EvidenceScopeError("The order is outside the authenticated organization")
    with session.no_autoflush:
        stored_org = session.scalar(
            select(Order.organization_id).where(Order.id == order_id)
        )
    if stored_org != tenant:
        raise EvidenceScopeError(
            "The persisted order is outside the authenticated organization"
        )
    declared_org = graph.get("metadata", {}).get("organization_id")
    if declared_org is not None and declared_org != tenant:
        raise EvidenceScopeError(
            "Graph organization differs from the authenticated order"
        )
    ids = [claim["id"] for claim, _ in claims]
    existing = {}
    if ids:
        existing = {
            row.claim_id: row
            for row in session.scalars(
                select(EquipmentEvidenceClaim).where(
                    EquipmentEvidenceClaim.organization_id == tenant,
                    EquipmentEvidenceClaim.order_id == order_id,
                    EquipmentEvidenceClaim.claim_id.in_(ids),
                )
            )
        }
    for claim, digest in claims:
        old = existing.get(claim["id"])
        if old is not None and (
            old.payload_hash != digest
            or canonical_bytes(old.payload) != canonical_bytes(claim)
        ):
            raise EvidenceCollisionError("A persisted claim ID cannot be overwritten")
    added = 0
    for claim, digest in claims:
        if claim["id"] in existing:
            continue
        values = {
            "organization_id": tenant,
            "order_id": order_id,
            "claim_id": claim["id"],
            "payload_hash": digest,
            "subject_id": claim["subject_id"],
            "feature": claim["feature"],
            "state": claim["state"],
            "source_sha256": claim["source"]["sha256"].lower(),
            "payload": claim,
        }
        inserted_id = session.scalar(
            insert(EquipmentEvidenceClaim)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_equipment_evidence_claim_scope")
            .returning(EquipmentEvidenceClaim.id)
        )
        if inserted_id:
            added += 1
        else:
            # A concurrent append is idempotent only for the identical payload.
            old = session.scalar(
                select(EquipmentEvidenceClaim).where(
                    EquipmentEvidenceClaim.organization_id == tenant,
                    EquipmentEvidenceClaim.order_id == order_id,
                    EquipmentEvidenceClaim.claim_id == claim["id"],
                )
            )
            if (
                old is None
                or old.payload_hash != digest
                or canonical_bytes(old.payload) != canonical_bytes(claim)
            ):
                raise EvidenceCollisionError("A concurrent append reused a claim ID")
    run = EquipmentEvidenceRun(
        organization_id=tenant,
        order_id=order_id,
        graph_hash=graph_hash,
        policy_hash=policy_hash,
        calibration_hash=calibration_hash,
        graph=deepcopy(graph),
    )
    session.add(run)
    session.flush()
    return {
        "run_id": run.id,
        "order_id": order_id,
        "organization_id": tenant,
        "graph_hash": graph_hash,
        "policy_hash": policy_hash,
        "calibration_hash": calibration_hash,
        "claims_inserted": added,
        "claims_reused": len(claims) - added,
        "total_claims": len(claims),
    }


def _private_directory(path):
    path = Path(path).expanduser().absolute()
    # Refuse symlinked archive components rather than following them out of the
    # operator's selected private package directory.
    missing = []
    current = path
    while not current.exists() and not current.is_symlink():
        missing.append(current)
        if current == current.parent:
            break
        current = current.parent
    for ancestor in [current, *current.parents]:
        if ancestor.is_symlink():
            raise ValueError("Evidence archive paths cannot contain symlinks")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Evidence archive must be a directory")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise PermissionError("Existing evidence directories must be private (0700)")
    return path


def _read_private(path):
    flags = os.O_RDONLY | os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise PermissionError(
                "Evidence artifacts must be private regular files (0600)"
            )
        return stream.read()


def _exclusive_json(directory, filename, payload):
    """Publish a fully fsynced private artifact using exclusive atomic linking."""
    destination = directory / filename
    data = canonical_bytes(payload) + b"\n"
    if destination.exists() or destination.is_symlink():
        if canonical_bytes(json.loads(_read_private(destination))) != canonical_bytes(
            payload
        ):
            raise EvidenceCollisionError(
                "Existing evidence artifact differs; no overwrite allowed"
            )
        return False
    temporary = directory / (".append-" + uuid4().hex)
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, destination, follow_symlinks=False)
            directory_fd = os.open(
                directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return True
        except FileExistsError:
            if canonical_bytes(
                json.loads(_read_private(destination))
            ) != canonical_bytes(payload):
                raise EvidenceCollisionError(
                    "Concurrent artifact append reused a different payload"
                ) from None
            return False
    finally:
        temporary.unlink(missing_ok=True)


def persist_graph(graph, path):
    """Archive source-complete claims and a new decision run without a database.

    ``path`` is a private archive directory. Repeated identical claims are
    reused; every call appends a run. No file is truncated or replaced.
    """
    claims, graph_hash, policy_hash, calibration_hash = _validated(graph)
    destination = _private_directory(path)
    _exclusive_json(
        destination,
        "scope.json",
        {
            "schema_version": "rivet.evidence-archive.v1",
            "scope_id": graph["scope_id"],
            "organization_id": graph.get("metadata", {}).get("organization_id"),
        },
    )
    claim_dir = _private_directory(destination / "claims")
    run_dir = _private_directory(destination / "runs")
    pending = []
    for claim, digest in claims:
        filename = hashlib.sha256(claim["id"].encode()).hexdigest() + ".json"
        payload = {"claim_id": claim["id"], "payload_hash": digest, "claim": claim}
        existing = claim_dir / filename
        if (existing.exists() or existing.is_symlink()) and canonical_bytes(
            json.loads(_read_private(existing))
        ) != canonical_bytes(payload):
            raise EvidenceCollisionError("A persisted claim ID cannot be overwritten")
        pending.append((filename, payload))
    added = sum(
        _exclusive_json(claim_dir, filename, payload) for filename, payload in pending
    )
    run_id = str(uuid4())
    receipt = {
        "run_id": run_id,
        "scope_id": graph["scope_id"],
        "graph_hash": graph_hash,
        "policy_hash": policy_hash,
        "calibration_hash": calibration_hash,
        "claims_inserted": added,
        "claims_reused": len(claims) - added,
        "total_claims": len(claims),
    }
    filename = run_id + "-" + graph_hash + ".json"
    _exclusive_json(
        run_dir,
        filename,
        {"schema_version": "rivet.evidence-run.v1", **receipt, "graph": graph},
    )
    return {
        **receipt,
        "archive_path": str(destination),
        "run_path": str(run_dir / filename),
    }
