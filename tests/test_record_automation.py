from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select
from test_records import create, edit_body, post, upload, view

from backend.records.automation import anchors_in, scheduled_pass
from backend.storage.db import Session
from backend.storage.models import NoticeDelivery, OrderRecord, now


def action(client, order, w, suffix, **values):
    return post(
        client,
        f"/orders/{order['id']}/record/coordination/{suffix}",
        {
            "expected_version": w["version"],
            "actor": "Test PM",
            "reason": "Reviewed the original evidence.",
            **values,
        },
    )


def example(client):
    o = create(client)
    upload(
        client,
        o,
        "drawing-E10.txt",
        b"Drawing sheet: E-10\nEquipment tag: CB-12\nRevision: B\nTrip unit: TU-2500",
        role="approval_drawing",
        rev="B",
    )
    upload(
        client,
        o,
        "drawing-E11.txt",
        b"Drawing sheet: E-11\nEquipment tag: MSB-2\nRevision: B",
        role="approval_drawing",
        rev="B",
    )
    upload(
        client,
        o,
        "drawing-E12.txt",
        b"Drawing sheet: E-12\nEquipment tag: MSB-2\nRevision: B",
        role="approval_drawing",
        rev="B",
    )
    w = upload(
        client,
        o,
        "comments.txt",
        b"Comment 7: Check CB-12 on E-10.\nComment 8: Confirm MSB-2 finish.\nComment 11: Update the red item.",
        rev="B",
    )
    return o, w


def test_exact_ambiguous_and_missing_anchors_are_distinct_and_stable(client):
    o, w = example(client)
    c7, c8, c11 = w["comments"]
    assert c7["target_source_id"] and not c7["reviewed"]
    assert not c8["target_source_id"] and not c11["target_source_id"]
    assert all(
        c["status"] == "open" and not c["response"] and c["text"] == c["original_text"]
        for c in w["comments"]
    )
    proposals = [
        x for x in w["coordination"]["suggestions"] if x["status"] == "pending"
    ]
    link = next(x for x in proposals if x["kind"] == "drawing_link")
    assert link["comment_id"] == c8["id"] and len(link["candidates"]) == 2
    assert any(
        x["kind"] == "clarification" and x["comment_id"] == c11["id"] for x in proposals
    )
    assert len([x for x in w["coordination"]["links"] if x["automatic"]]) == 1
    assert view(client, o) == w
    assert action(client, o, w, "run").json() == w
    assert not any(
        x["type"] in {"notified", "included_in_approval", "answers"}
        for x in w["coordination"]["links"]
    )
    assert any(
        x["status"] == "needs_review" for x in w["coordination"]["readiness"]["release"]
    )


def test_conflicting_explicit_sheet_never_autolinks(client):
    assert anchors_in("Check CB-12 on E-10.") == ({"CB-12"}, {"E-10"})
    o = create(client)
    upload(
        client,
        o,
        "wrong-sheet.txt",
        b"Sheet E-11\nEquipment CB-12",
        role="approval_drawing",
    )
    w = upload(client, o, "comment.txt", b"Comment 1: Check CB-12 on E-10.")
    assert not w["comments"][0]["target_source_id"]
    assert not any(x["automatic"] for x in w["coordination"]["links"])


def test_accept_edit_skip_and_idempotency_require_current_order_evidence(client):
    o, w = example(client)
    suggestion = next(
        x
        for x in w["coordination"]["suggestions"]
        if x["kind"] == "drawing_link" and x["status"] == "pending"
    )
    wrong = action(
        client,
        o,
        w,
        f"suggestions/{suggestion['id']}",
        action="accept",
        target_source_id="outside-order",
    )
    assert wrong.status_code == 422
    path = f"/api/orders/{o['id']}/record/coordination/suggestions/{suggestion['id']}"
    body = {
        "expected_version": w["version"],
        "actor": "Test PM",
        "reason": "Confirmed drawing E-11.",
        "action": "accept",
        "target_source_id": suggestion["candidates"][0]["source_id"],
    }
    headers = {"Idempotency-Key": str(uuid4())}
    r = client.post(path, json=body, headers=headers)
    assert r.status_code == 200, r.text
    w = r.json()
    assert client.post(path, json=body, headers=headers).json() == w
    c8 = w["comments"][1]
    assert c8["target_source_id"] == body["target_source_id"] and not c8["reviewed"]
    assert not next(
        x
        for x in w["coordination"]["links"]
        if x["from_id"] == c8["id"] and x["to_type"] == "source"
    )["automatic"]
    assert (
        next(t for t in w["coordination"]["tasks"] if t["comment_id"] == c8["id"])[
            "role"
        ]
        == "drafting"
    )
    clarification = next(
        x
        for x in w["coordination"]["suggestions"]
        if x["kind"] == "clarification" and x["status"] == "pending"
    )
    r = action(
        client,
        o,
        w,
        f"suggestions/{clarification['id']}",
        action="accept",
        draft="Please confirm the sheet and device for the red markup.",
    )
    assert r.status_code == 200, r.text
    w = r.json()
    assert (
        w["notices"][0]["status"] == "draft" and "red markup" in w["notices"][0]["body"]
    )
    status = next(
        x
        for x in w["coordination"]["suggestions"]
        if x["kind"] == "status_update" and x["status"] == "pending"
    )
    w = action(client, o, w, f"suggestions/{status['id']}", action="skip").json()
    assert w["coordination"]["metrics"]["accepted_edited"] >= 1
    assert w["coordination"]["metrics"]["skipped"] == 1


