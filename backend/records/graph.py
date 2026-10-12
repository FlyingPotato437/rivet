"""Typed, evidence-based projection. A mention is never a verified engineering fact."""

from .automation import anchors_in, digest


def project_graph(workspace, proposals=()):
    nodes, edges = {}, []

    def node(id, kind, label, **values):
        nodes[id] = {"id": id, "type": kind, "label": label, **values}

    def edge(source, target, kind, origin="imported", **values):
        if source in nodes and target in nodes:
            edges.append(
                {
                    "id": digest([source, target, kind])[:24],
                    "source": source,
                    "target": target,
                    "type": kind,
                    "origin": origin,
                    **values,
                }
            )

    order = workspace["order"]
    node(order["id"], "order", order["title"])
    for doc in workspace["documents"]:
        node(
            doc["id"],
            "document",
            doc["name"],
            revision=doc.get("revision_label", ""),
            state=doc["state"],
            coverage=doc.get("coverage", []),
            origin="imported",
        )
        edge(order["id"], doc["id"], "contains")
    referenced = {
        sid
        for c in workspace["comments"]
        for sid in [*c.get("source_ids", []), c.get("target_source_id", "")]
    }
    referenced.update(sid for p in proposals for sid in p.evidence_ids)
    referenced.update(
        sid
        for task in workspace.get("coordination", {}).get("tasks", [])
        for sid in task.get("source_ids", [])
    )
    referenced.update(
        sid
        for notice in workspace.get("notices", [])
        for sid in notice.get("source_ids", [])
    )
    sources = workspace["sources"]
    included = [s for s in sources if s["id"] in referenced]
    included += [s for s in sources if s["id"] not in referenced][
        : max(0, 600 - len(included))
    ]
    for source in included:
        loc = source.get("location", {})
        node(
            source["id"],
            "source",
            source["text"][:100],
            document_id=source["document_id"],
            location=loc,
            origin="imported",
        )
        edge(source["document_id"], source["id"], "contains")
        tags, sheets = anchors_in(source["text"])
        for tag in tags:
            id = "equipment:" + digest([order["id"], tag])[:20]
            node(
                id,
                "equipment_reference",
                tag,
                origin="rule_derived",
                review_state="unreviewed",
            )
            edge(source["id"], id, "mentions", "rule_derived")
        for sheet in sheets:
            id = "sheet:" + digest([source["document_id"], loc.get("page"), sheet])[:20]
            node(
                id,
                "sheet_reference",
                sheet,
                document_id=source["document_id"],
                page=loc.get("page"),
                origin="rule_derived",
                review_state="unreviewed",
            )
            edge(source["id"], id, "mentions", "rule_derived")
    for comment in workspace["comments"]:
        node(
            comment["id"],
            "comment",
            "Comment " + comment["number"],
            text=comment["text"],
            status=comment["status"],
            review_state="confirmed" if comment["reviewed"] else "unreviewed",
            source_page=comment.get("source_page"),
            origin=comment.get("origin", "imported"),
        )
        edge(order["id"], comment["id"], "contains")
        for sid in comment.get("source_ids", []):
            edge(comment["id"], sid, "extracted_from")
        if comment.get("target_source_id"):
            edge(
                comment["id"],
                comment["target_source_id"],
                "references",
                "human" if comment["reviewed"] else "unconfirmed",
                review_state="confirmed" if comment["reviewed"] else "unreviewed",
            )
        if comment.get("response"):
            rid = "response:" + comment["id"]
            node(
                rid,
                "response",
                comment["response"][:120],
                origin="human",
                author=comment.get("responder", ""),
            )
            edge(rid, comment["id"], "responds_to", "human")
    for change in workspace["changes"]:
        node(change["id"], "change", change["title"], origin="human")
        for cid in change.get("comment_ids", []):
            edge(change["id"], cid, "records_change_for", "human")
    for task in workspace.get("coordination", {}).get("tasks", []):
        origin = task.get("origin", "automatic" if task.get("managed") else "human")
        node(
            task["id"],
            "task",
            task["title"],
            status=task["status"],
            origin=origin,
            role=task.get("role", "pm"),
            run_id=task.get("run_id", ""),
        )
        edge(order["id"], task["id"], "has_work", origin)
        if task.get("comment_id"):
            edge(task["id"], task["comment_id"], "follows_up", origin)
        if task.get("change_id"):
            edge(task["id"], task["change_id"], "follows_up", origin)
        for sid in task.get("source_ids", []):
            edge(task["id"], sid, "depends_on", origin)
        role_id = "role:" + task.get("role", "pm")
        node(role_id, "team_role", task.get("role", "pm").title())
        edge(task["id"], role_id, "routed_to", origin)
        if task.get("owner"):
            owner_id = "owner:" + digest([order["id"], task["owner"]])[:20]
            node(
                owner_id,
                "person",
                "Assigned reviewer" if "@" in task["owner"] else task["owner"],
                origin="human",
            )
            edge(task["id"], owner_id, "assigned_to", "human")
    for notice in workspace.get("notices", []):
        origin = notice.get("origin", "human")
        node(
            notice["id"],
            "notice_draft",
            notice["title"],
            origin=origin,
            status="draft_record",
            run_id=notice.get("run_id", ""),
            note="Prepared communication. Delivery and acknowledgment are separate records.",
        )
        edge(order["id"], notice["id"], "has_communication", origin)
        if notice.get("comment_id"):
            edge(notice["id"], notice["comment_id"], "addresses", origin)
        for sid in notice.get("source_ids", []):
            edge(notice["id"], sid, "depends_on", origin)
        sent = [
            link
            for link in workspace.get("coordination", {}).get("links", [])
            if link["type"] == "notified" and link["from_id"] == notice["id"]
        ]
        if sent:
            delivery_id = "delivery:" + notice["id"]
            node(
                delivery_id,
                "recorded_delivery",
                "Notice recorded as sent",
                origin="human",
                status="sent",
                recipient_count=len(sent),
                acknowledgment="not_inferred",
            )
            edge(delivery_id, notice["id"], "sent_content", "human")
    for approval in workspace["approvals"]:
        node(
            approval["id"],
            "record_approval",
            approval["label"],
            origin="human",
            version=approval["version"],
            at=approval["at"],
        )
        edge(
            approval["id"],
            order["id"],
            "reviews_record_version",
            "human",
            version=approval["version"],
        )
    for proposal in proposals:
        node(
            proposal.id,
            "proposal",
            proposal.title,
            origin=proposal.origin,
            status=proposal.status,
            base_version=proposal.base_version,
        )
        for sid in proposal.evidence_ids:
            edge(proposal.id, sid, "depends_on", proposal.origin)
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "version": workspace["version"],
        "coverage": {
            "source_count": len(sources),
            "included_sources": len(included),
            "note": "Extracted references and recorded relationships; not a verified equipment model.",
        },
    }
