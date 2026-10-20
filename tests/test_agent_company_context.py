from test_record_agent import example, fetched, start, write
from test_records import post

from backend.company_context.schemas import RuleWrite
from backend.company_context.service import approve_rule, create_rule, revoke_rule
from backend.records import agent_service as agent
from backend.storage.db import Session
from backend.storage.models import RecordAgentRun
from backend.worker import claim, process


def approved_rule(order):
    with Session.begin() as s:
        rule = create_rule(s, RuleWrite(key="clarification-tone", title="Drawing clarifications",
            text="Ask for the exact sheet and revision before proposing a drawing reference.",
            order_id=order["id"], provenance_note="Approved pilot coordination instruction."))
        approve_rule(s, rule["id"], "Team administrator reviewed this instruction.")
        return rule["id"]


def test_revoked_guidance_blocks_prepared_agent_changes(client, monkeypatch):
    order, w = example(client, monkeypatch)
    rule_id = approved_rule(order)
    run = start(client, order, w)
    process(claim())
    done = fetched(client, order, run)
    proposal = done["proposals"][0]
    with Session.begin() as s:
        saved = s.get(RecordAgentRun, run["id"])
        assert saved.state["company_context"]["rules"][0]["id"] == rule_id
        revoke_rule(s, rule_id, "This instruction has been withdrawn by the team.")
    result = post(client, f"/orders/{order['id']}/record/agent/proposals/{proposal['id']}/resolve", write(w, action="accept"))
    assert result.status_code == 409


def test_guidance_changed_during_model_call_is_not_applied(client, monkeypatch):
    order, w = example(client, monkeypatch, ai=True)
    rule_id = approved_rule(order)
    seen = {}

    def provider(saved, _):
        seen.update(saved["state"]["company_context"])
        with Session.begin() as s:
            revoke_rule(s, rule_id, "Withdrawn while this investigation was running.")
        return "complete", {"summary": "Checked the source.", "source_ids": []}, {}

    monkeypatch.setattr(agent, "provider_call", provider)
    run = start(client, order, w)
    process(claim())
    done = fetched(client, order, run)
    assert seen["rules"][0]["id"] == rule_id
    assert done["status"] == "waiting_for_input"
    assert not done["proposals"]
