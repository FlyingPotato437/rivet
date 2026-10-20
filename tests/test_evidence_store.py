"""Persistence integrity controls; no synthetic control is real accuracy."""

import json
import os
import stat
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from backend.equipment.evidence import (
    append_claim,
    make_claim,
    new_graph,
    transition_claim,
)
from backend.equipment.evidence.models import (
    EquipmentEvidenceClaim,
    EquipmentEvidenceRun,
)
from backend.equipment.evidence.ontology import make_node
from backend.equipment.evidence.schema import append_record
from backend.equipment.evidence.store import (
    EvidenceCollisionError,
    EvidenceScopeError,
    append_graph,
    content_hash,
    persist_graph,
)
from backend.identity import LOCAL_ORG, Identity, identity_scope
from backend.storage.db import Session
from backend.storage.models import Order, Project

HASH = "a" * 64
REF = {"source_id": "drawing", "sha256": HASH, "page": 1, "bbox": [0, 0, 1, 1]}


def _graph(scope="order:test"):
    graph = append_record(
        new_graph(scope), "nodes", make_node("device", "device", scope)
    )
    graph = append_claim(
        graph,
        make_claim(
            "device",
            "trip_a",
            600,
            source=REF,
            extractor={"name": "vision", "version": "frozen"},
            raw_confidence="high",
        ),
    )
    graph["policy"] = {"version": "test-policy", "catalog_hash": "c" * 64}
    graph["calibration"] = {"version": "test-calibration", "sample_count": 0}
    graph["resolutions"] = [
        {
            "subject_id": "device",
            "feature": "trip_a",
            "value": 600,
            "needs_review": True,
        }
    ]
    return graph


def _order(session, org=LOCAL_ORG):
    project = Project(
        organization_id=org,
        title="Evidence persistence control",
        customer="Test",
        category="Control",
    )
    session.add(project)
    session.flush()
    order = Order(
        organization_id=org, project_id=project.id, number="TEST-STORE", state={}
    )
    session.add(order)
    session.flush()
    return order


def test_database_reuses_claims_but_appends_complete_decision_runs():
    graph = _graph()
    with Session.begin() as session:
        order = _order(session)
        first = append_graph(session, order, graph)
        next_graph = deepcopy(graph)
        next_graph["policy"]["version"] = "test-policy-v2"
        next_graph["resolutions"][0]["needs_review"] = False
        second = append_graph(session, order, next_graph)
        assert first["claims_inserted"] == 1 and second["claims_inserted"] == 0
        assert second["claims_reused"] == 1 and first["run_id"] != second["run_id"]
        assert first["policy_hash"] != second["policy_hash"]
        assert (
            session.scalar(select(func.count()).select_from(EquipmentEvidenceClaim))
            == 1
        )
        runs = list(
            session.scalars(
                select(EquipmentEvidenceRun).order_by(EquipmentEvidenceRun.created_at)
            )
        )
        assert len(runs) == 2
        assert runs[0].graph["resolutions"][0]["needs_review"] is True
        assert runs[1].graph["resolutions"][0]["needs_review"] is False
        assert runs[0].graph["claims"] == graph["claims"]


def test_existing_claim_cannot_be_changed_and_lifecycle_is_a_new_claim():
    graph = _graph()
    with Session.begin() as session:
        order = _order(session)
        first = append_graph(session, order, graph)
        changed = deepcopy(graph)
        changed["claims"][0]["source"]["page"] = 2
        with pytest.raises(EvidenceCollisionError):
            append_graph(session, order, changed)
        assert (
            session.scalar(select(func.count()).select_from(EquipmentEvidenceRun)) == 1
        )
        approved = transition_claim(
            graph,
            graph["claims"][0]["id"],
            "approved",
            actor="Test PM",
            authority="Test role verified",
            evidence=REF,
        )
        receipt = append_graph(session, order, approved)
        assert receipt["claims_inserted"] == 1 and receipt["claims_reused"] == 1
        stored = list(session.scalars(select(EquipmentEvidenceClaim)))
        assert {c.state for c in stored} == {"observed", "approved"}
        assert (
            next(c for c in stored if c.state == "observed").payload
            == graph["claims"][0]
        )
        assert first["graph_hash"] != receipt["graph_hash"]


