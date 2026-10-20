import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_company_context import ORG_B, identity, order

from backend.company_context import service as learning
from backend.company_context.models import CompanyFeedback
from backend.company_context.schemas import RuleWrite
from backend.mailbox.models import MailboxConnection, MailboxReceipt
from backend.reply_assistant import service as replies
from backend.reply_assistant.models import ReplyDraft
from backend.reply_assistant.schemas import GenerateReply, ReviewReply
from backend.storage.db import Session
from backend.storage.models import (
    Document,
    Job,
    NoticeDelivery,
    RecordIntake,
    Span,
    now,
)
from backend.worker import claim, process


def fixture_order():
    with Session.begin() as s:
        o, doc, source = order(s)
        source.text = (
            "Comment 1: Please confirm the cable entry position on sheet E-10."
        )
        doc.meta = {**doc.meta, "order_role": "markup"}
        return o.id, doc.id, source.id


def output(source_id):
    return {
        "subject": "Re: drawing review",
        "body": "Please provide sheet E-10 and its revision so we can confirm the cable entry location.",
        "source_ids": [source_id],
        "unresolved": ["Referenced sheet is not present in the retrieved excerpts."],
    }


def prepare(monkeypatch, order_id, source_id, key=None, **body):
    monkeypatch.setattr(replies, "model_reply", lambda snapshot: output(source_id))
    return generate_and_process(
        GenerateReply(order_id=order_id, **body), key or str(uuid4())
    )


def generate_and_process(body, key):
    result = replies.generate(body, key)
    if result["status"] == "generating":
        task = claim()
        assert task and task[2] == "reply_draft" and task[3]["draft_id"] == result["id"]
        process(task)
        with Session() as s:
            return replies.view(s.get(ReplyDraft, result["id"]))
    return result


def receipt(s, order_id, doc_id, message_id="<fixture@example.com>"):
    conn = MailboxConnection(microsoft_tenant_id=str(uuid4()), mailbox="pm@example.com")
    s.add(conn)
    s.flush()
    intake = RecordIntake(
        name="returned.eml",
        blob="fixture.eml",
        sha256="c" * 64,
        order_id=order_id,
        data={"document_id": doc_id},
    )
    s.add(intake)
    s.flush()
    rec = MailboxReceipt(
        connection_id=conn.id,
        provider_message_id=str(uuid4()),
        internet_message_id=message_id,
        intake_id=intake.id,
        data={
            "subject": "Cable entry drawing review",
            "sender": "customer@example.com",
        },
    )
    s.add(rec)
    s.flush()
    return rec


def test_generate_valid_draft_replays_and_never_sends(monkeypatch):
    with identity():
        o, _, source = fixture_order()
        first = prepare(monkeypatch, o, source, key="reply-one")
        assert first["status"] == "ready" and first["origin"] == "ai"
        assert first["source_refs"][0]["id"] == source
        assert "Comment 1" in first["source_refs"][0]["label"]
        assert not first["sent"]
        monkeypatch.setattr(
            replies,
            "model_reply",
            lambda _: pytest.fail("Replay must not generate again"),
        )
        assert replies.generate(GenerateReply(order_id=o), "reply-one") == first
        with pytest.raises(HTTPException) as error:
            replies.generate(
                GenerateReply(order_id=o, instruction="Different request"), "reply-one"
            )
        assert error.value.status_code == 409
        with Session() as s:
            assert not list(s.scalars(select(NoticeDelivery)))


def test_unknown_mail_misassigned_mail_and_tenant_cannot_generate(monkeypatch):
    with identity():
        o, doc, _source = fixture_order()
        other, _, _ = fixture_order()
        with Session.begin() as s:
            receipt(s, o, doc)
        monkeypatch.setattr(
            replies,
            "model_reply",
            lambda _: pytest.fail("Should fail before model call"),
        )
        for body in (
            GenerateReply(order_id=o, internet_message_id="<missing@example.com>"),
            GenerateReply(order_id=other, internet_message_id="<fixture@example.com>"),
        ):
            with pytest.raises(HTTPException) as error:
                replies.generate(body, str(uuid4()))
            assert error.value.status_code == 409
    with identity(ORG_B):
        with pytest.raises(HTTPException) as error:
            replies.generate(GenerateReply(order_id=o), str(uuid4()))
        assert error.value.status_code == 404


