from fastapi import APIRouter, Header

from backend.domain.service import idempotent
from backend.orders.service import lock_order
from backend.storage.db import Session

from . import agent_service as agent
from . import service as records
from .agent_schemas import AgentSettingsWrite, ProposalResolve, RunContinue, RunWrite
from .graph import project_graph

router = APIRouter(tags=["Order agents"])


@router.get("/orders/{id}/record/agent")
def agent_workspace(id: str):
    with Session.begin() as s:
        return agent.aggregate(s, lock_order(s, id))


@router.get("/orders/{id}/record/agent/runs")
def agent_runs(id: str):
    with Session.begin() as s:
        return {"runs": agent.aggregate(s, lock_order(s, id))["runs"]}


@router.post("/orders/{id}/record/agent/runs", status_code=202)
def start_agent(id: str, body: RunWrite, idempotency_key: str = Header()):
    with Session.begin() as s:
        order = lock_order(s, id)
        return idempotent(
            s,
            f"record-agent-start:{id}:{idempotency_key}",
            body.model_dump(),
            lambda: agent.run_view(s, agent.create_run(s, order, body)),
        )


@router.get("/orders/{id}/record/agent/runs/{run_id}")
def agent_run(id: str, run_id: str):
    with Session.begin() as s:
        order = lock_order(s, id)
        w = records.view(s, order)
        agent.invalidate(s, order, w)
        return agent.run_view(s, agent.same_order(s, order, run_id))


def control(id, run_id, body, key, action):
    with Session.begin() as s:
        order = lock_order(s, id)
        return idempotent(
            s,
            f"record-agent-{action}:{id}:{run_id}:{key}",
            body.model_dump(),
            lambda: agent.control_run(s, order, run_id, body, action),
        )


@router.post("/orders/{id}/record/agent/runs/{run_id}/resume", status_code=202)
def resume(id: str, run_id: str, body: RunContinue, idempotency_key: str = Header()):
    return control(id, run_id, body, idempotency_key, "resume")


@router.post("/orders/{id}/record/agent/runs/{run_id}/retry", status_code=202)
def retry(id: str, run_id: str, body: RunContinue, idempotency_key: str = Header()):
    return control(id, run_id, body, idempotency_key, "retry")


@router.post("/orders/{id}/record/agent/runs/{run_id}/cancel")
def cancel(id: str, run_id: str, body: RunContinue, idempotency_key: str = Header()):
    return control(id, run_id, body, idempotency_key, "cancel")


@router.post("/orders/{id}/record/agent/proposals/{proposal_id}/resolve")
def resolve(
    id: str, proposal_id: str, body: ProposalResolve, idempotency_key: str = Header()
):
    with Session.begin() as s:
        order = lock_order(s, id)
        return idempotent(
            s,
            f"record-agent-resolve:{id}:{proposal_id}:{idempotency_key}",
            body.model_dump(),
            lambda: agent.resolve(s, order, proposal_id, body),
        )


@router.post("/orders/{id}/record/agent/settings")
def settings(id: str, body: AgentSettingsWrite, idempotency_key: str = Header()):
    with Session.begin() as s:
        order = lock_order(s, id)
        return idempotent(
            s,
            f"record-agent-settings:{id}:{idempotency_key}",
            body.model_dump(),
            lambda: agent.update_settings(s, order, body),
        )


@router.get("/orders/{id}/record/graph")
def graph(id: str):
    with Session.begin() as s:
        order = lock_order(s, id)
        w = records.view(s, order)
        agent.invalidate(s, order, w)
        return project_graph(w, agent.proposals_for(s, order.id))
