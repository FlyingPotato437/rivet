"""Tenant/order and record-version controls for PM transcription edits."""

from uuid import uuid4

from test_records import create, upload, view


def test_correction_stores_label_without_closing_or_reviewing_comment(client):
    order = create(client)
    w = upload(client, order, "transcription-control.txt", b"Comment 1: LINE UP")
    comment = w["comments"][0]
    path = f"/api/orders/{order['id']}/record/comments/{comment['id']}/transcription"
    payload = {
        "expected_version": w["version"],
        "actor": "Test PM",
        "reason": "Checked the source",
        "text": "LINEUP",
    }
    key = str(uuid4())
    saved = client.post(path, json=payload, headers={"Idempotency-Key": key})
    assert saved.status_code == 200, saved.text
    value = saved.json()
    edited = value["comments"][0]
    assert (
        edited["text"] == "LINEUP"
        and edited["original_text"] == comment["original_text"]
    )
    assert not edited["reviewed"] and edited["status"] == comment["status"]
    assert len(value["transcription_examples"]) == 1
    assert value["transcription_examples"][0]["source_sha256"]
    assert (
        client.post(path, json=payload, headers={"Idempotency-Key": key}).status_code
        == 200
    )
    assert len(view(client, order)["transcription_examples"]) == 1
    assert (
        client.post(
            path,
            json={**payload, "text": "another edit"},
            headers={"Idempotency-Key": str(uuid4())},
        ).status_code
        == 409
    )
    other = create(client)
    bad = client.post(
        path.replace(order["id"], other["id"]),
        json={**payload, "expected_version": view(client, other)["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert bad.status_code == 404
