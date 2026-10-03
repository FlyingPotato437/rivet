"""Draft recipient edits never queue delivery or mutate an in-flight payload."""

from uuid import uuid4

import pytest
from sqlalchemy import select
from test_record_automation import action
from test_records import create, post, upload, view

from backend.storage.db import Session
from backend.storage.models import Job, NoticeDelivery


def setup_notice(client):
    order = create(client)
    w = upload(
        client, order, "comments.txt", b"Comment 1: Confirm the item marked red."
    )
    suggestion = next(
        s for s in w["coordination"]["suggestions"] if s["kind"] == "clarification"
    )
    w = action(
        client, order, w, f"suggestions/{suggestion['id']}", action="accept"
    ).json()
    notice = w["notices"][0]
    assert notice["recipients"] == []
    w = post(
        client,
        f"/orders/{order['id']}/record/subscribers",
        {
            "expected_version": w["version"],
            "actor": "Test PM",
            "reason": "Added the customer project manager.",
            "name": "Customer PM",
            "email": "pm@example.com",
            "team": "Customer",
        },
    ).json()
    assert w["notices"][0]["recipients"] == []
    return order, w, notice


def body(w, recipient_ids=None):
    return {
        "expected_version": w["version"],
        "actor": "Test PM",
        "reason": "Reviewed the current recipients for this draft.",
        "recipient_ids": recipient_ids
        if recipient_ids is not None
        else [p["id"] for p in w["subscribers"]],
    }


def test_draft_recipients_require_explicit_versioned_action_and_are_audited(client):
    order, w, notice = setup_notice(client)
    path = f"/api/orders/{order['id']}/record/notices/{notice['id']}/recipients"
    payload = body(w)
    headers = {"Idempotency-Key": str(uuid4())}
    response = client.post(path, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["version"] == w["version"] + 1
    assert changed["notices"][0]["recipients"] == w["subscribers"]
    assert changed["notices"][0]["body"] == notice["body"]
    audit = changed["events"][0]
    assert audit["kind"] == "notice_recipients" and audit["actor"] == "Test PM"
    assert audit["before"] == {"recipients": []}
    assert audit["after"] == {"recipients": w["subscribers"]}
    assert client.post(path, json=payload, headers=headers).json() == changed
    assert (
        client.post(
            path, json=payload, headers={"Idempotency-Key": str(uuid4())}
        ).status_code
        == 409
    )
    assert view(client, order) == changed
    with Session() as s:
        assert not s.scalar(select(NoticeDelivery))
        assert not s.scalar(select(Job).where(Job.kind == "send_notice"))


def test_empty_duplicate_unknown_and_cross_order_recipients_are_rejected(client):
    order, w, notice = setup_notice(client)
    other_order, other, _ = setup_notice(client)
    path = f"/orders/{order['id']}/record/notices/{notice['id']}/recipients"
    own_id = w["subscribers"][0]["id"]
    for ids in [
        [],
        [own_id, own_id],
        ["unknown-recipient"],
        [other["subscribers"][0]["id"]],
    ]:
        assert post(client, path, body(w, ids)).status_code == 422
    # Neither a notice ID nor recipients from another order can be injected.
    assert (
        post(
            client,
            f"/orders/{other_order['id']}/record/notices/{notice['id']}/recipients",
            body(other),
        ).status_code
        == 404
    )
    assert view(client, order) == w


@pytest.mark.parametrize("delivery_status", ["queued", "sending", "failed", "sent"])
def test_a_delivery_permanently_freezes_its_notice_recipients(client, delivery_status):
    order, w, notice = setup_notice(client)
    frozen = {
        **notice,
        "recipients": [
            {
                "id": "previous-person",
                "name": "Previous PM",
                "email": "previous@example.com",
                "team": "Customer",
            }
        ],
    }
    with Session.begin() as s:
        s.add(
            NoticeDelivery(
                order_id=order["id"],
                notice_id=notice["id"],
                status=delivery_status,
                payload=frozen,
            )
        )
    response = post(
        client,
        f"/orders/{order['id']}/record/notices/{notice['id']}/recipients",
        body(w),
    )
    assert response.status_code == 409
    assert view(client, order)["notices"][0]["recipients"] == []
    with Session() as s:
        assert s.scalar(select(NoticeDelivery)).payload == frozen
        assert not s.scalar(select(Job).where(Job.kind == "send_notice"))
