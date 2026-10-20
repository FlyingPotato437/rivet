"""First-day evidence reconciliation over already extracted source observations.

This module never receives reference labels or reads answer-key paths. It checks
original hashes, adds independent source-table candidates, reconciles all claims,
accounts for source ink, and appends an audit archive within one shared deadline.
It does not re-run native extraction, change CAD files or create/send email.
"""

from __future__ import annotations

import hashlib
import os
import time
from copy import deepcopy
from pathlib import Path

import pymupdf

from ..package_reading import _call_with_budget
from .adapters import adapt_sources
from .correspondence import append_correspondence_claims, match_source_local_rows
from .drawing_context import add_context_crops
from .drawing_reader import drawing_graph_candidates, run_drawing_stage
from .graph_diff import compare_reconciled_models
from .integration import _identity_table
from .ontology import FEATURES, make_node
from .reconcile import reconcile_graph
from .schema import make_claim, source_ref, stable_id
from .store import canonical_bytes, persist_graph
from .synthesis import synthesize_and_compare
from .table_reader import read_table_evidence

LEGACY_VERSION = "rivet.first24-evidence.v1"
VERSION = "rivet.first24-evidence.v2"
CORRESPONDENCE_POLICY = "source-local-v2"
DRAWING_PIPELINE_VERSION = "rivet.first24-evidence.v3"


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(canonical_bytes(value) + b"\n")


def _private_tree(directory):
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("Evidence outputs cannot contain symlinks")
        path.chmod(0o700 if path.is_dir() else 0o600)


def prepare_evidence_inputs(package, manifest_path):
    sources, contexts, pages = {}, {}, {}
    for original in package["sources"]:
        sid = original["id"]
        if sid in sources:
            raise ValueError("Duplicate source IDs in evidence package")
        path = (Path(manifest_path).resolve().parent / original["path"]).resolve()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != original["sha256"].lower():
            raise ValueError("Original hash changed before evidence reconciliation")
        source = {k: original[k] for k in ("id", "name", "role", "revision")}
        sources[sid] = {**source, "path": str(path), "sha256": digest}
        context = {
            "side": original.get("side", "target"),
            "role": original["role"],
            **package.get("source_context", {}).get(sid, {}),
        }
        derivation = original.get("derivation", {})
        if derivation.get("parent_sha256") and derivation.get("parent_physical_pages"):
            context.update(
                original_sha256=derivation["parent_sha256"],
                page_map={
                    str(i): page
                    for i, page in enumerate(derivation["parent_physical_pages"], 1)
                },
            )
        contexts[sid] = context
        if path.suffix.lower() == ".pdf" and original["role"] in {"drawing", "bom"}:
            with pymupdf.open(path) as document:
                pages[sid] = list(range(1, len(document) + 1))
    return sources, contexts, pages


def _ancestry(source, context):
    page = source.get("page")
    if context.get("original_sha256"):
        return stable_id(
            "original-page-ancestry",
            context["original_sha256"],
            context.get("page_map", {}).get(str(page), page),
            source.get("sheet"),
            None if page else source.get("entity", source.get("xml_path")),
        )
    return stable_id(
        "source-page-ancestry",
        source["sha256"],
        page,
        source.get("sheet"),
        None if page else source.get("entity", source.get("xml_path")),
    )


