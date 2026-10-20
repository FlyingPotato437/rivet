"""Recognize administrative attachment covers without discarding email evidence.

Unknown prose remains an unresolved email comment. This narrow allowlist only
avoids turning a transmittal and its workflow instructions into a second task
beside the actual attached markups. The original message and spans are kept for
the reply agent and source history in either case.
"""

import re

_JOB = r"(?: for job [a-z0-9]+(?:[-./][a-z0-9]+)*(?:,? rev(?:ision)? [a-z0-9]+)?)?"
_COVERS = (
    r"(?:please )?(?:review|see|find) (?:the )?(?:attached|enclosed) "
    r"(?:(?:returned|marked[- ]up|review) )?(?:drawing|drawings|pdf|comments|annotations|markups|documents|submittal)"
    + _JOB,
    r"(?:please )?(?:review|see) (?:the )?(?:\d+ )?(?:comments|annotations|markups) "
    r"(?:in|on) the (?:attached|enclosed) (?:returned )?(?:drawing|pdf|documents)"
    + _JOB,
    r"(?:the )?(?:returned )?(?:drawing|drawings|pdf|comments|annotations|markups|documents|submittal) "
    r"(?:is |are )?(?:attached|enclosed)" + _JOB,
)
_CONTEXT = (
    r"fictional product demonstration",
    r"(?:fictional|sample|test|demo) (?:product )?(?:rehearsal|message|example) only",
    r"this is not a customer order,? purchase authorization or engineering release",
    (
        r"use the existing (?:specification|accepted scope|(?:fictional )?engineering decision|customer history)"
        r"(?:(?:,? and |, )(?:specification|accepted scope|(?:fictional )?engineering decision|customer history))*"
        r" in the order record"
    ),
    (
        r"(?:please )?prepare (?:the |a )?(?:responses?|reply|comment log|proposed change order)"
        r"(?: and (?:the |a )?(?:responses?|reply|comment log|proposed change order))*"
        r"(?: for the (?:additional|added) (?:breaker|device))?"
    ),
    r"keep the (?:drawing|cad|bom) edits as an engineering draft",
)


def attachment_cover_reason(body, *, has_attachments):
    """A nonempty reason means context-only, never approval or completed work."""
    if not has_attachments or not body or len(body) > 6000:
        return ""
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return ""
    # Strip only conventional greetings. An unknown line stays in the message.
    lines = [
        line
        for line in lines
        if not re.fullmatch(
            r"(?:hi|hello|dear)(?: [a-z][a-z -]{0,50})?[,!]", line, re.IGNORECASE
        )
    ]
    for index, line in enumerate(lines):
        if re.fullmatch(r"(?:thanks|thank you|best|regards)[,!]?", line, re.IGNORECASE):
            signature = lines[index + 1 :]
            if len(signature) <= 2 and all(
                re.fullmatch(r"[a-z][a-z .'-]{0,79}", name, re.IGNORECASE)
                and not re.search(
                    r"\b(?:please|confirm|change|update|approve|release|require|must|shall|need|add|remove|check|review)\b",
                    name,
                    re.IGNORECASE,
                )
                for name in signature
            ):
                lines = lines[:index]
            break
    content = re.sub(r"\s+", " ", " ".join(lines)).strip()
    sentences = [
        part.strip(" ,") for part in re.split(r"[.;]\s*", content) if part.strip(" ,")
    ]
    if not sentences:
        return ""
    saw_cover = False
    for sentence in sentences:
        if any(re.fullmatch(pattern, sentence, re.IGNORECASE) for pattern in _COVERS):
            saw_cover = True
        elif not any(
            re.fullmatch(pattern, sentence, re.IGNORECASE) for pattern in _CONTEXT
        ):
            return ""
    return (
        "Attachment transmittal and preparation context; review comments remain on the attached source."
        if saw_cover
        else ""
    )
