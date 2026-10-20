"""Independent, bounded reconstruction scoring with explicit source-role gates.

The optional run reads original CUSTOMER documents before opening the answer
key. No labels, manufacturer reference or generated values enter extraction.
An input-only case is useful but never becomes an approved reconstruction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path


VERSION = "rivet.switchboard-reconstruction-score.v1"
PROJECTION_VERSION = "source-bound-observation-inventory-v6-physical-scope"
CUSTOMER_ROLES = {"specification", "customer_one_line", "addendum", "quote_exception", "purchase_order"}
KEY_ALIASES = {"enclosure": "enclosure_type", "section_barriers": "inter_section_barriers"}


def _read(path):
    return json.loads(Path(path).read_text())


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _path(base, value):
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(data, indent=2, allow_nan=False) + "\n").encode()
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _equal(actual, expected):
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isfinite(actual) and math.isfinite(expected) and math.isclose(actual, expected, abs_tol=1e-8)
    return actual == expected


def _feature_key(key):
    return KEY_ALIASES.get(key, key)


def _canonical_value(key, value):
    if key == "enclosure_type" and isinstance(value, str):
        # An enclosure designation spelling, not a certification or rating
        # inference. No numerical, device-identity or placement coercions.
        import re
        match = re.fullmatch(r"(?:NEMA\s*|Type\s*)?(1|3R|3RX|4|4X|12)", value.strip(), re.I)
        if match:
            return match[1].upper()
    return value


def _summary(rows):
    result = {"required": len(rows), "match": 0, "differ": 0, "missing": 0}
    for row in rows:
        result[row["status"]] += 1
    result["accuracy"] = result["match"] / len(rows) if rows else None
    return result


def _source_pages(refs):
    """Original-file/page anchors only; filenames and values are not identity."""
    return {(ref["sha256"], ref["page"]) for ref in refs or []
        if isinstance(ref, dict) and isinstance(ref.get("sha256"), str)
        and len(ref["sha256"]) == 64 and isinstance(ref.get("page"), int)
        and not isinstance(ref["page"], bool) and ref["page"] > 0}


def _literal_candidates(expected, rows, *, identity_key):
    """A repeated printed label never picks a winner using expected ratings."""
    literal = str(expected.get("source_identifier", expected.get("subject_id", expected.get("id", "order"))))
    anchored = _source_pages(expected.get("source_refs", []))
    display_rows = [row for row in rows if row.get("display_tag") is not None
        and str(row["display_tag"]) == literal]
    if display_rows:
        candidates = [row for row in display_rows if anchored
            and anchored & _source_pages(row.get("source_refs", []))
            and _inside_source_scope(row, expected.get("source_refs", []))]
        identities = {str(row.get(identity_key, "")) for row in candidates}
        if len(identities) != 1:
            return [], sorted(identities), candidates
        identity = next(iter(identities))
        return [row for row in rows if str(row.get(identity_key, "")) == identity
            and anchored & _source_pages(row.get("source_refs", []))
            and _inside_source_scope(row, expected.get("source_refs", []))], [], []
    # Preserve historical exact-ID runs. New source-local IDs are opaque and
    # cannot reach this path through a matching printed circuit numeral.
    exact_id = str(expected.get("subject_id", expected.get("id", "order")))
    return [row for row in rows if str(row.get(identity_key, "order")) == exact_id
        and (not anchored or anchored & _source_pages(row.get("source_refs", [])))
        and _inside_source_scope(row, expected.get("source_refs", []))], [], []


def _valid_box(box):
    return isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int,float)) and not isinstance(v,bool) and math.isfinite(v) and 0 <= v <= 1 for v in box) and box[0] < box[2] and box[1] < box[3]


def _inside_source_scope(row, expected_refs):
    """Match a pre-labelled physical region, never a numerical expected value.

    Ordinary keys with no declared scope retain the historical page-only rule.
    A declared invalid scope, missing actual location or wrong original fails
    closed. A small fixed tolerance accounts for a crop's border ink margin.
    """
    scoped = [r for r in expected_refs if "source_scope_bbox" in r]
    if not scoped:
        return True
    for expected in scoped:
        box = expected["source_scope_bbox"]
        if not _valid_box(box):
            continue
        for actual in row.get("source_refs", []):
            loc = actual.get("bbox")
            if actual.get("sha256") != expected.get("sha256") or actual.get("page") != expected.get("page") or not _valid_box(loc):
                continue
            if box[0]-.002 <= loc[0] and box[1]-.002 <= loc[1] and loc[2] <= box[2]+.002 and loc[3] <= box[3]+.002:
                return True
    return False


def _identity_index(requirements):
    result = {}
    for item in requirements.get("items", []):
        subject = str(item.get("subject_id", "order"))
        if subject == "order" and item.get("key", "").startswith("main."):
            subject = "main"
        if subject == "order":
            continue
        entry = result.setdefault(subject, {"tags": set(), "source_refs": []})
        if item.get("display_tag") is not None:
            entry["tags"].add(str(item["display_tag"]))
        entry["source_refs"].extend(item.get("source_refs", []))
    result = {subject: {"source_refs": row["source_refs"],
        **({"display_tag": next(iter(row["tags"]))}
            if len(row["tags"]) == 1 and len(_source_pages(row["source_refs"])) == 1 else {})}
        for subject, row in result.items()}
    return result


def _with_requirement_identities(design, requirements):
    index = _identity_index(requirements)
    devices = []
    for device in design.get("devices", []):
        identity = index.get(str(device.get("id")))
        # A generated object's own display label is insufficient provenance.
        # Exact source-local ID lineage back to requirements is necessary.
        row = {**device}
        row.pop("display_tag", None)
        if identity:
            row.update(identity)
        devices.append(row)
    return {**design, "devices": devices}


def _raw_drawing_inventory(prediction):
    """Source observation recall, including positions without numeric values.

    These records never enter configuration. No physical identity is inferred
    from a model object name, a rating, or an ordinal row position.
    """
    devices = []
    for report in prediction.get("drawing_reports", []):
        for page in report.get("pages", []):
            for obj in page.get("reading", {}).get("objects", []):
                tag = obj.get("raw_tag") or obj.get("circuit_label")
                if not tag:
                    continue
                row = {"id": f"{report['sha256']}:p{page['page']}:{obj['key']}",
                    "display_tag": str(tag), "source_kind": obj.get("kind"),
                    "source_position_state": obj.get("position_state"),
                    "source_refs": [{"source_id": report["source_id"], "sha256": report["sha256"],
                        "page": page["page"], "bbox": obj.get("bbox"), "coordinate_system": "normalized_top_left"}]}
                values = {}
                for prop in obj.get("properties", []):
                    feature = {"pole_count": "poles", "device_frame": "frame"}.get(prop["feature"], prop["feature"])
                    value, unit = prop.get("value"), prop.get("unit")
                    # Independent explicit unit arithmetic, never a gold-value
                    # choice or a conversion between interrupting and SCCR.
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        if feature.endswith("_ka"):
                            value = value / 1000 if unit == "A" else value if unit in (None, "kA") else None
                        elif feature.endswith("_a"):
                            value = value * 1000 if unit == "kA" else value if unit in (None, "A") else None
                        elif feature == "voltage_v":
                            value = value * 1000 if unit == "kV" else value if unit in (None, "V") else None
                    values.setdefault(feature, []).append(value)
                for feature, observed in values.items():
                    row[feature] = observed[0] if observed and all(_equal(v, observed[0]) for v in observed) else None
                devices.append(row)
    return {"devices": devices}


def score_requirements(key, requirements):
    """Exact bounded feature/subject scoring; contradictory values cannot win."""
    grouped = {}
    for item in requirements.get("items", []):
        if item.get("dismissed"):
            continue
        grouped.setdefault((str(item.get("subject_id", "order")), _feature_key(item.get("key"))), []).append(item)
    rows, identity_ambiguities, consumed, ambiguous = [], [], set(), set()
    all_items = [item for item in requirements.get("items", []) if not item.get("dismissed")]
    expected_ids = set()
    expected_features = {_feature_key(item["key"]) for item in key.get("requirements", [])}
    for expected in key.get("requirements", []):
        identity = (str(expected.get("subject_id", "order")), _feature_key(expected["key"]))
        expected_ids.add(identity)
        owner_candidates, unclear, ambiguous_items = _literal_candidates(
            {**expected, "subject_id": identity[0]}, all_items, identity_key="subject_id")
        found = [item for item in owner_candidates if _feature_key(item.get("key")) == identity[1]]
        consumed.update(id(item) for item in found)
        ambiguous.update(id(item) for item in ambiguous_items)
        if unclear:
            identity_ambiguities.append({"subject_id": identity[0], "key": identity[1], "candidate_subject_ids": unclear,
                "reason": "Printed label is repeated within the same original source/page; no rating- or ordinal-based assignment."})
        values = [item.get("value") for item in found if item.get("value") is not None]
        if not values:
            status = "missing"
        elif all(_equal(_canonical_value(identity[1], value), _canonical_value(identity[1], expected["value"])) for value in values):
            status = "match"
        else:
            status = "differ"
        rows.append({"id": expected["id"], "subject_id": identity[0], "key": identity[1],
            "expected": expected["value"], "observed": values, "status": status,
            "expected_source_refs": expected.get("source_refs", []),
            "prediction_item_ids": [item.get("id") for item in found]})
    # Only in-scope expected features count here. Omitted administrative and
    # instrumentation clauses are not mislabeled as model false positives.
    unmatched = [item for identity, group in grouped.items()
        if identity[1] in expected_features for item in group
        if id(item) not in consumed and id(item) not in ambiguous and item.get("value") is not None]
    key_refs = [r for e in key.get("requirements", []) for r in e.get("source_refs", [])]
    false_alarms = [item for item in unmatched if _inside_source_scope(item, key_refs)]
    outside = [item for item in unmatched if not _inside_source_scope(item, key_refs)]
    unscored = [item for identity, group in grouped.items() if identity[1] not in expected_features for item in group]
    summary = _summary(rows)
    summary.update(false_alarms=len(false_alarms), additional_unscored_items=len(unscored))
    sources, features = {}, {}
    for row in rows:
        for source_id in {ref.get("source_id") for ref in row["expected_source_refs"] if ref.get("source_id")}:
            sources.setdefault(source_id, []).append(row)
        features.setdefault(row["key"].removeprefix("main."), []).append(row)
    return {**summary, "rows": rows, "false_alarm_items": false_alarms,
        "out_of_scope_unmatched_items": outside,
        "by_source": {sid: _summary(group) for sid, group in sorted(sources.items())},
        "by_feature": {name: _summary(group) for name, group in sorted(features.items())},
        "additional_unscored_items_detail": unscored, "identity_ambiguities": identity_ambiguities,
        "scope": "Only the independently labelled scalar requirements; no engineering-completeness claim."}


def score_inventory(inventory, design, *, complete=False):
    """No rating-, position- or row-index-based identity matching."""
    actual = {}
    duplicates = []
    for device in design.get("devices", []):
        identity = str(device.get("id", ""))
        if identity in actual:
            duplicates.append(identity)
        actual[identity] = device
    rows, features, matched_ids, ambiguities = [], [], set(), []
    for expected in inventory:
        ident = str(expected["id"])
        candidates, unclear, _ambiguous = _literal_candidates(expected,
            [row for identifier, row in actual.items() if identifier not in duplicates], identity_key="id")
        device = candidates[0] if len(candidates) == 1 else None
        if device is not None:
            matched_ids.add(str(device.get("id")))
        if unclear:
            ambiguities.append({"expected_id": ident, "candidate_subject_ids": unclear})
        feature_rows = []
        for name, value in expected.get("features", {}).items():
            predicted = device.get(name) if device else None
            status = "missing" if predicted is None else "match" if _equal(predicted, value) else "differ"
            row = {"device_id": ident, "feature": name, "expected": value, "observed": predicted, "status": status}
            feature_rows.append(row)
            features.append(row)
        status = "missing" if device is None else "match" if all(r["status"] == "match" for r in feature_rows) else "differ"
        rows.append({"id": ident, "status": status, "identity_found": device is not None,
            "features": feature_rows, "source_refs": expected.get("source_refs", [])})
    key_refs = [r for e in inventory for r in e.get("source_refs", [])]
    extras = sorted(i for i in set(actual)-matched_ids if _inside_source_scope(actual[i], key_refs)) if complete else []
    return {**_summary(rows), "identities_found": sum(r["identity_found"] for r in rows),
        "feature_accuracy": _summary(features), "extra_devices": extras,
        "duplicate_device_ids": duplicates, "rows": rows, "identity_ambiguities": ambiguities,
        "out_of_scope_devices": sorted(i for i in set(actual)-matched_ids if not _inside_source_scope(actual[i],key_refs)),
        "identity_method": "Exact ID or an unambiguous literal printed label anchored to the same original SHA256/page and pre-labelled physical source scope when supplied; no values or ordinal/oracle assignment."}


def _not_applicable(reason):
    return {"status": "not_applicable", "reason": reason}


def _extracted_inventory(requirements):
    """Inventory actually represented by source requirement items, not defaults."""
    devices = {}
    for item in requirements.get("items", []):
        if item.get("dismissed") or item.get("value") is None:
            continue
        subject, key = str(item.get("subject_id", "order")), item.get("key", "")
        if subject == "order" and key.startswith("main."):
            subject, key = "main", key.split(".", 1)[1]
        if subject == "order":
            continue
        row = devices.setdefault(subject, {"id": subject, "source_claim_ids": [], "conflicting_values": {}})
        value = item["value"]
        if key in row and not _equal(row[key], value):
            row["conflicting_values"].setdefault(key, [row[key]]).append(value)
            row[key] = None
        elif key not in row:
            row[key] = value
        row["source_claim_ids"] += item.get("source_claim_ids", [])
    return {"devices": list(devices.values())}


def _approved_score(key, design):
    ref = key.get("approved_reference")
    if not key.get("approved_reconstruction_eligible") or not ref or not ref.get("approval_source_refs"):
        return _not_applicable(key.get("ineligibility_reason", "Customer-side input and documented manufacturer approval have not both been established."))
    inventory = score_inventory(ref.get("devices", []), design, complete=ref.get("device_inventory_complete", False))
    placements = []
    devices = {str(d.get("id")): d for d in design.get("devices", [])}
    for expected in ref.get("devices", []):
        got = devices.get(str(expected["id"]), {})
        for feature, value in expected.get("placement", {}).items():
            observed = got.get(feature)
            placements.append({"device_id": str(expected["id"]), "feature": feature, "expected": value,
                "observed": observed, "status": "missing" if observed is None else "match" if _equal(observed, value) else "differ"})
    missing = sum(row["status"] == "missing" for row in inventory["rows"])
    changed_features = sum(f["status"] != "match" for r in inventory["rows"] if r["identity_found"] for f in r["features"])
    placement_changes = [row for row in placements if row["status"] != "match" and row["device_id"] in devices]
    placement_edits = len(placement_changes)
    changed_ids = {row["id"] for row in inventory["rows"] if row["status"] == "differ"}
    changed_ids.update(row["device_id"] for row in placement_changes)
    inserts_removals = missing + len(inventory["extra_devices"])
    return {"status": "scored", "inventory": inventory, "placement": {**_summary(placements), "rows": placements},
        "bounded_logical_edit_estimate": {
            "lower": inserts_removals + len(changed_ids),
            "upper": inserts_removals + changed_features + placement_edits,
            "definition": "One logical device insertion/removal; each existing device may combine its property/placement repairs into one edit (lower) or require one per labelled field (upper). No CAD-click, drafting-time, wiring, title-block, or engineer-validation estimate.",
            "bounded_reference_only": True}}


def score_case(key, prediction):
    if key.get("kind") != "real_public":
        raise ValueError("Synthetic controls cannot be included in real reconstruction accuracy.")
    requirements = prediction.get("requirements", {})
    design = prediction.get("design") or prediction.get("selected_design") or {}
    configuration = prediction.get("configuration") or {}
    if not design and configuration.get("configurations"):
        design = configuration["configurations"][0]
    rules = design.get("rule_results", [])
    result = {
        "schema_version": VERSION, "package_id": key["package_id"], "kind": "real_public", "split": key.get("split"),
        "projection_version": PROJECTION_VERSION,
        "label_status": key.get("label_status"), "label_scope": key.get("scope"),
        "requirements_extraction": score_requirements(key, requirements),
        "source_extracted_device_inventory": score_inventory(key.get("customer_device_inventory", []),
            _with_requirement_identities(_extracted_inventory(requirements), requirements), complete=True),
        "raw_source_position_inventory": {**score_inventory(key.get("customer_device_inventory", []),
            _raw_drawing_inventory(prediction), complete=True),
            "scope": "Literal source-observation tags/positions and explicitly typed values only; not installed identity, approved requirements, or engineering validation."},
        "customer_inventory_compliance": score_inventory(key.get("customer_device_inventory", []),
            _with_requirement_identities(design, requirements), complete=True),
        "approved_reconstruction": _approved_score(key, design),
        "rule_validation": {"pass": sum(r.get("status") == "pass" for r in rules), "fail": sum(r.get("status") == "fail" for r in rules),
            "unknown": sum(r.get("status") == "unknown" for r in rules), "not_applicable": sum(r.get("status") == "not_applicable" for r in rules),
            "rule_results": rules, "scope": "Solver self-check counts, not independently validated engineering rule accuracy."},
        "unresolved_questions": design.get("unresolved_questions", configuration.get("unresolved_questions", [])),
        "unreadable_pages": requirements.get("unreadable_pages", []),
        "approval_claim_violation": bool(design.get("engineering_approved") or design.get("production_released")),
    }
    return result


def load_customer_documents(package, manifest_path):
    """Source-only native reader; unhandled scanned pages stay visible."""
    import pymupdf

    documents = []
    for source in package.get("sources", []):
        if source.get("role") not in CUSTOMER_ROLES:
            raise ValueError("Reconstruction inputs must all have an explicit customer-side role.")
        path = _path(Path(manifest_path).parent, source["path"])
        if _sha(path) != source["sha256"]:
            raise ValueError("Original source hash mismatch: " + source["id"])
        with pymupdf.open(path) as pdf:
            selected = source.get("pages", list(range(1, len(pdf) + 1)))
            spans, coverage = [], []
            for page_no in selected:
                if not isinstance(page_no, int) or not 1 <= page_no <= len(pdf):
                    raise ValueError("Invalid original source page")
                page = pdf[page_no - 1]
                text = page.get_text()
                coverage.append({"page": page_no, "state": "native" if text.strip() else "scanned", "text_characters": len(text)})
                for block_no, block in enumerate(page.get_text("blocks")):
                    if len(block) > 4 and str(block[4]).strip():
                        spans.append({"id": f"{source['id']}:p{page_no}:b{block_no}", "text": block[4],
                            "location": {"page": page_no, "bbox": list(block[:4]), "coordinate_system": "pdf_points"}})
            documents.append({"id": source["id"], "name": source.get("name", path.name), "sha256": source["sha256"],
                "role": source["role"], "path": str(path), "pages": selected, "spans": spans, "coverage": coverage,
                "source_authority": source.get("source_authority", package.get("source_authority", "customer_inputs_unconfirmed"))})
    return documents


def _implementation_snapshot(output):
    root = Path(__file__).resolve().parents[1]
    files = sorted(set([*root.joinpath("backend/engineering").rglob("*.py"),
        *root.joinpath("backend/engineering/rules").glob("*.json"),
        root / "backend/equipment/evidence/drawing_reader.py", root / "backend/equipment/evidence/schema.py",
        root / "backend/equipment/evidence/ontology.py", root / "backend/equipment/visual_reader.py", Path(__file__).resolve()]))
    hashes = {}
    for file in files:
        relative = file.relative_to(root)
        hashes[str(relative)] = _sha(file)
        archive = Path(output) / "implementation" / relative
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, archive)
    return root, hashes


def run_customer_case(package, manifest_path, output, *, read_text=False, vision=False, model="gpt-6.1-sol", max_drawing_pages=6, max_text_batches=40, extraction_only=False):
    from backend.engineering.requirements import extract_requirements, to_config_inputs

    if package.get("source_authority") == "historical_reference_only" and not extraction_only:
        raise ValueError("Historical reference observations cannot become a new purchase design; use --extract-only.")
    if read_text or vision:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    start = time.monotonic()
    root, before_hashes = _implementation_snapshot(output)
    _write(Path(output) / "implementation-before.json", {"files": before_hashes,
        "started_at": datetime.now(timezone.utc).isoformat(), "model": model,
        "read_text": read_text, "vision": vision, "answer_key_opened": False})
    documents = load_customer_documents(package, manifest_path)
    _write(Path(output) / "source-only-inputs.json", {"documents": documents, "answer_key_opened": False})
    requirements = extract_requirements(documents, scope_id=package["id"])
    text_records, drawing_reports = [], []
    if read_text:
        from backend.engineering.text_reader import read_customer_text
        requirements, text_records = read_customer_text(requirements, documents,
            output_dir=Path(output) / "text-reader", enabled=True, model=model, max_batches=max_text_batches)
    if vision:
        from backend.engineering.readers import read_customer_drawings
        requirements, drawing_reports = read_customer_drawings(requirements, documents,
            output_dir=Path(output) / "drawing-reader", enabled=True, model=model,
            max_pages=max_drawing_pages, read_notes=read_text)
    configuration, design, artifacts = None, None, None
    if not extraction_only:
        from backend.engineering.configurator import configure
        from backend.engineering.generation import generate_package
        inputs = to_config_inputs(requirements)
        configuration = configure(inputs, model_id=package["id"], model_version="1")
        design = next(iter(configuration.get("configurations", [])), {})
        if design:
            artifacts = generate_package(design, output_dir=Path(output) / "generated-proposed")
    after_hashes = {path: _sha(root / path) for path in before_hashes}
    implementation = {"before": before_hashes, "after": after_hashes, "changed_during_run": before_hashes != after_hashes}
    _write(Path(output) / "implementation-after.json", implementation)
    return {"requirements": requirements, "configuration": configuration, "design": design,
        "artifacts": artifacts, "elapsed_seconds": time.monotonic() - start,
        "extraction_only": extraction_only,
        "source_authority": package.get("source_authority", "customer_inputs_unconfirmed"),
        "reader": "source_customer_pipeline_v1" if read_text or vision else "native_only_source_reader_v1",
        "model": model if read_text or vision else None, "text_reading_enabled": read_text, "drawing_reading_enabled": vision,
        "text_records": text_records, "drawing_reports": drawing_reports,
        "reader_coverage": requirements.get("reader_coverage", []), "implementation": implementation,
        "budgets": {"max_drawing_pages": max_drawing_pages, "max_text_batches": max_text_batches},
        "answer_key_used_as_input": False,
        "source_hashes": {source["id"]: source["sha256"] for source in package["sources"]}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--prediction", type=Path)
    parser.add_argument("--run-native", action="store_true", help="Run originals through native text extraction and current configurator/generator; scans remain unreadable.")
    parser.add_argument("--run", action="store_true", help="Run the current source-only pipeline with the explicitly selected reader flags.")
    parser.add_argument("--read-text", action="store_true", help="Read customer native spans with the quoted semantic reader.")
    parser.add_argument("--vision", action="store_true", help="Read customer one-line pages with the source-only drawing reader.")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--max-drawing-pages", type=int, default=6)
    parser.add_argument("--max-text-batches", type=int, default=40)
    parser.add_argument("--extract-only", action="store_true", help="Evaluate source observations only; never configure or generate a design from historical/reference inputs.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if sum([bool(args.prediction), args.run_native, args.run]) != 1:
        parser.error("Choose exactly one of --prediction, --run-native or --run")
    if (args.read_text or args.vision) and not args.run:
        parser.error("Reader flags require --run; --run-native remains a comparable native-only baseline")
    if args.extract_only and not (args.run or args.run_native):
        parser.error("--extract-only is a source-run mode, not a way to relabel an existing prediction")
    if not 1 <= args.max_drawing_pages <= 50 or not 1 <= args.max_text_batches <= 100:
        parser.error("Drawing page budget must be1–50 and text batches1–100")
    package = next(p for p in _read(args.manifest)["packages"] if p["id"] == args.package)
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "prediction-frozen.json").exists():
        raise ValueError("A source prediction is already frozen here; use a new output directory to preserve that run.")
    if package.get("kind") != "real_public":
        raise ValueError("Real reconstruction reports require an original real public package.")
    # Predictions are complete and saved BEFORE opening the answer key.
    prediction = run_customer_case(package, args.manifest, args.output, read_text=args.read_text, vision=args.vision,
        model=args.model, max_drawing_pages=args.max_drawing_pages, max_text_batches=args.max_text_batches,
        extraction_only=args.extract_only) if args.run_native or args.run else _read(args.prediction)
    if prediction.get("answer_key_used_as_input") is True:
        raise ValueError("A label-derived prediction is not a reconstruction experiment.")
    sources = package.get("sources", [])
    if any(s.get("role") not in CUSTOMER_ROLES for s in sources):
        raise ValueError("Manufacturer outputs cannot be included in customer reconstruction inputs.")
    for source in sources:
        if _sha(_path(args.manifest.parent, source["path"])) != source["sha256"]:
            raise ValueError("Original source hash mismatch: " + source["id"])
    declared_sources = prediction.get("source_hashes")
    expected_sources = {s["id"]: s["sha256"] for s in sources}
    if declared_sources is not None and declared_sources != expected_sources:
        raise ValueError("Prediction source hashes do not match the customer input manifest.")
    prediction_path = args.output / "prediction-frozen.json"
    _write(prediction_path, prediction)
    if package.get("split") in {"heldout", "unseen_test"} and prediction.get("implementation", {}).get("changed_during_run"):
        raise ValueError("Implementation changed during the heldout run; source predictions are preserved without opening the key.")
    key_path = _path(args.manifest.parent, package["answer_key"])
    key = _read(key_path)
    if key["package_id"] != package["id"]:
        raise ValueError("Answer key belongs to a different package")
    after = score_case(key, prediction)
    before = score_case(key, {})
    result = {"schema_version": VERSION, "before_nothing_generated": before, "after": after,
        "elapsed_seconds": prediction.get("elapsed_seconds"), "prediction_sha256": _sha(prediction_path),
        "key_sha256": _sha(key_path), "manifest_sha256": _sha(args.manifest),
        "approved_reconstruction_eligible": bool(key.get("approved_reconstruction_eligible")),
        "limits": package.get("limits", []), "answer_key_used_as_input": prediction.get("answer_key_used_as_input"),
        "source_only_run_declared": prediction.get("answer_key_used_as_input") is False and declared_sources == expected_sources,
        "provenance_limit": "External prediction provenance is a recorded declaration, not proof of how a separate caller produced it."}
    _write(args.output / "score.json", result)
    print(json.dumps({"package_id": package["id"], "requirements": after["requirements_extraction"]["match"],
        "required": after["requirements_extraction"]["required"], "approved_reconstruction": after["approved_reconstruction"]["status"],
        "report": str((args.output / "score.json").resolve())}))
    return result


if __name__ == "__main__":
    main()