def _merge_table(graph, report, context):
    """Source-local candidates never join visual/native rows by row number."""
    graph = deepcopy(graph)
    nodes = {n["id"]: n for n in graph["nodes"]}
    claims = {c["id"]: c for c in graph["claims"]}
    subjects = {}
    for row in report.get("rows", []):
        local = row.get("source_local_id", str(row.get("subject_id")))
        node_id = stable_id(
            "scoped-table-position",
            graph["scope_id"],
            report["source_id"],
            report["sha256"],
            local,
        )
        ref = source_ref(
            {"source_id": report["source_id"], "sha256": report["sha256"]},
            page=row.get("page"),
            bbox=row.get("bbox"),
            entity=local,
        )
        subjects[row["subject_id"]] = node_id
        if node_id not in nodes:
            node = make_node(
                node_id,
                "unclassified_position",
                graph["scope_id"],
                source_refs=[ref],
                metadata={
                    "source_local": True,
                    "identity_verified": False,
                    "source_id": report["source_id"],
                    "source_local_id": local,
                    "section": row.get("section_no"),
                    "circuit": row.get("circuit_no"),
                    "reader": report.get("version"),
                },
            )
            graph["nodes"].append(node)
            nodes[node_id] = node
        # All raw cells survive, including unsupported/unclear table columns.
        for index, cell in enumerate(row.get("cells", []), 1):
            raw = cell.get("raw_text", cell.get("text", ""))
            cell_ref = source_ref(
                ref,
                bbox=cell.get("bbox", ref.get("bbox")),
                cell=cell.get("grid_column", index),
                row=row.get("grid_row"),
                table=row.get("table"),
            )
            raw_claim = make_claim(
                node_id,
                "unknown:raw_table_cell:"
                + str(cell.get("header") or cell.get("grid_column", index)),
                None,
                source=cell_ref,
                extractor={
                    "name": "source_table_raw_cell",
                    "version": report.get("version"),
                },
                raw_value=raw,
                raw_confidence=cell.get("ocr_confidence"),
                normalization={
                    "valid": False,
                    "reason": "raw_cell_retained_with_typed_claim_separately",
                },
                conditions={
                    "side": context.get("side", "target"),
                    "role": context.get("role"),
                    "candidate_only": True,
                },
                independence_group=_ancestry(cell_ref, context),
            )
            if raw_claim["id"] not in claims:
                claims[raw_claim["id"]] = raw_claim
                graph["claims"].append(raw_claim)
    for original in report.get("claims", []):
        subject = subjects.get(original["subject_id"])
        if not subject:
            raise ValueError("Table claim has no retained source-local row")
        ref = original["source"]
        if (
            ref.get("source_id") != report["source_id"]
            or ref.get("sha256") != report["sha256"]
        ):
            raise ValueError("A table claim differs from its original report source")
        claim = make_claim(
            subject,
            original["feature"],
            original.get("value"),
            source=ref,
            extractor=original["extractor"],
            raw_value=original.get("raw_value"),
            raw_confidence=original.get("raw_confidence"),
            unit=original.get("unit"),
            state="observed",
            normalization=original.get("normalization"),
            conditions={
                **original.get("conditions", {}),
                "side": context.get("side", "target"),
                "role": context.get("role"),
                "source_reader_claim_id": original["id"],
            },
            independence_group=_ancestry(ref, context),
        )
        if claim["id"] not in claims:
            claims[claim["id"]] = claim
            graph["claims"].append(claim)
    graph["scope_coverage"].extend(
        {
            "source_id": report["source_id"],
            "page": page.get("page"),
            "reader": "source_table_evidence",
            "status": page.get("status"),
            "identity_complete": False,
            "connectivity_complete": False,
            "unknown_regions": page.get("unknown_regions", []),
        }
        for page in report.get("pages", [])
    )
    return graph


def _pair_view(graph, source_id, side):
    result = deepcopy(graph)
    selected = [
        c
        for c in result["claims"]
        if c["source"].get("source_id") == source_id
        or (
            c["conditions"].get("side") == side
            and c["conditions"].get("role") == "catalog"
        )
    ]
    subjects = {c["subject_id"] for c in selected}
    result["claims"] = selected
    result["nodes"] = [n for n in result["nodes"] if n["id"] in subjects]
    result["resolutions"] = [
        r
        for r in result.get("resolutions", [])
        if r["subject_id"] in subjects and r.get("side") == side
    ]
    result["edges"] = [
        e for e in result["edges"] if e["from"] in subjects and e["to"] in subjects
    ]
    result["coverage"].update(
        complete=False, identity_complete=False, connectivity_complete=False
    )
    return result


