"""Real correction files reach unsent fake Outlook drafts without send authority."""

import base64
import json
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from test_company_context import ORG_B, identity

from backend.domain.service import get
from backend.equipment import outlook
from backend.equipment.demo_documents import build_demo_corpus
from backend.identity import current_org
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.storage import db
from backend.storage.models import Order, OrderEvent, Project, RecordIntake, now


class DraftMailbox:
    def __init__(self, *, create_status=201, attachment_status=201, existing=False):
        self.calls, self.messages = [], {}
        self.create_status, self.attachment_status = create_status, attachment_status
        self.existing = existing
        self.original_id = "genuine-order-email"
        self.after_create = None
        self.read_status = 200
        self.extra_inbound = []
        if existing:
            self.messages["person-draft"] = self.message(
                "person-draft",
                {"body": {"content": "My own reply. Keep this."}, "attachments": []},
            )

    def message(self, mid, payload):
        return {
            "id": mid,
            "isDraft": True,
            "parentFolderId": "drafts",
            "conversationId": "order-thread",
            "webLink": f"https://outlook.office.com/mail/deeplink/read/{mid}",
            **payload,
        }

    def __call__(self, snapshot):
        def handle(request):
            self.calls.append(request)
            path = request.url.path
            assert "/send" not in path and "/replyAll" not in path
            assert request.method in {"GET", "POST"}, (
                "No patch, delete or human-edit replacement"
            )
            if request.method == "POST":
                payload = json.loads(request.content)
                if path.endswith("/attachments"):
                    self.messages["native-correction"]["attachments"].append(payload)
                    return httpx.Response(
                        self.attachment_status,
                        json={"id": "attachment-1"}
                        if self.attachment_status < 300
                        else {"error": {"code": "ServiceUnavailable"}},
                    )
                assert path.endswith(("/messages", "/createReply"))
                if path.endswith("/createReply"):
                    assert self.original_id in path
                    content = {**payload["message"], "attachments": []}
                else:
                    assert payload["toRecipients"] == []
                    content = payload
                message = self.message("native-correction", content)
                if self.create_status != 403:
                    self.messages[message["id"]] = message
                if self.after_create:
                    self.after_create()
                return httpx.Response(
                    self.create_status,
                    json=message
                    if self.create_status < 300
                    else {
                        "error": {
                            "code": "ErrorAccessDenied"
                            if self.create_status == 403
                            else "ServiceUnavailable"
                        }
                    },
                )
            if "mailFolders/deleteditems" in path:
                return httpx.Response(200, json={"id": "deleted"})
            if "mailFolders/sentitems" in path:
                return httpx.Response(200, json={"id": "sent"})
            if path.endswith("/messages") and "/mailFolders/" not in path:
                return httpx.Response(
                    200,
                    json={
                        "value": [
                            {
                                "id": self.original_id,
                                "isDraft": False,
                                "conversationId": "order-thread",
                                "receivedDateTime": "2026-10-18T12:00:00Z",
                                "parentFolderId": "inbox",
                            }
                        ]
                        + self.extra_inbound
                    },
                )
            if path.endswith("/native-correction/attachments"):
                return httpx.Response(
                    200,
                    json={"value": self.messages["native-correction"]["attachments"]},
                )
            if "mailFolders/drafts/messages" in path:
                values = [self.messages["person-draft"]] if self.existing else []
                return httpx.Response(200, json={"value": values})
            if path.endswith("/" + self.original_id):
                return httpx.Response(
                    200,
                    json={
                        "id": self.original_id,
                        "isDraft": False,
                        "conversationId": "order-thread",
                        "receivedDateTime": "2026-10-18T12:00:00Z",
                        "parentFolderId": "inbox",
                        "body": {"content": "Please update the equipment."},
                    },
                )
            mid = path.rsplit("/", 1)[-1]
            if self.read_status != 200:
                return httpx.Response(
                    self.read_status,
                    json={
                        "error": {
                            "code": "ErrorItemNotFound"
                            if self.read_status == 404
                            else "ServiceUnavailable"
                        }
                    },
                )
            return httpx.Response(200, json=self.messages[mid])

        client = httpx.Client(transport=httpx.MockTransport(handle))
        return SimpleNamespace(
            mailbox=snapshot["mailbox"],
            client=client,
            headers=lambda: {"Prefer": 'IdType="ImmutableId"'},
            close=client.close,
        )


