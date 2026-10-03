"""Citation schemas allow only the evidence actually included in model context."""

import json
from copy import deepcopy

import pytest
from fastapi import HTTPException

from backend.records import assistant


def record():
    return {
        "order": {"id": "order-1"},
        "version": 7,
        "sources": [
            {
                "id": "source-1",
                "document_id": "document-1",
                "text": "Comment asks for a drawing reference.",
                "location": {"page": 2},
            }
        ],
        "comments": [
            {
                "id": "comment-1",
                "text": "Confirm the referenced drawing.",
                "source_ids": ["source-1"],
            }
        ],
        "changes": [
            {
                "id": "change-1",
                "title": "Recorded response",
                "comment_ids": ["comment-1"],
            }
        ],
        "approvals": [],
        "events": [],
        "documents": [],
        "coordination": {
            "links": [
                {
                    "id": "link-1",
                    "from_id": "comment-not-retrieved",
                    "to_id": "source-not-retrieved",
                    "source_ids": ["source-not-retrieved"],
                }
            ]
        },
    }


def capture_provider(monkeypatch, returned):
    captured = {}
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")

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
                                "arguments": json.dumps(returned),
                            }
                        ]
                    },
                },
            )()

    monkeypatch.setattr(assistant.httpx, "Client", Client)
    return captured


def test_provider_request_constrains_each_reference_kind_to_supplied_ids(monkeypatch):
    expected = {
        "answer": "The drawing reference still needs review.",
        "source_ids": ["source-1"],
        "comment_ids": ["comment-1"],
        "change_ids": ["change-1"],
    }
    captured = capture_provider(monkeypatch, expected)
    w = record()
    before = deepcopy(w)
    result = assistant.ask("What needs review?", w, [])
    assert result == {
        **expected,
        "version": 7,
        "coverage": {
            "source_count": 1,
            "included_sources": 1,
            "comments_included": 1,
            "comments_total": 1,
        },
    }
    tool = captured["tools"][0]
    assert tool["strict"] and tool["name"] == "answer"
    assert tool["parameters"]["additionalProperties"] is False
    assert set(tool["parameters"]["required"]) == set(expected)
    for field, valid in expected.items():
        if field != "answer":
            assert tool["parameters"]["properties"][field]["items"]["enum"] == valid
            assert tool["parameters"]["properties"][field]["maxItems"] == 1
    supplied = json.loads(captured["input"])["record"]
    assert supplied["allowed_references"] == {
        k: v for k, v in expected.items() if k != "answer"
    }
    assert "source-not-retrieved" not in json.dumps(supplied["allowed_references"])
    assert captured["store"] is False and captured["parallel_tool_calls"] is False
    assert len(captured["tools"]) == 1 and w == before


def test_no_references_use_valid_empty_array_schema_not_empty_enum(monkeypatch):
    empty = {
        "answer": "No review evidence has been imported.",
        "source_ids": [],
        "comment_ids": [],
        "change_ids": [],
    }
    captured = capture_provider(monkeypatch, empty)
    w = record()
    w.update(sources=[], comments=[], changes=[], coordination={})
    assert assistant.ask("What needs review?", w, [])["source_ids"] == []
    properties = captured["tools"][0]["parameters"]["properties"]
    for field in assistant.REFERENCE_COLLECTIONS:
        assert properties[field]["maxItems"] == 0
        assert properties[field]["items"] == {"type": "string"}
        assert "enum" not in properties[field]["items"]


@pytest.mark.parametrize(
    "field,foreign_id",
    [
        ("source_ids", "source-not-retrieved"),
        ("comment_ids", "comment-not-retrieved"),
        ("change_ids", "different-orders-change"),
    ],
)
def test_provider_cannot_bypass_server_validation_with_foreign_or_context_link_ids(
    monkeypatch, field, foreign_id
):
    returned = {
        "answer": "Unsupported citation.",
        "source_ids": [],
        "comment_ids": [],
        "change_ids": [],
    }
    returned[field] = [foreign_id]
    captured = capture_provider(monkeypatch, returned)
    w = record()
    before = deepcopy(w)
    with pytest.raises(HTTPException) as failure:
        assistant.ask(
            "What needs review?", w, [{"role": "assistant", "content": foreign_id}]
        )
    assert failure.value.status_code == 502
    assert "outside the supplied record" in failure.value.detail
    assert (
        foreign_id
        not in captured["tools"][0]["parameters"]["properties"][field]["items"]["enum"]
    )
    assert w == before


def test_reference_manifest_and_enums_respect_bounded_retrieval():
    w = record()
    w["sources"] = [
        {"id": f"source-{index}", "text": "Drawing text."} for index in range(120)
    ]
    w["comments"] = [
        {"id": f"comment-{index}", "source_ids": ["source-119"]} for index in range(120)
    ]
    w["changes"] = [
        {"id": f"change-{index}", "comment_ids": ["comment-119"]}
        for index in range(120)
    ]
    w["events"] = [{"after": {"id": "source-119"}}]
    data = assistant.context("Which drawing?", w)
    refs = data["allowed_references"]
    assert {k: len(v) for k, v in refs.items()} == {
        "source_ids": 80,
        "comment_ids": 100,
        "change_ids": 100,
    }
    assert all(
        not value.endswith("119") for values in refs.values() for value in values
    )
    schema = assistant.reference_schema(data)
    assert sum(len(schema[field]["items"]["enum"]) for field in refs) == 280
    assert data["coverage"] == {
        "source_count": 120,
        "included_sources": 80,
        "comments_included": 100,
        "comments_total": 120,
    }


def test_over_budget_context_fails_instead_of_widening_or_truncating_citation_schema():
    with pytest.raises(HTTPException) as failure:
        assistant.reference_schema(
            {"sources": [{"id": f"source-{index}"} for index in range(81)]}
        )
    assert failure.value.status_code == 503
    assert "retrieval budget" in failure.value.detail
