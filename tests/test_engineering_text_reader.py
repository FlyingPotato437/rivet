"""Citation/reader boundaries. Synthetic controls never count as accuracy."""

from copy import deepcopy

import pytest

from backend.engineering.requirements import extract_requirements
from backend.engineering.text_reader import read_customer_text


@pytest.fixture(autouse=True)
def database():
    yield


def documents():
    return [
        {
            "id": "spec",
            "sha256": "a" * 64,
            "role": "specification",
            "spans": [
                {
                    "id": "spec-p1",
                    "text": "Provide a 2000 A main bus. Provide a utility metering compartment.",
                    "location": {"page": 1},
                },
            ],
        }
    ]


def candidate(**extra):
    return {
        "span_id": "spec-p1",
        "key": "service_rating_a",
        "subject_id": "order",
        "value": 2000.0,
        "unit": "A",
        "quote": "2000 A main bus",
        "excluded": False,
        "confidence": "high",
        **extra,
    }


def run(tmp_path, items, docs=None, **kwargs):
    docs = docs or documents()
    return read_customer_text(
        extract_requirements(docs),
        docs,
        output_dir=tmp_path,
        enabled=True,
        model="gpt-6-luna",
        provider_fn=lambda spans, model: ({"items": items, "unhandled": []}, {}),
        **kwargs,
    )


def test_exact_quote_yields_proposed_claim_with_original_page(tmp_path):
    result, records = run(tmp_path, [candidate()])
    item = result["items"][-1]
    assert item["value"] == 2000 and not item["confirmed"]
    assert item["source_refs"][0]["page"] == 1
    assert len(records) == 1


def test_invented_quote_or_foreign_span_rejected(tmp_path):
    result, _ = run(
        tmp_path, [candidate(span_id="other-job"), candidate(quote="4000 A")]
    )
    assert not any(
        c["extractor"]["name"].endswith("text-requirements.v1")
        for c in result["evidence_claims"]
    )
    assert len(result["warnings"]) == 2


def test_number_not_printed_in_quote_stays_unknown(tmp_path):
    result, _ = run(tmp_path, [candidate(value=4000.0)])
    assert result["items"][-1]["value"] is None
    assert result["warnings"]


def test_manufacturer_output_never_enters_provider(tmp_path):
    docs = documents() + [
        {
            "id": "approved",
            "sha256": "b" * 64,
            "role": "manufacturer_reference",
            "spans": [
                {
                    "id": "maker",
                    "text": "Answer key main bus 4000 A",
                    "location": {"page": 1},
                }
            ],
        }
    ]
    seen = []

    def provider(spans, model):
        seen.extend(spans)
        return {"items": [], "unhandled": []}, {}

    read_customer_text(
        extract_requirements(docs),
        docs,
        output_dir=tmp_path,
        enabled=True,
        provider_fn=provider,
    )
    assert {s["id"] for s in seen} == {"spec-p1"}


def test_source_cache_is_pinned_and_second_run_does_not_call_provider(tmp_path):
    docs = documents()
    first, _ = run(tmp_path, [candidate()], docs)

    def fail(*args, **kwargs):
        raise ValueError("Unexpected external call")

    second, records = read_customer_text(
        extract_requirements(docs),
        docs,
        output_dir=tmp_path,
        enabled=True,
        model="gpt-6-luna",
        provider_fn=fail,
    )
    assert records[0]["cache_hit"] and first["items"] == second["items"]
    changed = deepcopy(docs)
    changed[0]["spans"][0]["text"] += " New addendum."
    _, records = read_customer_text(
        extract_requirements(changed),
        changed,
        output_dir=tmp_path,
        enabled=True,
        model="gpt-6-luna",
        provider_fn=fail,
    )
    assert not records


def test_budget_deferrals_and_provider_failures_are_visible(tmp_path):
    result, _ = run(tmp_path, [], max_batches=0)
    assert result["reader_coverage"][-1]["state"] == "partial"
    assert result["warnings"][0]["deferred_span_ids"] == ["spec-p1"]


def test_oversized_span_is_explicitly_deferred_and_blocks_coverage(tmp_path):
    from backend.engineering.service import _coverage_blockers

    docs = documents()
    docs[0]["spans"][0]["text"] = "Unread engineering clause " * 100
    result, records = run(tmp_path, [], docs, batch_tokens=20)
    assert not records
    assert result["reader_coverage"][-1]["state"] == "partial"
    assert result["warnings"][0]["verification_required"]
    assert result["warnings"][0]["deferred_span_ids"] == ["spec-p1"]
    assert _coverage_blockers(result)