def test_current_email_documents_are_prioritized_and_model_has_no_send_tools(
    monkeypatch,
):
    with identity():
        o, doc, source = fixture_order()
        with Session.begin() as s:
            receipt(s, o, doc)
        captured = {}

        def provider(url, **kwargs):
            captured.update(kwargs["json"])
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "name": "prepare_reply",
                            "arguments": json.dumps(output(source)),
                        }
                    ]
                },
            )

        monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-real")
        monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
        monkeypatch.setattr(replies.httpx, "post", provider)
        result = generate_and_process(
            GenerateReply(order_id=o, internet_message_id="<fixture@example.com>"),
            str(uuid4()),
        )
        assert result["status"] == "ready"
        data = json.loads(captured["input"])
        assert data["current_order"]["reply_to_document_ids"] == [doc]
        assert data["allowed_source_ids"] == [source]
        assert [t["name"] for t in captured["tools"]] == ["prepare_reply"]
        assert captured["tools"][0]["strict"] and captured["store"] is False
        assert captured["max_output_tokens"] == 8000
        assert len(captured["input"]) <= replies.MAX_CONTEXT_CHARS + 3000


@pytest.mark.parametrize(
    "failure", ["missing_key", "provider", "timeout", "invalid_json"]
)
def test_provider_failures_are_durable_and_not_fake_success(monkeypatch, failure):
    with identity():
        o, _, _ = fixture_order()
        monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
        monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-real")
        if failure == "missing_key":
            monkeypatch.delenv("OPENAI_API_KEY")
        else:

            def provider(*args, **kwargs):
                if failure == "timeout":
                    raise httpx.ReadTimeout("fixture timeout")
                return httpx.Response(
                    429 if failure == "provider" else 200,
                    json={
                        "output": [
                            {
                                "type": "function_call",
                                "name": "prepare_reply",
                                "arguments": "not json",
                            }
                        ]
                    },
                )

            monkeypatch.setattr(replies.httpx, "post", provider)
        result = generate_and_process(GenerateReply(order_id=o), str(uuid4()))
        assert result["status"] == "failed" and not result["body"]
        assert result["error"] and not result["sent"]


@pytest.mark.parametrize(
    "bad_output",
    [
        {"source_ids": ["unknown-source"]},
        {"source_ids": []},
        {"body": ""},
        {"body": 123},
        {"unresolved": "not a list"},
    ],
)
def test_unusable_or_unknown_citations_are_rejected(monkeypatch, bad_output):
    with identity():
        o, _, source = fixture_order()
        monkeypatch.setattr(
            replies, "model_reply", lambda _: {**output(source), **bad_output}
        )
        result = generate_and_process(GenerateReply(order_id=o), str(uuid4()))
        assert result["status"] == "failed" and not result["source_refs"]


def test_changed_source_during_generation_and_revoked_company_rule_invalidate(
    monkeypatch,
):
    with identity():
        o, _, source = fixture_order()

        def provider(_):
            with Session.begin() as s:
                s.get(Span, source).text = "Comment 1: Cable entry has changed."
            return output(source)

        monkeypatch.setattr(replies, "model_reply", provider)
        failed = generate_and_process(GenerateReply(order_id=o), str(uuid4()))
        assert failed["status"] == "needs_context"
        with Session.begin() as s:
            rule = learning.create_rule(
                s,
                RuleWrite(
                    key="reply-tone",
                    title="Reply tone",
                    text="Keep replies brief and ask for specific drawing revisions.",
                    provenance_note="PM lead supplied the company's reply format.",
                ),
            )
            learning.approve_rule(s, rule["id"], "Admin reviewed the reply format.")
        draft = prepare(monkeypatch, o, source)
        with Session.begin() as s:
            learning.revoke_rule(
                s, rule["id"], "The company withdrew this reply policy."
            )
        with Session.begin() as s, pytest.raises(HTTPException) as error:
            replies.review(
                s,
                draft["id"],
                ReviewReply(
                    body=draft["body"],
                    decision="approved",
                    expected_evidence_hash=draft["evidence_hash"],
                ),
            )
        assert error.value.status_code == 409


