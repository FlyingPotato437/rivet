"""Read-only comment dispositions from current drawing/BOM checks.

Draft responses are a projection, never recorded human responses or approvals.
They live outside comments so browsing cannot invalidate a saved reply's evidence
fingerprint. No model call, record write, or email action occurs here.
"""

import logging
import re

from fastapi import HTTPException

from backend.equipment.tags import TAG_RE

log = logging.getLogger(__name__)
LABELS = {
    "checked": "Checked in draft · engineer review required",
    "remaining": "Change still required",
    "clarification": "Needs clarification",
    "correspondence": "Correspondence · retained",
}


def _fields(text):
    fields = []
    for field, pattern in (
        ("nameplate", r"\b(?:name\s*plate|plate|label)\b"),
        ("trip_a", r"\btrip(?!\s+unit)\b"),
        ("frame_a", r"\bframe\b"),
        ("trip_unit", r"\btrip\s+unit\b"),
    ):
        if re.search(pattern, text, re.IGNORECASE):
            fields.append(field)
    return fields


def _value(cell):
    value = cell.get("value")
    return f"{value}{' ' + cell['unit'] if cell.get('unit') else ''}"


def _request_conflicts(text, check):
    required = check.get("required", {}).get("value")
    if required is None:
        return True
    if check["field"] in {"trip_a", "frame_a"}:
        word = "trip" if check["field"] == "trip_a" else "frame"
        explicit = re.search(
            rf"\b{word}\b[^.\n]{{0,35}}?(?:must be|shall be|set to|change to|:)\s*(\d+(?:\.\d+)?)\s*A\b",
            text,
            re.IGNORECASE,
        )
        return bool(explicit and float(explicit[1]) != float(required))
    if check["field"] == "nameplate" and re.search(
        r"\b(?:must|shall|read|plate\s*:)\b", text, re.IGNORECASE
    ):
        normalize = lambda s: re.sub(r"[^A-Z0-9]", "", str(s).upper())
        return normalize(required) not in normalize(text)
    return False


def _refs(check):
    refs = []
    for role in ("required", "target_drawing", "target_bom"):
        for evidence in check.get(role, {}).get("evidence", []):
            location = []
            if evidence.get("page"):
                location.append(f"p. {evidence['page']}")
            for key in ("worksheet", "cell", "entity_handle"):
                if evidence.get(key):
                    location.append(str(evidence[key]))
            label = " · ".join(
                filter(None, [evidence.get("name", "Source"), *location])
            )
            if label not in refs:
                refs.append(label)
    return refs


