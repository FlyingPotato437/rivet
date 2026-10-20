import json

import httpx
import pytest
from test_company_context import identity
from test_reply_assistant import fixture_order, generate_and_process, output

from backend.reply_assistant import service as replies
from backend.reply_assistant.evidence_policy import (
    DraftPolicyError,
    claims_outgoing_attachments,
    connected_fact_support,
    grounded_source_ids,
)
from backend.reply_assistant.schemas import GenerateReply


@pytest.mark.parametrize(
    "body",
    [
        "The released Rev B drawing is identified in the record, but no attachment is included with this draft.",
        "Please find attached the released drawing.",
        "The drawing is attached for reference.",
        "We have attached the released drawing.",
        "This reply contains the released drawing.",
        "I cannot attach the drawing.",
    ],
)
def test_model_cannot_claim_outgoing_attachment_state(body):
    assert claims_outgoing_attachments(body)


@pytest.mark.parametrize(
    "body",
    [
        "Please provide the marked-up drawing and identify the missing location.",
        "Please attach the customer markup so we can locate the red-marked item.",
        "The customer supplied Rev C. The released Rev B identifies TU-2500-DEMO.",
        "No marked drawing was provided by the customer.",
    ],
)
def test_missing_customer_information_remains_a_valid_question(body):
    assert not claims_outgoing_attachments(body)


def test_one_bounded_regeneration_corrects_attachment_claim(monkeypatch):
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-real")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")

    def provider(url, **kwargs):
        calls.append(kwargs["json"])
        draft = output("s1")
        if len(calls) == 1:
            draft["body"] = (
                "The drawing is identified, but no attachment is included with this draft."
            )
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "function_call",
                        "name": "prepare_reply",
                        "arguments": json.dumps(draft),
                    }
                ]
            },
        )

    monkeypatch.setattr(replies.httpx, "post", provider)
    result = replies.model_reply({"current_order": {"sources": [{"id": "s1"}]}})
    assert result == output("s1")
    assert len(calls) == 2
    assert "previous attempt was rejected" in calls[1]["instructions"]
    assert calls[0]["input"] == calls[1]["input"]


def test_repeat_violation_fails_without_draft_creation(monkeypatch):
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-real")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6-luna")
    with identity():
        oid, _, sid = fixture_order()

        def provider(url, **kwargs):
            calls.append(kwargs)
            draft = {
                **output(sid),
                "body": "No attachment is included with this draft.",
            }
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "function_call",
                            "name": "prepare_reply",
                            "arguments": json.dumps(draft),
                        }
                    ]
                },
            )

        monkeypatch.setattr(replies.httpx, "post", provider)
        result = generate_and_process(
            GenerateReply(order_id=oid), "attachment-retry-limit"
        )
        assert result["status"] == "failed" and not result["body"]
        assert "outgoing attachments" in result["error"]
        assert len(calls) == 2


def test_final_save_checks_attachment_policy_even_if_provider_is_bypassed(monkeypatch):
    with identity():
        oid, _, sid = fixture_order()
        monkeypatch.setattr(
            replies,
            "model_reply",
            lambda _: {**output(sid), "body": "The released drawing is attached."},
        )
        result = generate_and_process(
            GenerateReply(order_id=oid), "attachment-save-guard"
        )
        assert result["status"] == "failed" and not result["body"]
        assert "outgoing attachments" in result["error"]


def connected_fixture():
    sources = [
        {
            "id": "footer",
            "document_id": "spec",
            "text": "FICTIONAL INTEGRATION DEMO — NOT AN ENGINEERING DESIGN",
        },
        {
            "id": "spec-value",
            "document_id": "spec",
            "text": "Required trip unit: TU-3000-DEMO",
        },
        {
            "id": "drawing-value",
            "document_id": "drawing",
            "text": "Trip unit shown: TU-2500-DEMO",
        },
        {
            "id": "customer",
            "document_id": "email",
            "text": "Which trip unit governs CB-12?",
        },
    ]
    connected = {
        "resources": [
            {
                "document_id": "spec",
                "name": "Specification.pdf",
                "lifecycle": "approved",
                "facts": {"CB-12.trip_unit": "TU-3000-DEMO"},
                "source_ids": ["footer", "spec-value"],
            },
            {
                "document_id": "drawing",
                "name": "Drawing-B.pdf",
                "lifecycle": "released",
                "facts": {"CB-12.trip_unit": "TU-2500-DEMO"},
                "source_ids": ["drawing-value"],
            },
        ]
    }
    return connected, sources


def test_footer_only_citations_replaced_by_material_same_document_passages():
    connected, sources = connected_fixture()
    support = connected_fact_support(connected, sources)
    result = grounded_source_ids(
        {"sources": sources, "connected_fact_support": support}, ["footer", "customer"]
    )
    assert result == ["spec-value", "drawing-value", "customer"]


def test_mapping_without_actual_source_passage_fails_closed():
    connected, sources = connected_fixture()
    sources = [s for s in sources if s["id"] != "spec-value"]
    with pytest.raises(DraftPolicyError, match="no supporting passage"):
        connected_fact_support(connected, sources)


def test_same_value_in_another_document_cannot_substitute_for_missing_evidence():
    connected, sources = connected_fixture()
    sources[1]["document_id"] = "unrelated"
    with pytest.raises(DraftPolicyError, match="no supporting passage"):
        connected_fact_support(connected, sources)


def test_material_passages_are_pinned_ahead_of_many_incoming_comments():
    connected, sources = connected_fixture()
    support = connected_fact_support(connected, sources)
    sources += [
        {
            "id": f"comment-{i}",
            "document_id": "email",
            "text": f"Comment {i}: Which trip unit governs CB-12?",
        }
        for i in range(60)
    ]
    w = {
        "sources": sources,
        "connected_fact_support": support,
        "comments": [
            {"source_ids": [s["id"]], "document_id": "email"} for s in sources[3:]
        ],
    }
    docs = {"email": {"role": "email"}, "spec": {}, "drawing": {}}
    ranked = replies._rank_sources("Which trip unit governs?", w, {"email"}, docs)
    assert {s["id"] for s in ranked[:2]} == {"spec-value", "drawing-value"}
    grounded_source_ids(
        {"sources": ranked[:40], "connected_fact_support": support}, []
    )
