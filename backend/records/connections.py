"""A deterministic read model of explicit record relationships.

No embedding similarity, inferred drawing matches, or engineering conclusions.
The same projection drives navigation and the assistant's bounded context.
"""


def same_record(a, b):
    ignored = {"updated_at", "confidence", "flags"}
    return {k: v for k, v in a.items() if k not in ignored} == {
        k: v for k, v in b.items() if k not in ignored
    }


def connections(data, documents):
    latest = data.get("approvals", [])[-1] if data.get("approvals") else None
    approved = (latest or {}).get("snapshot", {})
    old_comments = {c["id"]: c for c in approved.get("comments", [])}
    old_changes = {c["id"]: c for c in approved.get("changes", [])}
    comments = data.get("comments", [])
    changes = data.get("changes", [])
    events = data.get("events", [])
    return {
        "comments": {
            c["id"]: {
                "source_ids": c.get("source_ids", []),
                "document_id": c.get("document_id", ""),
                "drawing_source_id": c.get("target_source_id", ""),
                "email_document_id": c.get("linked_email_id", ""),
                "change_ids": [
                    x["id"] for x in changes if c["id"] in x.get("comment_ids", [])
                ],
                "event_ids": [e["id"] for e in events if e.get("item_id") == c["id"]],
                "in_approved_record": c["id"] in old_comments,
                "matches_approved_record": c["id"] in old_comments
                and same_record(c, old_comments[c["id"]]),
            }
            for c in comments
        },
        "latest_approval_id": latest["id"] if latest else "",
        "after_approval": {
            "comment_ids": [
                c["id"]
                for c in comments
                if c["id"] not in old_comments
                or not same_record(c, old_comments[c["id"]])
            ],
            "change_ids": [
                c["id"]
                for c in changes
                if c["id"] not in old_changes
                or not same_record(c, old_changes[c["id"]])
            ],
            "document_ids": [
                d["id"]
                for d in documents
                if d["id"] not in {x["id"] for x in approved.get("documents", [])}
            ],
        }
        if latest
        else None,
    }
