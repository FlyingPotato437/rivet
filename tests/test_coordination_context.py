"""The assistant explains coordination records without acquiring write authority."""

from backend.records.assistant import INSTRUCTIONS, context


def test_coordination_context_excludes_recipient_lists_and_owner_contacts():
    w = {
        "order": {"id": "order"},
        "version": 1,
        "sources": [],
        "comments": [],
        "changes": [],
        "approvals": [],
        "events": [],
        "documents": [],
        "coordination": {
            "summary": "One clarification pending",
            "settings": {
                "customer_due_date": "2026-10-20",
                "role_owners": {"pm": "private@example.com"},
            },
            "links": [],
            "suggestions": [
                {
                    "id": "s",
                    "status": "pending",
                    "kind": "clarification",
                    "draft": "Which drawing?",
                    "recipients": ["private@example.com"],
                }
            ],
            "tasks": [
                {
                    "id": "t",
                    "role": "drafting",
                    "status": "open",
                    "owner": "private@example.com",
                }
            ],
            "readiness": {
                "release": [{"status": "unknown", "label": "Customer approval"}]
            },
        },
    }
    result = context("What is waiting on drafting?", w)["coordination"]
    assert result["tasks"][0] == {"id": "t", "role": "drafting", "status": "open"}
    assert result["suggestions"][0]["draft"] == "Which drawing?"
    assert result["customer_due_date"] == "2026-10-20"
    assert "private@example.com" not in str(result)
    assert "not proof" in INSTRUCTIONS and "no mutation tools" in INSTRUCTIONS
