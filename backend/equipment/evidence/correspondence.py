"""Source-bound correspondence between readings of one printed schedule.

An exact printed circuit identifier can relate a vision transcript to a ruled
source cell. It does not identify a device across drawings, lineups or jobs.
Ratings, reader row ordinals and model confidence never establish identity.
Original claims remain immutable; proven links append sourced projections.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from copy import deepcopy

from .schema import make_claim, source_ref, stable_id
from .table_reader import normalize_header

VERSION = "rivet.source-local-correspondence.v2"
POLICY = {
    "version": VERSION,
    "minimum_identifier_ocr_confidence": 75.0,
    "minimum_header_ocr_confidence": 75.0,
    "minimum_matching_typed_headers": 3,
    "identity_basis": "unique exact printed circuit within one hash-bound page and ruled table",
    "rating_values_used_for_identity": False,
    "row_ordinals_used_for_identity": False,
    "cross_drawing_identity_verified": False,
    "engineering_approval_granted": False,
}


def _box(value):
    return bool(isinstance(value, (list, tuple)) and len(value) == 4
                and all(isinstance(v, (float, int)) and 0 <= v <= 1 for v in value)
                and value[0] < value[2] and value[1] < value[3])


def _inside(inner, outer, tolerance=0.001):
    return bool(_box(inner) and _box(outer)
                and inner[0] >= outer[0] - tolerance and inner[1] >= outer[1] - tolerance
                and inner[2] <= outer[2] + tolerance and inner[3] <= outer[3] + tolerance)


def _confidence(value):
    return float(value) if isinstance(value, (float, int)) and not isinstance(value, bool) else None


def _literal(text):
    """Whitespace normalization only; 08 and 8 remain different identifiers."""
    text = re.sub(r"\s+", " ", str("" if text is None else text)).strip().upper()
    return text if re.fullmatch(r"[A-Z0-9]+(?:[-./ ][A-Z0-9]+)*", text) else None


def _field(cell):
    return normalize_header(cell.get("header") or "")


def _row_cells(row, feature):
    return [c for c in row.get("cells", []) if _field(c) == feature]


def _visual_identifier(row, feature):
    cells = _row_cells(row, feature)
    if len(cells) != 1:
        return None, None, ["missing_or_duplicate_visual_identifier_cell"]
    cell = cells[0]
    literal = _literal(cell.get("raw_text", cell.get("text")))
    problems = []
    if not literal:
        problems.append("identifier_not_printed_unambiguously")
    if cell.get("confidence") != "high" or cell.get("value_confidence", "high") != "high":
        problems.append("visual_identifier_requires_verification")
    value = cell.get("value", row.get("fields", {}).get(feature))
    if value is not None and _literal(value) != literal:
        problems.append("visual_literal_normalization_disagrees")
    return literal, cell, problems


def _source_identifier(row, feature, table):
    cells = [c for c in row.get("cells", []) if c.get("field") == feature
             or _field(c) == feature]
    headers = [h for h in table.get("headers", []) if h.get("field") == feature
               or normalize_header(h.get("raw_text", "")) == feature]
    if len(cells) != 1 or len(headers) != 1:
        return None, None, None, [], ["missing_or_duplicate_source_identifier_cell_or_header"]
    cell, header = cells[0], headers[0]
    literal = _literal(cell.get("raw_text"))
    problems = []
    if literal is None:
        problems.append("source_identifier_not_printed_unambiguously")
    if not cell.get("geometry_valid") or cell.get("row_span", 1) != 1:
        problems.append("identifier_cell_geometry_not_unique")
    if not _inside(cell.get("bbox"), table.get("bbox")) or not _inside(header.get("bbox"), table.get("bbox")):
        problems.append("identifier_cell_or_header_outside_ruled_table")
    if not _inside(cell.get("bbox"), row.get("bbox")):
        problems.append("identifier_cell_outside_source_row_geometry")
    axis = table.get("_column_axis")
    if axis is None or not _box(cell.get("bbox")) or not _box(header.get("bbox")):
        problems.append("source_column_orientation_not_established")
    else:
        a, b = cell["bbox"], header["bbox"]
        overlap = max(0, min(a[axis + 2], b[axis + 2]) - max(a[axis], b[axis]))
        if overlap / min(a[axis + 2] - a[axis], b[axis + 2] - b[axis]) < 0.6:
            problems.append("identifier_cell_not_aligned_with_printed_header_column")
    header_confidence = _confidence(header.get("ocr_confidence"))
    if header_confidence is None or header_confidence < POLICY["minimum_header_ocr_confidence"]:
        problems.append("source_identifier_header_requires_verification")
    # Require an actual OCR word at that source cell, not a row-wide model box.
    words = [w for w in cell.get("word_bboxes", []) if _literal(w.get("text")) == literal]
    if len(words) != 1 or not _inside(words[0].get("bbox"), cell.get("bbox")):
        problems.append("identifier_word_not_uniquely_located_in_cell")
    else:
        confidence = _confidence(words[0].get("confidence"))
        if confidence is None or confidence < POLICY["minimum_identifier_ocr_confidence"]:
            problems.append("source_identifier_word_requires_verification")
    return literal, cell, header, words, problems


def _ancestry(ref, context):
    page = ref["page"]
    if context.get("original_sha256"):
        return stable_id("original-page-ancestry", context["original_sha256"],
                         context.get("page_map", {}).get(str(page), page), None, None)
    return stable_id("source-page-ancestry", ref["sha256"], page, None, None)


def match_source_local_rows(predictions, table_reports, *, scope_id, source_context=None):
    """Return cited proven/candidate links without changing a reader's output.

    Multiple same-schema tables or duplicate circuit labels remain candidates.
    This API consumes source observations only; there is no answer-key input.
    """
    if not scope_id:
        raise ValueError("An explicit order/package scope is required")
    contexts = source_context or {}
    predictions = predictions if isinstance(predictions, list) else [predictions]
    visual_rows, groups = [], defaultdict(list)
    for prediction in predictions:
        hashes = prediction.get("source_hashes", {})
        for original in prediction.get("observed_bom_rows") or prediction.get("bom_rows", []):
            row = deepcopy(original)
            row["sha256"] = row.get("sha256") or hashes.get(row.get("source_id"))
            if not row.get("id") or not row.get("source_id") or not row.get("sha256") or not row.get("page"):
                raise ValueError("Correspondence rows need hash-bound source and local identity")
            visual_rows.append(row)
            groups[(row["source_id"], row["sha256"], row["page"], str(row.get("table")))].append(row)
    reports = table_reports if isinstance(table_reports, list) else [table_reports]
    links, unmatched = [], []
    for (sid, digest, page, _), rows in groups.items():
        matching_reports = [r for r in reports if r.get("source_id") == sid and r.get("sha256") == digest]
        tables = [(r, {**t, "_column_axis": (0 if p.get("orientation_clockwise") in {0, 180} else 1)
                            if p.get("orientation_established") is True else None})
                  for r in matching_reports for p in r.get("pages", []) if p.get("page") == page
                  for t in p.get("tables", [])]
        visual_headers = {_field(c) for row in rows for c in row.get("cells", [])} - {None}
        eligible = []
        for report, table in tables:
            source_headers = {h.get("field") or normalize_header(h.get("raw_text", ""))
                              for h in table.get("headers", [])
                              if (_confidence(h.get("ocr_confidence")) or 0) >= POLICY["minimum_header_ocr_confidence"]}
            common = visual_headers & source_headers
            if "circuit_no" in common and len(common) >= POLICY["minimum_matching_typed_headers"]:
                eligible.append((report, table, sorted(common)))
        vision_literals = [_visual_identifier(row, "circuit_no")[0] for row in rows]
        vision_counts = Counter(v for v in vision_literals if v)
        for row in rows:
            circuit, vcell, problems = _visual_identifier(row, "circuit_no")
            if not circuit:
                unmatched.append({"source_id": sid, "page": page, "visual_local_id": row["id"],
                                  "reason": "no_unambiguous_printed_circuit_identifier"})
                continue
            candidates = []
            for report, table, common in eligible:
                table_rows = [r for r in report.get("rows", []) if r.get("page") == page and r.get("table") == table.get("table")]
                source_literals = [_source_identifier(r, "circuit_no", table)[0] for r in table_rows]
                source_counts = Counter(v for v in source_literals if v)
                for source_row in table_rows:
                    literal, cell, header, words, source_problems = _source_identifier(source_row, "circuit_no", table)
                    if literal != circuit:
                        continue
                    reasons = problems + source_problems
                    if len(eligible) != 1:
                        reasons.append("multiple_matching_ruled_tables")
                    if vision_counts[circuit] != 1 or source_counts[circuit] != 1:
                        reasons.append("duplicate_circuit_literal")
                    # A separately printed, readable section disagreement blocks a link.
                    vsection, _, vsection_problems = _visual_identifier(row, "section_no")
                    ssection, _, _, _, ssection_problems = _source_identifier(source_row, "section_no", table)
                    if vsection and ssection and vsection != ssection:
                        reasons.append("printed_section_disagrees")
                    refbase = {"source_id": sid, "sha256": digest, "page": page, "table": table.get("table")}
                    refs = [source_ref(refbase, bbox=cell.get("bbox"), row=source_row.get("grid_row"),
                                       cell=cell.get("grid_column"), text=cell.get("raw_text")),
                            source_ref(refbase, bbox=header.get("bbox"), text=header.get("raw_text"))]
                    refs.extend(source_ref(refbase, bbox=w.get("bbox"), text=w.get("text")) for w in words if _box(w.get("bbox")))
                    visual_ref = source_ref({"source_id": sid, "sha256": digest}, page=page, entity=row["id"],
                                            bbox=vcell.get("bbox") if _box(vcell.get("bbox")) else None,
                                            text=vcell.get("raw_text", vcell.get("text")))
                    status = "candidate" if reasons else "proven_source_local"
                    link = {
                        "version": VERSION, "scope_id": scope_id, "source_id": sid, "sha256": digest, "page": page,
                        "visual_local_id": row["id"], "table_local_id": source_row.get("source_local_id"),
                        "table_subject_id": source_row["subject_id"], "circuit_literal": circuit,
                        "section_literal": ssection if not ssection_problems and not vsection_problems else None,
                        "status": status, "verified": status == "proven_source_local",
                        "physical_identity_verified": False, "engineering_approved": False,
                        "source_refs": refs, "visual_source_ref": visual_ref,
                        "independence_group": _ancestry(refbase, contexts.get(sid, {})),
                        "basis": {"matching_typed_headers": common, "exact_printed_circuit": circuit,
                                  "circuit_occurrences_in_source_table": source_counts[circuit],
                                  "circuit_occurrences_in_visual_table": vision_counts[circuit],
                                  "matching_ruled_table_count": len(eligible),
                                  "geometry_basis": "OCR identifier word enclosed by ruled source cell; header and cell inside table",
                                  "visual_geometry_used_as_precise_cell": False,
                                  "ratings_or_row_ordinals_used": False},
                        "unresolved": sorted(set(reasons)),
                    }
                    link["id"] = stable_id("source-local-correspondence", link)
                    candidates.append(link)
            links.extend(candidates)
            if not candidates:
                unmatched.append({"source_id": sid, "page": page, "visual_local_id": row["id"],
                                  "circuit_literal": circuit, "reason": "no_matching_cited_source_identifier"})
    return {
        "version": VERSION, "scope_id": scope_id, "policy": deepcopy(POLICY), "links": links,
        "unmatched_visual_rows": unmatched,
        "counts": {"visual_rows": len(visual_rows), "proven_source_local": sum(l["verified"] for l in links),
                   "candidate_links": sum(not l["verified"] for l in links), "unmatched_visual_rows": len(unmatched)},
        "engineering_approved": False,
    }


def append_correspondence_claims(graph, correspondence):
    """Append projections from proven local links; retain all original evidence.

    A page-local circuit match never creates an alias usable across revisions.
    Projections carry original claim ancestry, side and lifecycle unchanged.
    """
    if correspondence.get("scope_id") != graph["scope_id"]:
        raise ValueError("Correspondence belongs to a different order/package")
    result = deepcopy(graph)
    result.setdefault("identity_correspondence", [])
    existing_links = {l["id"]: l for l in result["identity_correspondence"]}
    original_claims = list(graph.get("claims", []))
    known_claims = {c["id"]: c for c in original_claims}
    nodes = result.get("nodes", [])
    for link in correspondence.get("links", []):
        if link["scope_id"] != graph["scope_id"]:
            raise ValueError("A correspondence link differs from its scope")
        if link["id"] in existing_links and existing_links[link["id"]] != link:
            raise ValueError("A source-local correspondence cannot be overwritten")
        if link["id"] not in existing_links:
            result["identity_correspondence"].append(deepcopy(link))
            existing_links[link["id"]] = link
        if not link.get("verified") or link.get("status") != "proven_source_local":
            continue
        if link.get("physical_identity_verified"):
            raise ValueError("Source-local correspondence cannot assert a physical device alias")
        vision = [n for n in nodes if n.get("metadata", {}).get("source_id") == link["source_id"]
                  and n.get("metadata", {}).get("source_local_id") == str(link["visual_local_id"])]
        tables = [n for n in nodes if n.get("metadata", {}).get("source_id") == link["source_id"]
                  and n.get("metadata", {}).get("source_local_id") == link["table_local_id"]]
        if len(vision) != 1 or len(tables) != 1:
            raise ValueError("Proven correspondence does not have unique retained source-local subjects")
        for original in original_claims:
            if original["subject_id"] != tables[0]["id"]:
                continue
            ref = original["source"]
            if ref["sha256"] != link["sha256"] or ref.get("page") != link["page"] or ref["source_id"] != link["source_id"]:
                raise ValueError("Projected claim escaped the matched original source page")
            if original.get("independence_group") != link["independence_group"]:
                raise ValueError("Correspondence cannot change original page ancestry")
            projected = make_claim(
                vision[0]["id"], original["feature"], original.get("value"), source=ref,
                extractor={**original["extractor"], "name": "source_local_correspondence_projection", "version": VERSION},
                raw_value=original.get("raw_value"), raw_confidence=original.get("raw_confidence"),
                unit=original.get("unit"), state=original["state"], normalization=original.get("normalization"),
                conditions={**original.get("conditions", {}), "source_local_correspondence_id": link["id"],
                            "physical_identity_verified": False, "candidate_only": True},
                independence_group=original["independence_group"], derived_from=[original["id"]],
            )
            if projected["id"] not in known_claims:
                result["claims"].append(projected)
                known_claims[projected["id"]] = projected
    if len(result["claims"]) != len(graph.get("claims", [])):
        for key in ("resolutions", "reconciliation", "rule_checks", "catalog_lookups", "joint_constraints", "constraint_rules"):
            result.pop(key, None)
        result["metadata"]["requires_reconciliation"] = True
    result["metadata"]["source_local_correspondence"] = {
        "version": VERSION, "counts": deepcopy(correspondence.get("counts", {})),
        "unmatched_visual_rows": deepcopy(correspondence.get("unmatched_visual_rows", [])),
        "physical_identity_verified": False,
    }
    return result