def test_review_captures_original_edit_and_explicit_reuse_without_sending(monkeypatch):
    with identity():
        o, _, source = fixture_order()
        draft = prepare(monkeypatch, o, source)
        review = ReviewReply(
            body="Please provide drawing E-10 and its revision before engineering can confirm the cable entry.",
            decision="approved",
            use_as_example=True,
            expected_evidence_hash=draft["evidence_hash"],
        )
        with Session.begin() as s:
            accepted = replies.review(s, draft["id"], review)
            assert (
                accepted["status"] == "approved"
                and accepted["learning_status"] == "approved"
            )
            feedback = s.scalar(select(CompanyFeedback))
            assert feedback.outcome == "edited"
            assert feedback.original_text == draft["body"]
            assert feedback.human_response == review.body
            assert "cable entry" in feedback.input_text
            assert feedback.generation["context_hash"]
            assert not list(s.scalars(select(NoticeDelivery)))
        with Session.begin() as s:
            assert replies.review(s, draft["id"], review) == accepted
            assert len(list(s.scalars(select(CompanyFeedback)))) == 1
        next_draft = prepare(monkeypatch, o, source)
        with Session() as s:
            assert s.get(ReplyDraft, next_draft["id"]).context["company"]["examples"]


def test_member_review_needs_admin_for_learning_and_rejection_not_positive_example(
    monkeypatch,
):
    with identity():
        o, _, source = fixture_order()
        draft = prepare(monkeypatch, o, source)
    with identity(role="org:member"), Session.begin() as s:
        reviewed = replies.review(
            s,
            draft["id"],
            ReviewReply(
                body=draft["body"],
                decision="approved",
                use_as_example=True,
                expected_evidence_hash=draft["evidence_hash"],
            ),
        )
        assert reviewed["learning_status"] == "pending_review"
        assert s.scalar(select(CompanyFeedback)).reuse_status == "unapproved"
    with identity():
        rejected = prepare(monkeypatch, o, source)
        with Session.begin() as s, pytest.raises(HTTPException):
            replies.review(
                s,
                rejected["id"],
                ReviewReply(
                    decision="rejected",
                    use_as_example=True,
                    expected_evidence_hash=rejected["evidence_hash"],
                ),
            )
        with Session.begin() as s:
            result = replies.review(
                s,
                rejected["id"],
                ReviewReply(
                    decision="rejected",
                    expected_evidence_hash=rejected["evidence_hash"],
                ),
            )
            assert result["status"] == "rejected" and not result["sent"]


@pytest.mark.parametrize(
    "state,expected",
    [
        ("queued", "needs_context"),
        ("parsing", "needs_context"),
        ("processing", "needs_context"),
        ("failed", "needs_context"),
        ("needs_mapping", "ready"),
        ("mapped", "ready"),
    ],
)
def test_pending_files_block_draft_but_extracted_unmapped_table_does_not(
    monkeypatch, state, expected
):
    with identity():
        o, doc, source = fixture_order()
        with Session.begin() as s:
            document = s.get(Document, doc)
            document.state = state
            name = document.name
        result = prepare(monkeypatch, o, source)
        assert result["status"] == expected
        if expected == "needs_context":
            assert name in result["error"] and state in result["error"]
        if state == "needs_mapping":
            assert name in result["coverage"]["warnings"][0]
            assert any(name in warning for warning in result["unresolved"])