def test_database_and_application_enforce_tenant_order_boundary():
    with Session.begin() as session:
        order = _order(session)
        order_id = order.id
    with (
        Session.begin() as session,
        identity_scope(Identity(str(uuid4()), "Other organization")),
    ):
        order = session.get(Order, order_id)
        with pytest.raises(EvidenceScopeError):
            append_graph(session, order, _graph())
    with (
        pytest.raises(IntegrityError, match="organization mismatch"),
        Session.begin() as session,
    ):
        session.execute(
            text(
                "INSERT INTO equipment_evidence_claims (id,organization_id,order_id,created_at,claim_id,payload_hash,subject_id,feature,state,source_sha256,payload) VALUES (:id,:org,:order,now(),'claim',:hash,'device','trip_a','observed',:hash,'{}')"
            ),
            {"id": str(uuid4()), "org": str(uuid4()), "order": order_id, "hash": HASH},
        )


def test_database_rejects_direct_claim_update_delete_and_decision_rewrite():
    with Session.begin() as session:
        order = _order(session)
        receipt = append_graph(session, order, _graph())
    for statement in (
        update(EquipmentEvidenceClaim).values(feature="changed"),
        delete(EquipmentEvidenceClaim),
        update(EquipmentEvidenceRun).values(graph={}),
        delete(EquipmentEvidenceRun),
        text("TRUNCATE equipment_evidence_claims"),
        text("TRUNCATE equipment_evidence_runs"),
    ):
        with (
            pytest.raises(IntegrityError, match="append-only"),
            Session.begin() as session,
        ):
            session.execute(statement)
    with Session.begin() as session:
        assert (
            session.scalar(select(func.count()).select_from(EquipmentEvidenceClaim))
            == 1
        )
        assert session.get(EquipmentEvidenceRun, receipt["run_id"]).graph["claims"]


def test_database_append_rolls_back_with_callers_transaction():
    with pytest.raises(RuntimeError, match="roll back"), Session.begin() as session:
        order = _order(session)
        append_graph(session, order, _graph())
        raise RuntimeError("roll back")
    with Session.begin() as session:
        assert (
            session.scalar(select(func.count()).select_from(EquipmentEvidenceRun)) == 0
        )
        assert (
            session.scalar(select(func.count()).select_from(EquipmentEvidenceClaim))
            == 0
        )


def test_disk_archive_is_private_idempotent_and_keeps_every_run(tmp_path):
    graph = _graph()
    archive = tmp_path / "archive"
    first = persist_graph(graph, archive)
    second = persist_graph(graph, archive)
    assert first["claims_inserted"] == 1 and second["claims_reused"] == 1
    assert len(list((archive / "claims").glob("*.json"))) == 1
    assert len(list((archive / "runs").glob("*.json"))) == 2
    payload = json.loads(Path(second["run_path"]).read_text())
    assert payload["graph"] == graph
    assert payload["graph_hash"] == content_hash(graph)
    for path in archive.rglob("*"):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    assert not list(archive.rglob(".append-*"))


def test_disk_archive_rejects_claim_collision_scope_change_and_symlinks(tmp_path):
    archive = tmp_path / "archive"
    graph = _graph()
    persist_graph(graph, archive)
    changed = deepcopy(graph)
    changed["claims"][0]["value"] = 60
    with pytest.raises(EvidenceCollisionError):
        persist_graph(changed, archive)
    with pytest.raises(EvidenceCollisionError):
        persist_graph(_graph("other:order"), archive)
    assert len(list((archive / "runs").glob("*.json"))) == 1
    link = tmp_path / "archive-link"
    link.symlink_to(archive, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        persist_graph(graph, link)
    claim = next((archive / "claims").glob("*.json"))
    saved = claim.read_bytes()
    claim.unlink()
    victim = tmp_path / "victim.json"
    victim.write_bytes(saved)
    victim.chmod(0o600)
    claim.symlink_to(victim)
    with pytest.raises(OSError):
        persist_graph(graph, archive)
    assert victim.read_bytes() == saved


def test_disk_archive_refuses_nonprivate_existing_directories(tmp_path):
    archive = tmp_path / "not-private"
    archive.mkdir(mode=0o755)
    os.chmod(archive, 0o755)
    with pytest.raises(PermissionError, match="0700"):
        persist_graph(_graph(), archive)
    assert not list(archive.iterdir())
