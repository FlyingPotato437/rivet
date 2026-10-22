"""Build a fictional PDU phase-change package offline. Never uses a mailbox."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

import ezdxf
import pymupdf
from openpyxl import load_workbook

from backend.engineering.pdu_phase import execute, file_sha, plan
from backend.engineering.pdu_phase_demo import (
    SOURCES,
    baseline,
    brief_pdf,
    drawing_pdf,
    fixture,
    preview_html,
    summary_pdf,
)
from backend.engineering.pdu_phase_verify import verify
from backend.equipment.native_model import set_xdata, xdata


def inject_faults(draft, catalog, request):
    """Independent saved-artifact mutations, never alternate expected output."""
    results = []
    for name in (
        "Missing third-phase conductor",
        "Stale outlet in BOM",
        "Wrong breaker pole count",
        "Conductor misses its terminal",
        "Missing native H3 terminal",
        "Stale visible nameplate",
        "Wrong internal panel phase bus",
    ):
        with tempfile.TemporaryDirectory(
            prefix="fault-check-", dir=draft.parent
        ) as tmp:
            root = Path(tmp)
            for file in ("PDU-30-Rev-B.dxf", "BOM-Rev-B.csv", "Connections-Rev-B.csv"):
                shutil.copyfile(draft / file, root / file)
            doc = ezdxf.readfile(root / "PDU-30-Rev-B.dxf")
            if name == "Missing third-phase conductor":
                entity = next(
                    e
                    for e in doc.modelspace()
                    if xdata(e)[3:7] == ["CB-M1", "T3", "XF-1", "H3"]
                )
                doc.modelspace().delete_entity(entity)
            elif name == "Stale outlet in BOM":
                with (root / "BOM-Rev-B.csv").open(newline="") as stream:
                    reader = csv.DictReader(stream)
                    rows, headers = list(reader), reader.fieldnames
                next(r for r in rows if r["equipment_tag"] == "R-7")["connection"] = (
                    "L14-30R"
                )
                with (root / "BOM-Rev-B.csv").open("w", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=headers)
                    writer.writeheader()
                    writer.writerows(rows)
            elif name == "Wrong breaker pole count":
                next(
                    e
                    for e in doc.modelspace().query("INSERT")
                    if e.get_attrib_text("EQUIPMENT_TAG") == "CB-M1"
                ).get_attrib("POLES").dxf.text = "2"
            elif name == "Conductor misses its terminal":
                ent = next(
                    e for e in doc.modelspace() if xdata(e)[:2] == ["pdu_owned", "wire"]
                )
                points = list(ent.get_points())
                points[0] = (points[0][0] + 8, *points[0][1:])
                ent.set_points(points)
            elif name == "Missing native H3 terminal":
                insert = next(
                    e
                    for e in doc.modelspace().query("INSERT")
                    if e.get_attrib_text("EQUIPMENT_TAG") == "XF-1"
                )
                block = doc.blocks.get(insert.dxf.name)
                pin = next(e for e in block if xdata(e) == ["terminal", "H3"])
                block.delete_entity(pin)
            elif name == "Wrong internal panel phase bus":
                insert = next(
                    e
                    for e in doc.modelspace().query("INSERT")
                    if e.get_attrib_text("EQUIPMENT_TAG") == "PNL-1"
                )
                block = doc.blocks.get(insert.dxf.name)
                link = next(
                    e for e in block if xdata(e) == ["pdu_internal", "L1", "C1_A"]
                )
                pin = next(e for e in block if xdata(e) == ["terminal", "L2"])
                point = pin.dxf.location
                points = list(link.get_points())
                points[0] = (point.x, point.y, *points[0][2:])
                points[1] = (points[1][0], point.y, *points[1][2:])
                link.set_points(points)
                set_xdata(link, ["pdu_internal", "L2", "C1_A"])
            else:
                ent = next(
                    e
                    for e in doc.modelspace().query("TEXT")
                    if e.dxf.text.startswith("OUTPUT:")
                )
                ent.dxf.text = "OUTPUT: 120/240 V / 1 PH / 3W+PE / 60 Hz / REV A"
            doc.saveas(root / "PDU-30-Rev-B.dxf")
            report = verify(
                root / "PDU-30-Rev-B.dxf",
                root / "BOM-Rev-B.csv",
                root / "Connections-Rev-B.csv",
                catalog,
                request,
            )
            results.append(
                {
                    "name": name,
                    "detected": not report["supported_checks_passed"],
                    "failed_check_ids": [
                        c["id"] for c in report["checks"] if not c["passed"]
                    ],
                }
            )
    return results


def workbook_data(root):
    books = []
    for revision, folder in (("A", "baseline"), ("B", "draft")):
        with (root / folder / f"BOM-Rev-{revision}.csv").open(newline="") as stream:
            records = list(csv.DictReader(stream))
        headers = [
            "Tag",
            "Part",
            "Qty",
            "Description",
            "Poles",
            "Phases",
            "Voltage (V)",
            "Capacity (kVA)",
            "Current (A)",
            "Connection",
            "Terminals",
            "Reference",
        ]
        fields = [
            "equipment_tag",
            "part_number",
            "quantity",
            "description",
            "poles",
            "phases",
            "voltage_v",
            "kva",
            "amps",
            "connection",
            "terminals",
            "source_ref",
        ]
        numbers = {"quantity", "poles", "phases", "voltage_v", "kva", "amps"}
        values = [
            [float(r[k]) if k in numbers else r[k] for k in fields] for r in records
        ]
        books.append(
            {
                "revision": revision,
                "path": f"{folder}/BOM-Rev-{revision}.xlsx",
                "headers": headers,
                "values": values,
            }
        )
    (root / "workbooks.json").write_text(
        json.dumps({"books": books, "sources": SOURCES}, indent=2)
    )


def complete_workbooks(root):
    """Use the bundled spreadsheet runtime without installing dependencies."""
    dependencies = (
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
    )
    executable, modules = dependencies / "bin/node", dependencies / "node_modules"
    if not executable.is_file() or not modules.is_dir():
        raise ValueError(
            "Bundled spreadsheet runtime unavailable; retain the verified CSV outputs."
        )
    with tempfile.TemporaryDirectory(prefix="phase-workbooks-") as temporary:
        work = Path(temporary)
        (work / "node_modules").symlink_to(modules, target_is_directory=True)
        shutil.copyfile(
            Path(__file__).with_name("build_phase_workbooks.mjs"), work / "build.mjs"
        )
        subprocess.run(
            [str(executable), str(work / "build.mjs"), str(root)],
            check=True,
            timeout=90,
        )


def finalize(root):
    root = root.resolve()
    catalog = json.loads((root / "catalog.json").read_text())
    request = json.loads((root / "request.json").read_text())
    report = json.loads((root / "draft/recheck.json").read_text())
    for name, sha in report["artifact_sha256"].items():
        if file_sha(root / "draft" / name) != sha:
            raise ValueError("A checked output changed before packaging.")
    for revision, folder in (("A", "baseline"), ("B", "draft")):
        if not (root / folder / f"BOM-Rev-{revision}.xlsx").is_file():
            raise ValueError(
                "Generate the Excel exports with the bundled spreadsheet builder first."
            )
    workbook_checks = []
    for book in json.loads((root / "workbooks.json").read_text())["books"]:
        workbook = load_workbook(root / book["path"], read_only=True, data_only=True)
        try:
            sheet = workbook["BOM"]
            actual = [
                list(row)
                for row in sheet.iter_rows(
                    min_row=6, max_row=26, min_col=1, max_col=12, values_only=True
                )
            ]
            actual = [["" if v is None else v for v in row] for row in actual]
            if actual != book["values"]:
                raise ValueError(
                    "Saved Excel rows differ from the verified CSV-derived inputs."
                )
            workbook_checks.append(
                {
                    "revision": book["revision"],
                    "rows_checked": len(actual),
                    "passed": True,
                }
            )
        finally:
            workbook.close()
    (root / "workbook-recheck.json").write_text(json.dumps(workbook_checks, indent=2))
    for revision, folder in (("A", "baseline"), ("B", "draft")):
        with pymupdf.open(root / folder / f"PDU-30-Rev-{revision}.pdf") as pdf:
            text = "\n".join(page.get_text() for page in pdf)
            required = "208Y/120 V / 3 PH" if revision == "B" else "120/240 V / 1 PH"
            if len(pdf) != 3 or required not in text:
                raise ValueError(
                    "Drawing PDF lacks the saved CAD's correct selectable nameplate."
                )
    checked = verify(
        root / "draft/PDU-30-Rev-B.dxf",
        root / "draft/BOM-Rev-B.csv",
        root / "draft/Connections-Rev-B.csv",
        catalog,
        request,
    )
    if not checked["supported_checks_passed"]:
        raise ValueError("Final native-file verification failed.")
    message = EmailMessage(policy=SMTP)
    message["Subject"] = (
        "FICTIONAL DEMO / PDU-30 / proposed three-phase assembly / Rev B"
    )
    message["X-Unsent"] = "1"
    message.set_content(
        "Prepared for engineering review.\n\nThe fictional PDU has draft three-phase components, terminal connections and a matching BOM. Saved-file checks passed. Attached: DXF, vector PDF, Excel BOM, connection report and limitations summary.\n\nThree-phase supply and a compatible replacement for the original 240 V C7 load are explicit scenario assumptions. Protection, conductor sizing, grounding, physical fit and manufacturer suitability need engineering review. Not approved or released.\n"
    )
    for path in (
        root / "draft/PDU-30-Rev-B.dxf",
        root / "draft/PDU-30-Rev-B.pdf",
        root / "draft/BOM-Rev-B.xlsx",
        root / "draft/Connections-Rev-B.csv",
        root / "Phase-change-summary.pdf",
    ):
        subtype = (
            "pdf"
            if path.suffix == ".pdf"
            else "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if path.suffix == ".xlsx"
            else "octet-stream"
        )
        message.add_attachment(
            path.read_bytes(),
            maintype="application",
            subtype=subtype,
            filename=path.name,
        )
    (root / "Review-draft.eml").write_bytes(message.as_bytes())
    with zipfile.ZipFile(
        root / "Phase-change-demo.zip", "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for path in sorted(root.rglob("*")):
            if (
                path.is_file()
                and path.suffix not in {".zip", ".ndjson"}
                and path.name != "workbooks.json"
            ):
                archive.write(
                    path, str(Path("Rivet-phase-prototype") / path.relative_to(root))
                )
    print(
        json.dumps(
            {
                "package": str(root),
                "preview": str(root / "index.html"),
                "native_checks": "passed",
                "devices": checked["devices_checked"],
                "connections": checked["connections_checked"],
                "email": "unsent .eml only; live Outlook unchanged",
                "engineering_approved": False,
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("output")
        / ("phase-conversion-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")),
    )
    parser.add_argument("--finalize", type=Path)
    parser.add_argument(
        "--complete",
        action="store_true",
        help="Build Excel exports and the review email/archive in the same run.",
    )
    parser.add_argument(
        "--inputs",
        type=Path,
        help="Existing fictional package with catalog.json, request.json and baseline/; originals are copied, never overwritten.",
    )
    args = parser.parse_args()
    if args.finalize:
        finalize(args.finalize)
        return
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    catalog, selected, loads, request = fixture()
    if args.inputs:
        catalog = json.loads((args.inputs / "catalog.json").read_text())
        request = json.loads((args.inputs / "request.json").read_text())
    for name, data in (
        ("catalog.json", catalog),
        ("request.json", request),
        ("source-manifest.json", SOURCES),
    ):
        (root / name).write_text(json.dumps(data, indent=2))
    if args.inputs:
        (root / "baseline").mkdir()
        for name in ("PDU-30-Rev-A.dxf", "BOM-Rev-A.csv", "Connections-Rev-A.csv"):
            shutil.copyfile(args.inputs / "baseline" / name, root / "baseline" / name)
    else:
        baseline(root / "baseline", catalog, selected, loads)
    before = root / "baseline"
    change_plan = plan(
        before / "PDU-30-Rev-A.dxf", before / "BOM-Rev-A.csv", catalog, request
    )
    (root / "plan.json").write_text(json.dumps(change_plan, indent=2))
    result = execute(
        before / "PDU-30-Rev-A.dxf",
        before / "BOM-Rev-A.csv",
        catalog,
        request,
        change_plan,
        root / "draft",
    )
    faults = inject_faults(root / "draft", catalog, request)
    if not all(f["detected"] for f in faults):
        raise ValueError("A deliberate output defect escaped detection.")
    (root / "fault-injection.json").write_text(json.dumps(faults, indent=2))
    for revision, folder in (("A", "baseline"), ("B", "draft")):
        drawing_pdf(
            root / folder / f"PDU-30-Rev-{revision}.dxf",
            root / folder / f"PDU-30-Rev-{revision}.pdf",
        )
        with pymupdf.open(root / folder / f"PDU-30-Rev-{revision}.pdf") as pdf:
            pdf[0].get_pixmap(dpi=110).save(
                root / ("before.png" if revision == "A" else "after.png")
            )
    brief_pdf(root / "Design-brief.pdf", request)
    summary_pdf(root / "Phase-change-summary.pdf", result, change_plan, faults)
    preview_html(root, result, change_plan, faults)
    workbook_data(root)
    if args.complete:
        complete_workbooks(root)
        finalize(root)
    print(
        json.dumps(
            {
                "out": str(root),
                "devices": result["devices_checked"],
                "connections": result["connections_checked"],
                "changed_components": len(change_plan["changes"]),
                "faults_detected": f"{sum(f['detected'] for f in faults)}/{len(faults)}",
                "next": "Ready in index.html"
                if args.complete
                else "Build Excel exports, then run with --finalize.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
