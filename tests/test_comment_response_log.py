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


def test_current_saved_agent_answers_reach_log_without_becoming_human_decisions(client):
    from test_records import create, upload

    from backend.domain.service import get
    from backend.records import agent_service
    from backend.records.agent_schemas import RunWrite
    from backend.storage.models import RecordAgentRun

    o = create(client)
    w = upload(client, o, "return.txt", b"Comment 1: Confirm enclosure rating.")
    c = w["comments"][0]
    with Session.begin() as s:
        order = get(s, Order, o["id"])
        run = agent_service.create_run(
            s,
            order,
            RunWrite(
                expected_version=w["version"],
                actor="Test PM",
                reason="Prepare cited responses",
                goal="Answer the returned comment",
            ),
        )
        agent_service.stage(
            s,
            order,
            run,
            w,
            [
                {
                    "type": "draft_summary",
                    "comment_id": c["id"],
                    "title": "Enclosure clarification",
                    "text": "Please confirm the required enclosure rating; no specification was supplied.",
                    "evidence_ids": c["source_ids"],
                }
            ],
            "Prepared response",
            "Draft for review",
        )
        run.status = "completed"
        rid = run.id
    current = client.get(f"/api/orders/{o['id']}/record").json()
    saved = current["response_log"]["comments"][c["id"]]
    assert saved["status"] == "prepared" and "enclosure rating" in saved["response"]
    assert (
        current["comments"][0]["response"] == ""
        and not current["comments"][0]["reviewed"]
    )
    from backend.records.exports import rows

    compact = next(rows(current, compact=True))
    full = next(rows(current))
    assert "Sources: D1" in compact[10] and "return.txt" not in compact[10]
    assert "Sources: return.txt" in full[10]
    # New source evidence invalidates the old draft instead of silently reusing it.
    later = upload(
        client, o, "spec.txt", b"Enclosure: NEMA 4", role="specification", rev="02"
    )
    assert not later["response_log"]["comments"]
    with Session() as s:
        assert get(s, RecordAgentRun, rid).status == "completed"


def test_pdf_continuation_chunks_preserve_newlines_and_long_tokens():
    from backend.records.exports import _pdf_text_chunks

    value = "first line\nsecond line\n" + ("X" * 83) + " end"
    chunks = _pdf_text_chunks(value, width=10, max_lines=2)

    assert len(chunks) > 3
    assert "".join(chunks) == value
    assert all(
        sum(max(1, (len(line) + 9) // 10) for line in chunk.splitlines()) <= 2
        for chunk in chunks
    )


def test_moderate_unbroken_pdf_comment_gets_attributed_continuation(client):
    import pdfplumber
    from test_records import create, upload

    order = create(client)
    w = upload(client, order, "review.txt", b"Comment 1: Confirm the returned cable route.")
    c = w["comments"][0]
    text = "X" * 1500
    saved = client.post(
        f"/api/orders/{order['id']}/record/comments/{c['id']}",
        headers={"Idempotency-Key": str(uuid4())},
        json=edit_body(w, c, revision="R-17", text=text),
    )
    assert saved.status_code == 200, saved.text

    response = client.get(f"/api/orders/{order['id']}/record/export?format=pdf")
    assert response.status_code == 200
    with pdfplumber.open(BytesIO(response.content)) as pdf:
        page_texts = [page.extract_text() or "" for page in pdf.pages]
    continued = [page for page in page_texts if "continued" in page]
    assert continued
    assert "X" * 1500 in "".join("".join(page.split()) for page in continued)
    assert all(
        "Revision: R-17" in page and "Recorded: open" in page
        for page in continued
    )

    excel = client.get(f"/api/orders/{order['id']}/record/export?format=xlsx")
    workbook = load_workbook(BytesIO(excel.content), read_only=True, data_only=True)
    comment_rows = list(workbook["Comment log"].iter_rows(min_row=3, values_only=True))
    assert len(comment_rows) == 1 and comment_rows[0][0] == "1"
    assert comment_rows[0][5] == text
    workbook.close()


def test_long_comment_and_response_pdfs_keep_all_text_and_recorded_status(client):
    import pdfplumber
    from test_records import create, upload

    order = create(client)
    long_comment = (
        "Confirm the returned cable route. " + ("DETAIL " * 1700) + "COMMENT_ENDMARKER"
    )
    w = upload(
        client, order, "long-review.txt", b"Comment 1: Confirm the returned cable route."
    )
    c = w["comments"][0]
    body = edit_body(w, c, revision="R-17", text=long_comment)
    saved = client.post(
        f"/api/orders/{order['id']}/record/comments/{c['id']}",
        headers={"Idempotency-Key": str(uuid4())},
        json=body,
    )
    assert saved.status_code == 200, saved.text

    matrix = client.get(f"/api/orders/{order['id']}/record/export?format=pdf")
    assert matrix.status_code == 200 and matrix.content.startswith(b"%PDF")
    with pdfplumber.open(BytesIO(matrix.content)) as pdf:
        matrix_pages = [page.extract_text() or "" for page in pdf.pages]
        matrix_text = "\n".join(matrix_pages)
        assert len(pdf.pages) > 1
    from backend.records.exports import rows

    assert next(rows(saved.json()))[-1] == "open"
    assert "COMMENT_ENDMARKER" in matrix_text
    assert "R-17" in matrix_text and "Not stated" in matrix_text, matrix_text[:3000]
    assert "Recorded" in matrix_text and "open" in matrix_text
    assert "Resolved" not in matrix_text and "Approved" not in matrix_text
    continued_pages = [page for page in matrix_pages if "continued" in page]
    assert continued_pages, matrix_text[:2500]
    for page in continued_pages:
        assert "R-17" in page and "Page Not stated" in page
        assert "Recorded" in page and "open" in page

    response_text = "Please verify the route against the field markup. " + (
        "REVIEW_DETAIL " * 900
    ) + "RESPONSE_ENDMARKER"
    updated = client.post(
        f"/api/orders/{order['id']}/record/comments/{c['id']}",
        headers={"Idempotency-Key": str(uuid4())},
        json=edit_body(
            saved.json(),
            c,
            revision="R-17",
            text=long_comment,
            reviewed=True,
            status="responded",
            response=response_text,
            responder="Casey Engineer",
        ),
    )
    assert updated.status_code == 200, updated.text
    letter = client.get(
        f"/api/orders/{order['id']}/record/export?format=response_pdf"
    )
    assert letter.status_code == 200 and letter.content.startswith(b"%PDF")
    with pdfplumber.open(BytesIO(letter.content)) as pdf:
        letter_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert len(pdf.pages) > 1
    assert "COMMENT_ENDMARKER" in letter_text
    assert "RESPONSE_ENDMARKER" in letter_text
    assert "DRAFT" in letter_text and "does not" in letter_text
