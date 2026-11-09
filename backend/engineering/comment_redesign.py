"""Comment -> source-bound native changes -> independent saved-file checks.

This module never substitutes a text edit for a physical engineering change.
All supplied annotations retain an outcome, including those outside the current
adapter. Manufacturer files, not a fictional fixture, are the executable inputs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from collections import Counter
from html import escape
from pathlib import Path

import pymupdf
from ezdxf.tools.text import plain_mtext

from .cad_evidence import anchor, equipment_evidence, verify_saved_drawing
from .cad_geometry import render_region
from .cad_snapshot import snapshot_dxf
from .drawing_calculations import propose_current_corrections, verify_saved_calculations
from .dxf_patch import inspect_text_records, patch_dxf

VERSION = "rivet.comment-native-redesign.v3"


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _id(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:24]


def plain(text):
    return " ".join(plain_mtext(text).replace("^J", " ").split())


def _csv_cell(value):
    # PDF comments must remain text when opened in spreadsheet applications.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def comments_from_pdfs(sources):
    comments = []
    for source in sources:
        path = Path(source["path"])
        if path.suffix.lower() != ".pdf":
            continue
        if file_hash(path) != source["sha256"]:
            raise ValueError("A comment original changed before redesign")
        with pymupdf.open(path) as pdf:
            for index, page in enumerate(pdf):
                for annotation in page.annots() or []:
                    text = annotation.info.get("content", "").strip()
                    if not text:
                        continue
                    anchor = annotation.info.get("id") or str(annotation.xref)
                    comments.append(
                        {
                            "id": _id(source["id"], index + 1, anchor),
                            "text": text,
                            "source_document_id": source["id"],
                            "source_name": source["name"],
                            "source_sha256": source["sha256"],
                            "page": index + 1,
                            "annotation_id": anchor,
                            "bbox": list(annotation.rect),
                            "status": "needs_information",
                            "explanation": "The request is retained; its engineering effect has not been independently established.",
                            "change_ids": [],
                            "evidence_handles": [],
                        }
                    )
    return comments


def graphical_annotations(sources):
    """Preserve non-text markup coverage separately from readable requests."""
    unresolved = []
    for source in sources:
        if Path(source["path"]).suffix.lower() != ".pdf":
            continue
        if file_hash(source["path"]) != source["sha256"]:
            raise ValueError("A comment original changed before redesign")
        with pymupdf.open(source["path"]) as pdf:
            for page_number, page in enumerate(pdf, 1):
                for annotation in page.annots() or []:
                    if annotation.info.get("content", "").strip() or annotation.type[
                        1
                    ] in {"Popup", "Link", "Widget"}:
                        continue
                    unresolved.append(
                        {
                            "source_document_id": source["id"],
                            "source_sha256": source["sha256"],
                            "page": page_number,
                            "annotation_id": annotation.info.get("id")
                            or str(annotation.xref),
                            "annotation_type": annotation.type[1],
                            "bbox": list(annotation.rect),
                            "status": "needs_visual_review",
                            "detail": "Graphical markup retained; it may support a text comment or contain an additional request. Not automatically resolved.",
                        }
                    )
    return unresolved


def propose(comments, snapshot):
    """Prepare bounded terminology and source-bound drawing arithmetic.

    The adapter is deliberately narrower than the engineering objective. Physical
    layout and electrical changes remain open until their actual geometry and
    governing constraints are available. A separate solver may extend this list;
    its output must still pass the same native evidence and saved-file gates.
    """
    comments = json.loads(json.dumps(comments))
    placed = snapshot["placed_text"]
    edits, used = [], set()
    for comment in comments:
        text = comment["text"]
        # NLTC and de-energized tap changer refer to the same operating mode.
        # OLTC is intentionally excluded: a label must never change the device.
        requested = re.search(
            r'mark\s+tap\s+changers?\s+as\s+["“\'](DETC)["”\']', text, re.IGNORECASE
        )
        if requested:
            for item in placed:
                before = item["text"]
                label = plain(before)
                if label == "NLTC":
                    after = before.replace("NLTC", "DETC")
                elif re.fullmatch(
                    r"(?:HV|TV|YV) No-load tap changer", label, re.IGNORECASE
                ):
                    after = re.sub(
                        "No-load tap changer", "DETC", before, flags=re.IGNORECASE
                    )
                else:
                    continue
                handle = item["handle"].upper()
                if handle in used:
                    continue
                used.add(handle)
                identifier = _id(comment["id"], handle, after)
                edits.append(
                    {
                        "id": identifier,
                        "comment_id": comment["id"],
                        "handle": handle,
                        "entity_type": item["kind"],
                        "expected_text": before,
                        "replacement_text": after,
                        "allow_shared_definition": bool(item["block_path"]),
                        "label": "Tap changer terminology",
                        "kind": "cad_text",
                        "source_locator": item["locator"],
                        "x": item["x"],
                        "y": item["y"],
                        "scope_note": "The explicit tap-changer label request applies to the referenced definition and all its placements. No device operating mode or geometry is changed."
                        if item["block_path"]
                        else "Existing named label only; geometry unchanged.",
                    }
                )
                comment["change_ids"].append(identifier)
            if comment["change_ids"]:
                # This annotation also asks for symbols and CT details. Applying
                # its naming clause must not close the other clauses.
                other = re.sub(requested.re, "", text).strip(" \r\n.;:")
                comment["status"] = "needs_information" if other else "changed"
                comment["explanation"] = (
                    "DETC labels are being updated in native CAD. Other requests in this annotation still need symbol/location and CT-identity verification."
                    if other
                    else "The explicit naming request is applied to existing native text entities and independently reread."
                )
        elif re.fullmatch(
            r"give\s+(?:the\s+)?value\s+in\s+feet[.!]?", text, re.IGNORECASE
        ):
            anchors = [
                t for t in placed if plain(t["text"]).casefold() == "operating altitude"
            ]
            values = [
                t
                for a in anchors
                for t in placed
                if 0 < t["x"] - a["x"] < 200
                and abs(t["y"] - a["y"]) < 2
                and re.search(r"\b(?:FT|FEET)\b", plain(t["text"]), re.IGNORECASE)
            ]
            if len(values) == 1:
                comment.update(
                    status="already_present",
                    explanation="The supplied CAD already expresses operating altitude in feet: "
                    + plain(values[0]["text"])
                    + ". This is a documentation check, not independent site validation.",
                    evidence_handles=[values[0]["handle"]],
                )
        elif re.search(
            r"add notation.*must not be energized", text, re.IGNORECASE | re.DOTALL
        ):
            first = [
                t
                for t in placed
                if "transformer must not be energized from any source when de-energized"
                in plain(t["text"]).casefold()
            ]
            matches = [
                (a, b)
                for a in first
                for b in placed
                if plain(b["text"]).casefold() == "tap changers are operated"
                and abs(a["y"] - b["y"]) < 20
                and abs(a["x"] - b["x"]) < 200
            ]
            if len(matches) == 1:
                comment.update(
                    status="already_present",
                    explanation="The complete de-energization warning is already present in the supplied native CAD. No release or safety certification follows from this text check.",
                    evidence_handles=[t["handle"] for t in matches[0]],
                )
        if comment["status"] == "needs_information" and not comment["change_ids"]:
            words = set(re.findall(r"[a-z][a-z0-9-]{2,}", text.casefold())) - {
                "the",
                "and",
                "this",
                "should",
                "with",
                "that",
                "add",
                "from",
                "for",
                "per",
                "value",
            }
            ranked = sorted(
                placed,
                key=lambda t: (
                    -len(
                        words
                        & set(
                            re.findall(
                                r"[a-z][a-z0-9-]{2,}", plain(t["text"]).casefold()
                            )
                        )
                    )
                ),
            )
            comment["evidence_handles"] = [
                t["handle"]
                for t in ranked[:5]
                if words
                & set(re.findall(r"[a-z][a-z0-9-]{2,}", plain(t["text"]).casefold()))
            ]
            if re.search(
                r"fan|ladder|between|located|side of tank|bracket|head.space",
                text,
                re.IGNORECASE,
            ):
                comment["explanation"] = (
                    "Physical arrangement request. Existing CAD must be mapped to the component bodies, dimensions and mounting/clearance constraints before moving or removing equipment; a text change would not satisfy this comment."
                )
            elif re.search(
                r"ground|BCT|CTs|connections|amp values|tap positions",
                text,
                re.IGNORECASE,
            ):
                comment["explanation"] = (
                    "Electrical design request. Native circuit/terminal identity, winding or tap data and applicable design constraints must be verified before modifying connections or ratings. Matching labels alone do not resolve it."
                )
            elif re.search(r"weights|gallons", text, re.IGNORECASE):
                comment["explanation"] = (
                    "The governing manufacturer mass and oil-volume records and their drawing locations must be confirmed before editing these values; word matches alone do not establish a discrepancy."
                )
    numeric_edits, calculations = propose_current_corrections(comments, snapshot)
    if any(e["handle"] in used for e in numeric_edits):
        raise ValueError("Conflicting edits target the same native object")
    edits.extend(numeric_edits)
    return {
        "version": VERSION,
        "comments": comments,
        "edits": edits,
        "source_sha256": snapshot["source_sha256"],
        "cad_coverage_complete": snapshot["coverage_complete"],
        "paper_space_coverage_complete": snapshot.get("layout_coverage_complete", True),
        "calculations": calculations,
    }


def _comparison_svg(snapshot, edit, path):
    """Same-scale vector evidence from located saved CAD; explicit scope caption."""
    x, y = edit["x"], edit["y"]
    svg, _ = render_region(
        snapshot,
        edit.get("review_bbox", [x - 60, y - 90, x + 300, y + 60]),
        width=1200,
        height=560,
        highlight_handles=(edit["handle"],),
    )
    path.write_text(svg)


def _report_pdf(path, report):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    styles["Title"].alignment = 0
    styles["BodyText"].fontSize, styles["BodyText"].leading = 9, 13
    story = [
        Paragraph("rivet. / Drawing change review", styles["Title"]),
        Paragraph(escape(report["summary"]), styles["BodyText"]),
        Spacer(1, 14),
    ]
    source_names = list(
        dict.fromkeys(c.get("source_name", "Document") for c in report["comments"])
    )
    for i, name in enumerate(source_names, 1):
        story.extend(
            [
                Paragraph(f"Source {i}: " + escape(name), styles["BodyText"]),
                Spacer(1, 4),
            ]
        )
    story.append(Spacer(1, 8))
    if report.get("calculation_audits"):
        story.append(
            Paragraph("Drawing arithmetic - engineer review", styles["Heading2"])
        )
        for audit in report["calculation_audits"]:
            if audit.get("reason"):
                story.append(Paragraph(escape(audit["reason"]), styles["BodyText"]))
            for cell in audit.get("calculations", []):
                statement = f"{cell['rating']['text']} / {cell['voltage']['text']} V: {cell['before']} A to {cell['replacement']} A. Single phase; nearest whole ampere."
                story.append(Paragraph(escape(statement), styles["BodyText"]))
            for blocked in audit.get("blocked_columns", []):
                story.append(Paragraph(escape(blocked["detail"]), styles["BodyText"]))
        story.append(Spacer(1, 10))
    if report.get("revision_checks"):
        story.append(Paragraph("Uploaded drawing recheck", styles["Heading2"]))
        for check in report["revision_checks"]:
            result = (
                "verified at prior placement"
                if check["status"] == "verified"
                else "needs engineer review"
            )
            story.append(
                Paragraph(
                    escape(f"{check['label']} · {check['expected']} · {result}"),
                    styles["BodyText"],
                )
            )
        story.append(Spacer(1, 10))
    for issue in report["remaining_issues"]:
        story.extend([Paragraph(escape(issue), styles["BodyText"]), Spacer(1, 5)])
    story += [Spacer(1, 12), Paragraph("Every supplied comment", styles["Heading2"])]
    rows = [["Source", "Customer request", "Result"]]
    for c in report["comments"]:
        rows.append(
            [
                Paragraph(
                    f"Source {source_names.index(c.get('source_name', 'Document')) + 1}<br/>p. {c['page']}",
                    styles["BodyText"],
                ),
                Paragraph(escape(c["text"]).replace("\n", "<br/>"), styles["BodyText"]),
                Paragraph(
                    escape(c["status"].replace("_", " ") + ". " + c["explanation"])
                    .replace("＜", "&lt;")
                    .replace("＞", "&gt;"),
                    styles["BodyText"],
                ),
            ]
        )
    table = Table(rows, colWidths=[48, 228, 232], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eceef1")),
                ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9dce0")),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(table)

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.drawString(
            44, 24, "PROPOSED CHANGES / ENGINEER REVIEW REQUIRED / NOT RELEASED"
        )
        canvas.drawRightString(568, 24, str(doc.page))

    SimpleDocTemplate(
        str(path),
        leftMargin=44,
        rightMargin=44,
        topMargin=36,
        bottomMargin=45,
        title="Rivet drawing change review",
    ).build(story, onFirstPage=footer, onLaterPages=footer)


def _recheck_prior_changes(plan, snapshot, prior, previous_snapshot):
    checks = []
    placed = {}
    for item in snapshot["placed_text"]:
        placed.setdefault(item["locator"], []).append(item)
    previous_placements = {}
    prior_hash = (prior.get("verification") or {}).get("output_sha256")
    if previous_snapshot and previous_snapshot["source_sha256"] != prior_hash:
        previous_snapshot = None
    if previous_snapshot:
        for item in previous_snapshot["placed_text"]:
            previous_placements.setdefault(item["handle"], []).append(item)
    previous = prior.get("saved_drawing_check", {})
    for change in prior.get("changes", []):
        expected = change["after"]
        baseline = [
            p["saved"]
            for p in previous.get("placements", [])
            if p["edit_id"] == change["id"] and p["verified"]
        ]
        if not baseline and previous_snapshot:
            source_handle = change["source_locator"].split("/")[-1]
            candidates = previous_placements.get(source_handle, [])
            if any(
                p["locator"] == change["source_locator"] for p in candidates
            ) and all(plain(p["text"]) == expected for p in candidates):
                baseline = [
                    anchor(p, previous_snapshot["source_sha256"]) for p in candidates
                ]
        locators = [p["locator"] for p in baseline] or [change["source_locator"]]
        found = [p for locator in locators for p in placed.get(locator, [])]
        evidence = [anchor(p, snapshot["source_sha256"]) for p in found]
        anchored = (
            bool(baseline)
            and len(found) == len(baseline)
            and all(
                {k: v for k, v in a.items() if k != "source_sha256"}
                == {k: v for k, v in b.items() if k != "source_sha256"}
                for a, b in zip(baseline, evidence)
            )
        )
        if anchored:
            handle = baseline[0]["handle"]
            anchored = {
                p["locator"] for p in snapshot["placed_text"] if p["handle"] == handle
            } == set(locators)
        verified = (
            change.get("verified")
            and anchored
            and all(plain(p["text"]) == expected for p in found)
            and change.get("review", {}).get("decision") != "needs_changes"
        )
        checks.append(
            {
                "change_id": change["id"],
                "comment_id": change["comment_id"],
                "label": change["label"],
                "expected": expected,
                "status": "verified" if verified else "needs_review",
                "evidence": evidence,
            }
        )
    for comment in plan["comments"]:
        if comment["change_ids"]:
            comment.update(
                status="needs_information",
                explanation="The revised drawing still has a detected difference. This check recorded the difference and retained the engineer's DXF as uploaded.",
                change_ids=[],
            )
            continue
        relevant = [check for check in checks if check["comment_id"] == comment["id"]]
        if (
            relevant
            and all(check["status"] == "verified" for check in relevant)
            and re.fullmatch(
                r'mark\s+tap\s+changers?\s+as\s+["“\']DETC["”\'][.!]?',
                comment["text"].strip(),
                re.IGNORECASE,
            )
        ):
            comment.update(
                status="already_present",
                explanation="The requested text was read at each verified native placement in the engineer's revised DXF.",
                evidence_handles=list(
                    dict.fromkeys(
                        e["handle"] for check in relevant for e in check["evidence"]
                    )
                ),
            )
    return checks


def build_package(
    sources,
    cad_source,
    output,
    *,
    snapshot=None,
    revised_from=None,
    previous_snapshot=None,
):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    if file_hash(cad_source["path"]) != cad_source["sha256"]:
        raise ValueError("Native source changed")
    snapshot = snapshot or snapshot_dxf(cad_source["path"])
    if snapshot["source_sha256"] != cad_source["sha256"]:
        raise ValueError("CAD snapshot is not bound to this original")
    comments = comments_from_pdfs(sources)
    graphics = graphical_annotations(sources)
    if not comments:
        raise ValueError(
            "No native PDF reviewer comments were found; retain the marked originals for visual reading"
        )
    plan = propose(comments, snapshot)
    revision_checks = (
        _recheck_prior_changes(plan, snapshot, revised_from, previous_snapshot)
        if revised_from is not None
        else None
    )
    edits = plan["edits"] if revised_from is None else []
    # Snapshot text is normalized Unicode; the patch precondition is the EXACT
    # raw native text, including legacy Unicode escapes and format controls.
    raw = (
        {
            r["handle"]: r
            for r in inspect_text_records(
                cad_source["path"], [e["handle"] for e in edits]
            )
        }
        if edits
        else {}
    )
    for edit in edits:
        native = raw[edit["handle"]]
        before = native["text"]
        if edit["kind"] == "cad_calculation":
            if before != plain(edit["expected_text"]):
                raise ValueError(
                    "Formatted or changed numeric CAD cell needs an explicit native mapping"
                )
            edit["expected_text"] = before
            continue
        edit["expected_text"] = before
        edit["replacement_text"] = (
            re.sub("No-load tap changer", "DETC", before, flags=re.IGNORECASE)
            if "No-load tap changer" in plain(before)
            else before.replace("NLTC", "DETC")
        )
    verification = (
        patch_dxf(
            cad_source["path"],
            root / "Proposed-drawing.dxf",
            edits,
            cad_source["sha256"],
        )
        if edits
        else None
    )
    # A separate parser rereads the actual saved drawing. Neither the preview
    # nor the placement audit is built by substituting the intended values.
    saved = snapshot_dxf(root / "Proposed-drawing.dxf") if edits else snapshot
    recheck = verify_saved_drawing(snapshot, saved, edits)
    calculation_check = verify_saved_calculations(saved, plan["calculations"])
    if not calculation_check["passed"] and revised_from is None:
        (root / "failed-arithmetic-check.json").write_text(
            json.dumps(calculation_check, indent=2)
        )
        raise ValueError("Independent saved arithmetic check failed")
    if not recheck["passed"]:
        (root / "failed-independent-check.json").write_text(
            json.dumps(recheck, indent=2)
        )
        raise ValueError(
            "Independent saved drawing check failed; output retained for inspection"
        )
    if edits and saved["source_sha256"] != verification["output_sha256"]:
        raise ValueError("Saved drawing changed after patch verification")
    if verification:
        verification["independent_saved_check"] = recheck
    evidence = equipment_evidence(saved)
    (root / "Equipment-evidence.json").write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False)
    )
    with (root / "Drawing-component-schedule.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "table",
                "row",
                "column",
                "printed_header",
                "value",
                "status",
                "source_sha256",
                "native_locators",
            ]
        )
        for table in evidence["schedules"]:
            for row in table["rows"]:
                for column in table["columns"]:
                    cell = row["cells"][column["field"]]
                    writer.writerow(
                        [
                            _csv_cell(v)
                            for v in [
                                table["id"],
                                row["row"],
                                column["field"],
                                column["header"]["value"],
                                cell["value"],
                                cell["status"],
                                evidence["source_sha256"],
                                ";".join(e["locator"] for e in cell["evidence"]),
                            ]
                        ]
                    )
    changes = []
    for edit in edits:
        placements = [p for p in recheck["placements"] if p["edit_id"] == edit["id"]]
        comparisons = []
        for index, placement in enumerate(placements):
            suffix = "" if index == 0 else f"-placement-{index + 1}"
            name = edit["id"] + suffix
            actual = {
                **edit,
                "x": placement["source"]["point"][0],
                "y": placement["source"]["point"][1],
            }
            _comparison_svg(snapshot, actual, root / f"{name}-before.svg")
            _comparison_svg(saved, actual, root / f"{name}-after.svg")
            comparisons.append(
                {
                    "locator": placement["locator"],
                    "before_name": f"{name}-before.svg",
                    "after_name": f"{name}-after.svg",
                }
            )
        changes.append(
            {
                k: edit[k]
                for k in (
                    "id",
                    "comment_id",
                    "label",
                    "kind",
                    "source_locator",
                    "scope_note",
                )
            }
            | {
                "before": plain(edit["expected_text"]),
                "after": plain(edit["replacement_text"]),
                "verified": bool(
                    verification
                    and verification["preservation_verified"]
                    and recheck["passed"]
                ),
                "affected_placements": comparisons,
                "comparison": {
                    "before_name": f"{edit['id']}-before.svg",
                    "after_name": f"{edit['id']}-after.svg",
                },
                **(
                    {"calculation": edit["calculation"]}
                    if "calculation" in edit
                    else {}
                ),
            }
        )
    counts = Counter(c["status"] for c in plan["comments"])
    issues = []
    if revision_checks is not None and any(
        check["status"] != "verified" for check in revision_checks
    ):
        issues.append(
            "Some previously proposed values could not be verified at their saved native locations in the uploaded revision."
        )
    if not snapshot.get("layout_coverage_complete", True):
        issues.append(
            "One or more layouts contain unsupported or unlocated CAD entities. The saved DXF preserves the original bytes outside approved edits, but the review excerpts do not represent a complete multi-layout drawing."
        )
    if plan["calculations"]:
        issues.append(
            "Drawing arithmetic uses printed source values only. Rating selection, winding design and protection remain subject to engineer approval."
        )
        for audit in plan["calculations"]:
            for blocked in audit.get("blocked_columns", []):
                issues.append(blocked["detail"])
        if revised_from is not None and not calculation_check["passed"]:
            issues.append(
                "Some current cells in the uploaded revision differ from the independently recomputed values."
            )
    if graphics:
        issues.append(
            f"{len(graphics)} graphical annotations without text are retained for visual review; they are not counted as answered comments."
        )
    if counts["needs_information"]:
        issues.append(
            f"{counts['needs_information']} of {len(comments)} comments still require verified engineering inputs or unsupported geometry/circuit work. They are not marked completed."
        )
    if not snapshot["coverage_complete"] and counts["needs_information"]:
        issues.append(
            "Native CAD extraction is partial. The full output DXF preserves all original geometry. Browser excerpts show supported lines, sampled curves and located text with fallback fonts; omitted CAD types remain unverified."
        )
    if not any(s.get("role") == "bom" for s in sources) and counts["needs_information"]:
        issues.append(
            "No manufacturer BOM was supplied. A drawing-change register is included; it is not a corrected production BOM."
        )
    report = {
        "schema": VERSION,
        "comments": plan["comments"],
        "unread_graphical_annotations": graphics,
        "changes": changes,
        "totals": {
            "comments": len(comments),
            "changed": counts["changed"],
            "already_present": counts["already_present"],
            "needs_information": counts["needs_information"],
        },
        "summary": f"Prepared {len(changes)} saved drawing changes ({sum(e['kind'] == 'cad_calculation' for e in edits)} numerical corrections). {counts['already_present']} requests are already documented in the supplied CAD; {counts['needs_information']} remain open. No physical redesign or production release is approved.",
        "remaining_issues": issues,
        "approval_eligible": not issues,
        "status": "needs_information" if issues else "ready_for_review",
        "verification": verification,
        "saved_drawing_check": recheck,
        "cad_model_space_coverage_complete": snapshot["coverage_complete"],
        "cad_layout_coverage_complete": snapshot.get("layout_coverage_complete", True),
        "calculation_audits": plan["calculations"],
        "saved_calculation_check": calculation_check,
        "equipment_evidence": {
            "counts": evidence["counts"],
            "artifact": "Equipment-evidence.json",
            "production_bom_verified": False,
            "electrical_connectivity_verified": False,
        },
        "source_manifest": [
            {k: s[k] for k in ("id", "name", "sha256", "role")}
            for s in [*sources, cad_source]
        ],
        "engineering_approved": False,
        "production_released": False,
    }
    if revision_checks is not None:
        report["revision_checks"] = revision_checks
        report["summary"] = (
            f"Rechecked the engineer's uploaded drawing. {counts['already_present']} requests are documented in the DXF and {counts['needs_information']} remain open."
        )
    # Recheck all originals at the end, including PDFs, before publishing a
    # review package that claims to be bound to their hashes.
    if any(file_hash(s["path"]) != s["sha256"] for s in [*sources, cad_source]):
        raise ValueError("A source changed while the review package was prepared")
    with (root / "Drawing-change-register.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "comment_id",
                "page",
                "request",
                "status",
                "response",
                "native_change_ids",
            ],
        )
        writer.writeheader()
        for c in report["comments"]:
            writer.writerow(
                {
                    key: _csv_cell(value)
                    for key, value in {
                        "comment_id": c["id"],
                        "page": c["page"],
                        "request": c["text"],
                        "status": c["status"],
                        "response": c["explanation"],
                        "native_change_ids": ";".join(c["change_ids"]),
                    }.items()
                }
            )
    (root / "verification.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    _report_pdf(root / "Drawing-change-review.pdf", report)
    with zipfile.ZipFile(
        root / "Drawing-review-package.zip", "x", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for path in sorted(root.iterdir()):
            if path.suffix != ".zip":
                archive.write(path, path.name)
    report["artifacts"] = [
        {
            "name": p.name,
            "sha256": file_hash(p),
            "bytes": p.stat().st_size,
            "role": "drawing"
            if p.suffix == ".dxf"
            else "comparison"
            if p.suffix == ".svg"
            else "review_package"
            if p.suffix == ".zip"
            else "review",
        }
        for p in root.iterdir()
        if p.is_file()
    ]
    for path in root.iterdir():
        path.chmod(0o600)
    return report
