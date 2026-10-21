"""Keep no-text support marks separate from answerable reviewer notes.

Nothing is deleted or resolved. Unknown ink/cloud areas stay in the comment log;
line/highlight/strike objects remain explicitly available as supporting evidence.
"""

import re


def separate(comments, documents, sources):
    docs = {d["id"]: d for d in documents}
    spans = {s["id"]: s for s in sources}
    annotated_docs = {
        s["document_id"] for s in sources if s.get("location", {}).get("annotation_id")
    }
    visible, supporting = [], []
    for comment in comments:
        anchors = [spans[sid] for sid in comment.get("source_ids", []) if sid in spans]
        untouched = not comment.get("reviewed") and not comment.get("response")
        reason = ""
        if untouched and anchors and not comment.get("original_text"):
            kinds = {s.get("location", {}).get("annotation_type") for s in anchors}
            if (
                kinds
                and kinds <= {"Line", "Highlight", "StrikeOut"}
                and all(not s.get("text", "").strip() for s in anchors)
            ):
                reason = "No-text line/highlight/strike mark; interpret with the original page, not as a separate readable instruction."
        doc = docs.get(comment.get("document_id"), {})
        first_line = next(
            (
                line.strip()
                for line in comment.get("text", "").splitlines()
                if line.strip()
            ),
            "",
        )
        if (
            untouched
            and anchors
            and not reason
            and comment.get("origin") == "numbered text"
            and doc.get("name", "").lower().endswith(".pdf")
            and doc.get("role") in {"markup", "markups"}
            and doc.get("id") in annotated_docs
            and all(not s.get("location", {}).get("annotation_id") for s in anchors)
            and re.fullmatch(r"[\d\s.,/+-]+", first_line)
            and not re.search(
                r"\b(?:add|remove|change|provide|confirm|revise|replace|please|must|shall)\b",
                comment.get("text", ""),
                re.IGNORECASE,
            )
        ):
            reason = "Numeric drawing/dimension text without a reviewer instruction; retained as source evidence."
        if reason:
            supporting.append({**comment, "context_only_reason": reason})
        else:
            visible.append(comment)
    return visible, supporting
