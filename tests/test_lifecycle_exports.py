from copy import deepcopy
from io import BytesIO
from uuid import uuid4

import pdfplumber
import pytest
import test_lifecycle
from fastapi import HTTPException
from test_lifecycle import price_fixture

from backend.lifecycle import adapters
from backend.lifecycle.exports import render_documents
from backend.storage import db

fixture = test_lifecycle.fixture


def samples():
    source_id = "source-a"
    base = {
        "id": "41d6a292-0907-44d2-a1e0-03618970aa74",
        "fingerprint": "a" * 64,
        "synthetic": True,
        "prepared_at": "2026-10-15T10:00:00Z",
        "gates": ["engineering", "money", "scope", "customer_commitment"],
        "sources": {source_id: {"hash": "b" * 64, "document_sha256": "c" * 64}},
        "blockers": [],
    }
    quote = {
        **base,
        "operation": "quote",
        "payload": {
            "currency": "USD",
            "total": "3200.00",
            "lines": [
                {
                    "tag": "CB-12",
                    "description": "Switchboard breaker package with TU-3000 trip unit",
                    "quantity": "2.0000",
                    "unit_cost": "1200.00",
                    "gross_margin_percent": "25.00",
                    "unit_price": "1600.00",
                    "extended_price": "3200.00",
                    "source_ids": [source_id],
                }
            ],
            "exceptions": [
                {
                    "text": "Breaker relocation and clearance validation require an engineering-reviewed layout. Delivery remains subject to the approved schedule.",
                    "source_ids": [source_id],
                }
            ],
        },
    }
    bundle = {
        "changes": [
            {
                "id": "edit-a",
                "type": "cad_attribute",
                "target": "CB-12 / TRIP_UNIT",
                "before": "TU-2500",
                "after": "TU-3000",
            }
        ],
        "artifacts": [
            {"name": "E-10-Rev-D.dxf", "role": "draft_drawing", "sha256": "d" * 64},
            {"name": "BOM-Rev-D.xlsx", "role": "draft_bom", "sha256": "e" * 64},
        ],
    }
    revision = {
        **base,
        "operation": "revision_review",
        "payload": {
            "revision": "C",
            "email_draft": {
                "subject": "DEMO-LIFECYCLE-001 - Revision C returned comments"
            },
            "change_order": {
                "total": "1200.00",
                "lines": [
                    {
                        "tag": "CB-EXTRA",
                        "description": "Additional feeder breaker requested in the returned markup",
                        "quantity": "1",
                        "unit_cost": "900.00",
                        "gross_margin_percent": "25.00",
                        "unit_price": "1200.00",
                        "extended_price": "1200.00",
                        "currency": "USD",
                        "billability": "contract_confirmed",
                        "source_ids": [source_id],
                        "billability_source_ids": [source_id],
                    }
                ],
            },
            "comments": [
                {
                    "comment_id": "c1",
                    "kind": "answer",
                    "text": "Please confirm the drawing and applicable revision.",
                    "response": "The supplied source is drawing E-10, Revision C. Revision C remains a draft; the supplied records do not establish production release.",
                    "source_ids": [source_id],
                },
                {
                    "comment_id": "c2",
                    "kind": "text_edit",
                    "text": "Update CB-12 to the confirmed TU-3000 trip unit.",
                    "response": "The trip-unit attribute was updated in the saved draft copy. Engineering review is required before issue.",
                    "source_ids": [source_id],
                    "draft_bundle": bundle,
                    "draft_change_id": "edit-a",
                },
                {
                    "comment_id": "c3",
                    "kind": "layout_task",
                    "text": "Move CB-12 lower after the clearance review.",
                    "response": "A located drafting task is prepared. The cabinet geometry has not been moved. A drafter must verify access and clearances.",
                    "source_ids": [source_id],
                    "draft_bundle": bundle,
                    "layout_location": {
                        "sheet": "E-10",
                        "x": 95,
                        "y": 106,
                        "entity_handle": "A2",
                    },
                },
            ],
        },
    }
    context = {
        "number": "DEMO-LIFECYCLE-001",
        "title": "Data hall A - low-voltage switchboard",
        "customer": "Fictional data-center customer",
    }
    evidence = {
        source_id: {"name": "Returned-Submittal-E10-RevC.pdf", "location": {"page": 1}}
    }
    return context, quote, revision, evidence


