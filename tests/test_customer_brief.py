from datetime import timedelta

import pytest
from fastapi import HTTPException
from test_company_context import ORG_B, identity, order
from test_customer_memory import customer, link, note, review

from backend.company_context import brief
from backend.company_context.models import CustomerMemory
from backend.company_context.schemas import CustomerDocumentCheck
from backend.storage.db import Session
from backend.storage.models import Span, now


def test_kickoff_requires_confirmed_customer_and_separates_history():
    with identity(), Session.begin() as s:
        c = customer(s)
        prior, _, source = order(s)
        target, _, _ = order(s)
        link(s, prior, c)
        review(s, note(s, c, prior, source, applicability="standing_preference"))
        review(
            s,
            note(
                s,
                c,
                prior,
                source,
                key="previous",
                applicability="historical_reference",
            ),
        )
        assert brief.order_brief(s, target.id)["customer"] is None
        link(s, target, c)
        b = brief.order_brief(s, target.id)
        assert len(b["preferences"]) == 1
        assert len(b["history"]) == 1
        assert b["preferences"][0]["source_order_id"] == prior.id
        assert b["preferences"][0]["source_url"].startswith(f"#order/{prior.id}")


@pytest.mark.parametrize(
    "reason", ["candidate", "expire", "source_changed", "conflict"]
)
def test_kickoff_withholds_unusable_notes(reason):
    with identity(), Session.begin() as s:
        c = customer(s)
        o, doc, source = order(s)
        link(s, o, c)
        n = note(s, c, o, source, applicability="standing_preference")
        if reason != "candidate":
            review(s, n)
        if reason == "expire":
            s.get(CustomerMemory, n["id"]).expires_at = now() - timedelta(seconds=1)
        if reason == "source_changed":
            doc.sha256 = "changed"
        if reason == "conflict":
            other = note(s, c, o, source, key="conflict", conflicts_with=[n["id"]])
            review(s, other)
        s.flush()
        b = brief.order_brief(s, o.id)
        assert not b["preferences"]
        assert n["id"] in [n["id"] for n in b["withheld"]]


def test_history_search_is_scoped_to_customer_and_reports_original_revision():
    with identity(), Session.begin() as s:
        c, other = customer(s), customer(s, "other")
        o, doc, source = order(s)
        second, _, _ = order(s)
        link(s, o, c)
        link(s, second, other)
        result = brief.search_history(s, c["id"], "cable entry")
        assert len(result["results"]) == 1
        assert result["results"][0]["source_id"] == source.id
        assert result["results"][0]["revision"] == "C"
        assert result["results"][0]["location"]["page"] == 2
        assert not brief.search_history(s, c["id"], "missing-text")["results"]
    with identity(ORG_B), Session.begin() as s:
        for operation in (
            lambda: brief.search_history(s, c["id"], "cable entry"),
            lambda: brief.order_brief(s, o.id),
            lambda: brief.check_documents(
                s, o.id, CustomerDocumentCheck(document_ids=[doc.id])
            ),
        ):
            with pytest.raises(HTTPException) as err:
                operation()
            assert err.value.status_code == 404


def test_check_uses_only_selected_documents_and_never_equates_text_to_compliance():
    with identity(), Session.begin() as s:
        c = customer(s)
        prior, _, source = order(s)
        target, drawing, excerpt = order(s)
        link(s, prior, c)
        link(s, target, c)
        review(s, note(s, c, prior, source, applicability="standing_preference"))
        result = brief.check_documents(
            s, target.id, CustomerDocumentCheck(document_ids=[drawing.id])
        )
        assert result["checks"][0]["status"] == "passages_found"
        assert result["checks"][0]["matches"][0]["source_id"] == excerpt.id
        assert "do not confirm" in result["notice"]
        assert result["brief_snapshot"] == brief.order_brief(s, target.id)["snapshot"]
        drawing.sha256 = "changed-content"
        s.flush()
        assert result["brief_snapshot"] != brief.order_brief(s, target.id)["snapshot"]
        # Contradictory wording is still a passage requiring human inspection.
        excerpt.text = "Cable entry must NOT be bottom entry on this order."
        s.flush()
        assert (
            brief.check_documents(
                s, target.id, CustomerDocumentCheck(document_ids=[drawing.id])
            )["checks"][0]["status"]
            == "passages_found"
        )
        excerpt.text = "Drawing title only."
        s.flush()
        missing = brief.check_documents(
            s, target.id, CustomerDocumentCheck(document_ids=[drawing.id])
        )
        assert missing["checks"][0]["status"] == "not_found_in_text"
        assert missing["snapshot"] != result["snapshot"]
        with pytest.raises(HTTPException):
            brief.check_documents(
                s, target.id, CustomerDocumentCheck(document_ids=[source.document_id])
            )


def test_unreadable_documents_and_search_limits_report_incomplete_coverage():
    with identity(), Session.begin() as s:
        c = customer(s)
        o, doc, source = order(s)
        link(s, o, c)
        review(s, note(s, c, o, source, applicability="standing_preference"))
        doc.state = "queued"
        s.flush()
        result = brief.check_documents(
            s, o.id, CustomerDocumentCheck(document_ids=[doc.id])
        )
        assert result["partial"]
        for i in range(32):
            s.add(
                Span(
                    document_id=doc.id,
                    text=f"Customer cable entry note {i}",
                    location={"page": i + 1},
                )
            )
        s.flush()
        result = brief.search_history(s, c["id"], "cable entry")
        assert len(result["results"]) == 30 and result["has_more"]


def test_history_search_preserves_drawing_tags_and_escapes_wildcards():
    with identity(), Session.begin() as s:
        c = customer(s)
        o, _, source = order(s)
        link(s, o, c)
        source.text = "Drawing E-10, equipment CB-12, identifier UNIT_A."
        s.flush()
        for query in ("E-10", "CB-12", "UNIT_A"):
            result = brief.search_history(s, c["id"], query)
            assert [r["source_id"] for r in result["results"]] == [source.id]
        source.text = "UNITBA"
        s.flush()
        assert not brief.search_history(s, c["id"], "UNIT_A")["results"]
