from copy import deepcopy
from io import BytesIO
from uuid import uuid4

from openpyxl import load_workbook
from test_equipment_workflow import corpus, correctable, prepare  # noqa: F401
from test_records import edit_body

from backend.records.response_log import build, export_response
from backend.storage.db import Session
from backend.storage.models import Order


def comment(text, **values):
    return {
        "id": "one",
        "text": text,
        "original_text": text,
        "response": "",
        "status": "open",
        "reviewed": False,
        "origin": "annotation",
        **values,
    }


def check(field="nameplate", value="COMMISSIONING TOOLS", **values):
    cell = {"value": value, "evidence": [{"name": "Spec R1", "page": 1}]}
    return {
        "device_tag": "CB-13",
        "field": field,
        "label": field,
        "status": "verified",
        "required": cell,
        "target_drawing": cell,
        "target_bom": cell,
        **values,
    }


def response(c, checks):
    return build([c], [], {"target_revision": "B", "checks": checks})["comments"][
        c["id"]
    ]


def test_unambiguous_field_answer_has_evidence_but_does_not_approve_comment():
    c = comment("CB-13 nameplate must read COMMISSIONING TOOLS")
    before = deepcopy(c)
    result = response(c, [check()])
    assert result["status"] == "checked"
    assert "COMMISSIONING TOOLS" in result["response"]
    assert result["references"] == ["Spec R1 · p. 1"]
    assert not result["reviewed"]
    assert c == before and c["status"] == "open"


def test_unknown_and_multiple_targets_never_become_verified():
    assert (
        response(comment("CB-13 and CB-14 nameplates"), [check()])["status"]
        == "clarification"
    )
    assert (
        response(comment("CB-13 nameplate"), [check(), check()])["status"]
        == "clarification"
    )
    assert (
        response(comment("CB-13 nameplate"), [check(required={"value": None})])[
            "status"
        ]
        == "clarification"
    )
    assert (
        response(comment("CB-13 nameplate must read SOMETHING ELSE"), [check()])[
            "status"
        ]
        == "clarification"
    )
    assert (
        response(comment("CB-13 trip must be 150 A"), [check("trip_a", "125")])[
            "status"
        ]
        == "clarification"
    )


def test_voltage_and_poles_are_not_extra_device_tags_and_transcription_stays_unreviewed():
    c = comment("CB-13 plate: 60 A / 208Y/120 V / 3P+N+G", origin="ai_transcription")
    result = response(c, [check(value="60 A / 208Y/120 V / 3P+N+G")])
    assert result["status"] == "checked"
    assert "AI transcription" in result["response"] and not result["reviewed"]


def test_graphical_marks_and_human_notes_are_not_falsely_completed():
    c = comment("Graphical markup", original_text="")
    assert response(c, [check()])["status"] == "clarification"
    human = comment(
        "CB-13 nameplate",
        response="Engineer requires alternate wording",
        responder="Morgan",
    )
    w = {"response_log": build([human], [], {"checks": [check()]})}
    assert w["response_log"]["comments"] == {}
    assert export_response(w, human) == (human["response"], "Morgan", "open")


def test_original_revision_mismatch_is_not_claimed_fixed():
    c = comment("CB-13 nameplate must read COMMISSIONING TOOLS")
    assert response(c, [check(status="mismatch")])["status"] == "remaining"


def test_saved_files_drive_log_and_exports_without_record_or_reply_mutation(
    client, request
):
    oid, before = correctable(client, request.getfixturevalue("corpus"))
    record = client.get(f"/api/orders/{oid}/record").json()
    result = client.post(
        f"/api/orders/{oid}/record/comments",
        headers={"Idempotency-Key": str(uuid4())},
        json={
            "expected_version": record["version"],
            "actor": "Test engineer",
            "reason": "Returned drawing request",
            "number": "1",
            "revision": "C",
            "text": "CB-12 trip unit must be TU-3000",
            "reviewed": False,
        },
    )
    assert result.status_code == 200, result.text
    w = result.json()
    c = next(c for c in w["comments"] if c["text"].startswith("CB-12 trip unit"))
    # Explicit manual text can be checked without fabricating source spans.
    assert w["response_log"]["comments"][c["id"]]["status"] == "remaining"
    # Refresh expected order version, then run actual saved DXF/XLSX correction.
    before = client.get(f"/api/orders/{oid}/equipment/corrections").json()
    made = prepare(client, oid, before)
    assert made.status_code == 200, made.text
    with Session() as s:
        state_before = deepcopy(s.get(Order, oid).state)
    w = client.get(f"/api/orders/{oid}/record").json()
    proposed = w["response_log"]["comments"][c["id"]]
    assert proposed["status"] == "checked"
    assert (
        "TU-3000" in proposed["response"]
        and "reopened and rechecked" in proposed["response"]
    )
    saved = next(item for item in w["comments"] if item["id"] == c["id"])
    assert (
        saved["response"] == "" and saved["status"] == "open" and not saved["reviewed"]
    )
    download = client.get(f"/api/orders/{oid}/record/export?format=xlsx")
    book = load_workbook(BytesIO(download.content))
    values = list(book["Comment log"].values)
    headers = values[1]
    row = next(row for row in values[2:] if row[5] == c["text"])
    assert "TU-3000" in row[headers.index("Response")]
    assert row[headers.index("Recorded status")] == "open"
    assert "engineer review" in row[headers.index("Status")]
    book.close()
    # Read projections must not alter the approved/sent record or order state.
    assert client.get(f"/api/orders/{oid}/record").json() == w
    with Session() as s:
        assert s.get(Order, oid).state == state_before
    # A changed saved artifact must remove the projected corrected-file pass.
    from backend.equipment import workflow

    with Session() as s:
        bundle = made.json()["bundles"][0]
        name = next(a["name"] for a in bundle["artifacts"] if a["role"] == "drawing")
        path, _ = workflow.artifact(s, s.get(Order, oid), bundle["id"], name)
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"\nCHANGED\n")
        stale = client.get(f"/api/orders/{oid}/record").json()
        assert stale["response_log"]["comments"][c["id"]]["status"] == "remaining"
    finally:
        path.write_bytes(original)
    # Saving an engineer note takes precedence without marking it sent/closed.
    written = client.post(
        f"/api/orders/{oid}/record/comments/{c['id']}",
        headers={"Idempotency-Key": str(uuid4())},
        json=edit_body(w, saved, response="Engineer clarification", responder="Morgan"),
    )
    assert written.status_code == 200, written.text
    assert c["id"] not in written.json()["response_log"]["comments"]
