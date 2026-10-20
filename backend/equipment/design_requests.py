"""Compile cited drawing-change instructions into typed native operations.

This is the manufacturer's change-schedule adapter, not a free-form execution
tool. Source bytes, page and literal instruction stay attached to every action.
Unrecognized design instructions remain blockers instead of disappearing.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from backend.drafting.service import _checked_bytes, _safe_value

from .readers import MAX_PAGES, stable_id
from .schemas import SourceInput

NUMBER = r"-?(?:\d+(?:\.\d*)?|\.\d+)"
POINT = rf"\(\s*({NUMBER})\s*,\s*({NUMBER})\s*\)"
TAG = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
TERMINAL = rf"{TAG}:[A-Za-z0-9_+.-]{{1,40}}"
DESIGN_VERB = re.compile(
    r"^(?:(?:please|shall)\s+)?(?:move|relocate|resize|rewire|reroute|add\s+(?:device|wire)|remove\s+(?:device|wire)|delete\s+(?:device|wire))\b",
    re.IGNORECASE,
)
PREFIX = re.compile(
    r"^\s*(?:[-•]\s*)?CHANGE\s+([A-Za-z0-9_-]{1,40})\s*:\s*(.+)", re.IGNORECASE
)


def _points(text):
    matches = list(re.finditer(POINT, text))
    remainder = re.sub(POINT, "", text)
    if not 2 <= len(matches) <= 100 or remainder.strip(" ;\t"):
        raise ValueError("Wire route must contain two to 100 explicit (x, y) points.")
    return [[float(m[1]), float(m[2])] for m in matches]


def parse_instruction(text):
    """Return one exact operation; no values are filled from a language model."""
    text = text.strip().rstrip(".").strip()
    match = re.fullmatch(
        rf"(?:Move|Relocate)\s+({TAG})\s+to\s+{POINT}\s*mm", text, re.IGNORECASE
    )
    if match:
        return {
            "kind": "move_device",
            "device_tag": match[1].upper(),
            "to": [float(match[2]), float(match[3])],
        }
    match = re.fullmatch(
        rf"Resize\s+({TAG})\s+to\s+({NUMBER})\s*[x×]\s*({NUMBER})\s*mm",
        text,
        re.IGNORECASE,
    )
    if match:
        return {
            "kind": "resize_device",
            "device_tag": match[1].upper(),
            "width": float(match[2]),
            "height": float(match[3]),
        }
    match = re.fullmatch(
        rf"Rewire\s+({TAG})\s+from\s+({TERMINAL})\s+to\s+({TERMINAL})(?:\s+via\s+(.+))?",
        text,
        re.IGNORECASE,
    )
    if match:
        result = {
            "kind": "rewire",
            "wire_id": match[1].upper(),
            "from_terminal": match[2].upper(),
            "to_terminal": match[3].upper(),
        }
        if match[4]:
            result["points"] = _points(match[4])
        return result
    match = re.fullmatch(rf"Reroute\s+({TAG})\s+via\s+(.+)", text, re.IGNORECASE)
    if match:
        return {
            "kind": "reroute",
            "wire_id": match[1].upper(),
            "points": _points(match[2]),
        }
    match = re.fullmatch(
        rf"(?:Remove|Delete)\s+(device|wire)\s+({TAG})", text, re.IGNORECASE
    )
    if match:
        return {
            "kind": "remove_" + match[1].lower(),
            "device_tag" if match[1].lower() == "device" else "wire_id": match[
                2
            ].upper(),
        }
    match = re.fullmatch(
        rf"Add\s+wire\s+({TAG})\s+net\s+({TAG})\s+from\s+({TERMINAL})\s+to\s+({TERMINAL})(?:\s+via\s+(.+))?",
        text,
        re.IGNORECASE,
    )
    if match:
        result = {
            "kind": "add_wire",
            "wire_id": match[1].upper(),
            "net_id": match[2].upper(),
            "from_terminal": match[3].upper(),
            "to_terminal": match[4].upper(),
        }
        if match[5]:
            result["points"] = _points(match[5])
        return result
    match = re.fullmatch(
        rf"Add\s+device\s+({TAG})\s+using\s+block\s+({TAG})\s+at\s+{POINT}\s*mm\s*;\s*(.+)",
        text,
        re.IGNORECASE,
    )
    if match:
        attributes = {}
        for item in match[5].split(";"):
            attr = re.fullmatch(
                r"\s*([A-Za-z][A-Za-z0-9_]{0,49})\s*=\s*(.{1,200}?)\s*", item
            )
            if not attr or attr[1].upper() in attributes:
                raise ValueError(
                    "Each added device attribute needs one unique explicit value."
                )
            key, value = attr[1].upper(), attr[2].strip()
            if key in {"EQUIPMENT_TAG", "TAG", "TAG1", "DEVICE_TAG"}:
                raise ValueError(
                    "The instruction's device tag cannot be overridden by an attribute."
                )
            _safe_value(value)
            attributes[key] = value
        if not attributes.get("PART_NUMBER") or not attributes.get("QUANTITY"):
            raise ValueError(
                "Adding a device requires an explicit part number and quantity for the BOM."
            )
        return {
            "kind": "add_device",
            "device_tag": match[1].upper(),
            "block_name": match[2],
            "at": [float(match[3]), float(match[4])],
            "attributes": attributes,
        }
    raise ValueError(
        "The design instruction needs exact device/terminal identities and dimensions or route points before Rivet can execute it."
    )


def extract_requests(sources):
    import pymupdf

    requests, blockers, recognized = [], [], []
    for value in sources:
        source = (
            value
            if isinstance(value, SourceInput)
            else SourceInput.model_validate(value)
        )
        if (
            source.role not in {"specification", "markup"}
            or Path(source.name).suffix.lower() != ".pdf"
        ):
            continue
        try:
            data = _checked_bytes(Path(source.path), source.sha256)
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if document.needs_pass or len(document) > MAX_PAGES:
                    raise ValueError(
                        "The requested-change PDF exceeds readable coverage."
                    )
                for page_index, page in enumerate(document):
                    # Selectable page text is not proof that an embedded scan
                    # or a returned handwriting image has been interpreted.
                    if page.get_image_info():
                        blockers.append(
                            {
                                "code": "uninterpreted_request_image",
                                "source_id": source.id,
                                "name": source.name,
                                "sha256": source.sha256,
                                "page": page_index + 1,
                                "reason": "This returned-change page contains raster content that has not been interpreted. Confirm its changes before preparing native corrections.",
                            }
                        )
                    lines = page.get_text().splitlines()
                    for line in lines:
                        instruction = PREFIX.fullmatch(line.strip())
                        raw = instruction[2] if instruction else line.strip()
                        if not instruction and not DESIGN_VERB.match(raw):
                            continue
                        if not instruction and not re.search(
                            r"\b(?:[A-Za-z0-9]{1,10}-\d+|W\d+)\b", raw
                        ):
                            # Section titles such as 'Relocate alarm relay with
                            # its wires' do not create a second incomplete task.
                            continue
                        evidence = {
                            "source_id": source.id,
                            "name": source.name,
                            "sha256": source.sha256,
                            "revision": source.revision,
                            "page": page_index + 1,
                            "text": line.strip(),
                        }
                        try:
                            operation = parse_instruction(raw)
                            boxes = page.search_for(line.strip())
                            if len(boxes) == 1:
                                b = boxes[0]
                                evidence["bbox"] = [
                                    b.x0 / page.rect.width,
                                    b.y0 / page.rect.height,
                                    b.x1 / page.rect.width,
                                    b.y1 / page.rect.height,
                                ]
                            requests.append(
                                {
                                    "id": stable_id(
                                        "native-request",
                                        source.id,
                                        source.sha256,
                                        page_index,
                                        instruction[1] if instruction else raw,
                                    ),
                                    "change_id": instruction[1]
                                    if instruction
                                    else hashlib.sha256(raw.encode()).hexdigest()[:8],
                                    **operation,
                                    "evidence": evidence,
                                }
                            )
                            recognized.append(evidence)
                        except ValueError as exc:
                            blockers.append(
                                {
                                    "code": "unresolved_native_instruction",
                                    "reason": str(exc),
                                    **evidence,
                                }
                            )
                    annotations = list(page.annots() or [])
                    if len(annotations) > 500:
                        blockers.append(
                            {
                                "code": "annotation_coverage_limit",
                                "source_id": source.id,
                                "page": page_index + 1,
                                "reason": "This returned page has too many annotations to inspect completely. Split the marked-up package before preparing native corrections.",
                            }
                        )
                        continue
                    for annotation in annotations:
                        content = (annotation.info or {}).get("content", "")
                        content = content.strip() if isinstance(content, str) else ""
                        rect = annotation.rect
                        evidence = {
                            "source_id": source.id,
                            "name": source.name,
                            "sha256": source.sha256,
                            "revision": source.revision,
                            "page": page_index + 1,
                            "text": content,
                            "annotation_xref": annotation.xref,
                            "annotation_type": annotation.type[1],
                            "bbox": [
                                rect.x0 / page.rect.width,
                                rect.y0 / page.rect.height,
                                rect.x1 / page.rect.width,
                                rect.y1 / page.rect.height,
                            ],
                        }
                        if (
                            annotation.type[0]
                            not in {pymupdf.PDF_ANNOT_TEXT, pymupdf.PDF_ANNOT_FREE_TEXT}
                            or not content
                        ):
                            blockers.append(
                                {
                                    "code": "unconsumed_returned_annotation",
                                    "reason": "This returned markup is not a fully readable text change. Confirm the ink, cloud, stamp or highlighted change before preparing native corrections.",
                                    **evidence,
                                }
                            )
                            continue
                        prefix = PREFIX.fullmatch(content)
                        literal = prefix[2] if prefix else content
                        try:
                            operation = parse_instruction(literal)
                        except ValueError as exc:
                            blockers.append(
                                {
                                    "code": "unresolved_annotation_instruction",
                                    "reason": str(exc),
                                    **evidence,
                                }
                            )
                            continue
                        duplicate = None
                        for previous in requests:
                            if (
                                previous["evidence"].get("annotation_xref")
                                or previous["evidence"]["source_id"] != source.id
                                or previous["evidence"]["page"] != page_index + 1
                            ):
                                continue
                            previous_text = previous["evidence"]["text"].strip()
                            previous_prefix = PREFIX.fullmatch(previous_text)
                            previous_literal = (
                                previous_prefix[2] if previous_prefix else previous_text
                            )
                            if previous_literal.strip() == literal.strip() and all(
                                previous.get(key) == value
                                for key, value in operation.items()
                            ):
                                duplicate = previous
                                break
                        if duplicate is not None:
                            duplicate.setdefault("supporting_evidence", []).append(
                                evidence
                            )
                        else:
                            requests.append(
                                {
                                    "id": stable_id(
                                        "native-request-annotation",
                                        source.id,
                                        source.sha256,
                                        page_index,
                                        annotation.xref,
                                    ),
                                    "change_id": prefix[1]
                                    if prefix
                                    else f"annotation-{annotation.xref}",
                                    **operation,
                                    "evidence": evidence,
                                }
                            )
                        recognized.append(evidence)
        except Exception:  # noqa: BLE001 - unreadable source bytes never authorize edits.
            blockers.append(
                {
                    "code": "unreadable_native_requests",
                    "source_id": source.id,
                    "reason": "The requested-change original could not be read at its recorded hash.",
                }
            )
    return {"requests": requests, "blockers": blockers, "recognized_lines": recognized}
