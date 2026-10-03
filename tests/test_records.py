from email.message import EmailMessage
from io import BytesIO
from uuid import uuid4

from openpyxl import load_workbook

from backend.storage.db import Session
from backend.storage.models import Document
from backend.worker import claim, process


def test_rivet_questions_are_read_only_and_reject_unscoped_citations(
    client, monkeypatch
):
    from backend.records import assistant

    o = create(client)
    w = upload(client, o, "review.txt", b"Comment 1: Confirm paint finish.")
    c = w["comments"][0]
    captured = {}

    def answer(prompt, context, history):
        captured.update(context)
        return {
            "answer": "The paint finish comment still needs a response.",
            "source_ids": c["source_ids"],
            "comment_ids": [c["id"]],
            "change_ids": [],
        }

    monkeypatch.setattr(assistant, "model_answer", answer)
    endpoint = f"/api/orders/{o['id']}/record/ask"
    result = client.post(endpoint, json={"question": "What needs a response?"})
    assert result.status_code == 200, result.text
    assert result.json()["version"] == w["version"]
    assert "subscribers" not in captured and "shares" not in captured
    assert view(client, o) == w
    monkeypatch.setattr(
        assistant,
        "model_answer",
        lambda *args: {
            "answer": "Unsupported citation",
            "source_ids": ["another-orders-source"],
            "comment_ids": [],
            "change_ids": [],
        },
    )
    assert client.post(endpoint, json={"question": "What changed?"}).status_code == 502
    assert view(client, o) == w
    assert (
        client.post(
            endpoint, json={"question": "Close it", "action": "close"}
        ).status_code
        == 422
    )


def test_rivet_question_provider_receives_no_mutation_tools(monkeypatch):
    import json

    from backend.records import assistant

    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            captured.update(kwargs["json"])
            return type(
                "Response",
                (),
                {
                    "status_code": 200,
                    "json": lambda _: {
                        "output": [
                            {
                                "type": "function_call",
                                "name": "answer",
                                "arguments": json.dumps(
                                    {
                                        "answer": "No response recorded.",
                                        "source_ids": [],
                                        "comment_ids": [],
                                        "change_ids": [],
                                    }
                                ),
                            }
                        ]
                    },
                },
            )()

    monkeypatch.setattr(assistant.httpx, "Client", Client)
    assistant.model_answer("Close the comment", {}, [])
    assert [t["name"] for t in captured["tools"]] == ["answer"]
    assert captured["store"] is False
    assert captured["tools"][0]["strict"] is True