def _merge_drawing(graph, stage):
    """Append raw drawing observations; never import their provisional decisions."""
    graph = deepcopy(graph)
    known = {
        kind: {item["id"]: item for item in graph[kind]}
        for kind in ("nodes", "edges", "claims")
    }
    for observed in stage.get("graphs", []):
        if observed["scope_id"] != graph["scope_id"]:
            raise ValueError("Drawing observations belong to another package scope")
        for claim in observed["claims"]:
            ref = claim["source"]
            if graph["source_hashes"].get(ref["source_id"]) != ref["sha256"]:
                raise ValueError(
                    "Drawing evidence is outside the pinned source package"
                )
        for kind in ("nodes", "edges", "claims"):
            for item in observed[kind]:
                previous = known[kind].get(item["id"])
                if previous is None:
                    graph[kind].append(deepcopy(item))
                    known[kind][item["id"]] = graph[kind][-1]
                elif kind == "nodes":
                    if (
                        previous["kind"] != item["kind"]
                        or previous["scope_key"] != item["scope_key"]
                    ):
                        raise ValueError("Drawing node identity collision")
                    for ref in item.get("source_refs", []):
                        if ref not in previous["source_refs"]:
                            previous["source_refs"].append(deepcopy(ref))
                elif previous != item:
                    raise ValueError("Conflicting immutable drawing evidence ID")
    graph["scope_coverage"].append(
        {
            "source_id": stage["source_id"],
            "sha256": stage["sha256"],
            "reader": "independent_drawing_page_image",
            "status": stage["status"],
            "complete": False,
            "identity_complete": False,
            "connectivity_complete": False,
            "deferred_pages": stage.get("deferred_pages", []),
            "errors": stage.get("errors", []),
        }
    )
    return graph


def _select_drawing_pages(selected, sources, budget):
    """Round-robin sources so a long first issue cannot consume both sides' budget."""
    groups = {}
    for sid, page in selected:
        if sources[sid]["role"] == "drawing":
            groups.setdefault(sid, []).append(page)
    ordered = []
    for index in range(max((len(pages) for pages in groups.values()), default=0)):
        ordered.extend(
            (sid, pages[index]) for sid, pages in groups.items() if index < len(pages)
        )
    return ordered[:budget], ordered[budget:]


def _summary(
    graph, table_reports, synthesis_reports, revision_reports, errors, eligible_pages
):
    typed = [
        r
        for r in graph.get("resolutions", [])
        if r.get("feature") in FEATURES
        and r.get("feature") != "unknown"
        and r.get("side") != "requirements"
        and r.get("state") == "observed"
    ]
    review = sum(r.get("needs_review", True) for r in typed)
    return {
        "schema_version": graph.get("metadata", {}).get("pipeline_version", VERSION),
        "decision_authority": "reconciled source evidence, not raw extractor confidence",
        "claim_count": len(graph["claims"]),
        "entity_count": len(graph["nodes"]),
        "typed_value_decisions": len(typed),
        "accepted_literal_or_calibrated_decisions": len(typed) - review,
        "values_needing_human_review": review,
        "values_needing_review_fraction": review / len(typed) if typed else None,
        "conflicting_value_decisions": sum(
            r.get("status") == "conflict" for r in typed
        ),
        "table_candidate_rows": sum(len(r.get("rows", [])) for r in table_reports),
        "eligible_source_pages": eligible_pages,
        "table_pages_attempted": sum(len(r.get("pages", [])) for r in table_reports),
        "synthesis_pages_processed": len(synthesis_reports),
        "unexplained_regions": sum(
            r.get("unexplained_region_count", 0) for r in synthesis_reports
        ),
        "equipment_extraction_recall": None,
        "recall_reason": "No independent equipment-region labels are supplied to this source-only pipeline. Ink coverage is not equipment recall.",
        "graph_revision_pairs_attempted": len(revision_reports),
        "graph_changes": sum(len(r.get("changes", [])) for r in revision_reports),
        "graph_revision_precision": None,
        "graph_revision_recall": None,
        "incomplete_stages": errors,
        "requires_human_verification": True,
        "engineering_approved": False,
        "production_released": False,
        "legacy_reader_scores_are_not_reconciled_accuracy": True,
    }


