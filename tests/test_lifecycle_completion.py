"""Whole local workflow: actual drafter files, failed/passing gates and memory review."""

import os
from pathlib import Path

import ezdxf
import pdfplumber

from backend.company_context import customers
from backend.company_context.brief import order_brief
from backend.company_context.models import CustomerMemory, OrderCustomerBinding
from backend.company_context.schemas import CustomerBindingWrite, CustomerNoteReview
from backend.domain.service import get, scoped
from backend.lifecycle import completion_replay, service
from backend.orders.service import new_order
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Order, Span


def test_complete_eleven_comments_requires_actual_drafter_output_and_separate_memory_review(
    tmp_path, monkeypatch
):
    # The command also runs directly against the dedicated isolated test DB.
    monkeypatch.setattr(db, "DATA", tmp_path / "work")
    if not os.getenv("RIVET_PDF_PYTHON"):
        # Optional only for the fixture authoring runtime; the API does not need it.
        candidate = (
            Path.home()
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
        )
        if candidate.is_file():
            monkeypatch.setenv("RIVET_PDF_PYTHON", str(candidate))
    result = completion_replay.complete(number="DEMO-FULL-REVISION-001")
    assert result["layout_failure_proven"] is True
    assert result["stale_signature_rejected"] is True
    assert result["verified_comments"] == result["total_comments"] == 11
    assert {
        item["operation"] for item in result["stages"] if item["status"] == "approved"
    } == {
        "quote",
        "kickoff",
        "revision_review",
        "purchasing",
        "revision_check",
        "release",
        "closeout",
    }
    assert result["automatically_reusable_customer_notes"] == 0
    with Session.begin() as s:
        order = get(s, Order, result["order_id"])
        assert order.status != "released"  # Only a local adapter release occurred.
        values = service.view(s, order)
        handover = next(
            item for item in values["packages"] if item["operation"] == "closeout"
        )
        manifest = handover["payload"]["documents"]
        assert len(manifest) == 3
        assert len(handover["sources"]) == 3
        assert set(handover["payload"]["source_ids"]) == set(handover["sources"])
        report = next(item for item in manifest if item["role"] == "test_report")
        assert report["revision"] == "0"
        report_document = get(s, Document, report["id"])
        with pdfplumber.open(db.BLOBS / report_document.blob) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "SIMULATED" in text and "FTR-17" in text
        assert "no test was performed on real equipment" in " ".join(text.split())
        native = ezdxf.readfile(result["actual_drafter_dxf"])
        titleblock = "\n".join(
            entity.dxf.text for entity in native.modelspace().query("TEXT")
        )
        assert "E-10 / REV D" in titleblock
        assert "SOURCE REV C" not in titleblock
        check = next(
            item
            for item in values["packages"]
            if item["operation"] == "revision_check" and item["status"] != "superseded"
        )
        assert check["payload"]["bom_matches"] is True
        assert check["payload"]["unreleased_files"] == []
        assert check["payload"]["resolution_failures"] == {}
        layout = next(
            item
            for item in values["packages"]
            if item["operation"] == "revision_review"
        )["payload"]["comments"][-1]
        assert layout["draft_bundle"]["layout_tasks"][0]["geometry_changed"] is False
        candidates = list(
            s.scalars(
                scoped(CustomerMemory).where(CustomerMemory.source_order_id == order.id)
            )
        )
        accepted_outcomes = [
            row
            for row in candidates
            if row.provenance.get("kind") == "lifecycle_decision_candidate"
        ]
        assert (
            len(accepted_outcomes) == 4
        )  # Three native draft edits and one scope/price proposal.
        assert all(row.status == "candidate" for row in accepted_outcomes)
        assert all(
            row.applicability == "historical_reference" for row in accepted_outcomes
        )
        assert all(
            row.provenance["not_training_data"] is True for row in accepted_outcomes
        )
        chosen = next(
            row
            for row in accepted_outcomes
            if row.provenance["outcome"]["kind"] == "accepted_draft_edit"
        )
        binding = s.scalar(
            scoped(OrderCustomerBinding).where(
                OrderCustomerBinding.order_id == order.id
            )
        )
        second = new_order(
            s,
            {
                "title": "Data Hall B - later fictional order",
                "customer": "Meridian Data Center - fictional replay",
                "number": "DEMO-SECOND-ORDER",
                "synthetic": True,
            },
        )
        customers.bind_order(
            s,
            second.id,
            CustomerBindingWrite(
                customer_id=binding.customer_id,
                expected_version=0,
                reason="Explicit same-customer test link; display names are not identity.",
            ),
        )
        before = order_brief(s, second.id)
        assert before["history"] == before["preferences"] == []
        customers.review_note(
            s,
            chosen.id,
            CustomerNoteReview(
                expected_version=chosen.version,
                reason="A separate customer-history administrator explicitly approves historical retrieval, without making this a standing requirement.",
            ),
            approve=True,
        )
        after = order_brief(s, second.id)
        assert [row["id"] for row in after["history"]] == [chosen.id]
        assert after["preferences"] == []
        assert after["history"][0]["source_order_id"] == order.id
        outcome = chosen.provenance["outcome"]
        accepted_preview = (
            db.DATA
            / "drafting"
            / order.organization_id
            / order.id
            / outcome["bundle_id"]
            / "after.svg"
        )
        original_preview = accepted_preview.read_bytes()
        accepted_preview.write_text(
            "changed after engineering accepted the native draft"
        )
        artifact_stale = order_brief(s, second.id)
        assert artifact_stale["history"] == []
        accepted_preview.write_bytes(original_preview)
        assert [row["id"] for row in order_brief(s, second.id)["history"]] == [
            chosen.id
        ]
        source_id = chosen.provenance["evidence"][0]["source_id"]
        get(s, Span, source_id).text += " Changed after the captured decision."
        s.flush()
        stale = order_brief(s, second.id)
        assert stale["history"] == []
        assert any(
            row["id"] == chosen.id
            and row["reason"] == "Source or customer link changed"
            for row in stale["withheld"]
        )
