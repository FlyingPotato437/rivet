"""Explicit customer-input bridge for the existing first-day package runner."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .requirements import CUSTOMER_ROLES


def customer_case(package):
    """Manufacturer outputs never become design inputs through filename guesses."""
    classified = package.get("engineering_inputs") or []
    if not classified:
        return None
    originals = {item["id"]: item for item in package["sources"]}
    seen, sources = set(), []
    for selection in classified:
        source_id, role = selection.get("source_id"), selection.get("role")
        if (
            source_id not in originals
            or role not in CUSTOMER_ROLES
            or source_id in seen
        ):
            raise ValueError(
                "Engineering inputs require unique existing source IDs with explicit customer roles"
            )
        source = deepcopy(originals[source_id])
        if Path(source["path"]).suffix.lower() != ".pdf":
            raise ValueError(
                "The first-day engineering requirements step currently requires customer PDFs; supply a PDF export alongside CAD or spreadsheets"
            )
        source["role"] = role
        if "pages" in selection:
            source["pages"] = deepcopy(selection["pages"])
        sources.append(source)
        seen.add(source_id)
    return {
        "id": package["id"],
        "kind": package["kind"],
        "split": "pilot_intake"
        if package["kind"] == "real_private"
        else package.get("split", "development"),
        "sources": sources,
        "approved_reconstruction_eligible": False,
    }


def run_customer_intake(
    package,
    manifest_path,
    output,
    *,
    remote_reading=False,
    model="gpt-6.1-sol",
    max_drawing_pages=6,
):
    from scripts.evaluate_switchboard_reconstruction import _write, run_customer_case

    case = customer_case(package)
    if case is None:
        return {
            "status": "not_run",
            "reason": "Customer-side engineering_inputs were not classified; manufacturer drawings were not reused as original requirements",
            "engineering_approved": False,
        }
    output = Path(output)
    if output.exists():
        raise ValueError(
            "Use a fresh engineering intake output directory to preserve prior model versions"
        )
    output.mkdir(parents=True, mode=0o700)
    prediction = run_customer_case(
        case,
        manifest_path,
        output,
        read_text=remote_reading,
        vision=remote_reading,
        model=model,
        max_drawing_pages=max_drawing_pages,
    )
    _write(output / "prediction-frozen.json", prediction)
    design = prediction.get("design") or {}
    requirements = prediction["requirements"]
    result = {
        "status": "proposed_with_verification_needed",
        "prediction": str(output / "prediction-frozen.json"),
        "source_inputs": len(case["sources"]),
        "requirements_proposed": len(requirements["items"]),
        "conflicts": len(requirements.get("conflicts", [])),
        "unreadable_pages": len(requirements.get("unreadable_pages", [])),
        "devices_proposed": len(design.get("devices", [])),
        "rule_questions": len(design.get("unresolved_questions", [])),
        "artifacts": prediction.get("artifacts"),
        "elapsed_seconds": prediction["elapsed_seconds"],
        "remote_reading_enabled": remote_reading,
        "accuracy": None,
        "accuracy_note": "No independent manufacturer key supplied; counts are not accuracy",
        "engineering_approved": False,
        "production_released": False,
        "customer_mail_sent": False,
    }
    _write(output / "intake-summary.json", result)
    return result