def test_comment_pdf_response_matrix_contains_saved_values(client):
    import pdfplumber

    o = create(client)
    w = upload(client, o, "review.txt", b"Comment 1: Confirm the drain location.")
    c = w["comments"][0]
    post(
        client,
        f"/orders/{o['id']}/record/comments/{c['id']}",
        edit_body(
            w,
            c,
            reviewed=True,
            status="responded",
            response="Location confirmed on drawing H-10.",
            responder="Morgan Lane",
        ),
    )
    response = client.get(f"/api/orders/{o['id']}/record/export?format=pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    with pdfplumber.open(BytesIO(response.content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        # Read the response column: lines wrap in a printed matrix.
        response_text = " ".join(
            " ".join(
                (
                    page.crop(
                        (page.width * 0.68, 0, page.width, page.height)
                    ).extract_text()
                    or ""
                ).split()
            )
            for page in pdf.pages
        )
    assert "Location confirmed on drawing H-10." in response_text
    assert "Morgan Lane" in text and "responded" in text


def post(client, path, body):
    return client.post(
        "/api" + path, json=body, headers={"Idempotency-Key": str(uuid4())}
    )


def create(client):
    response = post(
        client,
        "/orders",
        {
            "title": "Custom hydraulic skid",
            "customer": "Wren Manufacturing",
            "category": "Hydraulic equipment",
        },
    )
    assert response.status_code == 200
    return response.json()["order"]


def view(client, o):
    r = client.get(f"/api/orders/{o['id']}/record")
    assert r.status_code == 200, r.text
    return r.json()


def upload(client, o, name, content, role="markups", rev="02"):
    r = client.post(
        f"/api/projects/{o['project_id']}/documents",
        files={"file": (name, content, "text/plain")},
        data={"kind": role, "revision_label": rev},
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert r.status_code == 202, r.text
    process(claim())
    return view(client, o)


def edit_body(w, c, **overrides):
    fields = [
        "number",
        "revision",
        "text",
        "author",
        "authored_at",
        "status",
        "response",
        "responder",
        "target_source_id",
        "linked_email_id",
        "reviewed",
    ]
    return {
        **{k: c[k] for k in fields},
        "expected_version": w["version"],
        "actor": "Morgan Lane",
        "reason": "Checked the original and drawing reference.",
        **overrides,
    }


def test_custom_order_record_edits_audit_and_no_engineering_dependency(client):
    o = create(client)
    w = upload(
        client,
        o,
        "review.txt",
        b"Comment 1: Move the drain connection to page 10.\nComment 2: Confirm paint finish.",
    )
    assert len(w["comments"]) == 2
    assert all(c["confidence"] == "Needs review" for c in w["comments"])
    c = w["comments"][0]
    assert "page 10" in c["flags"][0]
    body = edit_body(
        w,
        c,
        text="Move the drain connection shown on drawing H-10.",
        author="Customer PM",
        reviewed=True,
        response="Updated connection location in revision 03.",
        responder="Morgan Lane",
        status="responded",
    )
    r = post(client, f"/orders/{o['id']}/record/comments/{c['id']}", body)
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited["comments"][0]["original_text"] == c["text"]
    assert edited["comments"][0]["response"] == body["response"]
    assert edited["events"][0]["before"]["text"] == c["text"]
    assert edited["events"][0]["after"]["text"] == body["text"]
    assert (
        post(client, f"/orders/{o['id']}/record/comments/{c['id']}", body).status_code
        == 409
    )
    # A later source cannot overwrite human corrections or remove prior-round comments.
    final = upload(
        client, o, "review-next.txt", b"Comment 1: Confirm the motor label.", rev="03"
    )
    assert len(final["comments"]) == 3
    assert (
        next(x for x in final["comments"] if x["id"] == c["id"])["text"] == body["text"]
    )
    with Session.begin() as s:
        docs = list(s.query(Document).filter_by(project_id=o["project_id"]))
        assert len(docs) == 2
    excel = client.get(f"/api/orders/{o['id']}/record/export")
    assert excel.status_code == 200
    rows = list(load_workbook(BytesIO(excel.content)).active.values)
    assert any(body["text"] in row and c["text"] in row for row in rows)


def test_record_source_scope_status_gates_and_immutable_shared_approval(client):
    o = create(client)
    other = create(client)
    foreign = upload(client, other, "other.txt", b"Comment 1: Other private source.")
    w = upload(client, o, "comments.txt", b"Comment 1: Confirm the connection.")
    c = w["comments"][0]
    base = f"/orders/{o['id']}/record"
    assert (
        post(
            client,
            base + "/comments/" + c["id"],
            edit_body(w, c, target_source_id=foreign["sources"][0]["id"]),
        ).status_code
        == 422
    )
    assert (
        post(
            client, base + "/comments/" + c["id"], edit_body(w, c, status="closed")
        ).status_code
        == 422
    )
    assert (
        post(
            client,
            base + "/approvals",
            {
                "expected_version": w["version"],
                "actor": "Morgan Lane",
                "reason": "Reviewed comment record.",
                "label": "Rev 02 approved log",
            },
        ).status_code
        == 422
    )
    w = post(
        client, base + "/comments/" + c["id"], edit_body(w, c, reviewed=True)
    ).json()
    w = post(
        client,
        base + "/approvals",
        {
            "expected_version": w["version"],
            "actor": "Morgan Lane",
            "reason": "Reviewed comment record.",
            "label": "Rev 02 approved log",
        },
    ).json()
    share = post(
        client,
        base + "/shares",
        {
            "expected_version": w["version"],
            "actor": "Morgan Lane",
            "reason": "Share reviewed record.",
            "approval_id": w["approvals"][0]["id"],
        },
    ).json()
    public = client.get("/api/shared/" + share["token"])
    assert public.status_code == 200
    assert "subscribers" not in public.json() and "events" not in public.json()
    assert (
        client.get(
            f"/api/shared/{share['token']}/documents/{foreign['documents'][0]['id']}"
        ).status_code
        == 404
    )
    original = public.json()["comments"][0]["text"]
    post(
        client,
        base + "/comments/" + c["id"],
        edit_body(w, w["comments"][0], text="Later working edit"),
    )
    assert (
        client.get("/api/shared/" + share["token"]).json()["comments"][0]["text"]
        == original
    )
    w = view(client, o)
    revoked = post(
        client,
        base + f"/shares/{share['id']}/revoke",
        {
            "expected_version": w["version"],
            "actor": "Morgan Lane",
            "reason": "Stop access to this link.",
        },
    )
    assert revoked.status_code == 200
    assert client.get("/api/shared/" + share["token"]).status_code == 404


def test_email_import_attachments_dedup_and_notice_drafts(client):
    o = create(client)
    message = EmailMessage()
    message["From"] = "Customer Reviewer <reviewer@example.com>"
    message["To"] = "pm@example.com"
    message["Date"] = "Thu, 01 Oct 2026 10:00:00 -0700"
    message["Subject"] = "Round 2 comments"
    message["Message-ID"] = "<round2@example.com>"
    message.set_content(
        "Please clarify the connection. The location on page 2 refers to page 10."
    )
    message.add_attachment(
        b"Comment 4: Confirm mounting bracket.",
        maintype="text",
        subtype="plain",
        filename="markups.txt",
    )

    def import_email():
        return client.post(
            f"/api/orders/{o['id']}/record/email",
            files={"file": ("review.eml", message.as_bytes(), "message/rfc822")},
            data={"revision_label": "02"},
            headers={"Idempotency-Key": str(uuid4())},
        )

    r = import_email()
    assert r.status_code == 202, r.text
    while task := claim():
        process(task)
    w = view(client, o)
    assert len(w["documents"]) == 2 and len(w["comments"]) == 2
    email_comment = next(c for c in w["comments"] if c["origin"] == "email")
    assert "reviewer@example.com" in email_comment["author"]
    assert email_comment["authored_at"]
    assert import_email().json()["duplicate"] is True
    w = post(
        client,
        f"/orders/{o['id']}/record/subscribers",
        {
            "expected_version": w["version"],
            "actor": "Morgan Lane",
            "reason": "Include production on revisions.",
            "name": "Production team",
            "email": "production@example.com",
            "team": "Production",
        },
    ).json()
    w = post(
        client,
        f"/orders/{o['id']}/record/comments/{email_comment['id']}",
        edit_body(w, email_comment, reviewed=True),
    ).json()
    assert len(w["notices"]) == 1
    n = w["notices"][0]
    assert n["status"] == "draft"
    draft = client.get(f"/api/orders/{o['id']}/record/notices/{n['id']}/draft")
    assert (
        draft.status_code == 200
        and b"X-Unsent: 1" in draft.content
        and b"production@example.com" in draft.content
    )


def test_text_comparison_and_comment_linked_change_are_order_scoped(client):
    o = create(client)
    w = upload(
        client,
        o,
        "drawing-01.txt",
        b"Drain: left\nMotor: 12 kW",
        "approval_drawing",
        "01",
    )
    a = w["documents"][0]["id"]
    w = upload(
        client,
        o,
        "drawing-02.txt",
        b"Drain: right\nMotor: 12 kW",
        "approval_drawing",
        "02",
    )
    b = w["documents"][1]["id"]
    r = client.get(f"/api/orders/{o['id']}/record/compare?before={a}&after={b}")
    assert (
        r.status_code == 200
        and r.json()["changes"][0]["before"] == "Drain: left"
        and r.json()["changes"][0]["after"] == "Drain: right"
    )
    w = upload(client, o, "comments.txt", b"Comment 9: Relocate drain.")
    body = {
        "expected_version": w["version"],
        "actor": "Morgan Lane",
        "reason": "Confirmed from revision documents.",
        "title": "Drain location",
        "before": "left",
        "after": "right",
        "from_revision": "01",
        "to_revision": "02",
        "comment_ids": [w["comments"][0]["id"]],
        "requested_by": "Customer PM",
        "approved_by": "Customer PM",
        "approved_at": "2026-10-01",
    }
    r = post(client, f"/orders/{o['id']}/record/changes", body)
    assert r.status_code == 200, r.text
    assert r.json()["changes"][0]["comment_ids"] == body["comment_ids"]


def test_pdf_metadata_and_empty_markup_preserve_exact_area(client, tmp_path):
    from scripts.make_order_replay import write_pdf

    path = tmp_path / "review.pdf"
    write_pdf(
        path,
        "Custom tank review",
        ["Drawing G-01 | Revision 04"],
        [
            {
                "id": "c1",
                "text": "Comment 1: Move the access panel on page 10.",
                "author": "Ana Fields",
                "date": "D:20261001123000Z",
            },
            {"id": "cloud", "text": "", "kind": "Polygon"},
        ],
    )
    o = create(client)
    w = upload(client, o, "review.pdf", path.read_bytes())
    assert len(w["comments"]) == 2
    comment = next(c for c in w["comments"] if c["number"] == "1")
    assert comment["author"] == "Ana Fields"
    assert "20261001" in comment["authored_at"]
    assert any("page 10" in f for f in comment["flags"])
    cloud = next(c for c in w["comments"] if c["original_text"] == "")
    assert cloud["source_ids"] and not cloud["reviewed"]
    span = next(sp for sp in w["sources"] if sp["id"] == cloud["source_ids"][0])
    assert span["location"]["annotation_id"] == "cloud"
    assert len(span["location"]["bbox"]) == 4