def build(comments, documents, comparison=None, corrected=None):
    checks = (comparison or {}).get("checks", [])
    known_tags = {check.get("device_tag", "").upper() for check in checks}
    corrections = {
        (c["device_tag"], c["field"]): c
        for c in (corrected or {}).get("verification", {}).get("checks", [])
    }
    entries = {}
    for c in comments:
        if c.get("response"):
            continue  # A person's saved text always wins.
        text = c.get("text", "")
        tags = {
            m[1].upper()
            for m in TAG_RE.finditer(text)
            if not re.fullmatch(r"\d+(?:P|PH|HZ|KVA|VAC|VDC)", m[1], re.IGNORECASE)
            and not (
                re.fullmatch(r"\d+Y", m[1], re.IGNORECASE)
                and re.match(r"/\s*\d+\s*V\b", text[m.end() :], re.IGNORECASE)
            )
        }
        fields = _fields(text)
        if fields == ["trip_unit"]:
            tags -= {
                str(check.get("required", {}).get("value", "")).upper()
                for check in checks
                if check.get("field") == "trip_unit"
            }
        matches = [
            check
            for check in checks
            if check.get("device_tag", "").upper() in tags
            and check.get("field") in fields
        ]
        status = "clarification"
        response = (
            "A source-backed answer has not been established for this request. "
            "Please confirm the requested change and drawing location."
        )
        references = []
        if c.get("origin") == "email" and not (tags & known_tags) and not fields:
            status = "correspondence"
            response = (
                "Email retained with its attachments and revision history. "
                "Technical responses are listed against the individual drawing comments. "
                "This message does not establish engineering approval or production release."
            )
        elif (not c.get("original_text") and c.get("origin") != "manual") or c.get(
            "origin"
        ) == "unreadable area":
            response = (
                "This graphical mark has no confirmed readable instruction. "
                "Please clarify the marked area before it can be answered or closed."
            )
        elif len(tags) == 1 and len(fields) == 1 and len(matches) == 1:
            check = matches[0]
            key = (check["device_tag"], check["field"])
            selected = corrections.get(key, check)
            subject = check["label"]
            if not subject.startswith(check["device_tag"]):
                subject = f"{check['device_tag']} · {subject}"
            references = _refs(check)
            required = check.get("required", {})
            drawing = selected.get("target_drawing", {})
            bom = selected.get("target_bom", {})
            if (
                selected.get("status") == "verified"
                and required.get("evidence")
                and all(
                    cell.get("value") is not None for cell in (required, drawing, bom)
                )
                and not _request_conflicts(text, check)
            ):
                status = "checked"
                revision = (
                    corrected["target_revision"]
                    if key in corrections
                    else (comparison or {}).get("target_revision", "")
                )
                response = (
                    f"{subject}: drawing {_value(drawing)}; "
                    f"BOM {_value(bom)}. Both match the supplied requirement "
                    f"({_value(required)}) in checked draft revision {revision}."
                )
                if key in corrections:
                    response += (
                        f" Revision {(comparison or {}).get('target_revision', '')} "
                        "did not satisfy this field; the saved corrected files were reopened and rechecked."
                    )
                    references.extend(
                        f"{a['name']} · rechecked {a['sha256'][:12]}"
                        for a in corrected.get("artifacts", [])
                        if a.get("role") in {"drawing", "bom"}
                    )
                response += " Engineer review is required; not an approval or production release."
            elif selected.get("status") in {"mismatch", "missing"}:
                status = "remaining"
                response = (
                    f"{subject}: the drawing/BOM check "
                    "still reports a missing or conflicting value. "
                    "Resolve the cited evidence before closing this comment."
                )
            else:
                response = (
                    f"{subject}: available drawing/BOM "
                    "evidence does not establish that this request was satisfied. Engineer clarification is required."
                )
            if c.get("origin") == "ai_transcription" and not c.get(
                "transcription_reviewed"
            ):
                response += " The handwritten instruction is an AI transcription; confirm it against the source."
        entries[c["id"]] = {
            "response": response,
            "author": "Rivet · evidence-backed draft",
            "status": status,
            "label": LABELS[status],
            "references": references,
            "reviewed": False,
        }
    return {
        "comments": entries,
        "counts": {
            key: sum(e["status"] == key for e in entries.values()) for key in LABELS
        },
        "note": "Prepared responses and draft checks; recorded engineer decisions are unchanged.",
    }


def for_order(s, order, workspace):
    if not order.state.get("equipment_check") and not order.state.get(
        "equipment_corrections", {}
    ).get("bundles"):
        return {"comments": {}, "counts": {}, "note": ""}
    from backend.equipment import service, workflow

    comparison, corrected = None, None
    try:
        comparison = service.view(s, order).get("comparison")
        for bundle in reversed(
            order.state.get("equipment_corrections", {}).get("bundles", [])
        ):
            public = workflow.public_bundle(s, order, bundle)
            if public.get("current") and public.get("attachable"):
                corrected = public
                break
    except (HTTPException, OSError, ValueError):
        # Failed freshness/hash checks never reuse a historical passing result.
        log.warning("Comment response evidence unavailable for order %s", order.id)
        corrected = None
        comparison = None
    return build(workspace["comments"], workspace["documents"], comparison, corrected)


def entry(workspace, comment):
    return workspace.get("response_log", {}).get("comments", {}).get(comment["id"], {})


def export_response(workspace, comment):
    proposed = entry(workspace, comment) if not comment.get("response") else {}
    text = comment.get("response") or proposed.get("response", "")
    if proposed.get("references"):
        text += "\nSources: " + "; ".join(proposed["references"])
    return (
        text,
        comment.get("responder") or proposed.get("author", ""),
        proposed.get("label") or comment["status"],
    )