def test_undo_is_safe_suppressed_and_preserves_original(client):
    o, w = example(client)
    c = w["comments"][0]
    auto = next(a for a in w["coordination"]["activity"] if a["kind"] == "drawing_link")
    w = action(client, o, w, f"activity/{auto['id']}/undo").json()
    assert not w["comments"][0]["target_source_id"]
    assert w["comments"][0]["original_text"] == c["original_text"]
    assert not view(client, o)["comments"][0]["target_source_id"]
    # A later reviewer edit prevents an old automatic action from clobbering it.
    suggestion = next(
        x
        for x in w["coordination"]["suggestions"]
        if x["kind"] == "drawing_link"
        and x["comment_id"] == c["id"]
        and x["status"] == "pending"
    )
    w = action(client, o, w, f"suggestions/{suggestion['id']}", action="accept").json()
    accepted = next(
        a
        for a in w["coordination"]["activity"]
        if a["kind"] == "drawing_link" and a["actor"] == "Test PM"
    )
    r = post(
        client,
        f"/orders/{o['id']}/record/comments/{c['id']}",
        edit_body(w, w["comments"][0], reviewed=True),
    )
    w = r.json()
    assert action(client, o, w, f"activity/{accepted['id']}/undo").status_code == 409


def test_changed_context_stales_draft_and_never_changes_approval(client):
    o = create(client)
    w = upload(client, o, "comments.txt", b"Comment 1: Confirm the red item.", rev="A")
    old = next(
        s for s in w["coordination"]["suggestions"] if s["kind"] == "clarification"
    )
    c = w["comments"][0]
    w = post(
        client,
        f"/orders/{o['id']}/record/comments/{c['id']}",
        edit_body(w, c, revision="B", reviewed=True),
    ).json()
    assert (
        next(s for s in w["coordination"]["suggestions"] if s["id"] == old["id"])[
            "status"
        ]
        == "stale"
    )
    new = next(
        s
        for s in w["coordination"]["suggestions"]
        if s["kind"] == "clarification" and s["status"] == "pending"
    )
    assert "(B)" in new["draft"]
    assert (
        action(client, o, w, f"suggestions/{old['id']}", action="accept").status_code
        == 409
    )
    w = post(
        client,
        f"/orders/{o['id']}/record/approvals",
        {
            "expected_version": w["version"],
            "actor": "PM",
            "reason": "Reviewed the recorded source.",
            "label": "Internal record approval",
        },
    ).json()
    approval_id = w["approvals"][-1]["id"]
    w = action(
        client,
        o,
        w,
        "settings",
        weekly_digest_enabled=True,
        customer_due_date="",
        role_owners={"pm": "Test PM"},
    ).json()
    assert w["connections"]["after_approval"] == {
        "comment_ids": [],
        "change_ids": [],
        "document_ids": [],
    }
    assert w["approvals"][-1]["id"] == approval_id


def test_weekly_draft_opt_in_idempotency_and_due_without_digest(client):
    o = create(client)
    w = upload(client, o, "comments.txt", b"Comment 1: Confirm the red item.")
    assert scheduled_pass() == 0
    future = now() + timedelta(days=2)
    w = action(
        client,
        o,
        w,
        "settings",
        weekly_digest_enabled=False,
        customer_due_date=future.date().isoformat(),
        role_owners={},
    ).json()
    assert not any(s["kind"] == "reminder" for s in w["coordination"]["suggestions"])
    assert scheduled_pass(instant=future + timedelta(days=1)) == 1
    with Session() as s:
        data = s.scalar(select(OrderRecord).where(OrderRecord.order_id == o["id"])).data
        assert any(
            x["kind"] == "reminder" and x["status"] == "pending"
            for x in data["coordination"]["suggestions"]
        )
    w = view(client, o)
    w = action(
        client,
        o,
        w,
        "settings",
        weekly_digest_enabled=True,
        customer_due_date="",
        role_owners={},
    ).json()
    assert scheduled_pass() == 1
    w = view(client, o)
    assert scheduled_pass() == 0
    assert view(client, o) == w
    weekly = next(
        s
        for s in w["coordination"]["suggestions"]
        if s["kind"] == "weekly_digest" and s["status"] == "pending"
    )
    c = w["comments"][0]
    w = post(
        client,
        f"/orders/{o['id']}/record/comments/{c['id']}",
        edit_body(
            w, c, response="Please refer to E-12.", responder="PM", status="responded"
        ),
    ).json()
    assert (
        action(client, o, w, f"suggestions/{weekly['id']}", action="accept").status_code
        == 409
    )
    assert not w["notices"]