@pytest.fixture
def packet(tmp_path):
    corpus = tmp_path / "corpus"
    build_demo_corpus(corpus)
    with identity(), db.Session.begin() as session:
        project = Project(
            title="Fictional correction files", customer="Fictional water district"
        )
        session.add(project)
        session.flush()
        order = Order(project_id=project.id, number="DEMO-EQUIPMENT-DRAFT")
        connection = MailboxConnection(
            mailbox="pm@example.com", microsoft_tenant_id=str(uuid4())
        )
        session.add_all([order, connection])
        session.flush()
        oid, cid = order.id, connection.id
        bundle_id = str(uuid4())
        destination = (
            db.BLOBS / "equipment-corrections" / current_org() / oid / bundle_id
        )
        destination.mkdir(parents=True)
        outputs = []
        for suffix, role in [
            ("dxf", "draft_drawing"),
            ("xlsx", "draft_bom"),
            ("pdf", "draft_preview"),
        ]:
            stem = "BOM" if suffix == "xlsx" else "E-10"
            original = corpus / "03-complete-E" / f"{stem}-Rev-E.{suffix}"
            target = destination / f"{stem}-Rev-E-DRAFT.{suffix}"
            target.write_bytes(original.read_bytes())
            outputs.append(
                {
                    "id": str(uuid4()),
                    "name": target.name,
                    "path": str(target),
                    "sha256": sha256(target.read_bytes()).hexdigest(),
                    "role": role,
                    "content_type": "application/octet-stream",
                }
            )
        bundle = {
            "id": bundle_id,
            "order_id": oid,
            "status": "draft",
            "current": True,
            "engineering_approved": False,
            "production_released": False,
            "fingerprint": "source-and-recheck-fingerprint",
            "outputs": outputs,
            "subject": "DEMO-EQUIPMENT-DRAFT - corrected draft copies",
            "body": "CB-12 updated in actual DXF and BOM. Rechecked against explicit requirements.",
            "recheck": {"summary": {"outcome": "supported_fields_match"}},
        }

    def provider(session, order, requested):
        assert requested == bundle_id
        return deepcopy(bundle)

    return oid, cid, bundle, provider


def add_thread(packet):
    oid, cid, _, _ = packet
    with identity(), db.Session.begin() as session:
        intake = RecordIntake(
            name="genuine-order-email.eml",
            blob="email",
            sha256="a" * 64,
            status="imported",
            order_id=oid,
        )
        session.add(intake)
        session.flush()
        session.add(
            MailboxReceipt(
                connection_id=cid,
                provider_message_id="genuine-order-email",
                status="imported",
                intake_id=intake.id,
            )
        )


def save(packet, graph):
    oid, cid, bundle, provider = packet
    return outlook.save(
        oid, bundle["id"], cid, client_factory=graph, bundle_provider=provider
    )


def test_file_only_package_attaches_actual_bytes_and_preserves_person_edits(packet):
    graph = DraftMailbox()
    with identity():
        result = save(packet, graph)
        assert result["draft"]["status"] == "created"
        assert result["draft"]["threaded"] is False
        files = graph.messages["native-correction"]["attachments"]
        assert len(files) == 3
        for file, expected in zip(files, packet[2]["outputs"]):
            assert (
                sha256(base64.b64decode(file["contentBytes"])).hexdigest()
                == expected["sha256"]
            )
        graph.messages["native-correction"]["body"]["content"] = "PM edits this."
        assert save(packet, graph)["draft"]["id"] == result["draft"]["id"]
        assert (
            graph.messages["native-correction"]["body"]["content"] == "PM edits this."
        )
        with db.Session() as session:
            public = outlook.metadata(session, get(session, Order, packet[0]))
            assert "graph_draft_id" not in public[packet[2]["id"]]
            assert "path" not in json.dumps(public)
    assert len([r for r in graph.calls if r.method == "POST"]) == 1


