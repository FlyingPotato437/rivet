"""Display identity and email context without rewriting the recorded evidence."""

from .email_comment_context import attachment_cover_reason


def comment_presentation(comments, documents):
    docs = {d["id"]: d for d in documents}
    visible, context = [], []
    for index, comment in enumerate(comments, 1):
        item = {**comment, "log_reference": f"C-{index:03d}"}
        doc = docs.get(item.get("document_id"), {})
        if (
            item.get("origin") == "unreadable area"
            and not item.get("reviewed")
            and not item.get("response")
            and doc.get("vision", {})
            .get("pages", {})
            .get(str(item.get("source_page")), {})
            .get("status")
            == "transcribed"
        ):
            context.append(
                {
                    **item,
                    "context_only_reason": "Original scan warning superseded by a located AI transcription. The original is retained.",
                }
            )
            continue
        # Never reclassify a PM's reviewed response, edits or manually entered item.
        reason = (
            attachment_cover_reason(
                item.get("original_text", ""),
                has_attachments=bool(doc.get("attachment_document_ids")),
            )
            if (
                item.get("origin") == "email"
                and not item.get("reviewed")
                and not item.get("response")
                and item.get("text") == item.get("original_text")
            )
            else ""
        )
        if reason:
            context.append({**item, "context_only_reason": reason})
        else:
            visible.append(item)
    return visible, context
