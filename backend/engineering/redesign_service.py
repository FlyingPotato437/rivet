"""Durable order-owned comment redesign, files and one final engineer decision."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from backend.company_context.service import require_writer
from backend.domain.service import digest, fail, get, invalidate, scoped
from backend.identity import current_actor, current_org
from backend.ingestion.markup_grounding import (
    ground_redesign_items,
    stored_visual_redesign_items,
)
from backend.orders import service as orders
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Event, Job, Project, Quote, now, uid

from .cad_snapshot import snapshot_dxf
from .comment_redesign import VERSION, build_package, file_hash, graphical_annotations

REVISION_MAX_BYTES = 200 * 1024 * 1024


def _implementation():
    return [
        [name, file_hash(Path(__file__).with_name(name))]
        for name in (
            "comment_redesign.py",
            "dxf_patch.py",
            "cad_snapshot.py",
            "cad_geometry.py",
            "cad_evidence.py",
            "drawing_calculations.py",
            "redesign_service.py",
        )
    ]


def _sources(s, order, native_id=None):
    docs = list(
        s.scalars(
            scoped(Document)
            .where(Document.project_id == order.project_id)
            .order_by(Document.created_at, Document.id)
        )
    )
    # Prefer the explicit drawing selected for this run. A historical DXF's
    # upload order cannot establish which source the engineer is reviewing.
    latest = order.state.get("engineering_redesign", {}).get("runs", [])
    if not native_id and latest:
        native_id = latest[-1].get("native_source_id")
    selected = (
        [native_id]
        if native_id
        else [d.id for d in docs if d.meta.get("redesign_selected") is True]
    )
    sources = []
    for doc in docs:
        if doc.meta.get("active") is False or doc.meta.get("generated"):
            continue
        path = db.BLOBS / doc.blob
        if path.suffix.lower() not in {".pdf", ".dxf", ".xlsx", ".csv"}:
            continue
        if path.suffix.lower() == ".dxf" and selected and doc.id not in selected:
            continue
        if (
            Path(doc.blob).name != doc.blob
            or path.is_symlink()
            or not path.resolve().is_relative_to(db.BLOBS.resolve())
            or not path.is_file()
        ):
            fail("A redesign original is missing or outside this order's storage.", 409)
        actual = file_hash(path)
        if actual != doc.sha256:
            fail(
                "An original changed. Register the new revision before preparing changes.",
                409,
            )
        role = (
            (doc.meta.get("equipment_package") or {}).get("role")
            or doc.meta.get("order_role")
            or doc.kind
        )
        role = {"approval_drawing": "drawing", "markups": "markup"}.get(role, role)
        sources.append(
            {
                "id": doc.id,
                "name": doc.name,
                "path": str(path),
                "sha256": actual,
                "role": role,
            }
        )
    cad = [
        source for source in sources if Path(source["path"]).suffix.lower() == ".dxf"
    ]
    native = cad[0] if len(cad) == 1 else None
    return (
        sources,
        native,
        digest(
            [[v["id"], v["sha256"], v["role"]] for v in sources]
            + [
                ["native", native["id"] if native else None],
                ["engine", VERSION, _implementation()],
            ]
        ),
    )


def _root(order, run_id):
    path = db.BLOBS / "engineering-redesign" / current_org() / order.id / run_id
    if (
        path.is_symlink()
        or any(p.is_symlink() for p in path.parents)
        or not path.resolve().is_relative_to(db.BLOBS.resolve())
    ):
        fail("Redesign storage is unavailable.", 409)
    return path


def _runs(order):
    return deepcopy(order.state.get("engineering_redesign", {}).get("runs", []))


def _manifest(sources):
    return sorted(
        (source["id"], source["sha256"], source["role"]) for source in sources
    )


def _bound_sources(s, order, run):
    if "source_manifest" in run:
        return run["source_manifest"]
    # Older saved packages predate a DB manifest. Their immutable review
    # report contains the originals and is bound by the registered artifact hash.
    if any(a["name"] == "verification.json" for a in run.get("artifacts", [])):
        path = artifact(s, order, run["id"], "verification.json")
        return json.loads(path.read_text())["source_manifest"]
    return []


def _sources_match(s, order, run, sources, signature):
    if run["source_fingerprint"] == signature:
        return True
    try:
        return _manifest(_bound_sources(s, order, run)) == _manifest(sources)
    except (HTTPException, KeyError, OSError, ValueError):
        return False


def _latest_run(order, run_id):
    runs = _runs(order)
    if not any(run["id"] == run_id for run in runs):
        fail("Drawing revision not found.", 404)
    if runs[-1]["id"] != run_id:
        fail("A newer drawing revision is available. Review that package.", 409)
    return runs[-1]


def _save(s, order, run, summary):
    runs = _runs(order)
    found = next((i for i, row in enumerate(runs) if row["id"] == run["id"]), None)
    if found is None:
        runs.append(deepcopy(run))
    else:
        runs[found] = deepcopy(run)
    order.state = {**order.state, "engineering_redesign": {"runs": runs}}
    orders.commit(
        s,
        order,
        summary,
        "engineering_redesign",
        {"run_id": run["id"], "status": run["status"]},
    )


def view(s, order):
    sources, native, signature = _sources(s, order)
    source_ready = bool(
        native
        and any(Path(source["path"]).suffix.lower() == ".pdf" for source in sources)
    )
    runs = _runs(order)
    latest = runs[-1] if runs else None
    if latest:
        latest["current"] = _sources_match(s, order, latest, sources, signature)
        latest["implementation_current"] = latest["source_fingerprint"] == signature
        latest["can_reprepare"] = (
            source_ready
            and latest["status"] not in {"queued", "running"}
            and (
                not latest["implementation_current"]
                or latest["status"] in {"failed", "changes_requested"}
            )
        )
        latest["review_eligible"] = latest["current"] and latest["status"] in {
            "needs_information",
            "ready_for_review",
        }
        latest["visual_findings"] = stored_visual_redesign_items(s, order, sources)
        if (
            not latest["implementation_current"]
            or latest["visual_findings"]
            or any(
                change.get("review", {}).get("decision") == "needs_changes"
                for change in latest.get("changes", [])
            )
        ):
            latest["approval_eligible"] = False
        if not latest["current"] or latest["visual_findings"]:
            latest["engineering_approved"] = False
        if native and latest["current"]:
            latest.setdefault("native_source_id", native["id"])
            latest["native_source_name"] = native["name"]
        comments = latest.get("comments", [])
        graphics = latest.get("unread_graphical_annotations")
        if graphics is None:
            graphics = (
                graphical_annotations(sources) if comments and latest["current"] else []
            )
        grounded = ground_redesign_items(s, order, [*comments, *graphics])
        latest["comments"] = grounded[: len(comments)]
        latest["unread_graphical_annotations"] = grounded[len(comments) :]
        latest.pop("source_fingerprint", None)
        for item in latest.get("artifacts", []):
            item["url"] = (
                f"/api/orders/{order.id}/engineering/redesign/{latest['id']}/files/{quote(item['name'])}"
            )
            item["media_type"] = (
                mimetypes.guess_type(item["name"])[0] or "application/octet-stream"
            )
        for change in latest.get("changes", []):
            pair = change["comparison"]
            change["comparison"] = {
                key
                + "_url": f"/api/orders/{order.id}/engineering/redesign/{latest['id']}/files/{quote(pair[key + '_name'])}"
                for key in ("before", "after")
            }
            for placement in change.get("affected_placements", []):
                for key in ("before", "after"):
                    placement[key + "_url"] = (
                        f"/api/orders/{order.id}/engineering/redesign/{latest['id']}/files/{quote(placement.pop(key + '_name'))}"
                    )
        latest.pop("output_directory", None)
        latest.pop("source_manifest", None)
    return {
        "order_id": order.id,
        "order_version": order.version,
        "source_ready": source_ready,
        "latest": latest,
    }


def prepare(s, order):
    require_writer()
    sources, native, signature = _sources(s, order)
    if native is None:
        fail("Select one current native DXF drawing for this order.", 409)
    if not any(Path(v["path"]).suffix.lower() == ".pdf" for v in sources):
        fail("Add the customer's marked PDF before preparing its changes.", 409)
    runs = _runs(order)
    if (
        runs
        and runs[-1]["source_fingerprint"] == signature
        and runs[-1]["status"]
        in {"queued", "running", "needs_information", "ready_for_review", "approved"}
    ):
        return
    parent = (
        runs[-1].get("revised_from_run_id")
        if runs and runs[-1].get("native_source_id") == native["id"]
        else None
    )
    _queue(s, order, sources, native, signature, revised_from=parent)


def _queue(s, order, sources, native, signature, *, revised_from=None):
    run = {
        "id": uid(),
        "status": "queued",
        "current": True,
        "summary": "Rivet is preparing the supplied drawing comments.",
        "source_fingerprint": signature,
        "source_ids": [v["id"] for v in sources],
        "source_manifest": [
            {key: v[key] for key in ("id", "sha256", "role")} for v in sources
        ],
        "native_source_id": native["id"],
        "created_at": now().isoformat(),
        "comments": [],
        "changes": [],
        "artifacts": [],
        "totals": {
            "comments": 0,
            "changed": 0,
            "already_present": 0,
            "needs_information": 0,
        },
        "remaining_issues": [],
        "approval_eligible": False,
        "engineering_approved": False,
        "production_released": False,
    }
    if revised_from:
        run["revised_from_run_id"] = revised_from
        run["summary"] = "Rivet is rechecking the engineer's uploaded drawing."
    _save(
        s,
        order,
        run,
        "Rivet queued the engineer's drawing for recheck"
        if revised_from
        else "Rivet queued a comment-driven native drawing revision",
    )
    s.add(
        Job(
            project_id=order.project_id,
            kind="engineering_redesign",
            key="engineering-redesign:" + run["id"],
            payload={"order_id": order.id, "run_id": run["id"]},
        )
    )


def review_change(s, order, run_id, change_id, choice, note):
    require_writer()
    run = _latest_run(order, run_id)
    sources, native, signature = _sources(s, order)
    if not native or not _sources_match(s, order, run, sources, signature):
        fail("The source revision changed. Review a fresh package.", 409)
    if run["status"] not in {"needs_information", "ready_for_review"}:
        fail("This drawing package is unavailable for change review.", 409)
    change = next((item for item in run["changes"] if item["id"] == change_id), None)
    if change is None:
        fail("This change does not belong to the drawing revision.", 404)
    if not change.get("verified"):
        fail("The proposed change has no verified saved drawing evidence.", 409)
    artifact(s, order, run_id, "Proposed-drawing.dxf")
    comparisons = {
        pair[f"{side}_name"]
        for pair in [change["comparison"], *change.get("affected_placements", [])]
        for side in ("before", "after")
    }
    for name in comparisons:
        artifact(s, order, run_id, name)
    checked = {*comparisons, "Proposed-drawing.dxf"}
    change["review"] = {
        "decision": choice,
        "note": note,
        "actor": current_actor(),
        "at": now().isoformat(),
        "source_sha256": native["sha256"],
        "artifacts": {
            item["name"]: item["sha256"]
            for item in run["artifacts"]
            if item["name"] in checked
        },
    }
    _save(
        s,
        order,
        run,
        "Engineer accepted a saved drawing change"
        if choice == "accepted"
        else "Engineer requested work on a saved drawing change",
    )


def validate_recheck(s, order, run_id):
    require_writer()
    run = _latest_run(order, run_id)
    if run["status"] not in {
        "needs_information",
        "ready_for_review",
        "changes_requested",
        "approved",
    } and not (run["status"] == "failed" and run.get("revised_from_run_id")):
        fail("Review a finished drawing package before adding a revision.", 409)
    manifest = _bound_sources(s, order, run)
    native_id = run.get("native_source_id")
    if not native_id and run.get("verification"):
        prior_hash = run["verification"]["source_sha256"]
        matches = [
            source["id"] for source in manifest if source["sha256"] == prior_hash
        ]
        if len(matches) == 1:
            native_id = matches[0]
    if not native_id:
        fail("The prior native drawing identity could not be verified.", 409)
    sources, native, signature = _sources(s, order, native_id)
    if not native or not _sources_match(s, order, run, sources, signature):
        fail("The sources changed. Prepare a fresh review package first.", 409)
    return run, native


def queue_recheck(s, order, prior, prior_native, document_id):
    document = get(s, Document, document_id)
    if document.project_id != order.project_id:
        fail("The drawing belongs to another order.", 404)
    sources, native, signature = _sources(s, order, document_id)
    if not native or native["role"] != "drawing":
        fail("Select an active DXF drawing from this order.", 409)
    if native["sha256"] == prior_native["sha256"]:
        fail("The revised drawing matches the prior original byte for byte.", 409)
    _queue(s, order, sources, native, signature, revised_from=prior["id"])


def stage_revision(stream, path):
    checksum = hashlib.sha256()
    size = 0
    prefix = stream.read(65536).removeprefix(b"\xef\xbb\xbf")
    if not re.search(
        rb"(?m)^\s*0[ \t]*\r?\nSECTION[ \t]*\r?\n\s*2[ \t]*\r?\nHEADER[ \t]*\r?$",
        prefix,
    ):
        fail("Export the revised drawing as an ASCII DXF with a header.", 422)
    stream.seek(0)
    with path.open("xb") as saved:
        path.chmod(0o600)
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > REVISION_MAX_BYTES:
                fail("Keep the revised DXF within 200 MB.", 413)
            checksum.update(chunk)
            saved.write(chunk)
    return checksum.hexdigest()


def register_revision(s, order, path, name, sha, revision):
    document = Document(
        project_id=order.project_id,
        name=name,
        kind="approval_drawing",
        blob=path.name,
        sha256=sha,
        state="parsed",
        meta={"order_role": "approval_drawing", "revision_label": revision},
        coverage=[
            {
                "label": "Engineer-supplied native drawing",
                "state": "partial",
                "detail": "Uploaded DXF is retained for the drawing recheck.",
            }
        ],
    )
    s.add(document)
    project = get(s, Project, order.project_id)
    project.input_revision += 1
    quote = s.scalar(
        scoped(Quote).where(Quote.project_id == project.id).with_for_update()
    )
    invalidate(s, quote)
    s.add(Event(project_id=project.id, summary="Uploaded " + name, kind="upload"))
    s.flush()
    orders.refresh(s, order)
    return document


def mark_failed(s, payload, message):
    order = orders.lock_order(s, payload["order_id"])
    run = next((r for r in _runs(order) if r["id"] == payload["run_id"]), None)
    if run and run["status"] in {"queued", "running"}:
        run.update(status="failed", approval_eligible=False, error=message)
        _save(
            s, order, run, "Drawing revision stopped; original files remain unchanged"
        )


def process(payload, leased=lambda: True):
    with Session.begin() as s:
        order = orders.lock_order(s, payload["order_id"])
        runs = _runs(order)
        run = next((r for r in runs if r["id"] == payload["run_id"]), None)
        if not run or run["status"] not in {"queued", "running"} or not leased():
            return
        sources, native, signature = _sources(s, order)
        if signature != run["source_fingerprint"] or not native:
            mark_failed(
                s,
                payload,
                "The source package changed; prepare from the new originals.",
            )
            return
        run["status"] = "running"
        _save(
            s,
            order,
            run,
            "Rivet is applying and rechecking source-bound drawing changes",
        )
        root = _root(order, run["id"])
        prior = None
        prior_path = None
        if run.get("revised_from_run_id"):
            by_id = {r["id"]: r for r in runs}
            prior = by_id[run["revised_from_run_id"]]
            while prior.get("revised_from_run_id"):
                prior = by_id[prior["revised_from_run_id"]]
            if prior.get("changes") and not prior.get("saved_drawing_check"):
                try:
                    prior_path = artifact(s, order, prior["id"], "Proposed-drawing.dxf")
                except HTTPException:
                    pass
    try:
        result_path = root / "result.json"
        if result_path.exists():
            result = json.loads(result_path.read_text())
            for item in result["artifacts"]:
                if file_hash(root / item["name"]) != item["sha256"]:
                    raise ValueError("A saved redesign artifact changed")
        else:
            if root.exists():
                raise ValueError(
                    "An interrupted output is retained. Start a fresh revision after reviewing its saved work."
                )
            result = build_package(
                [v for v in sources if v["id"] != native["id"]],
                native,
                root,
                revised_from=prior,
                previous_snapshot=snapshot_dxf(prior_path) if prior_path else None,
            )
            result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
            result_path.chmod(0o600)
        if not leased():
            return
        with Session.begin() as s:
            order = orders.lock_order(s, payload["order_id"])
            live = next(r for r in _runs(order) if r["id"] == run["id"])
            if live["status"] != "running":
                return
            _, _, current = _sources(s, order)
            if current != signature:
                raise ValueError("Source files changed while the revision was prepared")
            live.update(
                {
                    key: deepcopy(result[key])
                    for key in (
                        "status",
                        "summary",
                        "comments",
                        "changes",
                        "artifacts",
                        "totals",
                        "remaining_issues",
                        "approval_eligible",
                        "engineering_approved",
                        "production_released",
                        "equipment_evidence",
                        "saved_drawing_check",
                        "unread_graphical_annotations",
                        "calculation_audits",
                        "saved_calculation_check",
                    )
                }
            )
            live["finished_at"] = now().isoformat()
            live["verification"] = result["verification"]
            if "revision_checks" in result:
                live["revision_checks"] = result["revision_checks"]
            _save(s, order, live, result["summary"])
    except Exception as exc:
        with Session.begin() as s:
            mark_failed(
                s,
                payload,
                "Redesign could not finish ("
                + type(exc).__name__
                + "). Saved work and originals are retained.",
            )
        raise


def artifact(s, order, run_id, name):
    run = next((r for r in _runs(order) if r["id"] == run_id), None)
    item = (
        next((a for a in run.get("artifacts", []) if a["name"] == name), None)
        if run
        else None
    )
    if not item or Path(name).name != name or "\\" in name:
        fail("This file does not belong to the drawing revision.", 404)
    path = _root(order, run_id) / name
    if path.is_symlink() or not path.is_file() or file_hash(path) != item["sha256"]:
        fail("This saved drawing artifact changed or is unavailable.", 409)
    return path


def decision(s, order, run_id, choice, note):
    require_writer()
    run = _latest_run(order, run_id)
    sources, _, signature = _sources(s, order)
    if not _sources_match(s, order, run, sources, signature):
        fail("The source revision changed. Review a fresh package.", 409)
    if choice == "approve":
        if stored_visual_redesign_items(s, order, sources):
            fail(
                "Additional visual findings still need review on the original pages.",
                409,
            )
        if (
            run["status"] != "ready_for_review"
            or signature != run["source_fingerprint"]
            or not run["approval_eligible"]
            or run["remaining_issues"]
            or any(
                change.get("review", {}).get("decision") == "needs_changes"
                for change in run["changes"]
            )
        ):
            fail(
                "Unverified engineering work remains; this package cannot be approved as completed.",
                409,
            )
        for item in run["artifacts"]:
            artifact(s, order, run_id, item["name"])
    if choice not in {"approve", "request_changes"}:
        fail("Unknown engineer decision.")
    run.update(
        status="approved" if choice == "approve" else "changes_requested",
        decision={
            "choice": choice,
            "note": note,
            "actor": current_actor(),
            "at": now().isoformat(),
        },
        engineering_approved=choice == "approve",
    )
    _save(
        s,
        order,
        run,
        "Engineer "
        + (
            "approved the checked drawing revision"
            if choice == "approve"
            else "requested drawing changes"
        ),
    )
