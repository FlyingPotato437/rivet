"""Durable order-owned comment redesign, files and one final engineer decision."""

from __future__ import annotations

import json
import mimetypes
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote

from backend.company_context.service import require_writer
from backend.domain.service import digest, fail, scoped
from backend.identity import current_actor, current_org
from backend.orders import service as orders
from backend.storage import db
from backend.storage.db import Session
from backend.storage.models import Document, Job, now, uid

from .comment_redesign import VERSION, build_package, file_hash


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


def _sources(s, order):
    docs = list(
        s.scalars(
            scoped(Document)
            .where(Document.project_id == order.project_id)
            .order_by(Document.created_at, Document.id)
        )
    )
    sources = []
    for doc in docs:
        if doc.meta.get("active") is False or doc.meta.get("generated"):
            continue
        path = db.BLOBS / doc.blob
        if path.suffix.lower() not in {".pdf", ".dxf", ".xlsx", ".csv"}:
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
    # Multiple live CAD revisions need an explicit selected source. A filename
    # or upload order cannot establish which design the customer requested.
    selected = [d.id for d in docs if d.meta.get("redesign_selected") is True]
    if selected:
        cad = [source for source in cad if source["id"] in selected]
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
    runs = _runs(order)
    latest = runs[-1] if runs else None
    if latest:
        latest["current"] = latest["source_fingerprint"] == signature
        if not latest["current"]:
            latest["approval_eligible"] = False
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
    return {
        "order_id": order.id,
        "order_version": order.version,
        "source_ready": bool(
            native and any(Path(v["path"]).suffix.lower() == ".pdf" for v in sources)
        ),
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
    run = {
        "id": uid(),
        "status": "queued",
        "current": True,
        "summary": "Rivet is preparing the supplied drawing comments.",
        "source_fingerprint": signature,
        "source_ids": [v["id"] for v in sources],
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
    _save(s, order, run, "Rivet queued a comment-driven native drawing revision")
    s.add(
        Job(
            project_id=order.project_id,
            kind="engineering_redesign",
            key="engineering-redesign:" + run["id"],
            payload={"order_id": order.id, "run_id": run["id"]},
        )
    )


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
        run = next((r for r in _runs(order) if r["id"] == payload["run_id"]), None)
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
    try:
        result_path = root / "result.json"
        if result_path.exists():
            result = json.loads(result_path.read_text())
            for artifact in result["artifacts"]:
                if file_hash(root / artifact["name"]) != artifact["sha256"]:
                    raise ValueError("A saved redesign artifact changed")
        else:
            if root.exists():
                raise ValueError(
                    "An interrupted output is retained. Start a fresh revision after reviewing its saved work."
                )
            result = build_package(
                [v for v in sources if v["id"] != native["id"]], native, root
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
    run = next((r for r in _runs(order) if r["id"] == run_id), None)
    if not run:
        fail("Drawing revision not found.", 404)
    if run["id"] != _runs(order)[-1]["id"]:
        fail("A newer drawing revision is available. Review that package.", 409)
    _, _, signature = _sources(s, order)
    if signature != run["source_fingerprint"]:
        fail("The source revision changed. Review a fresh package.", 409)
    if choice == "approve":
        if (
            run["status"] != "ready_for_review"
            or not run["approval_eligible"]
            or run["remaining_issues"]
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