def test_genuine_thread_reply_attaches_each_file_once_without_send(packet):
    add_thread(packet)
    graph = DraftMailbox()
    with identity():
        result = save(packet, graph)
        assert result["draft"]["threaded"] is True
        assert len(result["draft"]["attachments"]) == 3
        save(packet, graph)
    posts = [r for r in graph.calls if r.method == "POST"]
    assert len(posts) == 4
    assert posts[0].url.path.endswith("/genuine-order-email/createReply")
    assert len(graph.messages["native-correction"]["attachments"]) == 3


def test_existing_human_reply_is_preserved_and_not_claimed_as_handoff(packet):
    add_thread(packet)
    graph = DraftMailbox(existing=True)
    with identity():
        result = save(packet, graph)
        assert result["draft"]["status"] == "existing_draft"
        assert result["draft"]["attachments"] == []
        assert "did not change" in result["draft"]["notes"][0]
        observed = outlook.sync_one(packet[0], packet[2]["id"], client_factory=graph)
        assert observed["draft"]["status"] == "existing_draft"
        assert observed["draft"]["notes"] == result["draft"]["notes"]
        assert observed["draft"]["attachments"] == []
    assert not [r for r in graph.calls if r.method == "POST"]
    assert (
        graph.messages["person-draft"]["body"]["content"] == "My own reply. Keep this."
    )


@pytest.mark.parametrize(
    "change",
    [
        "foreign_order",
        "stale",
        "failed_recheck",
        "approved",
        "released",
        "tamper",
        "outside",
    ],
)
def test_unverified_or_foreign_artifacts_never_touch_outlook(packet, tmp_path, change):
    bundle = packet[2]
    if change == "foreign_order":
        bundle["order_id"] = str(uuid4())
    elif change == "stale":
        bundle["current"] = False
    elif change == "failed_recheck":
        bundle["recheck"]["summary"]["outcome"] = "cannot_verify"
    elif change == "approved":
        bundle["engineering_approved"] = True
    elif change == "released":
        bundle["production_released"] = True
    elif change == "tamper":
        Path(bundle["outputs"][0]["path"]).write_bytes(b"tampered")
    else:
        original = Path(bundle["outputs"][0]["path"])
        outside = tmp_path / original.name
        outside.write_bytes(original.read_bytes())
        bundle["outputs"][0]["path"] = str(outside)
    graph = DraftMailbox()
    with identity(), pytest.raises(HTTPException):
        save(packet, graph)
    assert graph.calls == []


def test_unknown_create_outcome_is_durable_and_never_creates_another(packet):
    graph = DraftMailbox(create_status=503)
    with identity():
        with pytest.raises(HTTPException):
            save(packet, graph)
        with pytest.raises(HTTPException):
            save(packet, graph)
        with db.Session() as session:
            assert (
                outlook.metadata(session, packet[0])[packet[2]["id"]]["status"]
                == "manual_attention"
            )
    assert len([r for r in graph.calls if r.method == "POST"]) == 1


def test_interrupted_reservation_is_committed_as_unknown_not_recreated(packet):
    oid, _, bundle, _ = packet
    with identity(), db.Session.begin() as session:
        order = get(session, Order, oid, True)
        order.state = {
            **order.state,
            "equipment_outlook": {
                bundle["id"]: {
                    "status": "creating",
                    "lease": "interrupted-process",
                    "lease_until": (now() - timedelta(minutes=10)).isoformat(),
                }
            },
        }
    graph = DraftMailbox()
    with identity():
        with pytest.raises(HTTPException):
            save(packet, graph)
        with db.Session() as session:
            assert (
                outlook.metadata(session, oid)[bundle["id"]]["status"]
                == "manual_attention"
            )
    assert graph.calls == []


