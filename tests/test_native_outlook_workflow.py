"""Real native corrections through intake, durable work, and an unsent mailbox."""

import base64
from hashlib import sha256
from uuid import uuid4

import pytest
from test_equipment_api import new_order, upload
from test_equipment_outlook import DraftMailbox

from backend.domain.service import get
from backend.equipment import workflow
from backend.equipment.native_demo import build_native_corpus
from backend.mailbox.models import MailboxConnection
from backend.storage.db import Session
from backend.storage.models import Job, Order


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-outlook-originals")
    manifest = build_native_corpus(root)
    sources = {s["id"]: {**s, "file": root / s["path"]} for s in manifest["sources"]}
    return manifest, sources


def register(client, corpus, number):
    manifest, sources = corpus
    case = next(c for c in manifest["cases"] if c["id"].startswith(f"{number:02d}-"))
    oid = new_order(client)
    for side in ("baseline", "target", "requirements"):
        for key in case[side]:
            source = sources[key]
            if source["role"] == "markup":
                sources = {**sources, key: {**source, "revision": "D"}}
            upload(client, oid, sources, [key])
    view = client.get(f"/api/orders/{oid}/equipment/corrections").json()
    assert view["plan"]["status"] == "ready", view["plan"]["blockers"]
    with Session.begin() as s:
        connection = MailboxConnection(
            mailbox="pm@example.com", microsoft_tenant_id=str(uuid4())
        )
        s.add(connection)
        s.flush()
        job = s.get(Job, view["automation"]["job_id"])
        assert job.kind == "equipment_corrections"
        payload = job.payload
    return oid, payload


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5, 13, 14])
def test_native_work_runs_from_uploaded_sources_and_attaches_checked_bytes_without_a_send(
    client, corpus, number
):
    oid, payload = register(client, corpus, number)
    graph = DraftMailbox()
    result = workflow.process(payload, client_factory=graph)
    assert result["draft"]["status"] == "created"
    updated = client.get(f"/api/orders/{oid}/equipment/corrections").json()
    bundle = updated["bundles"][0]
    assert bundle["current"] and bundle["attachable"] and bundle["geometry_changed"]
    assert bundle["native_verification"]["status"] == "verified"
    assert all(c["status"] == "pass" for c in bundle["native_verification"]["checks"])
    expected = {
        a["name"]: a["sha256"]
        for a in bundle["artifacts"]
        if a["role"] in {"drawing", "bom", "preview"}
    }
    attached = graph.messages["native-correction"]["attachments"]
    assert len(attached) == 3
    assert {
        a["name"]: sha256(base64.b64decode(a["contentBytes"])).hexdigest()
        for a in attached
    } == expected
    assert graph.messages["native-correction"]["isDraft"]
    assert graph.messages["native-correction"]["toRecipients"] == []
    assert not any("/send" in str(r.url) for r in graph.calls)
    # Native geometry is part of the ordinary typed equipment model too.
    model = client.get(f"/api/orders/{oid}/equipment/model")
    assert model.status_code == 200, model.text
    assert len(model.json()["native"]["devices"]) == 9
    assert model.json()["coverage"]["connectivity_reconstructed"]
    with Session() as s:
        order = get(s, Order, oid)
        assert order.status != "released"
        assert len(order.state["equipment_corrections"]["bundles"]) == 1
    before = len([r for r in graph.calls if r.method == "POST"])
    again = workflow.process(payload, client_factory=graph)
    assert again["draft"]["id"] == result["draft"]["id"]
    assert len([r for r in graph.calls if r.method == "POST"]) == before


def test_disabling_automatic_outlook_drafts_still_prepares_files_and_allows_explicit_handoff(
    client, corpus
):
    from backend.autopilot.models import AutopilotSettings
    from backend.equipment import outlook

    oid, payload = register(client, corpus, 1)
    with Session.begin() as s:
        s.add(AutopilotSettings(draft_in_outlook=False))
    graph = DraftMailbox()
    result = workflow.process(payload, client_factory=graph)
    assert result["status"] == "checked" and result["outlook"] == "disabled"
    assert not graph.calls
    view = client.get(f"/api/orders/{oid}/equipment/corrections").json()
    assert view["bundles"][0]["attachable"] and view["bundles"][0]["current"]
    assert view["automation"]["status"] == "checked"
    explicit = outlook.save(oid, result["bundle_id"], client_factory=graph)
    assert explicit["draft"]["status"] == "created"
    assert graph.messages["native-correction"]["isDraft"]
