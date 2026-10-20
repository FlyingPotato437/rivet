"""Independent synthetic regression controls; not real-document accuracy."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.domain.service import digest, get
from backend.engineering import service
from backend.engineering.requirements import (
    extract_requirements,
    find_conflicts,
    to_config_inputs,
)
from backend.engineering.text_reader import read_customer_text
from backend.storage.db import Session
from backend.storage.models import Document, Order, Span


@pytest.fixture(autouse=True)
def offline_readers(monkeypatch):
    monkeypatch.setenv("RIVET_ENGINEERING_TEXT_ENABLED", "false")
    monkeypatch.setenv("RIVET_VISION_ENABLED", "false")


def _item(identity, key, value, *, unit=None):
    return {
        "id": identity,
        "key": key,
        "value": value,
        "unit": unit,
        "subject_id": "order",
        "source_claim_ids": [identity + "-claim"],
        "confirmed": False,
    }


def _post(client, path, body):
    response = client.post(path, json=body, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 200, response.text
    return response.json()


def _source_order(client):
    response = _post(
        client,
        "/api/orders",
        {
            "title": "Synthetic source-freshness control",
            "customer": "Software fixture",
            "synthetic": True,
        },
    )
    oid = response["order"]["id"]
    from backend.ingestion.parser import persist_blob

    with Session.begin() as session:
        order = get(session, Order, oid)
        blob, checksum = persist_blob(
            b"Synthetic original; not a real engineering document.", ".pdf"
        )
        document = Document(
            project_id=order.project_id,
            name="synthetic-spec.pdf",
            blob=blob,
            sha256=checksum,
            state="ready",
            coverage=[],
        )
        session.add(document)
        session.flush()
        span = Span(
            document_id=document.id, text="Main bus: 2000 A", location={"page": 1}
        )
        session.add(span)
        session.flush()
        did, sid = document.id, span.id
    url = f"/api/orders/{oid}/engineering"
    data = client.get(url).json()
    data = _post(
        client,
        url + "/sources",
        {"expected_version": data["version"], "source_roles": {did: "specification"}},
    )
    return oid, did, sid, url, data


def test_equal_int_float_do_not_create_fake_conflicts_but_bool_is_distinct():
    rows = [
        _item("native", "service_rating_a", 2000),
        _item("semantic", "service_rating_a", 2000.0),
    ]
    assert find_conflicts(rows) == []
    assert find_conflicts([_item("bool", "spd", True), _item("number", "spd", 1)])


def test_distinct_additive_options_retain_all_citations_without_false_conflict():
    rows = [
        _item("one", "option", "neutral current transformer"),
        _item("two", "option", "gray finish"),
    ]
    requirements = {
        "items": rows,
        "evidence_claims": [],
        "conflicts": find_conflicts(rows),
    }
    assert requirements["conflicts"] == []
    options = to_config_inputs(requirements)["options"]
    assert [o["value"] for o in options] == [
        "neutral current transformer",
        "gray finish",
    ]
    assert [o["source_claim_ids"] for o in options] == [["one-claim"], ["two-claim"]]


@pytest.mark.parametrize(
    "key,number,unit,expected,canonical_unit",
    [
        ("voltage_v", 0.48, "kV", 480, "V"),
        ("main.trip_a", 0.6, "kA", 600, "A"),
        ("minimum_interrupting_ka", 65000, "A", 65, "kA"),
        ("max_height_in", 2540, "mm", 100, "in"),
    ],
)
def test_literal_units_are_canonical_for_solver_but_original_claim_survives(
    tmp_path, key, number, unit, expected, canonical_unit
):
    text = f"Required scalar: {number} {unit}."
    docs = [
        {
            "id": "source",
            "sha256": "a" * 64,
            "role": "specification",
            "spans": [{"id": "p1", "text": text, "location": {"page": 1}}],
        }
    ]
    candidate = {
        "span_id": "p1",
        "key": key,
        "subject_id": "order",
        "value": float(number),
        "unit": unit,
        "quote": f"{number} {unit}",
        "excluded": False,
        "confidence": "high",
    }
    requirements, _ = read_customer_text(
        extract_requirements(docs),
        docs,
        output_dir=tmp_path / "reader",
        enabled=True,
        provider_fn=lambda *_args, **_kwargs: (
            {"items": [candidate], "unhandled": []},
            {},
        ),
    )
    projected = requirements["items"][-1]
    original = requirements["evidence_claims"][-1]
    assert projected["value"] == pytest.approx(expected)
    assert projected["unit"] == canonical_unit
    assert original["value"] == number and original["unit"] == unit
    assert original["source"]["text"] == f"{number} {unit}"


def test_incompatible_units_stay_unknown_instead_of_becoming_ampere_rating(tmp_path):
    docs = [
        {
            "id": "source",
            "sha256": "a" * 64,
            "role": "specification",
            "spans": [{"id": "p1", "text": "Trip: 600 V", "location": {"page": 1}}],
        }
    ]
    candidate = {
        "span_id": "p1",
        "key": "main.trip_a",
        "subject_id": "order",
        "value": 600.0,
        "unit": "V",
        "quote": "600 V",
        "excluded": False,
        "confidence": "high",
    }
    requirements, _ = read_customer_text(
        extract_requirements(docs),
        docs,
        output_dir=tmp_path / "reader",
        enabled=True,
        provider_fn=lambda *_args, **_kwargs: (
            {"items": [candidate], "unhandled": []},
            {},
        ),
    )
    assert requirements["items"][-1]["value"] is None
    assert "trip_a" not in to_config_inputs(requirements).get("main", {})


def test_source_signature_reads_concurrently_changed_db_values_with_cached_objects(
    client,
):
    oid, did, sid, _url, _data = _source_order(client)
    with Session() as first:
        order = get(first, Order, oid)
        cached_doc = get(first, Document, did)
        cached_span = get(first, Span, sid)
        initial = service.source_signature(first, order, service.state(order))
        with Session.begin() as other:
            get(other, Document, did).coverage = [{"page": 1, "state": "failed"}]
            get(other, Span, sid).text = "Main bus: 3000 A"
        assert cached_doc.coverage == [] and cached_span.text == "Main bus: 2000 A"
        assert service.source_signature(first, order, service.state(order)) != initial


def test_changed_original_bytes_change_source_signature(client):
    from backend.storage import db

    oid, did, _sid, _url, _data = _source_order(client)
    with Session() as session:
        order = get(session, Order, oid)
        document = get(session, Document, did)
        initial = service.source_signature(session, order, service.state(order))
        (db.BLOBS / document.blob).write_bytes(
            b"Changed source bytes, old declared database hash."
        )
        assert service.source_signature(session, order, service.state(order)) != initial


def test_sources_changing_during_model_call_do_not_bind_old_reading_to_new_state(
    client, monkeypatch
):
    oid, _did, sid, url, data = _source_order(client)
    from backend.engineering import text_reader

    def race(requirements, _documents, **_kwargs):
        with Session.begin() as other:
            get(other, Span, sid).text = "Main bus: 3000 A"
        return requirements, []

    monkeypatch.setattr(text_reader, "read_customer_text", race)
    response = client.post(
        url + "/extract",
        json={"expected_version": data["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 409, response.text
    assert "changed during extraction" in response.text
    with Session() as session:
        assert service.state(get(session, Order, oid))["requirements"] is None


def _approval_case(monkeypatch):
    from backend.engineering import configurator

    requirements = {
        "items": [{"confirmed": True}],
        "conflicts": [],
        "unreadable_pages": [],
        "warnings": [],
        "reader_coverage": [],
    }
    data = {
        "requirements": requirements,
        "model_id": "fixture",
        "model_version": 1,
        "selected_design": {"id": "fixture", "fully_configured": True},
        "packages": [{"model_version": 1}],
        "precheck": {
            "model_version": 1,
            "signature": "same",
            "saved_drawing_bom_check": {"status": "verified"},
            "source_conformance": {"status": "pass"},
        },
        "approval": None,
    }
    monkeypatch.setattr(service, "_current", lambda *_args: None)
    monkeypatch.setattr(service, "_precheck_signature", lambda *_args: "same")
    monkeypatch.setattr(service, "_effective_rules", lambda *_args: {})
    monkeypatch.setattr(
        configurator,
        "validate_configuration",
        lambda *_args, **_kwargs: {"fully_configured": True},
    )
    monkeypatch.setattr(service, "to_config_inputs", lambda *_args: {})
    return data


@pytest.mark.parametrize(
    "change",
    [
        {"reader_coverage": [{"state": "partial"}]},
        {
            "warnings": [
                {
                    "verification_required": True,
                    "reason": "Unhandled ground fault requirement",
                }
            ]
        },
        {"warnings": [{"deferred_span_ids": ["unread-page-300"]}]},
        {"warnings": [{"reason": "Semantic text reading failed"}]},
    ],
)
def test_partial_reading_or_unhandled_clause_cannot_receive_approval(
    monkeypatch, change
):
    data = _approval_case(monkeypatch)
    data["requirements"].update(change)
    with pytest.raises(HTTPException, match="coverage") as error:
        service.approve(
            None,
            SimpleNamespace(id="fixture", state={"engineering": data}),
            "Fixture signer",
            "Software guard control",
        )
    assert error.value.status_code == 409


def test_cached_success_does_not_override_current_rule_failure(monkeypatch):
    from backend.engineering import configurator

    data = _approval_case(monkeypatch)
    monkeypatch.setattr(
        configurator,
        "validate_configuration",
        lambda *_args, **_kwargs: {"fully_configured": False},
    )
    with pytest.raises(HTTPException, match="unknown/failed"):
        service.approve(
            None,
            SimpleNamespace(id="fixture", state={"engineering": data}),
            "Fixture signer",
            "Software guard control",
        )


def test_failed_saved_drawing_bom_check_blocks_approval(monkeypatch):
    data = _approval_case(monkeypatch)
    data["precheck"]["saved_drawing_bom_check"]["status"] = "cannot_verify"
    with pytest.raises(HTTPException, match="did not verify"):
        service.approve(
            None,
            SimpleNamespace(id="fixture", state={"engineering": data}),
            "Fixture signer",
            "Software guard control",
        )


@pytest.mark.parametrize("source_check", [None, {}, {"status": "fail"}])
def test_missing_or_failed_source_conformance_blocks_approval(
    monkeypatch, source_check
):
    data = _approval_case(monkeypatch)
    data["precheck"]["source_conformance"] = source_check
    with pytest.raises(HTTPException, match="Source conformance") as error:
        service.approve(
            None,
            SimpleNamespace(id="fixture", state={"engineering": data}),
            "Fixture signer",
            "Software guard control",
        )
    assert error.value.status_code == 409


def test_changed_files_or_rules_invalidate_cached_precheck(monkeypatch):
    data = _approval_case(monkeypatch)
    monkeypatch.setattr(service, "_precheck_signature", lambda *_args: "changed")
    with pytest.raises(HTTPException, match="precheck changed"):
        service.approve(
            None,
            SimpleNamespace(id="fixture", state={"engineering": data}),
            "Fixture signer",
            "Software guard control",
        )


def test_changed_precheck_binding_blocks_previously_approved_export(monkeypatch):
    data = _approval_case(monkeypatch)
    data["approval"] = {
        "model_version": 1,
        "model_hash": digest(data["selected_design"]),
        "precheck_signature": "old",
    }
    with pytest.raises(HTTPException, match="stale"):
        service.release_export(
            None, SimpleNamespace(id="fixture", state={"engineering": data})
        )
