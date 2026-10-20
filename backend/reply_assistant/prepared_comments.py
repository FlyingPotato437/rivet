"""Reuse current per-comment work when composing the native Outlook reply."""

from backend.agents.context_budget import token_count
from backend.domain.service import digest, scoped
from backend.storage.models import RecordAgentRun


def attach(s, order, w, data, focused):
    from backend.records.agent_service import input_hash
    from backend.records.agent_tasks import rows

    current = input_hash(w)
    run = s.scalar(
        scoped(RecordAgentRun)
        .where(
            RecordAgentRun.order_id == order.id,
            RecordAgentRun.input_hash == current,
            RecordAgentRun.status == "completed",
        )
        .order_by(RecordAgentRun.created_at.desc())
        .limit(1)
    )
    if not run or run.state.get("parallel_merged_generation") != run.state.get(
        "generation", 0
    ):
        return ""
    sources = {sp["id"]: sp for sp in w["sources"]}
    active_documents = {d["id"] for d in data["documents"]}
    tasks = [
        t
        for t in rows(s, run)
        if t.status == "prepared"
        and (not focused or t.snapshot["comment"].get("document_id") in focused)
        and set(t.result.get("source_ids", [])) <= sources.keys()
        and all(
            sources[id]["document_id"] in active_documents
            for id in t.result.get("source_ids", [])
        )
    ]
    if not tasks:
        return ""
    prepared = []
    for t in tasks[:40]:
        item = {
            "comment_id": t.comment_id,
            "draft": t.result["text"],
            "source_ids": t.result["source_ids"],
            "limitations": t.result.get("limitations", []),
            "origin": "AI-prepared; not human approval or evidence of an executed drawing edit",
        }
        if token_count(prepared + [item]) > 6000:
            break
        prepared.append(item)
    if not prepared:
        return ""
    data["prepared_comment_responses"] = prepared
    existing = {sp["id"] for sp in data["sources"]}
    for id in sorted({id for p in prepared for id in p["source_ids"]} - existing):
        source = sources[id]
        data["sources"].append(
            {
                **source,
                "text": source.get("text", "")[:500],
                "text_truncated": len(source.get("text", "")) > 500,
                "excerpt_start": 0,
                "excerpt_end": min(len(source.get("text", "")), 500),
                "evidence_role": "prepared_response_reference",
            }
        )
    data["coverage"]["parallel_responses_prepared"] = len(tasks)
    data["coverage"]["parallel_responses_included"] = len(prepared)
    data["coverage"]["included_sources"] = len(data["sources"])
    return digest(prepared)