def text(content):
    with pdfplumber.open(BytesIO(content)) as doc:
        return "\n".join(page.extract_text() for page in doc.pages)


def test_quote_keeps_decimal_prices_but_never_exposes_cost_or_margin():
    context, quote, _, evidence = samples()
    pdf = render_documents(context, quote, evidence)["quote.pdf"]
    output = text(pdf)
    assert "USD 1,600.00" in output and "USD 3,200.00" in output
    assert "1,200" not in output and "25.00" not in output
    assert "Specification exceptions" in output and "clearance validation" in output
    assert "DRAFT - REVIEW BEFORE SENDING" in output
    assert "Not a commercial offer or production release" in output
    assert quote["fingerprint"] in output.replace("\n", "")
    assert pdf == render_documents(context, quote, evidence)["quote.pdf"]


def test_quote_groups_passages_from_one_page_without_hiding_exception_references():
    context, quote, _, _ = samples()
    ids = [f"source-{index}" for index in range(10)]
    quote["sources"] = {
        id: {"hash": f"{index:064x}", "document_sha256": "d" * 64}
        for index, id in enumerate(ids)
    }
    quote["payload"]["lines"][0]["source_ids"] = ids
    quote["payload"]["exceptions"][0]["source_ids"] = [ids[7]]
    evidence = {
        id: {
            "document_id": "same-rfq",
            "name": "RFQ requirements.pdf",
            "location": {"page": 1},
        }
        for id in ids
    }
    original = deepcopy(quote)
    pdf = render_documents(context, quote, evidence)["quote.pdf"]
    with pdfplumber.open(BytesIO(pdf)) as document:
        assert len(document.pages) == 1
    output = text(pdf)
    assert output.count("RFQ requirements.pdf") == 1
    assert "Evidence: E08" in output
    assert all(f"E{number:02d}" in output for number in range(1, 11))
    assert quote == original


def test_revision_exports_actual_responses_changes_locations_and_proposed_addition():
    context, _, revision, evidence = samples()
    documents = render_documents(context, revision, evidence)
    assert set(documents) == {
        "change-order.pdf",
        "response-letter.pdf",
        "drafting-list.pdf",
    }
    change = text(documents["change-order.pdf"])
    assert "USD 1,200.00" in change and "900.00" not in change
    assert "not accepted, invoiced or authorized" in change
    response = text(documents["response-letter.pdf"])
    assert "Review item 03" in response
    assert "do not establish production release" in " ".join(response.split())
    drafting = text(documents["drafting-list.pdf"])
    assert "TU-2500" in drafting and "TU-3000" in drafting
    assert "anchor x=95, y=106" in drafting and "GEOMETRY UNCHANGED" in drafting
    assert "E-10-Rev-D.dxf" in drafting and "BOM-Rev-D.xlsx" in drafting
    assert "Returned-Submittal-E10-RevC.pdf" in drafting and "page 1" in drafting


def test_commercial_registers_keep_price_request_contract_and_exceptions_only():
    context, quote, revision, evidence = samples()
    for source_id, name in {
        "price": "ERP-price.json",
        "contract": "Accepted-scope.json",
        "exception": "Specification-exception.pdf",
        **{f"unrelated-{i}": f"Unrelated-finish-note-{i}.pdf" for i in range(23)},
    }.items():
        evidence[source_id] = {"name": name, "location": {"page": 1}}
        for package in [quote, revision]:
            package["sources"][source_id] = {"hash": "c" * 64}
    quote["payload"]["lines"][0]["source_ids"] = ["price", "source-a"]
    quote["payload"]["exceptions"][0]["source_ids"] = ["exception"]
    revision["payload"]["change_order"]["lines"][0]["source_ids"] = [
        "price",
        "source-a",
    ]
    revision["payload"]["change_order"]["lines"][0]["billability_source_ids"] = [
        "contract"
    ]
    original = deepcopy([quote, revision, evidence])
    quote_pdf = render_documents(context, quote, evidence)["quote.pdf"]
    outputs = render_documents(context, revision, evidence)
    change_pdf = outputs["change-order.pdf"]
    quoted, changed = text(quote_pdf), text(change_pdf)
    for output in [quoted, changed]:
        assert "ERP-price.json" in output
        assert "Returned-Submittal-E10-RevC.pdf" in output
        assert "Unrelated-finish-note" not in output
    assert "Specification-exception.pdf" in quoted
    assert "Accepted-scope.json" not in quoted
    assert "Accepted-scope.json" in changed
    assert "Specification-exception.pdf" not in changed
    assert "not accepted, invoiced or authorized" in changed
    assert "Unrelated-finish-note" in text(outputs["response-letter.pdf"])
    assert [quote, revision, evidence] == original
    with pdfplumber.open(BytesIO(change_pdf)) as doc:
        assert len(doc.pages) == 1


