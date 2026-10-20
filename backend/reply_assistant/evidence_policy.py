"""Delivery claims and connected-fact citations are checked outside the model."""

import re


class DraftPolicyError(ValueError):
    pass


ATTACHMENT_ERROR = (
    "The generated reply described outgoing attachments before delivery could verify them. "
    "No email was changed or sent. Retry preparation."
)


def claims_outgoing_attachments(body):
    if not isinstance(body, str):
        return False
    text = body.lower().replace("’", "'")
    patterns = (
        r"\b(?:no|without any)\s+attachments?\b.{0,80}\b(?:included|attached|enclosed|draft|email|reply)\b",
        r"\b(?:this|the outgoing)\s+(?:draft|email|reply|message)\b.{0,80}\b(?:attach\w*|include\w*|contain\w*|without|enclose\w*)\b",
        r"\b(?:attachment|file|drawing|document|pdf)s?\b.{0,60}\b(?:is|are|was|were|has been|have been|will be|isn't|aren't)\s+(?:not\s+)?(?:attached|included|enclosed)\b",
        r"\b(?:i|we|i've|we've)\s+(?:(?:have|has|am|are|will|can|cannot|can't|won't|not)\s+){0,3}(?:attach\w*|enclos\w*)\b",
        r"\b(?:please find|see|refer to)\s+(?:the\s+)?(?:attached|enclosed)\b",
        r"\b(?:attached|enclosed)\s+(?:is|are|you will find)\b",
        r"\b(?:no|not any)\s+attachments?\s*[.!]",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _words(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value).lower()))


def _contains(text, value):
    needle = _words(value)
    return bool(needle) and f" {needle} " in f" {_words(text)} "


def connected_fact_support(connected, sources):
    """Pin actual passages for mapped conflicts, never a document's generic footer.

    Provider mappings describe what to compare; they are not a substitute for a
    passage in the retrieved document. Every selected ID must belong to that
    resource and the current order's source set.
    """
    resources = connected.get("resources", [])
    values = {}
    for resource in resources:
        if resource.get("lifecycle") in {"accepted", "approved", "released"}:
            for key, value in resource.get("facts", {}).items():
                values.setdefault(key, set()).add(str(value))
    conflicting = {key for key, vals in values.items() if len(vals) > 1}
    required = []
    for resource in resources:
        for key, value in resource.get("facts", {}).items():
            if key not in conflicting:
                continue
            entity, _, field = key.partition(".")
            candidates = [
                source
                for source in sources
                if source["document_id"] == resource["document_id"]
                and source["id"] in resource.get("source_ids", [])
                and _contains(source["text"], value)
                and (
                    _contains(source["text"], entity)
                    or _contains(source["text"], field)
                )
            ]
            if not candidates:
                raise DraftPolicyError(
                    f"The mapped {key} value in {resource['name']} has no supporting "
                    "passage in the current retrieved document. Check the source mapping before retrying."
                )
            source = max(
                candidates,
                key=lambda item: (
                    _contains(item["text"], entity) + _contains(item["text"], field),
                    -len(item["text"]),
                ),
            )
            required.append(
                {
                    "document_id": resource["document_id"],
                    "source_id": source["id"],
                    "fact": key,
                    "value": str(value),
                }
            )
    return required


def grounded_source_ids(current, selected):
    support = current.get("connected_fact_support", [])
    sources = {source["id"]: source for source in current["sources"]}
    covered_documents = {item["document_id"] for item in support}
    for item in support:
        source = sources.get(item["source_id"])
        if (
            not source
            or source["document_id"] != item["document_id"]
            or not _contains(source["text"], item["value"])
        ):
            raise DraftPolicyError(
                "A required connected-source passage was omitted from retrieval. "
                "No email was changed or sent. Narrow the reply scope and retry."
            )
    # Replace model-selected fragments of these documents with the verified
    # material passages. Keep its citations to the customer's mail and markups.
    return list(
        dict.fromkeys(
            [item["source_id"] for item in support]
            + [
                sid
                for sid in selected
                if sources[sid]["document_id"] not in covered_documents
            ]
        )
    )