def build_evidence_graph(
    *,
    sources,
    source_context,
    scope_id,
    comparison=None,
    source_observations=None,
    predictions=None,
    table_reports=None,
    verified_identities=None,
    calibration=None,
    reconcile=True,
    correspondence_policy=None,
):
    """Build from completed observations only; never inspect reference labels.

    Native designations may bind native equipment records; OCR/vision rows stay
    source-local unless an independently cited correspondence is supplied.
    """
    if correspondence_policy not in {None, CORRESPONDENCE_POLICY}:
        raise ValueError("Unknown evidence correspondence policy")
    sources = (
        {source["id"]: source for source in sources}
        if isinstance(sources, list)
        else sources
    )
    contexts = source_context or {}
    hashes = {sid: source["sha256"] for sid, source in sources.items()}
    predictions = predictions or {}
    native = comparison or {
        side: {"facts": [], "requested_changes": [], "sources": []}
        for side in ("baseline", "target", "requirements")
    }
    observation = source_observations or {}
    visual = {
        "schema_version": "retained-source-observations",
        "source_hashes": hashes,
        "producer": {"name": "retained_visual_equipment_observations"},
        "observed_bom_rows": observation.get(
            "visual_schedule_rows",
            predictions.get("observed_bom_rows") or predictions.get("bom_rows", []),
        ),
        "devices": observation.get("visual_devices", predictions.get("devices", [])),
        "connections": observation.get(
            "visual_connections", predictions.get("connections", [])
        ),
        "sheet_identities": observation.get(
            "sheet_identities", predictions.get("sheet_identities", [])
        ),
        "pages": [
            p
            for p in predictions.get("pages", [])
            if p.get("reading_kind") == "equipment"
        ],
    }
    native_rows = {
        "schema_version": "retained-native-row-observations",
        "source_hashes": hashes,
        "producer": {"name": "source_bom_row_reader"},
        "observed_bom_rows": observation.get("native_schedule_rows", []),
    }
    graph = adapt_sources(
        sources=list(sources.values()),
        rawcomparison=native,
        visual_predictions=[native_rows, visual],
        cad_reports=observation.get("cad_reports", []),
        scope_id=scope_id,
        source_context=contexts,
        verified_identities=_identity_table(native, scope_id)
        + list(verified_identities or []),
        metadata={
            "pipeline_version": VERSION if correspondence_policy else LEGACY_VERSION,
            "source_hashes": hashes,
            "native_reextraction_performed": False,
        },
    )
    graph["source_hashes"] = hashes
    for claim in graph["claims"]:
        if hashes.get(claim["source"]["source_id"]) != claim["source"]["sha256"]:
            raise ValueError("An observation is outside the pinned package sources")
    for report in table_reports or []:
        if hashes.get(report.get("source_id")) != report.get("sha256"):
            raise ValueError("Table evidence differs from the pinned original")
        graph = _merge_table(graph, report, contexts.get(report["source_id"], {}))
    if correspondence_policy:
        graph, _ = _apply_correspondence(graph, visual, table_reports or [], contexts)
    return reconcile_graph(graph, calibration=calibration) if reconcile else graph


def _apply_correspondence(graph, visual, tables, contexts):
    correspondence = match_source_local_rows(
        visual, tables, scope_id=graph["scope_id"], source_context=contexts
    )
    graph = append_correspondence_claims(graph, correspondence)
    graph["metadata"]["pipeline_version"] = VERSION
    graph["metadata"]["correspondence_policy"] = CORRESPONDENCE_POLICY
    return graph, correspondence