def test_source_markup_is_printed_as_text_not_executed_or_linked():
    context, quote, _, evidence = samples()
    quote["payload"]["lines"][0]["description"] = (
        '<img src="https://example.invalid/private"/><a href="javascript:alert(1)">CB-12 & 5 Ω</a>'
    )
    pdf = render_documents(context, quote, evidence)["quote.pdf"]
    assert "<img" in text(pdf) and "5 Ω" in text(pdf)
    with pdfplumber.open(BytesIO(pdf)) as doc:
        assert all(not page.hyperlinks for page in doc.pages)


def test_long_responses_and_long_price_rows_paginate_without_dropping_content():
    context, quote, revision, evidence = samples()
    quote["payload"]["lines"][0]["description"] = (
        "Long equipment description. " * 500 + "END-QUOTE-ROW"
    )
    assert "END-QUOTE-ROW" in text(
        render_documents(context, quote, evidence)["quote.pdf"]
    )
    revision["payload"]["comments"] = [
        {
            **deepcopy(revision["payload"]["comments"][0]),
            "comment_id": f"c{i}",
            "response": "Review the documented source before issuing this response. "
            * 80
            + f" END-RESPONSE-{i}",
        }
        for i in range(12)
    ]
    pdf = render_documents(context, revision, evidence)["response-letter.pdf"]
    with pdfplumber.open(BytesIO(pdf)) as doc:
        assert len(doc.pages) > 2
        extracted = "\n".join(page.extract_text() for page in doc.pages)
        assert all(f"END-RESPONSE-{i}" in extracted for i in range(12))
        assert all(
            "DRAFT - REVIEW BEFORE SENDING" in page.extract_text() for page in doc.pages
        )


def test_binary_artifact_writer_is_immutable_and_rejects_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    org, order, package = str(uuid4()), str(uuid4()), str(uuid4())
    context, quote, _, evidence = samples()
    pdf = render_documents(context, quote, evidence)["quote.pdf"]
    manifest = adapters.write_artifact(org, order, package, "quote.pdf", pdf)
    assert manifest["media_type"] == "application/pdf"
    assert adapters.read_artifact(org, order, package, manifest) == pdf
    with pytest.raises(HTTPException) as changed:
        adapters.write_artifact(org, order, package, "quote.pdf", pdf + b"changed")
    assert changed.value.status_code == 409
    with pytest.raises(HTTPException):
        adapters.write_artifact(org, order, package, "../../quote.pdf", pdf)
    with pytest.raises(HTTPException):
        adapters.write_artifact(org, order, package, "quote.pdf", b"not a PDF")


def test_real_package_includes_authenticated_pdf_download_with_immutable_manifest(
    fixture, client
):
    prepared, _ = price_fixture(fixture)
    package = prepared["packages"][0]
    manifest = next(a for a in package["artifacts"] if a["name"] == "quote.pdf")
    response = client.get(manifest["url"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert "quote.pdf" in response.headers["content-disposition"]
    assert "USD 3,200.00" in text(response.content)
    assert any(a["name"] == "quote.json" for a in package["artifacts"])


def test_renderer_bounds_oversized_payload_without_silently_truncating():
    context, quote, _, evidence = samples()
    quote["payload"]["lines"][0]["description"] = "X" * 4_000_001
    with pytest.raises(HTTPException) as oversized:
        render_documents(context, quote, evidence)
    assert oversized.value.status_code == 413
