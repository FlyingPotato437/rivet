import base64
import hashlib
import json
from datetime import timedelta

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_company_context import ORG_A, ORG_B, identity, order

from backend.connected_context import providers, service
from backend.domain.service import get
from backend.reply_assistant import attachments
from backend.reply_assistant import service as replies
from backend.reply_assistant.schemas import GenerateReply, ReviewReply
from backend.storage.db import Session
from backend.storage.models import Document, Job, Order, now


def resource(job, id, value="A", state="released"):
    text = f"FICTIONAL\nJob: {job}\nEquipment: CB-12\nTrip unit: {value}\nLifecycle: {state}\n"
    data = text.encode()
    return providers.Resource(
        external_id=id,
        job_number=job,
        name=id + ".txt",
        revision=id,
        lifecycle=state,
        family="E-10",
        content_base64=base64.b64encode(data).decode(),
        sha256=hashlib.sha256(data).hexdigest(),
        facts={"CB-12.trip_unit": value},
    )


def setup(monkeypatch, tmp_path):
    with Session.begin() as s:
        o, _doc, _source = order(s)
        oid, job = o.id, o.number
    config = {
        "organizations": {
            ORG_A: {
                "jobs": {
                    job: {
                        "connectors": [
                            {
                                "key": kind,
                                "label": kind,
                                "kind": kind,
                                "mode": "local_demo",
                                "adapter": "rivet_readonly_v1",
                                "base_url": "http://127.0.0.1:8791",
                            }
                            for kind in ("erp", "sharepoint", "vault")
                        ]
                    }
                }
            }
        }
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    monkeypatch.setenv("RIVET_CONNECTED_CONTEXT_CONFIG", str(path))
    records = {
        "erp": [resource(job, "scope", state="accepted")],
        "sharepoint": [resource(job, "spec", "B", "approved")],
        "vault": [resource(job, "released"), resource(job, "draft", "B", "draft")],
    }
    monkeypatch.setattr(providers, "fetch", lambda b, j: records[b["kind"]])
    return oid, job, records, path


def test_sync_links_sources_conflicts_and_released_attachments(monkeypatch, tmp_path):
    with identity():
        oid, _, _, _ = setup(monkeypatch, tmp_path)
        service.synchronize(oid)
        with Session() as s:
            o = get(s, Order, oid)
            status = service.summary(s, o)
            assert status["status"] == "current"
            assert len(status["systems"]) == 3 and len(status["resources"]) == 4
            assert {f["kind"] for f in status["findings"]} == {
                "source_conflict",
                "unreleased_revision",
            }
            _, current, company, _hash = replies.context(s, o, "Confirm CB-12")
            snapshot = {"current_order": current, "company": company}
            files = attachments.candidates(s, o, snapshot)
            assert next(x for x in files if x["name"] == "released.txt")["eligible"]
            assert not next(x for x in files if x["name"] == "draft.txt")["eligible"]
            assert not next(x for x in files if x["name"] == "scope.txt")["eligible"]
            assert all(r["source_ids"] for r in status["resources"])


def test_refresh_is_duplicate_safe_and_hash_stable(monkeypatch, tmp_path):
    with identity():
        oid, _, _, _ = setup(monkeypatch, tmp_path)
        service.synchronize(oid)
        with Session() as s:
            hash = replies.context(s, get(s, Order, oid), "reply")[-1]
            count = len(list(s.scalars(select(Document))))
        service.synchronize(oid)
        with Session() as s:
            assert replies.context(s, get(s, Order, oid), "reply")[-1] == hash
            assert len(list(s.scalars(select(Document)))) == count


def test_revision_change_withdraws_old_source_and_invalidates_draft(
    monkeypatch, tmp_path
):
    with identity():
        oid, job, records, _ = setup(monkeypatch, tmp_path)
        service.synchronize(oid)
        with Session.begin() as s:
            draft, _ = replies.begin(s, GenerateReply(order_id=oid), "one")
            source = draft.context["current_order"]["sources"][0]["id"]
            result = replies.finish(
                s,
                draft.id,
                {
                    "subject": "Review",
                    "body": "The source values conflict; please confirm the required trip unit.",
                    "source_ids": [source],
                    "unresolved": [],
                },
            )
            assert result["status"] == "ready" and result["unresolved"]
            old_hash = draft.evidence_hash
            draft_id = draft.id
        records["vault"] = [resource(job, "released", "C")]
        service.synchronize(oid)
        with Session.begin() as s:
            o = get(s, Order, oid)
            assert replies.context(s, o, "reply")[-1] != old_hash
            retired = [
                d for d in s.scalars(select(Document)) if d.meta.get("active") is False
            ]
            assert len(retired) == 2
            with pytest.raises(HTTPException) as e:
                replies.review(
                    s,
                    draft_id,
                    ReviewReply(
                        body="reply",
                        decision="approved",
                        expected_evidence_hash=old_hash,
                    ),
                )
            assert e.value.status_code == 409


def test_outage_never_silently_uses_last_successful_snapshot(monkeypatch, tmp_path):
    with identity():
        oid, _, records, _ = setup(monkeypatch, tmp_path)
        service.synchronize(oid)

        def fetch(binding, job):
            if binding["kind"] == "erp":
                raise providers.ConnectorError("Source unavailable (HTTP 503).")
            return records[binding["kind"]]

        monkeypatch.setattr(providers, "fetch", fetch)
        service.synchronize(oid)
        with Session() as s:
            o = get(s, Order, oid)
            assert service.summary(s, o)["status"] == "attention_required"
            with pytest.raises(HTTPException):
                replies.context(s, o, "reply")


def test_old_checkpoint_and_changed_config_block_generation(monkeypatch, tmp_path):
    with identity():
        oid, _, _, path = setup(monkeypatch, tmp_path)
        service.synchronize(oid)
        with Session.begin() as s:
            o = get(s, Order, oid)
            service.row_for(s, o).checked_at = now() - timedelta(minutes=6)
            assert service.summary(s, o)["status"] == "stale"
        service.synchronize(oid)
        config = json.loads(path.read_text())
        for job in config["organizations"][ORG_A]["jobs"].values():
            job["connectors"][0]["base_url"] = "http://localhost:8799"
        path.write_text(json.dumps(config))
        with Session() as s, pytest.raises(HTTPException):
            replies.context(s, get(s, Order, oid), "reply")


def test_tenant_isolation_and_exact_job_binding(monkeypatch, tmp_path):
    with identity():
        oid, job, _, _ = setup(monkeypatch, tmp_path)
        assert providers.bindings(job)
        assert not providers.bindings(job + "-other")
    with identity(ORG_B):
        assert not providers.bindings(job)
        with pytest.raises(HTTPException):
            service.prepare_before_reply(oid)


def test_enqueue_is_durable_and_duplicate_safe(monkeypatch, tmp_path):
    with identity():
        oid, _, _, _ = setup(monkeypatch, tmp_path)
        with Session.begin() as s:
            o = get(s, Order, oid)
            assert service.enqueue(s, o)
            assert not service.enqueue(s, o)
            pending = service.summary(s, o)
            assert len(pending["systems"]) == 3
            assert pending["findings"] == pending["resources"] == []
            assert (
                len(list(s.scalars(select(Job).where(Job.kind == "connected_context"))))
                == 1
            )


@pytest.mark.parametrize("corruption", ["job", "hash", "duplicate", "redirect", "type"])
def test_adapter_rejects_bad_evidence_and_redirects(corruption):
    r = resource("JOB1", "A").model_dump()
    payload = {"job_number": "JOB1", "kind": "vault", "resources": [r]}
    if corruption == "job":
        r["job_number"] = "ANOTHER-JOB"
    if corruption == "hash":
        r["sha256"] = "0" * 64
    if corruption == "duplicate":
        payload["resources"].append(r)
    if corruption == "type":
        payload["kind"] = "erp"
    transport = httpx.MockTransport(
        lambda req: (
            httpx.Response(302, headers={"location": "https://example.com/steal"})
            if corruption == "redirect"
            else httpx.Response(200, json=payload)
        )
    )
    with pytest.raises(providers.ConnectorError):
        providers.read_adapter(
            {
                "kind": "vault",
                "mode": "local_demo",
                "base_url": "http://127.0.0.1:8793",
            },
            "JOB1",
            transport,
        )


def test_removed_binding_excludes_cached_evidence_from_reply(monkeypatch, tmp_path):
    with identity():
        oid, _, _, path = setup(monkeypatch, tmp_path)
        service.synchronize(oid)
        path.write_text('{"organizations": {}}')
        with Session() as s:
            o = get(s, Order, oid)
            _, context, _, _ = replies.context(s, o, "reply")
            assert not any(x.get("connected_source") for x in context["sources"])
            assert not any(
                x.get("connected_source")
                for x in attachments.candidates(s, o, {"current_order": context})
            )


def test_captured_mail_prepares_once_and_outlook_recovers_only_current_work(
    monkeypatch, tmp_path
):
    from test_reply_assistant import receipt

    from backend.agents import gateway
    from backend.reply_assistant.models import ReplyDraft

    monkeypatch.setattr(gateway, "configured", lambda: True)
    with identity():
        oid, job, records, path = setup(monkeypatch, tmp_path)
        with Session.begin() as s:
            doc = s.scalar(
                select(Document).where(
                    Document.project_id == get(s, Order, oid).project_id
                )
            )
            mail = receipt(s, oid, doc.id)
            mail.status = "imported"
        service.synchronize(oid)
        with Session() as s:
            assert len(list(s.scalars(select(ReplyDraft)))) == 1
        service.synchronize(oid)
        service.synchronize(oid)
        with Session.begin() as s:
            drafts = list(s.scalars(select(ReplyDraft)))
            assert len(drafts) == 1
            draft = drafts[0]
            assert (
                replies.outlook_context(s, "<fixture@example.com>")["prepared_reply"][
                    "id"
                ]
                == draft.id
            )
            source = draft.context["current_order"]["sources"][0]["id"]
            replies.finish(
                s,
                draft.id,
                {
                    "subject": "Review required",
                    "body": "The sources disagree; please confirm the scope.",
                    "source_ids": [source],
                    "unresolved": [],
                },
            )
            assert replies.view(draft)["prepared_automatically"]
            assert (
                replies.outlook_context(s, "<other@example.com>")["prepared_reply"]
                is None
            )
        records["vault"] = [resource(job, "replacement", "C")]
        service.synchronize(oid)
        with Session() as s:
            drafts = list(s.scalars(select(ReplyDraft).order_by(ReplyDraft.created_at)))
            assert len(drafts) == 2
            assert (
                replies.outlook_context(s, "<fixture@example.com>")["prepared_reply"][
                    "id"
                ]
                == drafts[-1].id
            )
        path.write_text('{"organizations": {}}')
        with Session() as s:
            assert (
                replies.outlook_context(s, "<fixture@example.com>")["prepared_reply"]
                is None
            )


@pytest.mark.parametrize("explicit_job", [True, False])
def test_intake_and_later_confirmation_enqueue_context(
    monkeypatch, tmp_path, explicit_job
):
    from backend.records import intake
    from backend.storage.models import Quote, RecordIntake

    with identity():
        oid, job, _, _ = setup(monkeypatch, tmp_path)
        with Session.begin() as s:
            s.add(Quote(project_id=get(s, Order, oid).project_id, number="TEST-QUOTE"))
            s.flush()
            payload = f"Job {job}\nRevision D\n" if explicit_job else ""
            payload += "Please confirm the breaker trip unit on E-10."
            received = intake.receive(s, "returned.txt", payload.encode())
            row = get(s, RecordIntake, received["intake_id"])
            if not explicit_job:
                assert row.order_id is None
                assert not list(
                    s.scalars(select(Job).where(Job.kind == "connected_context"))
                )
                intake.attach(s, row, get(s, Order, oid), "D")
            assert row.order_id == oid
            queued = list(s.scalars(select(Job).where(Job.kind == "connected_context")))
            assert len(queued) == 1 and queued[0].payload["order_id"] == oid


@pytest.mark.parametrize(
    "scenario",
    [
        "ok",
        "changed",
        "unapproved_host",
        "unknown_lifecycle",
        "wrong_tenant",
        "wrong_item",
    ],
)
def test_graph_uses_selected_items_and_checks_download_and_revision(
    monkeypatch, scenario
):
    from backend.mailbox import graph

    monkeypatch.setenv("RIVET_MS_TENANT_ID", "test-tenant")
    monkeypatch.setenv("RIVET_MS_CLIENT_ID", "test-client")
    monkeypatch.setattr(
        graph, "credential_fields", lambda *_: {"client_secret": "test-only"}
    )
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "test-token"})
        if request.url.host == "tenant.sharepoint.com":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=b"Fictional selected specification")
        assert request.headers["authorization"] == "Bearer test-token"
        if request.url.path.endswith("/content"):
            host = (
                "unapproved.example"
                if scenario == "unapproved_host"
                else "tenant.sharepoint.com"
            )
            return httpx.Response(
                302, headers={"location": f"https://{host}/original.txt"}
            )
        if "$expand" not in str(request.url):
            return httpx.Response(
                200,
                json={
                    "id": "item-1",
                    "eTag": "changed" if scenario == "changed" else "version-2",
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "other-item" if scenario == "wrong_item" else "item-1",
                "name": "spec.txt",
                "size": 38,
                "eTag": "version-2",
                "webUrl": "https://tenant.sharepoint.com/spec.txt",
                "listItem": {
                    "fields": {
                        "EngineeringRevision": "B",
                        "TripUnit": "TU-3000-DEMO",
                        "ReviewStatus": "Unknown"
                        if scenario == "unknown_lifecycle"
                        else "Approved",
                    }
                },
            },
        )

    binding = {
        "tenant_id": "another-tenant" if scenario == "wrong_tenant" else "test-tenant",
        "download_hosts": ["tenant.sharepoint.com"],
        "items": [
            {
                "drive_id": "drive-1",
                "item_id": "item-1",
                "family": "spec",
                "revision_field": "EngineeringRevision",
                "lifecycle_field": "ReviewStatus",
                "lifecycle_map": {"Approved": "approved"},
                "fact_fields": {"CB-12.trip_unit": "TripUnit"},
            }
        ],
    }
    if scenario in {"changed", "unapproved_host", "wrong_tenant", "wrong_item"}:
        with pytest.raises(providers.ConnectorError):
            providers.read_sharepoint(binding, "JOB-1", httpx.MockTransport(handler))
        if scenario == "wrong_tenant":
            assert not calls
    else:
        result = providers.read_sharepoint(
            binding, "JOB-1", httpx.MockTransport(handler)
        )
        assert len(result) == 1 and result[0].revision == "B"
        assert result[0].lifecycle == (
            "unknown" if scenario == "unknown_lifecycle" else "approved"
        )
        assert result[0].external_id == "drive-1:item-1"
        assert result[0].facts == {"CB-12.trip_unit": "TU-3000-DEMO"}
        assert result[0].content() == b"Fictional selected specification"


def test_sharepoint_facts_only_include_explicit_nonempty_columns():
    item = {"fact_fields": {"breaker": "TripUnit", "missing": "NotSet"}}
    assert providers._sharepoint_facts(
        item, {"TripUnit": " TU-3000-DEMO ", "InternalNotes": "Ignore this"}
    ) == {"breaker": "TU-3000-DEMO"}
    assert providers._sharepoint_facts(item, {"TripUnit": " "}) == {}
    assert providers._sharepoint_facts({}, {"TripUnit": "TU-3000-DEMO"}) == {}


@pytest.mark.parametrize(
    "mapping, fields",
    [
        ([], {}),
        ({"breaker": None}, {}),
        ({"breaker": "TripUnit"}, {"TripUnit": {"value": "ambiguous"}}),
        ({"breaker": "TripUnit"}, {"TripUnit": "x" * 501}),
    ],
)
def test_sharepoint_fact_mapping_rejects_ambiguous_or_oversized_values(mapping, fields):
    with pytest.raises(providers.ConnectorError):
        providers._sharepoint_facts({"fact_fields": mapping}, fields)