def run_evidence_pipeline(
    package,
    manifest_path,
    *,
    comparison,
    source_observations,
    predictions,
    output_dir,
    deadline,
    calibration=None,
    max_pages=500,
    correspondence_policy=CORRESPONDENCE_POLICY,
    drawing_vision=False,
    drawing_model=None,
    drawing_page_budget=500,
):
    """Run local evidence phases with a shared absolute monotonic deadline.

    Inputs are extraction observations only. The caller must not pass an answer
    key, reference regions or the scored evaluation report into this function.
    ``comparison`` is the already extracted native comparison; never re-read it.
    """
    if correspondence_policy not in {None, CORRESPONDENCE_POLICY}:
        raise ValueError("Unknown evidence correspondence policy")
    started = time.monotonic()
    sources, contexts, pages = prepare_evidence_inputs(package, manifest_path)
    output = Path(output_dir).absolute()
    if output.is_symlink():
        raise ValueError("Evidence output cannot be a symlink")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    hashes = {sid: source["sha256"] for sid, source in sources.items()}
    scope_id = (
        "pilot-package:"
        + str(package["id"])
        + ":"
        + hashlib.sha256(canonical_bytes(hashes)).hexdigest()
    )
    graph = build_evidence_graph(
        sources=sources,
        source_context=contexts,
        scope_id=scope_id,
        comparison=comparison,
        source_observations=source_observations,
        predictions=predictions,
        verified_identities=package.get("verified_identities", []),
        reconcile=False,
    )
    errors, tables, synthesis, revisions, drawings, drawing_revisions = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    if comparison is None:
        errors.append(
            {
                "stage": "native_comparison",
                "status": "unavailable",
                "message": "Native extraction did not finish; available visual claims remain unverified.",
            }
        )
    eligible = [(sid, p) for sid, source_pages in pages.items() for p in source_pages]
    if not 1 <= max_pages <= 5000:
        raise ValueError("Use a bounded positive evidence page budget")
    selected, deferred = eligible[:max_pages], eligible[max_pages:]
    if not 1 <= drawing_page_budget <= 500:
        raise ValueError("Use a bounded drawing page budget between 1 and 500")
    if deferred:
        errors.append(
            {
                "stage": "page_budget",
                "status": "not_processed_budget",
                "pages": [{"source_id": sid, "page": p} for sid, p in deferred],
            }
        )
    for sid in pages:
        selected_pages = [p for source_id, p in selected if source_id == sid]
        for index in range(0, len(selected_pages), 4):
            batch = selected_pages[index : index + 4]
            if time.monotonic() >= deadline:
                errors.append(
                    {
                        "stage": "source_table_reading",
                        "status": "not_processed_deadline",
                        "source_id": sid,
                        "pages": selected_pages[index:],
                    }
                )
                break
            directory = (
                output
                / "table-evidence"
                / hashlib.sha256(sid.encode()).hexdigest()[:20]
                / str(batch[0])
            )
            result = _call_with_budget(
                read_table_evidence,
                sources[sid],
                batch,
                max_pages=4,
                output_dir=directory,
                deadline=deadline,
            )
            if result["status"] != "completed":
                errors.append(
                    {
                        "stage": "source_table_reading",
                        "source_id": sid,
                        "pages": batch,
                        "status": result["status"],
                        "error_type": result.get("error_type"),
                    }
                )
                continue
            table = result["result"]
            tables.append(table)
            _write(directory / "table-evidence.json", table)
            graph = _merge_table(graph, table, contexts[sid])
    drawing_selected, drawing_deferred = _select_drawing_pages(
        selected, sources, drawing_page_budget
    )
    if drawing_vision:
        for sid, physical in drawing_selected:
            stage = run_drawing_stage(
                sources[sid],
                [physical],
                scope_id=scope_id,
                output_dir=output
                / "drawing-evidence"
                / hashlib.sha256(sid.encode()).hexdigest()[:20],
                deadline=deadline,
                model=drawing_model,
                side=contexts[sid].get("side", "target"),
                lineup_lineage=package.get("drawing_lineage"),
                max_pages=1,
                allow_remote=True,
                source_context=contexts[sid],
            )
            drawings.append(stage)
            graph = _merge_drawing(graph, stage)
            _write(output / "drawing-reading-stages.json", drawings)
            if stage["status"] != "completed_requires_human_verification":
                errors.append(
                    {
                        "stage": "drawing_graph_reading",
                        "source_id": sid,
                        "status": stage["status"],
                        "errors": stage.get("errors", []),
                        "deferred_pages": stage.get("deferred_pages", []),
                    }
                )
        if drawing_deferred:
            errors.append(
                {
                    "stage": "drawing_graph_reading",
                    "status": "not_processed_page_budget",
                    "pages": [
                        {"source_id": sid, "page": page}
                        for sid, page in drawing_deferred
                    ],
                }
            )
    correspondence = None
    if correspondence_policy and time.monotonic() < deadline:
        visual = {
            "source_hashes": hashes,
            "observed_bom_rows": source_observations.get(
                "visual_schedule_rows",
                predictions.get("observed_bom_rows") or predictions.get("bom_rows", []),
            ),
        }
        matched = _call_with_budget(
            _apply_correspondence, graph, visual, tables, contexts, deadline=deadline
        )
        if matched["status"] == "completed":
            graph, correspondence = matched["result"]
            _write(output / "source-local-correspondence.json", correspondence)
        else:
            errors.append(
                {
                    "stage": "source_local_correspondence",
                    "status": matched["status"],
                    "error_type": matched.get("error_type"),
                }
            )
    if time.monotonic() >= deadline:
        errors.append(
            {
                "stage": "reconciliation",
                "status": "not_processed_deadline",
                "message": "Claims were preserved but canonical decisions could not run within the shared deadline.",
            }
        )
        graph["resolutions"] = []
    else:
        if drawing_vision:
            graph["metadata"]["pipeline_version"] = DRAWING_PIPELINE_VERSION
        result = _call_with_budget(
            reconcile_graph, graph, calibration=calibration, deadline=deadline
        )
        if result["status"] == "completed":
            graph = result["result"]
        else:
            errors.append(
                {
                    "stage": "reconciliation",
                    "status": result["status"],
                    "error_type": result.get("error_type"),
                }
            )
            graph["resolutions"] = []
    receipt = persist_graph(graph, output / "archive")
    _write(output / "canonical-equipment-graph.json", graph)
    for sid, physical in selected:
        if time.monotonic() >= deadline:
            errors.append(
                {
                    "stage": "analysis_by_synthesis",
                    "source_id": sid,
                    "page": physical,
                    "status": "not_processed_deadline",
                }
            )
            continue
        directory = (
            output
            / "source-accounting"
            / hashlib.sha256(sid.encode()).hexdigest()[:20]
            / str(physical)
        )
        result = _call_with_budget(
            synthesize_and_compare,
            graph,
            sources[sid]["path"],
            sid,
            physical,
            directory,
            sha256=hashes[sid],
            deadline=deadline,
        )
        if result["status"] == "completed":
            synthesis.append(result["result"])
        else:
            errors.append(
                {
                    "stage": "analysis_by_synthesis",
                    "source_id": sid,
                    "page": physical,
                    "status": result["status"],
                    "error_type": result.get("error_type"),
                }
            )
    for index, pair in enumerate(package.get("revision_pairs", []), 1):
        old, new = pair.get("baseline_source_id"), pair.get("target_source_id")
        if (
            old not in sources
            or new not in sources
            or contexts[old].get("side") != "baseline"
            or contexts[new].get("side") != "target"
        ):
            errors.append(
                {
                    "stage": "graph_revision",
                    "status": "invalid_explicit_pair",
                    "pair": {"baseline_source_id": old, "target_source_id": new},
                }
            )
            continue
        if time.monotonic() >= deadline:
            errors.append(
                {
                    "stage": "graph_revision",
                    "status": "not_processed_deadline",
                    "pair": index,
                }
            )
            continue
        result = _call_with_budget(
            compare_reconciled_models,
            _pair_view(graph, old, "baseline"),
            _pair_view(graph, new, "target"),
            before_side="baseline",
            after_side="target",
            output_dir=output / "graph-revisions" / str(index),
            sources=sources,
            deadline=deadline,
        )
        if result["status"] == "completed":
            revisions.append(
                {"baseline_source_id": old, "target_source_id": new, **result["result"]}
            )
        else:
            errors.append(
                {
                    "stage": "graph_revision",
                    "status": result["status"],
                    "pair": index,
                    "error_type": result.get("error_type"),
                }
            )
        if drawing_vision:
            old_graphs = [
                g
                for stage in drawings
                if stage["source_id"] == old
                for g in stage["graphs"]
            ]
            new_graphs = [
                g
                for stage in drawings
                if stage["source_id"] == new
                for g in stage["graphs"]
            ]
            if len(old_graphs) == len(new_graphs) == 1:
                candidate = drawing_graph_candidates(
                    old_graphs[0],
                    new_graphs[0],
                    output_dir=output / "drawing-graph-revisions" / str(index),
                    sources=sources,
                )
                candidate = add_context_crops(
                    old_graphs[0],
                    new_graphs[0],
                    candidate,
                    sources=sources,
                    output_dir=output
                    / "drawing-graph-revisions"
                    / str(index)
                    / "source-context-v1",
                    page_reports=[
                        report
                        for stage in drawings
                        if stage["source_id"] in {old, new}
                        for report in stage["reports"]
                    ],
                )
                drawing_revisions.append(
                    {"baseline_source_id": old, "target_source_id": new, **candidate}
                )
            else:
                drawing_revisions.append(
                    {
                        "baseline_source_id": old,
                        "target_source_id": new,
                        "candidate_changes": [],
                        "confirmed_changes": [],
                        "unknowns": [
                            {
                                "reason": "Missing page graphs or multiple sheets without explicit sheet correspondence."
                            }
                        ],
                        "engineering_verified": False,
                    }
                )
    summary = _summary(graph, tables, synthesis, revisions, errors, len(eligible))
    summary["source_local_correspondence"] = (
        correspondence["counts"] if correspondence is not None else None
    )
    summary["correspondence_policy"] = correspondence_policy
    summary["drawing_reader"] = {
        "status": "requested"
        if drawing_vision
        else "not_run_remote_vision_not_requested",
        "pages_processed": sum(len(stage["reports"]) for stage in drawings),
        "cached_model_pages": sum(
            page.get("cache_hit", False)
            for stage in drawings
            for report in stage["reports"]
            for page in report["pages"]
        ),
        "page_budget": drawing_page_budget,
        "deferred_pages": len(drawing_deferred)
        if drawing_vision
        else len(drawing_selected) + len(drawing_deferred),
        "candidate_changes": sum(
            len(pair["candidate_changes"]) for pair in drawing_revisions
        ),
        "confirmed_changes": 0,
        "unknown_revision_pairs": sum(
            bool(pair["unknowns"]) for pair in drawing_revisions
        ),
        "engineering_approved": False,
        "production_released": False,
    }
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    summary["status"] = (
        "partial_requires_human_verification"
        if errors
        else "completed_requires_human_verification"
    )
    _write(output / "verification-summary.json", summary)
    _write(output / "source-accounting.json", synthesis)
    _write(output / "graph-revision-checks.json", revisions)
    _write(output / "drawing-graph-revision-candidates.json", drawing_revisions)
    _write(output / "drawing-reading-stages.json", drawings)
    _private_tree(output)
    return {
        "status": summary["status"],
        "summary": summary,
        "errors": errors,
        "archive_receipt": receipt,
        "artifacts": {
            "canonical_graph": str(output / "canonical-equipment-graph.json"),
            "verification_summary": str(output / "verification-summary.json"),
            "source_accounting": str(output / "source-accounting.json"),
            "graph_revisions": str(output / "graph-revision-checks.json"),
            "drawing_graph_candidates": str(
                output / "drawing-graph-revision-candidates.json"
            ),
            "drawing_reading_stages": str(output / "drawing-reading-stages.json"),
            **(
                {
                    "source_local_correspondence": str(
                        output / "source-local-correspondence.json"
                    )
                }
                if correspondence is not None
                else {}
            ),
        },
    }