def test_second_request_cannot_create_during_first_provider_write(packet):
    graph = DraftMailbox()

    def competing_request():
        with pytest.raises(HTTPException) as error:
            save(packet, graph)
        assert error.value.status_code == 409

    graph.after_create = competing_request
    with identity():
        assert save(packet, graph)["draft"]["status"] == "created"
    assert len([r for r in graph.calls if r.method == "POST"]) == 1


def test_unknown_attachment_outcome_preserves_partial_draft_without_duplicates(packet):
    add_thread(packet)
    graph = DraftMailbox(attachment_status=503)
    with identity():
        with pytest.raises(HTTPException):
            save(packet, graph)
        with pytest.raises(HTTPException):
            save(packet, graph)
    assert len(graph.messages["native-correction"]["attachments"]) == 1
    assert len([r for r in graph.calls if r.url.path.endswith("/createReply")]) == 1


def test_known_permission_denial_can_retry_after_consent(packet):
    graph = DraftMailbox(create_status=403)
    with identity():
        with pytest.raises(HTTPException):
            save(packet, graph)
        graph.create_status = 201
        assert save(packet, graph)["draft"]["status"] == "created"


def test_source_change_during_create_marks_native_draft_for_review(packet):
    graph = DraftMailbox()
    graph.after_create = lambda: packet[2].update(fingerprint="new-input-fingerprint")
    with identity():
        with pytest.raises(HTTPException):
            save(packet, graph)
        with db.Session() as session:
            assert (
                outlook.metadata(session, packet[0])[packet[2]["id"]]["status"]
                == "manual_attention"
            )
        with pytest.raises(HTTPException):
            save(packet, graph)
    assert len([r for r in graph.calls if r.method == "POST"]) == 1


def test_provider_attachment_bytes_are_checked_before_claiming_success(packet):
    graph = DraftMailbox()

    def corrupt_saved_attachment():
        graph.messages["native-correction"]["attachments"][0]["contentBytes"] = (
            base64.b64encode(b"wrong provider bytes").decode()
        )

    graph.after_create = corrupt_saved_attachment
    with identity():
        with pytest.raises(HTTPException):
            save(packet, graph)
        with db.Session() as session:
            assert (
                outlook.metadata(session, packet[0])[packet[2]["id"]]["status"]
                == "manual_attention"
            )
        with pytest.raises(HTTPException):
            save(packet, graph)
    assert len([r for r in graph.calls if r.method == "POST"]) == 1


def test_cross_tenant_and_viewer_requests_have_no_mailbox_access(packet):
    graph = DraftMailbox()
    with identity(ORG_B), pytest.raises(HTTPException):
        save(packet, graph)
    with identity(role="org:viewer"), pytest.raises(HTTPException):
        save(packet, graph)
    assert graph.calls == []


def test_sent_observation_records_once_with_actual_attachment_proof_not_approval(
    packet,
):
    graph = DraftMailbox()
    oid, _, bundle, _ = packet
    with identity():
        save(packet, graph)
        graph.messages["native-correction"].update(
            isDraft=False, sentDateTime=(now() - timedelta(seconds=20)).isoformat()
        )
        first = outlook.sync_one(oid, bundle["id"], client_factory=graph)
        assert first["draft"]["status"] == "sent_for_review"
        assert first["draft"]["attachments_verified"] is True
        outlook.sync_one(oid, bundle["id"], client_factory=graph)
        with db.Session() as session:
            events = list(
                session.scalars(
                    outlook.scoped(OrderEvent).where(
                        OrderEvent.order_id == oid,
                        OrderEvent.kind == "equipment_correction_outcome",
                    )
                )
            )
            assert len(events) == 1
            assert events[0].data["engineering_approved"] is False
            assert events[0].data["production_released"] is False
            assert events[0].data["learning_status"] == "outcome_recorded_only"
            assert len(events[0].data["outputs"]) == 3
    assert all(request.method == "GET" for request in graph.calls[-3:])


