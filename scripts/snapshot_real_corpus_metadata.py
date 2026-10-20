"""Publish source metadata, never original documents, for the local real corpus."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "benchmarks" / "real-packages"
LOCAL = ROOT / ".data" / "real-document-corpus"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_path(folder: Path, relative_path: str, expected_hash: str) -> str:
    path = (folder / relative_path).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError("Source path escapes its acquisition folder")
    if _sha256(path) != expected_hash:
        raise ValueError(f"Original source changed: {path.name}")
    return "../../" + path.relative_to(ROOT).as_posix()


def snapshot() -> dict[str, int]:
    folder = LOCAL / "new-official"
    manifest = json.loads((folder / "manifest.json").read_text())
    public = copy.deepcopy(manifest)
    public["schema_version"] = "rivet.real-acquisition-catalog.v1"
    public["purpose"] = (
        "Reproducible publisher provenance only; not an accuracy report or a "
        "redistribution licence. Originals stay in the ignored local data folder."
    )
    source_count = 0
    pages = 0
    for package in public["packages"]:
        for source in package["sources"]:
            source["path"] = _verified_path(folder, source["path"], source["sha256"])
            source.pop("first_page_identity_excerpt", None)
            source.pop("acquisition", None)
            source_count += 1
            pages += source["pages"]
    public["counts"] = {
        "distinct_project_or_record_packages": len(public["packages"]),
        "unchanged_original_pdfs": source_count,
        "physical_pages": pages,
        "completed_manufacturer_revision_cycles_established": 0,
    }

    native_folder = LOCAL / "official-native-exports"
    native = json.loads((native_folder / "manifest.json").read_text())
    native["schema_version"] = "rivet.native-vendor-source-catalog.v1"
    for item in native["items"]:
        item["path"] = _verified_path(native_folder, item["path"], item["sha256"])
    for item in native["documentation"]:
        item["path"] = _verified_path(native_folder, item.pop("file"), item["sha256"])
    native["report_export_availability"] = {
        "archive_inventory_checked": True,
        "nested_archives_checked": True,
        "csv_reports_found": 0,
        "xls_reports_found": 0,
        "xlsx_reports_found": 0,
        "pdf_reports_found_in_archives": 0,
        "fresh_cad_exports_performed": 0,
        "eplan_native_or_report_files_acquired": 0,
        "quick_reference_pdf_is_a_report_export": False,
    }
    native["local_exporter_check"] = {
        "checked_on": "2026-10-18",
        "application_locations_checked": ["/Applications", "~/Applications"],
        "autocad_electrical_application_found": False,
        "eplan_application_found": False,
        "other_application_observed": "Autodesk Fusion",
        "commands_not_found_on_path": [
            "AutoCAD", "EPLAN", "eplan", "ODAFileConverter", "dwgread",
            "dwg2dxf", "dwgconvert", "wine", "wine64", "QCAD", "qcad", "LibreCAD",
        ],
        "verified_runnable_licensed_exporter": False,
        "scope": (
            "Inventory of the stated application locations and PATH, not proof "
            "that no other host, virtual machine or installation exists. Fusion "
            "presence does not establish AutoCAD Electrical export capability."
        ),
    }
    native["platform_and_rights_sources"] = [
        {
            "url": "https://help.autodesk.com/view/ACD/2026/ENU/?caas=caas%2Fsfdcarticles%2Fsfdcarticles%2FSystem-requirements-for-AutoCAD-2026-including-Specialized-Toolsets.html",
            "finding": "Official AutoCAD 2026 specialized-toolset requirements identify Windows.",
        },
        {
            "url": "https://eplan.help/en-us/infoportal/content/swreqs/Content/htm/SwReqs_k_win10_2025.htm",
            "finding": "Official EPLAN requirements describe supported Windows environments.",
        },
        {
            "url": "https://www.autodesk.com/company/legal-notices-trademarks/access-use/website-terms-of-use",
            "finding": (
                "Website terms restrict use of downloadable materials, subject to "
                "applicable additional terms. No open licence, commercial benchmark, "
                "training, modification or redistribution permission is inferred."
            ),
        },
        {
            "url": "https://www.autodesk.com/company/legal-notices-trademarks/intellectual-property/copyright",
            "finding": "Different material categories can have different terms; no general Creative Commons licence is assigned to the AU ZIPs.",
        },
    ]
    native["rights_status"] = (
        "Publisher source and file identity established; applicable permission for "
        "commercial evaluation, model training or redistribution has not been "
        "established. No original files are included in this metadata registry."
    )

    DESTINATION.mkdir(parents=True, exist_ok=True)
    for filename, data in [
        ("acquisition-catalog.json", public),
        ("native-vendor-sources.json", native),
    ]:
        (DESTINATION / filename).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )
    return public["counts"]


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2))