def test_revision_impact_routes_to_production_without_inventing_fix(client):
    o, w = example(client)
    upload(
        client,
        o,
        "bom.csv",
        b"Equipment tag,Part\nCB-12,TU-2500\n",
        role="bom",
        rev="B",
    )
    w = upload(
        client,
        o,
        "drawing-E10-C.txt",
        b"Drawing sheet: E-10\nEquipment tag: CB-12\nRevision: C\nTrip unit: TU-3000",
        role="approval_drawing",
        rev="C",
    )
    impacts = [
        s
        for s in w["coordination"]["suggestions"]
        if s["kind"] == "impact_review" and s["status"] == "pending"
    ]
    assert impacts and "No mismatch has been confirmed" in impacts[0]["draft"]
    assert any(t["role"] == "production" for t in w["coordination"]["tasks"])
    assert (
        not w["changes"]
        and not w["approvals"]
        and all(c["status"] == "open" for c in w["comments"])
    )


def test_queued_delivery_prevents_undo_and_sent_links_use_frozen_recipients(client):
    o = create(client)
    w = upload(client, o, "comments.txt", b"Comment 1: Confirm the red item.")
    suggestion = next(
        s for s in w["coordination"]["suggestions"] if s["kind"] == "clarification"
    )
    w = action(client, o, w, f"suggestions/{suggestion['id']}", action="accept").json()
    notice = w["notices"][0]
    a = next(a for a in w["coordination"]["activity"] if a["kind"] == "draft")
    with Session.begin() as s:
        s.add(
            NoticeDelivery(
                order_id=o["id"],
                notice_id=notice["id"],
                status="queued",
                payload={
                    "recipients": [
                        {
                            "id": "recipient-1",
                            "email": "pm@example.com",
                            "name": "PM",
                            "team": "Customer",
                        }
                    ]
                },
            )
        )
    w = view(client, o)
    assert not next(x for x in w["coordination"]["activity"] if x["id"] == a["id"])[
        "undoable"
    ]
    assert action(client, o, w, f"activity/{a['id']}/undo").status_code == 409
    with Session.begin() as s:
        s.scalar(select(NoticeDelivery)).status = "sent"
    w = view(client, o)
    links = [x for x in w["coordination"]["links"] if x["type"] == "notified"]
    assert len(links) == 1 and links[0]["to_id"] == "recipient-1"


def test_new_evidence_flags_old_auto_link_and_manual_assignment_is_preserved(client):
    o = create(client)
    upload(
        client, o, "E10.txt", b"Sheet E-10\nEquipment CB-12", role="approval_drawing"
    )
    w = upload(client, o, "comments.txt", b"Comment 1: Confirm CB-12.")
    original_target = w["comments"][0]["target_source_id"]
    assert original_target
    task = w["coordination"]["tasks"][0]
    w = action(
        client,
        o,
        w,
        f"tasks/{task['id']}",
        role="production",
        owner="Production PM",
        status="in_progress",
        note="Checking the build record.",
    ).json()
    w = upload(
        client, o, "E11.txt", b"Sheet E-11\nEquipment CB-12", role="approval_drawing"
    )
    assert w["comments"][0]["target_source_id"] == original_target
    assert any(
        s["kind"] == "impact_review"
        and s["status"] == "pending"
        and "Recheck drawing" in s["title"]
        for s in w["coordination"]["suggestions"]
    )
    task = next(t for t in w["coordination"]["tasks"] if t["id"] == task["id"])
    assert (
        task["role"] == "production"
        and task["owner"] == "Production PM"
        and task["status"] == "in_progress"
    )
    assert view(client, o) == w


def test_exact_email_thread_headers_link_documents_without_answering_comments(client):
    from backend.records.automation import projection

    data = {
        "comments": [],
        "changes": [],
        "events": [],
        "approvals": [],
        "notices": [],
        "subscribers": [],
    }
    documents = [
        {"id": "mail-1", "email": {"message_id": "<thread@example.com>"}},
        {
            "id": "mail-2",
            "email": {
                "message_id": "<reply@example.com>",
                "in_reply_to": "<thread@example.com>",
                "references": "<thread@example.com>",
            },
        },
    ]
    result = projection(data, documents, [])
    assert len(result["links"]) == 1
    assert (
        result["links"][0]["from_id"] == "mail-2"
        and result["links"][0]["to_id"] == "mail-1"
    )
    assert result["links"][0]["type"] == "references"
