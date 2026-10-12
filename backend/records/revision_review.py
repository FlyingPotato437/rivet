"""Evidence-linked revision checks, deliberately narrower than engineering review.

These checks compare recorded comments. They never infer that drawing geometry was
fixed, that production is authorized, or that a commercial change is chargeable.
"""

import re
from datetime import UTC, datetime
from hashlib import sha256

KINDS = ("new", "repeated", "unresolved", "late_change", "scope_change")


def workflow_status(comment):
    if comment.get("status") == "closed":
        return "resolved"
    if not comment.get("reviewed"):
        return "needs_review"
    explicit = comment.get("workflow_status")
    if explicit in {"sent_to_drafting", "awaiting_customer"}:
        return explicit
    return (
        "awaiting_customer"
        if comment.get("status") == "responded"
        else "needs_response"
    )


def normalized(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


def instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except (ValueError, AttributeError):
        return None


def review_summary(w):
    documents = {d["id"]: d for d in w.get("documents", [])}
    sources = {s["id"]: s for s in w.get("sources", [])}
    comments = w.get("comments", [])
    ordered = sorted(
        comments,
        key=lambda c: (
            documents.get(c.get("document_id"), {}).get("created_at", ""),
            c.get("created_at", ""),
        ),
    )
    revisions = []
    for comment in ordered:
        revision = comment.get("revision") or "Unspecified"
        if revision in revisions:
            revisions.remove(revision)
        revisions.append(revision)
    comment_documents = {c.get("document_id") for c in comments}
    returned_documents = sorted(
        [
            d
            for d in documents.values()
            if (d["id"] in comment_documents or d.get("role") in {"markup", "markups"})
            and d.get("revision_label")
            and d.get("state") not in {"queued", "parsing", "processing", "failed"}
        ],
        key=lambda d: (
            (instant(d.get("created_at")) or datetime.min.replace(tzinfo=UTC)),
            d.get("created_at", ""),
        ),
    )
    for document in returned_documents:
        revision = document["revision_label"]
        if revision in revisions:
            revisions.remove(revision)
        revisions.append(revision)
    latest = revisions[-1] if revisions else "Unspecified"
    prior_revision = revisions[-2] if len(revisions) > 1 else ""
    current = [c for c in comments if (c.get("revision") or "Unspecified") == latest]
    prior = [c for c in comments if c not in current]
    # Only exact normalized wording is a repeated comment. Similar language is
    # not evidence that two engineering requests have the same meaning.
    previous_text = {}
    for comment in prior:
        text = normalized(comment.get("original_text") or comment.get("text", ""))
        if text and comment.get("origin") != "unreadable area":
            previous_text.setdefault(text, []).append(comment)
    flags = []

    def add(kind, comment, title, detail, previous=None, extra=None):
        ids = list(
            dict.fromkeys(
                [
                    *comment.get("source_ids", []),
                    *(previous or {}).get("source_ids", []),
                ]
            )
        )
        evidence = [
            {
                "source_id": sid,
                "document_id": sources[sid]["document_id"],
                "page": sources[sid].get("location", {}).get("page"),
                "text": sources[sid].get("text", "")[:2000],
            }
            for sid in ids
            if sid in sources
        ]
        flags.append(
            {
                "id": sha256(
                    f"{kind}:{comment['id']}:{(previous or {}).get('id', '')}".encode()
                ).hexdigest()[:24],
                "kind": kind,
                "title": title,
                "detail": detail,
                "comment_id": comment["id"],
                "prior_comment_id": (previous or {}).get("id", ""),
                "source_ids": [e["source_id"] for e in evidence],
                "revision": comment.get("revision") or "Unspecified",
                "prior_revision": (previous or {}).get("revision", ""),
                "evidence": evidence,
                "requires_review": True,
                "origin": "automatic",
                **(extra or {}),
            }
        )

    approvals = sorted(w.get("approvals", []), key=lambda a: a.get("at", ""))
    approval = approvals[-1] if approvals else None
    for comment in current:
        if comment.get("origin") == "unreadable area":
            continue
        original = comment.get("original_text") or comment.get("text", "")
        matches = previous_text.get(normalized(original), [])
        if matches:
            previous = matches[-1]
            add(
                "repeated",
                comment,
                "Repeated comment",
                "The same normalized comment text appears in an earlier revision. Confirm whether it is the same issue.",
                previous,
            )
        else:
            add(
                "new",
                comment,
                "New in this return",
                "No identical comment text was found in earlier recorded revisions. This does not establish that the design request is new.",
            )
        received_at = documents.get(comment.get("document_id"), {}).get(
            "created_at", ""
        )
        received_instant = instant(received_at)
        approved_instant = instant(approval.get("at")) if approval else None
        if (
            received_instant
            and approved_instant
            and received_instant > approved_instant
        ):
            add(
                "late_change",
                comment,
                "After approved record",
                "This source arrived after the recorded approval. Confirm whether it changes approved scope; arrival timing alone does not prove a design conflict.",
                extra={
                    "approval_id": approval["id"],
                    "approval_at": approval["at"],
                    "approval_label": approval.get("label", "Approved record"),
                },
            )
        # Quote the commercial language; never derive entitlement from the word
        # 'add', quantity changes, or a generic request to revise a drawing.
        signal = next(
            (
                match
                for match in re.finditer(
                    r"\b(?:outside (?:the |original |agreed )?scope|out[- ]of[- ]scope|change order|additional (?:cost|charge)|not included in (?:the )?(?:quote|quotation|contract|scope))\b",
                    original,
                    re.IGNORECASE,
                )
                if not re.search(
                    r"\b(?:no|without|not a|does not require|no need for a)\s*$",
                    original[max(0, match.start() - 35) : match.start()],
                    re.IGNORECASE,
                )
            ),
            None,
        )
        if signal:
            add(
                "scope_change",
                comment,
                "Possible scope change",
                f"The source says “{signal.group(0)}”. Compare the request with the agreed scope before making a commercial decision.",
                extra={"matched_text": signal.group(0)},
            )
    for comment in prior:
        if comment.get("status") != "closed":
            add(
                "unresolved",
                comment,
                "Prior item still open",
                "An earlier revision comment has no recorded closure. Review the new drawing and response before deciding whether it was missed.",
            )
    return {
        "latest_revision": latest,
        "prior_revision": prior_revision,
        "current_document_ids": list(
            dict.fromkeys(
                [
                    *[
                        d["id"]
                        for d in returned_documents
                        if d["revision_label"] == latest
                    ],
                    *[c["document_id"] for c in current if c.get("document_id")],
                ]
            )
        ),
        "prior_document_ids": list(
            dict.fromkeys(c["document_id"] for c in prior if c.get("document_id"))
        ),
        "flags": flags,
        "counts": {kind: sum(f["kind"] == kind for f in flags) for kind in KINDS},
        "limitations": [
            "Checks use recorded comments and source arrival times, not drawing geometry or engineering approval.",
            "Identical wording indicates a possible repeated issue. An open prior item does not prove it was missed in the drawing.",
            "Scanned handwriting, flattened clouds, and unreadable stamps require manual review. They are not automatically transcribed.",
        ],
    }
