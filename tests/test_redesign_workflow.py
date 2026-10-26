"""The actual order/job/file/decision path, using synthetic native originals."""

from uuid import uuid4

import ezdxf
import pymupdf
import pytest
from test_records import create

from backend.domain.service import get
from backend.engineering.comment_redesign import file_hash
from backend.identity import Identity, identity_scope
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Order
from backend.worker import claim, process


def test_exhausted_drawing_job_leaves_a_visible_failure(client, tmp_path):
    from datetime import timedelta

    from backend.storage.models import Job, now

    order, _, _ = package(client, tmp_path)
    root = f"/api/orders/{order['id']}/engineering/redesign"
    before = client.get(root).json()
    started = post(
        client, root + "/prepare", {"expected_version": before["order_version"]}
    )
    assert started.status_code == 200
    runid = started.json()["latest"]["id"]
    with Session.begin() as s:
        job = s.query(Job).filter(Job.key == "engineering-redesign:" + runid).one()
        job.status = "running"
        job.attempts = 3
        job.lease_until = now() - timedelta(seconds=1)
    assert claim(kinds=("engineering_redesign",)) is None
    failed = client.get(root).json()["latest"]
    assert failed["status"] == "failed"
    assert not failed["approval_eligible"]
    assert "lease expired" in failed["error"]


def package(client, tmp_path, text='Mark tap changers as "DETC"'):
    order = create(client)
    drawing = db.BLOBS / (str(uuid4()) + ".dxf")
    doc = ezdxf.new("R2010")
    doc.modelspace().add_text("NLTC", dxfattribs={"insert": (10, 10), "height": 3})
    doc.saveas(drawing)
    markup = db.BLOBS / (str(uuid4()) + ".pdf")
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.add_freetext_annot(pymupdf.Rect(30, 30, 450, 220), text)
        pdf.save(markup)
    with Session.begin() as s:
        owned = get(s, Order, order["id"])
        for path, role in ((drawing, "drawing"), (markup, "markup")):
            s.add(
                Document(
                    project_id=owned.project_id,
                    name=path.name,
                    kind=role,
                    blob=path.name,
                    sha256=file_hash(path),
                    state="parsed",
                    meta={"order_role": role},
                )
            )
    return order, drawing, markup


def post(client, path, data):
    return client.post(path, json=data, headers={"Idempotency-Key": str(uuid4())})


def test_saved_arithmetic_and_conflict_survive_background_job(client, tmp_path):
    from shutil import copyfile

    from test_drawing_calculations import fixture

    sources, cad, _, _ = fixture(tmp_path, upper="10.01")
    order = create(client)
    with Session.begin() as s:
        owned = get(s, Order, order["id"])
        for source in [*sources, cad]:
            suffix = ".dxf" if source["role"] == "drawing" else ".pdf"
            blob = str(uuid4()) + suffix
            copyfile(source["path"], db.BLOBS / blob)
            s.add(
                Document(
                    project_id=owned.project_id,
                    name=source["name"],
                    kind=source["role"],
                    blob=blob,
                    sha256=source["sha256"],
                    state="parsed",
                    meta={"order_role": source["role"]},
                )
            )
    root = f"/api/orders/{order['id']}/engineering/redesign"
    before = client.get(root).json()
    assert (
        post(
            client, root + "/prepare", {"expected_version": before["order_version"]}
        ).status_code
        == 200
    )
    process(claim(kinds=("engineering_redesign",)))
    result = client.get(root).json()
    run = result["latest"]
    assert run["status"] == "needs_information"
    assert run["saved_calculation_check"]["cells_checked"] == 3
    assert run["saved_calculation_check"]["passed"]
    assert (
        run["calculation_audits"][0]["blocked_columns"][0]["code"]
        == "capacity_conflict"
    )
    assert not run["approval_eligible"]
    artifact = next(a for a in run["artifacts"] if a["name"] == "Proposed-drawing.dxf")
    assert client.get(artifact["url"]).status_code == 200
    rejected = post(
        client,
        root + f"/{run['id']}/decision",
        {"expected_version": result["order_version"], "decision": "approve"},
    )
    assert rejected.status_code == 409


def test_single_request_edits_rechecks_exports_and_leaves_only_final_approval(
    client, tmp_path
):
    order, drawing, _ = package(client, tmp_path)
    root = f"/api/orders/{order['id']}/engineering/redesign"
    before = client.get(root).json()
    assert before["source_ready"] and before["latest"] is None
    started = post(
        client, root + "/prepare", {"expected_version": before["order_version"]}
    )
    assert started.status_code == 200, started.text
    runid = started.json()["latest"]["id"]
    job = claim(kinds=("engineering_redesign",))
    assert job
    process(job)
    result = client.get(root).json()
    run = result["latest"]
    assert run["status"] == "ready_for_review", run
    assert run["current"] and run["approval_eligible"]
    assert run["totals"]["changed"] == 1
    assert run["saved_drawing_check"]["passed"]
    assert not run["saved_drawing_check"]["engineering_correctness_verified"]
    assert not run["equipment_evidence"]["production_bom_verified"]
    evidence = next(
        a for a in run["artifacts"] if a["name"] == "Equipment-evidence.json"
    )
    assert client.get(evidence["url"]).status_code == 200
    dxf = next(a for a in run["artifacts"] if a["name"].endswith(".dxf"))
    returned = client.get(dxf["url"])
    assert returned.status_code == 200
    assert b"DETC" in returned.content and b"NLTC" in drawing.read_bytes()
    pair = run["changes"][0]["comparison"]
    assert client.get(pair["after_url"]).status_code == 200
    placement = run["changes"][0]["affected_placements"][0]
    assert client.get(placement["after_url"]).status_code == 200
    approved = post(
        client,
        root + f"/{runid}/decision",
        {
            "expected_version": result["order_version"],
            "decision": "approve",
            "note": "Reviewed the saved drawing and scope.",
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["latest"]["status"] == "approved"
    assert not approved.json()["latest"]["production_released"]


def test_partial_engineering_cannot_be_approved_and_paths_remain_order_scoped(
    client, tmp_path
):
    order, _, _ = package(
        client, tmp_path, "Remove ladder and install the fans on the side."
    )
    root = f"/api/orders/{order['id']}/engineering/redesign"
    view = client.get(root).json()
    post(client, root + "/prepare", {"expected_version": view["order_version"]})
    process(claim(kinds=("engineering_redesign",)))
    result = client.get(root).json()
    run = result["latest"]
    assert run["status"] == "needs_information"
    rejected = post(
        client,
        root + f"/{run['id']}/decision",
        {"expected_version": result["order_version"], "decision": "approve"},
    )
    assert rejected.status_code == 409
    from backend.engineering.redesign_api import view as direct_view

    with identity_scope(Identity(str(uuid4()), "foreign")):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            direct_view(order["id"])


def test_source_or_output_drift_blocks_final_signoff(client, tmp_path):
    order, _, _ = package(client, tmp_path)
    root = f"/api/orders/{order['id']}/engineering/redesign"
    before = client.get(root).json()
    post(client, root + "/prepare", {"expected_version": before["order_version"]})
    process(claim(kinds=("engineering_redesign",)))
    result = client.get(root).json()
    run = result["latest"]
    from backend.engineering.redesign_service import artifact

    with Session() as s:
        path = artifact(
            s, get(s, Order, order["id"]), run["id"], "Proposed-drawing.dxf"
        )
    path.write_bytes(b"Changed after verification")
    rejected = post(
        client,
        root + f"/{run['id']}/decision",
        {"expected_version": result["order_version"], "decision": "approve"},
    )
    assert rejected.status_code == 409
