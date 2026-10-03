"""Evidence-linked, reversible coordination. Never approves or sends anything.

All matches are scoped to the caller's order and deterministic. A tag on two
sheets is ambiguous, and a response, engineering fix, or notification is never
inferred from a matching string. Derived operational state lives separately
from the immutable source text and frozen approved record.
"""

import json
import re
from copy import deepcopy
from hashlib import sha256

from backend.domain.service import fail
from backend.storage.models import now, uid

from .connections import connections

ENGINE_VERSION = "connected-review-v1.1"

ROLES = ("pm", "drafting", "production", "commercial")
TAG = re.compile(
    r"(?<![\w-])(?:[A-Z]{1,8}-\d{1,6}[A-Z]?|\d{2}-[A-Z]\d{1,4})(?![\w-])", re.IGNORECASE
)
SHEET = re.compile(
    r"\b(?:drawing\s+sheet|sheet|drawing)\s*[:#]?\s*([A-Z]{1,4}-?\d{1,4}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
REV = re.compile(r"\b(?:rev(?:ision)?)[\s.:]+([A-Z0-9]{1,8})\b", re.IGNORECASE)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def key(*values):
    return digest(values)[:24]


def stamp():
    return now().isoformat()


def normalized(value):
    return value.upper().replace(" ", "")


def anchors_in(text):
    sheets = {normalized(x) for x in SHEET.findall(text)}
    bare = {
        normalized(x)
        for x in re.findall(r"\b(?:E|EL|M|S|A|P)-\d+(?:\.\d+)?\b", text, re.IGNORECASE)
    }
    sheets |= bare
    tags = {normalized(x) for x in TAG.findall(text)} - sheets
    # Common electrical sheet identifiers must not become equipment tags.
    tags = {t for t in tags if not re.fullmatch(r"(?:E|EL|M|S|A|P)-\d+(?:\.\d+)?", t)}
    return tags, sheets


def state(data):
    return data.setdefault(
        "coordination",
        {
            "suggestions": [],
            "activity": [],
            "tasks": [],
            "settings": {
                "weekly_digest_enabled": False,
                "customer_due_date": "",
                "role_owners": dict.fromkeys(ROLES, ""),
            },
            "suppressed": [],
            "last_run_at": "",
            "input_hash": "",
        },
    )


def drawings(documents, sources):
    docs = {
        d["id"]: d
        for d in documents
        if d.get("role") in {"drawing", "approval_drawing", "submittal"}
        and d.get("state") not in {"queued", "parsing", "processing", "failed"}
    }
    pages = {}
    for source in sources:
        if source["document_id"] not in docs or source.get("location", {}).get(
            "annotation_id"
        ):
            continue
        loc = source.get("location", {})
        page = loc.get("page", 1)
        bucket = pages.setdefault(
            (source["document_id"], page),
            {"sources": [], "document": docs[source["document_id"]], "page": page},
        )
        bucket["sources"].append(source)
    output = []
    for value in pages.values():
        text = "\n".join(x.get("text", "") for x in value["sources"])
        tags, sheets = anchors_in(text)
        value.update(tags=tags, sheets=sheets, text=text)
        output.append(value)
    return output


def candidate_pages(comment, pages):
    tags, sheets = anchors_in(comment.get("text", ""))
    if not tags and not sheets:
        return []
    candidates = [p for p in pages if (tags & p["tags"] or sheets & p["sheets"])]
    revision = comment.get("revision", "").upper()
    revision = re.sub(r"^REV(?:ISION)?[\s.:]*", "", revision)
    if revision and revision != "UNSPECIFIED":
        candidates = [
            p
            for p in candidates
            if not p["document"].get("revision_label")
            or re.sub(
                r"^REV(?:ISION)?[\s.:]*", "", p["document"]["revision_label"].upper()
            )
            == revision
        ]
    result = []
    for page in candidates:
        matched_tags, matched_sheets = tags & page["tags"], sheets & page["sheets"]
        exact = bool(
            (not tags or tags <= page["tags"])
            and (not sheets or sheets <= page["sheets"])
        )
        chosen = next(
            (s for s in page["sources"] if tags & anchors_in(s.get("text", ""))[0]),
            page["sources"][0],
        )
        label = ", ".join(sorted(page["sheets"])) or page["document"]["name"]
        revision_label = page["document"].get("revision_label")
        label += (
            " · Rev " + revision_label if revision_label else ""
        ) + f" · page {page['page']}"
        reason = (
            "Exact "
            + ", ".join(
                [
                    *("tag " + t for t in sorted(matched_tags)),
                    *("sheet " + t for t in sorted(matched_sheets)),
                ]
            )
            + " appears in the comment and drawing text."
        )
        result.append(
            {
                "source_id": chosen["id"],
                "label": label,
                "reason": reason,
                "exact": exact,
                "document_id": page["document"]["id"],
            }
        )
    return result


def activity(
    data, kind, title, reason, actor="Rivet", comment_id="", source_ids=None, undo=None
):
    entry = {
        "id": uid(),
        "kind": kind,
        "title": title,
        "reason": reason,
        "actor": actor,
        "comment_id": comment_id,
        "source_ids": source_ids or [],
        "at": stamp(),
        "undone_at": "",
        "undoable": bool(undo),
        "undo": undo,
    }
    state(data)["activity"].insert(0, entry)
    # Source changes belong in the same durable audit trail as human edits.
    data["events"].insert(
        0,
        {
            "id": entry["id"],
            "kind": "coordination",
            "summary": title,
            "reason": reason,
            "actor": actor,
            "at": entry["at"],
            "before": (undo or {}).get("before"),
            "after": (undo or {}).get("after"),
            "item_id": comment_id,
        },
    )
    return entry


def evidence(data, documents, sources):
    return digest(
        {
            "engine_version": ENGINE_VERSION,
            "comments": data["comments"],
            "changes": data["changes"],
            "approvals": [a["id"] for a in data["approvals"]],
            "documents": documents,
            "sources": sources,
            "settings": state(data)["settings"],
        }
    )


def synchronize(data, documents, sources, scheduled=False, instant=None):
    """Mutate only when evidence changed, or an opted-in weekly draft is due."""
    original = deepcopy(data)
    co = state(data)
    instant = instant or now()
    week = instant.strftime("%G-W%V")
    overdue = bool(
        co["settings"]["customer_due_date"]
        and co["settings"]["customer_due_date"] < instant.date().isoformat()
    )
    signature = digest([evidence(data, documents, sources), overdue])
    weekly_due = (
        scheduled
        and co["settings"]["weekly_digest_enabled"]
        and co.get("digest_week") != week
    )
    if signature == co.get("input_hash") and not weekly_due:
        return data != original
    pages = drawings(documents, sources)
    valid = set()
    source_by_id = {s["id"]: s for s in sources}
    active_comments = [c for c in data["comments"] if c.get("status") != "closed"]

    def propose(
        kind,
        identity,
        title,
        reason,
        comment=None,
        candidates=None,
        draft="",
        refs=None,
        target="",
    ):
        proposal_evidence = digest(
            {
                "comment": comment,
                "refs": [source_by_id.get(r) for r in refs or []],
                "draft": draft,
                "candidates": candidates,
                "changes": data["changes"] if kind == "impact_review" else None,
            }
        )
        sid = key(kind, identity, proposal_evidence)
        valid.add(sid)
        existing = next((x for x in co["suggestions"] if x["id"] == sid), None)
        if existing:
            return existing
        item = {
            "id": sid,
            "kind": kind,
            "title": title,
            "reason": reason,
            "confidence": "needs_review",
            "comment_id": (comment or {}).get("id", ""),
            "source_ids": refs or [],
            "target_source_id": target,
            "candidates": [
                {k: v for k, v in c.items() if k in {"source_id", "label", "reason"}}
                for c in candidates or []
            ],
            "draft": draft,
            "status": "pending",
            "created_at": stamp(),
            "resolved_at": "",
            "evidence_hash": proposal_evidence,
        }
        co["suggestions"].append(item)
        return item

    for comment in active_comments:
        candidates = candidate_pages(comment, pages)
        exact = [x for x in candidates if x["exact"]]
        refs = comment.get("source_ids", [])
        if not comment.get("target_source_id"):
            auto = exact[0] if len(exact) == 1 and len(candidates) == 1 else None
            suppression = key(comment["id"], (auto or {}).get("source_id", ""))
            if (
                auto
                and not comment.get("reviewed")
                and suppression not in co["suppressed"]
            ):
                before = deepcopy(comment)
                comment["target_source_id"] = auto["source_id"]
                comment["updated_at"] = stamp()
                activity(
                    data,
                    "drawing_link",
                    f"Linked comment {comment['number']} to {auto['label']}",
                    auto["reason"] + " Human review remains required.",
                    comment_id=comment["id"],
                    source_ids=list(dict.fromkeys([*refs, auto["source_id"]])),
                    undo={
                        "type": "comment",
                        "before": before,
                        "after": deepcopy(comment),
                        "after_hash": digest(comment),
                        "suppression": suppression,
                    },
                )
            elif candidates:
                propose(
                    "drawing_link",
                    [
                        comment["id"],
                        digest(comment.get("text")),
                        [x["source_id"] for x in candidates],
                    ],
                    f"Confirm drawing for comment {comment['number']}",
                    "Multiple or conflicting drawing anchors require a person to choose."
                    if len(candidates) > 1
                    else "Confirm the drawing before applying this link.",
                    comment,
                    candidates,
                    refs=list(
                        dict.fromkeys([*refs, *(x["source_id"] for x in candidates)])
                    ),
                    target=candidates[0]["source_id"] if len(candidates) == 1 else "",
                )
            else:
                propose(
                    "clarification",
                    [comment["id"], digest(comment.get("text"))],
                    f"Clarify comment {comment['number']}",
                    "No unique drawing match was found in this order. No drawing location has been guessed.",
                    comment,
                    draft=f"For comment {comment['number']} ({comment.get('revision') or 'revision not stated'}):\n\n“{comment['text']}”\n\nCould you confirm the equipment tag, drawing sheet and revision this refers to?",
                    refs=refs,
                )
        elif not comment.get("reviewed") and (
            len(exact) != 1
            or len(candidates) != 1
            or exact[0]["source_id"] != comment["target_source_id"]
        ):
            auto_link = next(
                (
                    a
                    for a in co["activity"]
                    if a["kind"] == "drawing_link"
                    and a["actor"] == "Rivet"
                    and a["comment_id"] == comment["id"]
                    and not a["undone_at"]
                ),
                None,
            )
            if auto_link:
                propose(
                    "impact_review",
                    [
                        "link-context",
                        comment["id"],
                        [c["source_id"] for c in candidates],
                    ],
                    f"Recheck drawing link for comment {comment['number']}",
                    "Additional or changed evidence means the earlier automatic drawing match is no longer unique. The saved link is retained for review, not silently replaced.",
                    comment,
                    refs=list(
                        dict.fromkeys(
                            [
                                *refs,
                                comment["target_source_id"],
                                *(c["source_id"] for c in candidates),
                            ]
                        )
                    ),
                    draft=f"Please review the drawing reference recorded for comment {comment['number']}. The current source set no longer supports a unique match. Confirm the equipment tag, drawing sheet and revision before relying on this link.",
                )
        task_id = key("comment-task", comment["id"])
        if not any(t["id"] == task_id for t in co["tasks"]):
            role = "drafting" if comment.get("target_source_id") else "pm"
            if key("task", task_id) not in co["suppressed"]:
                task = {
                    "id": task_id,
                    "managed": True,
                    "title": f"Review comment {comment['number']}"
                    if role == "drafting"
                    else f"Confirm context for comment {comment['number']}",
                    "role": role,
                    "owner": co["settings"]["role_owners"].get(role, ""),
                    "status": "open",
                    "comment_id": comment["id"],
                    "source_ids": list(
                        dict.fromkeys(
                            [
                                *refs,
                                *(
                                    [comment["target_source_id"]]
                                    if comment.get("target_source_id")
                                    else []
                                ),
                            ]
                        )
                    ),
                    "reason": "Drawing-linked comment routed for review; no engineering fix inferred."
                    if role == "drafting"
                    else "Unconfirmed drawing context routed to the PM.",
                    "note": "",
                    "created_at": stamp(),
                    "updated_at": stamp(),
                }
                co["tasks"].append(task)
                activity(
                    data,
                    "assignment",
                    task["title"] + " → " + role,
                    task["reason"],
                    comment_id=comment["id"],
                    source_ids=task["source_ids"],
                    undo={
                        "type": "task",
                        "after": deepcopy(task),
                        "after_hash": digest(task),
                        "suppression": key("task", task_id),
                    },
                )

    for task in co["tasks"]:
        comment = next(
            (c for c in data["comments"] if c["id"] == task.get("comment_id")), None
        )
        if (
            not comment
            or not task.get("managed")
            or key("task", task["id"]) in co["suppressed"]
        ):
            continue
        role = "drafting" if comment.get("target_source_id") else "pm"
        updates = {
            "role": role,
            "owner": co["settings"]["role_owners"].get(role, ""),
            "title": f"Review comment {comment['number']}"
            if role == "drafting"
            else f"Confirm context for comment {comment['number']}",
            "source_ids": list(
                dict.fromkeys(
                    [
                        *comment.get("source_ids", []),
                        *(
                            [comment["target_source_id"]]
                            if comment.get("target_source_id")
                            else []
                        ),
                    ]
                )
            ),
            "status": "done" if comment["status"] == "closed" else "open",
        }
        if any(task.get(k) != v for k, v in updates.items()):
            before = deepcopy(task)
            task.update(updates, updated_at=stamp())
            activity(
                data,
                "assignment",
                "Updated review routing: " + task["title"],
                "Routing reflects the current confirmed drawing link or human-recorded comment closure.",
                comment_id=comment["id"],
                source_ids=task["source_ids"],
                undo={
                    "type": "task",
                    "before": before,
                    "after": deepcopy(task),
                    "after_hash": digest(task),
                    "suppression": key("task", task["id"]),
                },
            )

    missing = [c for c in active_comments if not c.get("response", "").strip()]
    if missing:
        propose(
            "status_update",
            digest([(c["id"], c["text"]) for c in missing]),
            f"Prepare status update · {len(missing)} unanswered",
            "These comments have no recorded manufacturer response. The draft does not infer who owes the answer.",
            draft="Review status\n\n"
            + "\n".join(
                f"Comment {c['number']} ({c.get('revision') or 'revision not stated'}): {c['text']}\nManufacturer response: not recorded.\n"
                for c in missing
            ),
            refs=list(
                dict.fromkeys(sid for c in missing for sid in c.get("source_ids", []))
            ),
        )

    # Same sheet/tag on multiple revisions is a textual change to inspect, not a
    # claim that a particular part/BOM/engineering setting has been changed.
    for index, page in enumerate(pages):
        for other in pages[index + 1 :]:
            common = page["tags"] & other["tags"]
            if (
                not common
                or not page["sheets"] & other["sheets"]
                or page["document"].get("revision_label")
                == other["document"].get("revision_label")
                or page["text"] == other["text"]
            ):
                continue
            pair = sorted([page["document"]["id"], other["document"]["id"]])
            refs = [p["sources"][0]["id"] for p in (page, other)]
            bom = [
                s
                for s in sources
                if any(
                    d["id"] == s["document_id"] and d.get("role") == "bom"
                    for d in documents
                )
                and anchors_in(s["text"])[0] & common
            ]
            refs += [s["id"] for s in bom]
            tags = ", ".join(sorted(common))
            title = f"Review revision impact · {tags}"
            suggestion = propose(
                "impact_review",
                [pair, tags, digest([page["text"], other["text"]])],
                title,
                "The same tag and sheet appear in different revisions with different extracted text. Geometry, implementation and BOM correctness require engineering review.",
                refs=refs,
                draft=f"Please review {tags} on {', '.join(sorted(page['sheets'] & other['sheets']))}: revision {page['document'].get('revision_label')} and revision {other['document'].get('revision_label')} contain different extracted text.\n\n"
                + (
                    "A BOM entry with the same tag is attached for production review. No mismatch has been confirmed."
                    if bom
                    else "Confirm whether related production documents need an update."
                )
                + "\n\nNo approval, release, or engineering correction is implied.",
            )
            task_id = key("impact", suggestion["id"])
            if (
                not any(t["id"] == task_id for t in co["tasks"])
                and key("task", task_id) not in co["suppressed"]
            ):
                role = "production" if bom else "drafting"
                task = {
                    "id": task_id,
                    "managed": True,
                    "title": title,
                    "role": role,
                    "owner": co["settings"]["role_owners"].get(role, ""),
                    "status": "open",
                    "comment_id": "",
                    "source_ids": refs,
                    "reason": suggestion["reason"],
                    "note": "",
                    "created_at": stamp(),
                    "updated_at": stamp(),
                }
                co["tasks"].append(task)
                activity(
                    data,
                    "assignment",
                    title + " → " + role,
                    task["reason"],
                    source_ids=refs,
                    undo={
                        "type": "task",
                        "after": deepcopy(task),
                        "after_hash": digest(task),
                        "suppression": key("task", task_id),
                    },
                )

    deviations = connections(data, documents)["after_approval"]
    if deviations and any(deviations.values()):
        propose(
            "impact_review",
            [data["approvals"][-1]["id"], digest(deviations)],
            "Review changes since the approved record",
            "The working record differs from the frozen approved record. This does not prove a design contradiction; the PM must review the differences.",
            draft="The working order record has changed since the approved snapshot.\n\n"
            + f"{len(deviations['comment_ids'])} comment records, {len(deviations['change_ids'])} change records and {len(deviations['document_ids'])} new documents need review.\n\nPlease confirm the effect on the approved scope and the customer and production recipients before sharing an update.",
            refs=list(
                dict.fromkeys(
                    sid
                    for c in data["comments"]
                    if c["id"] in deviations["comment_ids"]
                    for sid in c.get("source_ids", [])
                )
            ),
        )

    weekly_evidence = digest(
        [
            data["comments"],
            data["changes"],
            [a["id"] for a in data["approvals"]],
            documents,
        ]
    )
    for old_digest in co["suggestions"]:
        if (
            old_digest["kind"] == "weekly_digest"
            and old_digest["status"] == "pending"
            and old_digest.get("weekly_evidence") != weekly_evidence
        ):
            old_digest.update(status="stale", resolved_at=stamp())
    if weekly_due:
        co["digest_week"] = week
        body = (
            "Weekly order status · "
            + week
            + "\n\n"
            + f"{len(active_comments)} open comments; {len(missing)} without a recorded response.\n"
            + "\n".join(
                f"Comment {c['number']}: {c['status']} — {c['text']}"
                for c in active_comments
            )
            + "\n\nRecorded changes:\n"
            + ("\n".join(c["title"] for c in data["changes"]) or "No changes recorded.")
            + "\n\nPrepared from the current record. Review recipient list and content before sending."
        )
        weekly_item = propose(
            "weekly_digest",
            week,
            "Weekly digest · " + week,
            "Weekly draft schedule is enabled for this order. No email has been sent.",
            draft=body,
            refs=list(
                dict.fromkeys(
                    sid for c in active_comments for sid in c.get("source_ids", [])
                )
            ),
        )
        weekly_item["weekly_evidence"] = weekly_evidence
        activity(
            data,
            "weekly_digest",
            "Prepared weekly digest · " + week,
            "Opt-in schedule; draft only. No recipients were contacted.",
        )
    due = co["settings"]["customer_due_date"]
    if due and due < instant.date().isoformat() and active_comments:
        propose(
            "reminder",
            [due, digest([c["id"] for c in active_comments])],
            "Customer review date has passed",
            "The configured review date has passed and comments remain open; responsibility has not been inferred.",
            draft=f"The recorded customer review date was {due}. We have {len(active_comments)} open comments in the order record. Please confirm the current review status and expected next update.",
        )
    for suggestion in co["suggestions"]:
        if (
            suggestion["status"] == "pending"
            and suggestion["id"] not in valid
            and suggestion["kind"] != "weekly_digest"
        ):
            suggestion.update(status="stale", resolved_at=stamp())
    co["input_hash"] = digest([evidence(data, documents, sources), overdue])
    co["last_run_at"] = stamp()
    return data != original


def projection(data, documents, sources, delivery_by_notice=None):
    data = deepcopy(data)
    deliveries = delivery_by_notice or {}
    for notice in data["notices"]:
        delivery = deliveries.get(notice["id"])
        if delivery:
            notice["status"] = delivery["status"]
            notice["recipients"] = delivery.get("payload", {}).get(
                "recipients", notice["recipients"]
            )
    co = deepcopy(state(deepcopy(data)))
    anchor_map = {}

    def add_anchor(kind, value, source_ids=None, comment_id="", document_id=""):
        if not value:
            return
        item = anchor_map.setdefault(
            (kind, value),
            {
                "id": key(kind, value),
                "kind": kind,
                "value": value,
                "source_ids": [],
                "comment_ids": [],
                "document_ids": [],
            },
        )
        for field, vals in [
            ("source_ids", source_ids or []),
            ("comment_ids", [comment_id] if comment_id else []),
            ("document_ids", [document_id] if document_id else []),
        ]:
            item[field] = sorted(set(item[field]) | set(vals))

    for source in sources:
        tags, sheets = anchors_in(source.get("text", ""))
        for kind, vals in [
            ("equipment", tags),
            ("sheet", sheets),
            ("revision", REV.findall(source.get("text", ""))),
        ]:
            for value in vals:
                add_anchor(
                    kind,
                    normalized(value),
                    [source["id"]],
                    document_id=source["document_id"],
                )
    for doc in documents:
        mail = doc.get("email") or {}
        for field in ("message_id", "in_reply_to"):
            add_anchor("email_thread", mail.get(field, ""), document_id=doc["id"])
        for value in (
            mail.get("references", [])
            if isinstance(mail.get("references"), list)
            else re.findall(r"<[^>]+>", mail.get("references", ""))
        ):
            add_anchor("email_thread", value, document_id=doc["id"])
        add_anchor("person", mail.get("from", ""), document_id=doc["id"])
    links = []

    def link(kind, ft, fid, tt, tid, refs, reason, automatic=False):
        links.append(
            {
                "id": key(kind, ft, fid, tt, tid),
                "type": kind,
                "from_type": ft,
                "from_id": fid,
                "to_type": tt,
                "to_id": tid,
                "source_ids": refs,
                "reason": reason,
                "confidence": "explicit",
                "automatic": automatic,
            }
        )

    message_documents = {}
    for document in documents:
        message_id = (document.get("email") or {}).get("message_id", "").strip()
        if message_id:
            message_documents.setdefault(message_id, []).append(document["id"])
    for document in documents:
        mail = document.get("email") or {}
        raw_refs = mail.get("references", "")
        thread_refs = ([raw_refs] if isinstance(raw_refs, str) else raw_refs) + [
            mail.get("in_reply_to", "")
        ]
        ids = {
            value for field in thread_refs for value in re.findall(r"<[^>]+>", field)
        }
        for message_id in sorted(ids):
            parents = message_documents.get(message_id, [])
            if len(parents) == 1 and parents[0] != document["id"]:
                link(
                    "references",
                    "document",
                    document["id"],
                    "document",
                    parents[0],
                    [
                        source["id"]
                        for source in sources
                        if source["document_id"] == document["id"]
                    ],
                    "Exact email Message-ID is present in the reply References or In-Reply-To header. No comment answer or approval is inferred.",
                    True,
                )

    for comment in data["comments"]:
        cid, refs = comment["id"], comment.get("source_ids", [])
        tags, sheets = anchors_in(comment["text"])
        for kind, vals in [
            ("equipment", tags),
            ("sheet", sheets),
            ("comment", [comment.get("revision", "") + " · " + comment["number"]]),
            ("person", [comment.get("author", "")]),
        ]:
            for value in vals:
                add_anchor(kind, value, refs, cid, comment.get("document_id", ""))
        target = comment.get("target_source_id")
        if target:
            auto = next(
                (
                    a
                    for a in co["activity"]
                    if a["kind"] == "drawing_link"
                    and a.get("comment_id") == cid
                    and a.get("actor") == "Rivet"
                    and not a.get("undone_at")
                    and (a.get("undo") or {}).get("after", {}).get("target_source_id")
                    == target
                ),
                None,
            )
            link(
                "references",
                "comment",
                cid,
                "source",
                target,
                list(dict.fromkeys([*refs, target])),
                auto["reason"] if auto else "Drawing reference recorded by a reviewer.",
                bool(auto),
            )
        if comment.get("response"):
            link(
                "answers",
                "response",
                cid,
                "comment",
                cid,
                refs,
                "A response was explicitly saved on this comment; status is controlled by a person.",
            )
        if comment.get("linked_email_id"):
            link(
                "references",
                "comment",
                cid,
                "document",
                comment["linked_email_id"],
                refs,
                "Imported email or reviewer-selected email; no response or approval inferred.",
            )
    for change in data["changes"]:
        for cid in change.get("comment_ids", []):
            c = next((c for c in data["comments"] if c["id"] == cid), {})
            link(
                "caused_change",
                "comment",
                cid,
                "change",
                change["id"],
                c.get("source_ids", []),
                "A reviewer linked this change to the comment.",
            )
    for approval in data["approvals"]:
        for comment in approval.get("snapshot", {}).get("comments", []):
            link(
                "included_in_approval",
                "comment",
                comment["id"],
                "approval",
                approval["id"],
                comment.get("source_ids", []),
                "Comment included in a human-recorded approved snapshot; this is not proof of customer approval or production release.",
            )
    for notice in data["notices"]:
        if notice.get("status") == "sent":
            for recipient in notice.get("recipients", []):
                link(
                    "notified",
                    "notice",
                    notice["id"],
                    "person",
                    recipient.get("id") or key("recipient", recipient["email"]),
                    [],
                    "Delivery was recorded as sent; acknowledgment has not been inferred.",
                )
    for item in co["activity"]:
        item["undoable"] = can_undo(data, item) and not (
            (item.get("undo") or {}).get("type") == "notice"
            and (item.get("undo") or {}).get("after", {}).get("id") in deliveries
        )
        item.pop("undo", None)
    for suggestion in co["suggestions"]:
        suggestion.pop("evidence_hash", None)
    metrics = {
        "accepted_unchanged": 0,
        "accepted_edited": 0,
        "skipped": 0,
        "pending": 0,
    }
    for item in co["suggestions"]:
        if item["status"] == "accepted":
            metrics[
                "accepted_edited" if item.get("edited") else "accepted_unchanged"
            ] += 1
        elif item["status"] in metrics:
            metrics[item["status"]] += 1
    comments = data["comments"]
    summary = f"{len(comments)} comments · {sum(bool(c.get('response')) for c in comments)} with responses · {sum(not c.get('reviewed') for c in comments)} need review · {sum(t['status'] != 'done' and t['role'] == 'drafting' for t in co['tasks'])} with drafting"
    return {
        k: co[k]
        for k in ("suggestions", "activity", "tasks", "settings", "last_run_at")
    } | {
        "anchors": list(anchor_map.values()),
        "links": links,
        "readiness": readiness(data, documents, sources),
        "metrics": metrics,
        "summary": summary,
    }


def readiness(data, documents, sources):
    def check(id, label, bad, detail, manual=False):
        return {
            "id": id,
            "label": label,
            "status": "needs_review" if manual else "blocked" if bad else "pass",
            "detail": detail,
            "comment_ids": [
                c["id"] for c in bad if isinstance(c, dict) and "number" in c
            ],
            "source_ids": list(
                dict.fromkeys(
                    s
                    for c in bad
                    if isinstance(c, dict)
                    for s in c.get("source_ids", [])
                )
            ),
        }

    comments = data["comments"]
    unanswered = [c for c in comments if not c.get("response", "").strip()]
    unreviewed = [c for c in comments if not c.get("reviewed")]
    failed = [
        d
        for d in documents
        if d.get("state") in {"queued", "parsing", "processing", "failed"}
    ]
    changed = connections(data, documents)["after_approval"]
    approval_current = bool(data["approvals"]) and not any((changed or {}).values())
    notices = [n for n in data["notices"] if n.get("status") != "sent"]
    return {
        "resubmit": [
            check(
                "sources",
                "Documents processed",
                failed or ([] if documents else [True]),
                "All sources processed."
                if documents and not failed
                else "Upload documents and resolve processing failures.",
            ),
            check(
                "responses",
                "Manufacturer responses recorded",
                unanswered or ([] if comments else [True]),
                f"{len(unanswered)} comments have no response."
                if comments
                else "No comments recorded.",
            ),
            check(
                "review",
                "Comments reviewed",
                unreviewed or ([] if comments else [True]),
                f"{len(unreviewed)} comments need human review.",
            ),
            check(
                "implementation",
                "Promised changes verified in drawings",
                [],
                "A responsible engineer must verify the drawing geometry and promised changes. Text matches do not certify implementation.",
                True,
            ),
            check(
                "title_blocks",
                "Revision, date and stamp checked",
                [],
                "Review the actual title blocks, revision dates and approval stamps before resubmitting.",
                True,
            ),
        ],
        "release": [
            check(
                "approved_record",
                "Current approved record",
                [] if approval_current else [True],
                "The working record matches a frozen human-approved snapshot."
                if approval_current
                else "Record an approval after reviewing all changes to the working record.",
            ),
            check(
                "distribution",
                "Change distribution recorded",
                notices or ([] if data["subscribers"] else [True]),
                f"{len(notices)} notices remain unsent. Delivery is not acknowledgment."
                if data["subscribers"]
                else "No recipient list is configured.",
            ),
            check(
                "production",
                "Customer approval and production acknowledgment",
                [],
                "Explicit customer approval and production acknowledgment must be verified. Rivet does not infer them from email wording or internal record approval.",
                True,
            ),
        ],
    }


def can_undo(data, item):
    action = item.get("undo")
    if not action or item.get("undone_at"):
        return False
    collection = {"comment": "comments", "task": "tasks", "notice": "notices"}[
        action["type"]
    ]
    items = state(data)["tasks"] if collection == "tasks" else data[collection]
    current = next((x for x in items if x["id"] == action["after"]["id"]), None)
    if not current or digest(current) != action["after_hash"]:
        return False
    if action["type"] == "comment":
        if any(a["at"] >= item["at"] for a in data["approvals"]):
            return False
        if any(
            current["id"] in c.get("comment_ids", [])
            and c.get("updated_at", c["created_at"]) >= item["at"]
            for c in data["changes"]
        ):
            return False
    return not (action["type"] == "notice" and current.get("status") != "draft")


def mutate(data, documents, sources, body, kind, item_id=""):
    synchronize(data, documents, sources)
    co = state(data)
    actor = body.actor
    if kind == "run":
        synchronize(data, documents, sources)
        return
    if kind == "settings":
        co["settings"] = body.model_dump(
            exclude={"expected_version", "actor", "reason"}
        )
        activity(data, "settings", "Updated coordination settings", body.reason, actor)
    elif kind == "task":
        item = next((x for x in co["tasks"] if x["id"] == item_id), None)
        if not item:
            fail("Task not found.", 404)
        item["managed"] = False
        item.update(
            body.model_dump(exclude={"expected_version", "actor", "reason"}),
            updated_at=stamp(),
        )
        activity(
            data,
            "assignment",
            "Updated task: " + item["title"],
            body.reason,
            actor,
            item.get("comment_id", ""),
            item.get("source_ids", []),
        )
    elif kind == "undo":
        item = next((x for x in co["activity"] if x["id"] == item_id), None)
        if not item:
            fail("Activity not found.", 404)
        if not can_undo(data, item):
            fail(
                "This action has newer edits or downstream records and can no longer be safely undone.",
                409,
            )
        action = item["undo"]
        if action["type"] == "comment":
            comment = next(
                c for c in data["comments"] if c["id"] == action["after"]["id"]
            )
            # Restore only the field this action changed; source/review state is untouched.
            comment["target_source_id"] = action["before"].get("target_source_id", "")
            comment["updated_at"] = stamp()
        else:
            collection = co["tasks"] if action["type"] == "task" else data["notices"]
            collection[:] = [x for x in collection if x["id"] != action["after"]["id"]]
            if action.get("before"):
                collection.append(deepcopy(action["before"]))
        if action.get("suppression"):
            co["suppressed"].append(action["suppression"])
        item["undone_at"] = stamp()
        activity(
            data,
            "undo",
            "Undid: " + item["title"],
            body.reason,
            actor,
            item.get("comment_id", ""),
            item.get("source_ids", []),
        )
    elif kind == "suggestion":
        item = next((x for x in co["suggestions"] if x["id"] == item_id), None)
        if not item:
            fail("Suggestion not found.", 404)
        if item["status"] != "pending":
            fail("This suggestion is no longer current. Refresh the record.", 409)
        if body.action == "skip":
            item.update(status="skipped", resolved_at=stamp())
            activity(
                data,
                "suggestion",
                "Skipped: " + item["title"],
                body.reason,
                actor,
                item["comment_id"],
                item["source_ids"],
            )
        else:
            if item["kind"] == "drawing_link":
                comment = next(
                    (c for c in data["comments"] if c["id"] == item["comment_id"]), None
                )
                if not comment or comment.get("target_source_id"):
                    fail("Comment context changed. Refresh the current proposal.", 409)
                target = body.target_source_id or item["target_source_id"]
                if (
                    not target
                    or not any(x["source_id"] == target for x in item["candidates"])
                    or not any(s["id"] == target for s in sources)
                ):
                    fail(
                        "Select one of the evidence-linked drawing candidates in this order.",
                        422,
                    )
                before = deepcopy(comment)
                comment.update(target_source_id=target, updated_at=stamp())
                item["edited"] = bool(
                    body.target_source_id
                    and body.target_source_id != item["target_source_id"]
                )
                activity(
                    data,
                    "drawing_link",
                    "Confirmed: " + item["title"],
                    body.reason,
                    actor,
                    comment["id"],
                    [*comment.get("source_ids", []), target],
                    {
                        "type": "comment",
                        "before": before,
                        "after": deepcopy(comment),
                        "after_hash": digest(comment),
                        "suppression": key(comment["id"], target),
                    },
                )
            else:
                draft = body.draft or item["draft"]
                if not draft.strip():
                    fail("Draft content is required.", 422)
                notice = {
                    "id": uid(),
                    "event_id": "",
                    "title": item["title"],
                    "body": draft,
                    "recipients": deepcopy(data["subscribers"]),
                    "status": "draft",
                    "at": stamp(),
                    "suggestion_id": item["id"],
                }
                data["notices"].insert(0, notice)
                item["edited"] = draft != item["draft"]
                activity(
                    data,
                    "draft",
                    "Saved unsent draft: " + item["title"],
                    body.reason,
                    actor,
                    item["comment_id"],
                    item["source_ids"],
                    {
                        "type": "notice",
                        "after": deepcopy(notice),
                        "after_hash": digest(notice),
                    },
                )
            item.update(status="accepted", resolved_at=stamp())
    synchronize(data, documents, sources)


def scheduled_pass(instant=None):
    """Opt-in, once-per-week draft creation under tenant scope and order locks."""
    from sqlalchemy import select

    from backend.identity import Identity, identity_scope
    from backend.orders.service import lock_order
    from backend.storage.db import Session
    from backend.storage.models import OrderRecord

    from .service import ensure, source_data

    instant = instant or now()
    with Session() as s:
        candidates = [
            (r.organization_id, r.order_id)
            for r in s.scalars(select(OrderRecord))
            if (r.data.get("coordination") or {})
            .get("settings", {})
            .get("weekly_digest_enabled")
            or (r.data.get("coordination") or {})
            .get("settings", {})
            .get("customer_due_date")
        ]
    count = 0
    for tenant, order_id in candidates:
        with (
            identity_scope(Identity(tenant, "Rivet scheduled coordinator")),
            Session.begin() as s,
        ):
            order = lock_order(s, order_id)
            record = ensure(s, order)
            data = deepcopy(record.data)
            docs, sources = source_data(s, order)
            if synchronize(data, docs, sources, scheduled=True, instant=instant):
                record.data = data
                record.version += 1
                order.updated_at = now()
                count += 1
    return count