def test_reply_uses_drawing_despite_unmapped_bom_and_discloses_mapping_limit(
    monkeypatch,
):
    with identity():
        o, doc, source = fixture_order()
        with Session.begin() as s:
            s.get(Span, source).text = "Drawing MSB2 Rev C: enclosure finish RAL 7035."
            bom = Document(
                project_id=s.get(Document, doc).project_id,
                name="production-bom-rev-B.csv",
                kind="schedule",
                state="needs_mapping",
                blob="bom-fixture.csv",
                sha256="d" * 64,
                meta={"order_role": "schedule"},
            )
            s.add(bom)
            s.flush()
            bom_id = bom.id
            s.add(
                Span(
                    document_id=bom.id,
                    text="MSB2,4,1200,4800",
                    location={"row": 1},
                )
            )
        captured = {}

        def provider(snapshot):
            captured.update(snapshot)
            return {
                "subject": "Re: MSB2 finish",
                "body": "The Rev C drawing records enclosure finish RAL 7035.",
                "source_ids": [source],
                "unresolved": [],
            }

        monkeypatch.setattr(replies, "model_reply", provider)
        result = generate_and_process(
            GenerateReply(order_id=o, instruction="What is the MSB2 finish?"),
            "unmapped-bom-drawing-reply",
        )
        assert result["status"] == "ready"
        assert result["coverage"]["documents_read"] == 2
        assert result["coverage"]["unmapped_tables"][0]["document_id"] == bom_id
        assert "production-bom-rev-B.csv" in result["unresolved"][0]
        assert "not mapped" in result["unresolved"][0]
        table_source = next(
            item
            for item in captured["current_order"]["sources"]
            if item["document_id"] == bom_id
        )
        assert table_source["table_mapping_status"] == "not_mapped"
        assert "must not infer column meanings" in replies.INSTRUCTIONS
        with Session() as s:
            assert s.get(Document, bom_id).state == "needs_mapping"


def test_unmapped_table_without_extracted_text_blocks_and_names_file(monkeypatch):
    with identity():
        o, doc, source = fixture_order()
        with Session.begin() as s:
            s.get(Document, doc).state = "needs_mapping"
            s.get(Document, doc).name = "empty-extraction.csv"
            s.get(Span, source).text = "  "
        monkeypatch.setattr(
            replies, "model_reply", lambda _: pytest.fail("Missing text must block")
        )
        result = generate_and_process(GenerateReply(order_id=o), "empty-unmapped-table")
        assert result["status"] == "needs_context"
        assert "empty-extraction.csv (no extracted text)" in result["error"]


def test_large_context_is_bounded_and_partial_coverage_is_explicit(monkeypatch):
    with identity():
        o, doc, source = fixture_order()
        with Session.begin() as s:
            for i in range(120):
                s.add(
                    Span(
                        document_id=doc,
                        text=f"Comment {i + 2}: Cable entry question. "
                        + "large text " * 1000,
                        location={"page": i + 3},
                    )
                )
        captured = {}

        def provider(snapshot):
            captured.update(snapshot)
            return output(source)

        monkeypatch.setattr(replies, "model_reply", provider)
        result = generate_and_process(GenerateReply(order_id=o), str(uuid4()))
        assert len(json.dumps(captured)) < replies.MAX_CONTEXT_CHARS
        assert result["status"] == "ready"
        assert any("Partial retrieval" in item for item in result["unresolved"])
        assert not result["coverage"]["complete_drawing_review"]


