import json
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import pdfplumber
import pytest
from fastapi import HTTPException

from backend.lifecycle import adapters, closeout
from backend.storage import db


@pytest.fixture
def handover():
    order = SimpleNamespace(
        id=str(uuid4()), project_id="handover-project", number="RV-26145"
    )
    documents = {}

    def source(name, role, contents, *, revision="D", release="released"):
        id = str(uuid4())
        blob = id + ".txt"
        (db.BLOBS / blob).write_bytes(contents)
        document = SimpleNamespace(
            id=id,
            project_id=order.project_id,
            name=name,
            kind=role,
            blob=blob,
            sha256=sha256(contents).hexdigest(),
            meta={
                "order_role": role,
                "revision_label": revision,
                "release_status": release,
            },
        )
        documents[id] = document
        return document

    report = source(
        "FTR-17 - functional test.txt",
        "test_report",
        b"Recorded fictional functional test result. No certification.",
        release="approved",
    )
    drawing = source(
        "E-10 - as built.txt", "as_built", b"Fictional completed drawing revision D."
    )
    return order, source, documents, report, drawing


def package_for(payload):
    return {
        "id": str(uuid4()),
        "operation": "closeout",
        "payload": payload,
        "fingerprint": "f" * 64,
        "synthetic": True,
        "prepared_at": "2026-10-15T12:00:00Z",
        "gates": ["engineering", "customer_commitment"],
    }


def test_handover_contains_the_actual_released_files_and_a_readable_index(
    handover, monkeypatch
):
    order, _, documents, report, drawing = handover
    payload = closeout.prepare_payload(order, [report], [drawing], [], "D")
    package = package_for(payload)
    context = {
        "number": order.number,
        "title": "Data hall A switchboard",
        "customer": "Fictional customer",
    }
    original = deepcopy(payload)
    monkeypatch.setattr(closeout, "get", lambda s, cls, id: documents[id])
    pdf = closeout.render_index(context, package)
    with pdfplumber.open(BytesIO(pdf)) as rendered:
        text = "\n".join(page.extract_text() for page in rendered.pages)
    assert "Handover document index" in text
    assert report.name in text and drawing.name in text
    assert "as-built revision d" in text.lower()
    bundle = closeout.render_bundle(None, order, package, pdf)
    assert bundle == closeout.render_bundle(None, order, package, pdf)
    with ZipFile(BytesIO(bundle)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["status"] == "prepared_for_review"
        assert (
            manifest["index_sha256"]
            == sha256(archive.read("Handover-index.pdf")).hexdigest()
        )
        for saved in manifest["documents"]:
            original_bytes = (db.BLOBS / documents[saved["id"]].blob).read_bytes()
            assert archive.read(saved["archive_name"]) == original_bytes
            assert sha256(original_bytes).hexdigest() == saved["sha256"]
        assert len(archive.namelist()) == 4
    assert payload == original


@pytest.mark.parametrize(
    "changed",
    ["draft_report", "wrong_revision", "foreign_order", "duplicate", "internal_cost"],
)
def test_handover_rejects_unreleased_wrong_order_or_internal_material(
    handover, changed
):
    order, source, _, report, drawing = handover
    other = []
    if changed == "draft_report":
        report.meta["release_status"] = "draft"
    elif changed == "wrong_revision":
        drawing.meta["revision_label"] = "C"
    elif changed == "foreign_order":
        drawing.project_id = "other-project"
    elif changed == "duplicate":
        other = [drawing]
    else:
        other = [source("internal cost.txt", "erp_price", b"Internal unit cost.")]
    with pytest.raises(HTTPException) as error:
        closeout.prepare_payload(order, [report], [drawing], other, "D")
    assert error.value.status_code == 409


@pytest.mark.parametrize("changed", ["missing", "tampered", "path", "empty"])
def test_handover_checks_physical_bytes_and_stays_inside_the_blob_store(
    handover, changed
):
    order, _, _, report, drawing = handover
    path = db.BLOBS / drawing.blob
    if changed == "missing":
        path.unlink()
    elif changed == "tampered":
        path.write_bytes(b"Unreviewed drawing replacement")
    elif changed == "empty":
        path.write_bytes(b"")
        drawing.sha256 = sha256(b"").hexdigest()
    else:
        drawing.blob = "../outside.txt"
    with pytest.raises(HTTPException) as error:
        closeout.prepare_payload(order, [report], [drawing], [], "D")
    assert error.value.status_code == 409


def test_archive_filenames_do_not_become_paths_and_late_changes_fail(
    handover, monkeypatch
):
    order, _, documents, report, drawing = handover
    drawing.name = "../../private\\drawing.txt"
    payload = closeout.prepare_payload(order, [report], [drawing], [], "Rev D")
    assert payload["documents"][1]["archive_name"] == "Documents/02-drawing.txt"
    monkeypatch.setattr(closeout, "get", lambda s, cls, id: documents[id])
    assert closeout.current(None, order, payload)
    (db.BLOBS / drawing.blob).write_bytes(b"Changed after review")
    with pytest.raises(HTTPException):
        closeout.render_bundle(None, order, package_for(payload), b"%PDF-fixture")


def test_zip_and_pdf_artifacts_remain_hash_verified_and_immutable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "DATA", tmp_path)
    org, order, package = (str(uuid4()) for _ in range(3))
    raw = BytesIO()
    with ZipFile(raw, "w") as archive:
        archive.writestr("manifest.json", "{}")
    artifact = adapters.write_artifact(
        org, order, package, "handover-documents.zip", raw.getvalue()
    )
    assert artifact["media_type"] == "application/zip"
    assert adapters.read_artifact(org, order, package, artifact) == raw.getvalue()
    with pytest.raises(HTTPException) as error:
        adapters.write_artifact(
            org, order, package, "handover-documents.zip", b"PK\x03\x04changed"
        )
    assert error.value.status_code == 409


def test_legacy_metadata_only_closeout_is_stale_instead_of_being_a_handover(handover):
    order, _, _, report, _ = handover
    assert not closeout.current(
        None, order, {"documents": [{"id": report.id, "name": report.name}]}
    )
