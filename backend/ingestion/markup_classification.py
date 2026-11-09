"""Conservative markup classification and atomic numbered-note splitting.

These rules do not consult evaluation labels. Unknown marks stay unclassified;
stamps, signatures and product selections remain evidence rather than tasks.
"""

import re
from copy import deepcopy


def actionability(finding):
    kind = str(finding.get("kind", "")).casefold().replace("_", " ")
    text = str(finding.get("text", "")).strip()
    if re.match(r"^(?:illegible\s+)?handwritten\s+signature\b", text, re.IGNORECASE):
        return False
    if any(
        term in kind
        for term in (
            "stamp",
            "signature",
            "selection",
            "highlight",
            "overprint",
            "underlying",
            "check mark",
            "review mark",
            "supporting markup",
        )
    ):
        return False
    if re.fullmatch(
        r"[-\s\d.)]*(?:light fixtures|alternate wall switch)?\s*[-–—]?\s*no comment[.!]?",
        text,
        re.IGNORECASE,
    ):
        return False
    if any(term in kind for term in ("ambiguous", "unreadable", "underline")):
        return None
    if any(term in kind for term in ("comment", "note", "handwriting")):
        return True
    return None


def atomic_findings(findings):
    """Split only explicitly numbered lines. Preserve the parent region exactly.

    A shared bbox is intentionally marked approximate; no invented per-line
    geometry or semantic subdivision is supplied.
    """
    output = []
    for finding in findings:
        item = deepcopy(finding)
        text = str(item.get("text", ""))
        starts = list(re.finditer(r"(?m)^\s*(\d{1,3})[.)]\)?\s+", text))
        if (
            actionability(item) is True
            and len(starts) > 1
            and not text[: starts[0].start()].strip()
        ):
            numbers = [int(match[1]) for match in starts]
            if numbers == list(range(numbers[0], numbers[0] + len(numbers))):
                for index, start in enumerate(starts):
                    end = (
                        starts[index + 1].start()
                        if index + 1 < len(starts)
                        else len(text)
                    )
                    output.append(
                        {
                            **item,
                            "text": text[start.start() : end].strip(),
                            "parent_text": text,
                            "subcomment_index": index + 1,
                            "shared_parent_bbox": True,
                        }
                    )
                continue
        output.append(item)
    return output


def annotation_actionability(annotation):
    """Fixed content rules, never answer-key page numbers or expected counts."""
    text = " ".join(str(annotation.get("text", "")).split()).casefold()
    kind = str(annotation.get("kind", "")).casefold()
    if not text:
        return None, "Graphical annotation has no readable text."
    if "stamp" in kind or "signature" in kind:
        return (
            False,
            "Explicit stamp/signature annotation; not an actionable instruction.",
        )
    if re.search(r"\bno comments?\s*[.!]?\s*$", text):
        return False, "Explicit no-comment disposition; not a requested action."
    actions = re.compile(
        r"\b(?:confirm|provide|change|revise|replace|add|remove|show|clarify|verify|coordinate|relocate|include|indicate|correct|submit|explain|ensure|need|shall|must|use|check)\b"
    )
    if actions.search(text):
        return (
            True,
            "Readable review instruction candidate; engineering correctness is ungraded.",
        )
    date = r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},?\s+\d{2,4})"
    if re.fullmatch(date, text):
        return False, "Date-only review metadata."
    if re.match(r"^(?:rec(?:eived)?|recv|rcvd)\.?\s+(?:by|on)\b", text):
        return False, "Receipt acknowledgement, retained as source metadata."
    if re.fullmatch(r"(?:[0-9][0-9 .:/-]*|[a-z]{1,3}|[×✓✔])", text):
        return (
            None,
            "Short reference/initial/check mark is retained but its technical meaning is unclassified.",
        )
    metadata = re.sub(date, " ", text)
    metadata = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?\b", " ", metadata)
    metadata = re.sub(
        r"\b(?:received|receipt|date|time|reviewed|approved|submitted|signed|by|on)\b[:\s-]*",
        " ",
        metadata,
    )
    if not re.sub(r"[\s,.;:/()-]+", "", metadata):
        return False, "Date, receipt or review-status metadata without an instruction."
    original = " ".join(str(annotation.get("text", "")).split())
    name = re.fullmatch(r"[A-Z][a-z]+(?:[ -][A-Z][a-z]+){1,3}", original)
    date_and_name = (
        re.search(date, text)
        and len(metadata.split()) <= 4
        and re.fullmatch(r"[a-z .,'-]+", metadata.strip())
    )
    if name or date_and_name:
        return False, "Name/signature metadata without an instruction."
    return (
        True,
        "Readable review annotation candidate; metadata heuristic did not exclude it.",
    )