def test_oversize_provider_context_is_rejected_before_network(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-real")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
    monkeypatch.setattr(
        replies.httpx,
        "post",
        lambda *a, **kw: pytest.fail("Oversize context must not reach model"),
    )
    with pytest.raises(HTTPException) as error:
        replies.model_reply(
            {
                "current_order": {"sources": [{"id": "source"}]},
                "instruction": "x" * replies.MAX_CONTEXT_CHARS,
            }
        )
    assert error.value.status_code == 409


@pytest.mark.parametrize("change", ["source", "example_revoked"])
def test_approved_reply_replay_rechecks_evidence_and_own_promoted_example(
    monkeypatch, change
):
    with identity():
        o, _, source = fixture_order()
        draft = prepare(monkeypatch, o, source)
        body = ReviewReply(
            body=draft["body"],
            decision="approved",
            use_as_example=True,
            expected_evidence_hash=draft["evidence_hash"],
        )
        with Session.begin() as s:
            accepted = replies.review(s, draft["id"], body)
        with Session.begin() as s:
            assert replies.review(s, draft["id"], body) == accepted
            if change == "source":
                s.get(
                    Span, source
                ).text = "The cable entry question changed after review."
            else:
                feedback = s.scalar(select(CompanyFeedback))
                learning.revoke_example(
                    s, feedback.id, "The team withdrew this response example."
                )
        with Session.begin() as s, pytest.raises(HTTPException) as error:
            replies.review(s, draft["id"], body)
        assert error.value.status_code == 409


def test_generation_is_durable_and_queued_replay_does_not_create_another_job(
    monkeypatch,
):
    with identity():
        o, _, source = fixture_order()
        calls = []

        def provider(snapshot):
            calls.append(snapshot)
            return output(source)

        monkeypatch.setattr(replies, "model_reply", provider)
        first = replies.generate(GenerateReply(order_id=o), "durable-one")
        repeat = replies.generate(GenerateReply(order_id=o), "durable-one")
        assert first == repeat and first["status"] == "generating"
        assert not calls
        with Session() as s:
            jobs = list(s.scalars(select(Job).where(Job.kind == "reply_draft")))
            assert len(jobs) == 1 and jobs[0].payload == {"draft_id": first["id"]}
        process(claim())
        with Session() as s:
            assert s.get(ReplyDraft, first["id"]).status == "ready"
        assert len(calls) == 1
        replies.process_draft(first["id"])
        assert len(calls) == 1


def test_reclaimed_lease_discards_old_worker_output_and_new_worker_can_finish(
    monkeypatch,
):
    with identity():
        o, _, source = fixture_order()
        draft = replies.generate(GenerateReply(order_id=o), "lease-replay")
        old_task = claim()
        replacement = []

        def old_lease():
            with Session() as s:
                j = s.get(Job, old_task[0])
                return j.lease == old_task[1] and j.status == "running"

        def old_provider(_):
            with Session.begin() as s:
                s.get(Job, old_task[0]).lease_until = now() - timedelta(seconds=1)
            replacement.append(claim())
            return {**output(source), "body": "Old worker result must not commit."}

        monkeypatch.setattr(replies, "model_reply", old_provider)
        assert replies.process_draft(draft["id"], old_lease) is None
        with Session() as s:
            assert s.get(ReplyDraft, draft["id"]).status == "generating"
        monkeypatch.setattr(replies, "model_reply", lambda _: output(source))
        process(replacement[0])
        with Session() as s:
            assert s.get(ReplyDraft, draft["id"]).body == output(source)["body"]
            job = s.get(Job, old_task[0])
            assert job.status == "succeeded" and job.attempts == 2
            assert not list(s.scalars(select(NoticeDelivery)))


def test_exhausted_worker_attempts_mark_draft_failed_and_do_not_leave_spinner(
    monkeypatch,
):
    with identity():
        o, _, _ = fixture_order()
        draft = replies.generate(GenerateReply(order_id=o), "exhausted")
        task = claim()
        with Session.begin() as s:
            job = s.get(Job, task[0])
            job.attempts, job.lease_until = 3, now() - timedelta(seconds=1)
        assert claim() is None
        with Session() as s:
            row = s.get(ReplyDraft, draft["id"])
            assert row.status == "failed" and row.error
            assert not row.body


def test_queued_draft_checks_freshness_before_using_provider(monkeypatch):
    with identity():
        o, _, source = fixture_order()
        draft = replies.generate(GenerateReply(order_id=o), "outdated-queue")
        with Session.begin() as s:
            s.get(Span, source).text = "Comment 1: The cable entry request has changed."
        monkeypatch.setattr(
            replies,
            "model_reply",
            lambda _: pytest.fail("Stale queued draft should not consume model work"),
        )
        process(claim())
        with Session() as s:
            assert s.get(ReplyDraft, draft["id"]).status == "needs_context"
