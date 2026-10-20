"""Build source-backed equipment projections for APIs and delivery checks."""

from copy import deepcopy
from pathlib import Path

from backend.domain.service import digest

from . import service
from .model import compile_model


def project(raw, fingerprint, baseline, target, target_sources, baseline_sources=()):
    raw = deepcopy(raw)
    native_snapshot = None
    candidates = [
        v
        for v in target_sources
        if v["role"] == "drawing" and Path(v["name"]).suffix.lower() == ".dxf"
    ]
    if len(candidates) == 1:
        from .native_model import NativeModelError, inspect_native

        original = candidates[0]
        try:
            graph = inspect_native(original["path"], expected_sha256=original["sha256"])
            if (
                graph["units"] == "mm"
                and graph["devices"]
                and any(d["terminals"] for d in graph["devices"])
            ):
                native_snapshot = graph, original
            elif graph["units"] != "mm":
                raw.setdefault("coverage", {})["native_source_issue"] = (
                    "Native source units are unverified; connectivity and physical dimensions remain unknown."
                )
        except NativeModelError:
            raw.setdefault("coverage", {})["native_source_issue"] = (
                "The original native design is unavailable or changed; connectivity remains unknown."
            )
    from .pdf_model import PDFModelError, compare_pdf, inspect_pdf

    snapshots = []
    pdf_inputs = {"baseline": [], "target": []}
    for side, inputs in (("baseline", baseline_sources), ("target", target_sources)):
        for original in inputs:
            if (
                original["role"] != "drawing"
                or Path(original["name"]).suffix.lower() != ".pdf"
            ):
                continue
            if len(snapshots) >= 12:
                raw.setdefault("coverage", {})["pdf_source_issue"] = (
                    "This package exceeds the bounded 12-drawing graph inspection; remaining drawing coverage is unknown."
                )
                break
            try:
                graph = inspect_pdf(original)
                snapshots.append((graph, original, side))
                pdf_inputs[side].append(original)
            except PDFModelError as exc:
                raw.setdefault("coverage", {}).setdefault(
                    "pdf_source_issues", []
                ).append(
                    {
                        "document_id": original["id"],
                        "name": original["name"],
                        "reason": str(exc),
                    }
                )
    revision_check = None
    if len(pdf_inputs["baseline"]) == len(pdf_inputs["target"]) == 1:
        revision_check = compare_pdf(pdf_inputs["baseline"][0], pdf_inputs["target"][0])
        # Snapshots already retain both drawings; omit a second copy of all
        # vectors and page geometry from the comparison response.
        revision_check.pop("baseline", None)
        revision_check.pop("target", None)
    return compile_model(
        raw,
        fingerprint,
        baseline,
        target,
        native_snapshot=native_snapshot,
        pdf_snapshots=snapshots,
        pdf_revision_check=revision_check,
    )


def current(s, order):
    from . import profile_service

    _, _, sources, baseline, target, fingerprint = service.selected_sources(s, order)
    ctx = profile_service.context(s, order)
    model = project(
        service.raw_for_order(s, order, sources, fingerprint),
        digest([fingerprint, ctx["signature"]]) if ctx["signature"] else fingerprint,
        baseline,
        target,
        sources["target"],
        sources["baseline"],
    )
    result = model
    result["company_rules"] = profile_service.check_model(
        s, order, model, [v for side in sources.values() for v in side]
    )
    return result