def test_sent_changed_attachment_is_recorded_as_unverified(packet):
    graph = DraftMailbox()
    with identity():
        save(packet, graph)
        message = graph.messages["native-correction"]
        message.update(isDraft=False, sentDateTime=now().isoformat())
        message["attachments"][0]["contentBytes"] = base64.b64encode(
            b"PM replaced attachment"
        ).decode()
        result = outlook.sync_one(packet[0], packet[2]["id"], client_factory=graph)
        assert result["draft"]["status"] == "sent_for_review"
        assert result["draft"]["attachments_verified"] is False


def test_non_draft_without_sent_timestamp_does_not_establish_send(packet):
    graph = DraftMailbox()
    with identity():
        save(packet, graph)
        graph.messages["native-correction"].update(isDraft=False)
        result = outlook.sync_one(packet[0], packet[2]["id"], client_factory=graph)
        assert result["draft"]["status"] == "created"
        assert not result["draft"].get("sent_at")


def test_missing_draft_requires_ten_minutes_and_network_resets_continuous_absence(
    packet,
):
    graph = DraftMailbox()
    oid, _, bundle, _ = packet
    with identity():
        save(packet, graph)
        graph.read_status = 404
        assert (
            outlook.sync_one(oid, bundle["id"], client_factory=graph)["draft"]["status"]
            == "created"
        )
        graph.read_status = 503
        outlook.sync_one(oid, bundle["id"], client_factory=graph)
        with db.Session.begin() as session:
            order = get(session, Order, oid, True)
            row = deepcopy(order.state["equipment_outlook"][bundle["id"]])
            assert row["missing_since"] is None
            row["missing_since"] = (now() - timedelta(minutes=11)).isoformat()
            outlook._put(order, bundle["id"], row)
        graph.read_status = 404
        assert (
            outlook.sync_one(oid, bundle["id"], client_factory=graph)["draft"]["status"]
            == "discarded"
        )


def test_outcome_polling_is_tenant_scoped(packet):
    graph = DraftMailbox()
    with identity():
        save(packet, graph)
    before = len(graph.calls)
    with identity(ORG_B):
        assert outlook.sync_due(client_factory=graph) == []
    assert len(graph.calls) == before


def test_draft_poll_recovers_from_transient_provider_error_without_writes(packet):
    graph = DraftMailbox()
    with identity():
        save(packet, graph)
        before = len(graph.calls)
        graph.read_status = 503
        uncertain = outlook.sync_one(packet[0], packet[2]["id"], client_factory=graph)
        assert uncertain["draft"]["error"]
        graph.read_status = 200
        recovered = outlook.sync_one(packet[0], packet[2]["id"], client_factory=graph)
        assert recovered["draft"]["status"] == "created"
        assert recovered["draft"]["error"] == ""
        assert recovered["draft"]["attachments_verified"] is True
    assert all(request.method == "GET" for request in graph.calls[before:])


def test_changed_draft_attachment_is_visible_and_restoring_bytes_recovers(packet):
    graph = DraftMailbox()
    with identity():
        save(packet, graph)
        before = len(graph.calls)
        attachment = graph.messages["native-correction"]["attachments"][0]
        original = attachment["contentBytes"]
        attachment["contentBytes"] = base64.b64encode(
            b"A person's replacement file"
        ).decode()
        changed = outlook.sync_one(packet[0], packet[2]["id"], client_factory=graph)
        assert changed["draft"]["status"] == "manual_attention"
        assert changed["draft"]["attachments_verified"] is False
        assert "Review changed attachments" in changed["draft"]["error"]
        assert attachment["contentBytes"] != original
        attachment["contentBytes"] = original
        restored = outlook.sync_one(packet[0], packet[2]["id"], client_factory=graph)
        assert restored["draft"]["status"] == "created"
        assert restored["draft"]["error"] == ""
        assert restored["draft"]["attachments_verified"] is True
    assert all(request.method == "GET" for request in graph.calls[before:])
