"""Transformer phase-change preflight, with deterministic electrical arithmetic.

This prototype reads printed claims and evaluates explicit what-if assumptions.
It never treats a unit's phase count as the bank's, selects protection, changes
CAD/BOM files, or supplies engineering approval. No model or network call occurs.
"""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Literal

import pymupdf
from pydantic import Field

from backend.domain.service import digest
from backend.equipment.pdf_batches import PDF_MAX_PAGES
from backend.orders.schemas import StrictModel

VERSION = "rivet.transformer-phase-preflight.v1"
FORMULA_REF = "https://www.se.com/us/en/faqs/FA101694/"
SUPPLY_REF = "https://www.se.com/us/en/faqs/FA91854/"


class PhaseScenario(StrictModel):
    target: Literal[
        "unspecified", "single_phase", "split_phase", "three_phase", "two_phase"
    ] = "unspecified"
    supply: Literal["unknown", "single_phase", "three_phase"] = "unknown"
    total_kva: float | None = Field(
        default=None, ge=1e-6, le=1e9, allow_inf_nan=False, strict=True
    )
    terminal_voltage_v: float | None = Field(
        default=None, ge=1e-3, le=1e7, allow_inf_nan=False, strict=True
    )
    balanced_load: bool = False


def current_calculation(total_kva, voltage_v, phases):
    """Terminal current only; no conductor, breaker or transformer selection."""
    if (
        phases not in {1, 3}
        or isinstance(phases, bool)
        or any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or v <= 0
            for v in (total_kva, voltage_v)
        )
    ):
        raise ValueError(
            "Positive finite kVA/voltage and explicit 1 or 3 phases required"
        )
    if not 1e-6 <= total_kva <= 1e9 or not 1e-3 <= voltage_v <= 1e7:
        raise ValueError("The input exceeds this prototype's numerical range")
    factor = 1 if phases == 1 else math.sqrt(3)
    amps = total_kva * 1000 / (factor * voltage_v)
    # Separate inverse squared-power check; does not call the forward function.
    residual = abs(
        ((total_kva * 1000) ** 2 - phases * voltage_v**2 * amps**2)
        / (total_kva * 1000) ** 2
    )
    return {
        "total_kva": total_kva,
        "terminal_voltage_v": voltage_v,
        "phases": phases,
        "current_a": amps,
        "formula": "I = S_VA / V"
        if phases == 1
        else "I_line = S_total_VA / (sqrt(3) * V_LL)",
        "arithmetic_recheck": {
            "relative_residual": residual,
            "passed": residual < 1e-10,
        },
        "reference": FORMULA_REF,
        "engineering_verified": False,
    }


PATTERNS = (
    (
        "unit_phase_count",
        r"\bPhase:\s*(Single|Three|1|3)[ -]Phase",
        lambda m: 1 if m[1].lower() in {"single", "1"} else 3,
    ),
    ("unit_phase_count", r"\bNumber of Phases\s*\n?\s*([13])\b", lambda m: int(m[1])),
    ("bank_phase_count", r"\b(?:three|3)[ -]phase bank\b", lambda m: 3),
    (
        "bank_unit_count",
        r"\b(?:three|3) single[ -]\s*phase transformer units\b",
        lambda m: 3,
    ),
    ("winding_count", r"\b3 Separate Windings\b", lambda m: 3),
    (
        "primary_wye_voltages",
        r"\bPrimary Voltage:\s*([\d,]+)Y/([\d,]+)",
        lambda m: {
            "line_line_v": float(m[1].replace(",", "")),
            "line_neutral_v": float(m[2].replace(",", "")),
        },
    ),
    (
        "secondary_wye_voltages",
        r"\bSecondary Voltage:\s*([\d,]+)Y/([\d,]+)",
        lambda m: {
            "line_line_v": float(m[1].replace(",", "")),
            "line_neutral_v": float(m[2].replace(",", "")),
        },
    ),
    (
        "tertiary_voltage_v",
        r"\bTertiary Voltage:\s*([\d,]+)\s*V?",
        lambda m: float(m[1].replace(",", "")),
    ),
    (
        "capacity_without_fans_mva",
        r"\bCapacity \(w/o Fans\):\s*([\d.]+)\s*MVA",
        lambda m: float(m[1]),
    ),
    (
        "bank_capacity_mva",
        r"\b(?:bank|system) is rated\s*([\d.]+)\s*MVA",
        lambda m: float(m[1]),
    ),
    (
        "primary_reported_current",
        r"\bPrimary Line Continuous Current Ratings \(at ([\d.]+) MVA\):\s*([\d,]+)\s*A\b",
        lambda m: {
            "rating_mva": float(m[1]),
            "current_a": float(m[2].replace(",", "")),
        },
    ),
    (
        "secondary_reported_current",
        r"\bSecondary Line Continuous Current Ratings \(at ([\d.]+) MVA\):\s*([\d,]+)\s*A\b",
        lambda m: {
            "rating_mva": float(m[1]),
            "current_a": float(m[2].replace(",", "")),
        },
    ),
)


def read_claims(sources):
    claims, coverage, manifest = [], [], []
    for source in sources:
        path = Path(source.path)
        if (
            path.is_symlink()
            or hashlib.sha256(path.read_bytes()).hexdigest() != source.sha256
        ):
            raise ValueError("A source changed; use the unchanged authorized original")
        manifest.append(
            {
                k: getattr(source, k)
                for k in ("id", "name", "role", "revision", "sha256")
            }
        )
        if path.suffix.lower() != ".pdf":
            coverage.append(
                {"document_id": source.id, "status": "not_read_by_phase_reader"}
            )
            continue
        with pymupdf.open(path) as pdf:
            for index, page in enumerate(pdf):
                if index >= PDF_MAX_PAGES:
                    coverage.append(
                        {
                            "document_id": source.id,
                            "status": "deferred",
                            "pages": len(pdf) - index,
                        }
                    )
                    break
                # FreeText/reviewer marks are requests, not installed design facts.
                annotation_boxes = [
                    a.rect
                    for a in page.annots() or []
                    if a.type[1] in {"FreeText", "Text"}
                ]
                lines = []
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        box = pymupdf.Rect(line["bbox"])
                        if any(
                            (box & a).get_area() > box.get_area() * 0.15
                            for a in annotation_boxes
                        ):
                            continue
                        text = "".join(sp["text"] for sp in line["spans"])
                        # Match rows in the visible page orientation. CAD pages
                        # can store vertical text under a 90-degree page rotation.
                        lines.append((text, box * page.rotation_matrix))
                text, positions = "", []
                for line, box in lines:
                    positions.append((len(text), len(text) + len(line), box))
                    text += line + "\n"
                # CAD PDF text often stores a label and its value in separate
                # objects, far apart in extraction order. Use a tightly aligned
                # explicit row; never take a nearby unrelated phase number.
                for line, label_box in lines:
                    if line.strip().casefold() != "number of phases":
                        continue
                    values = [
                        (v.strip(), box)
                        for v, box in lines
                        if v.strip() in {"1", "3"}
                        and 0 <= box.x0 - label_box.x1 <= page.rect.width * 0.25
                        and abs((box.y0 + box.y1 - label_box.y0 - label_box.y1) / 2)
                        <= min(box.height, label_box.height) * 0.4
                    ]
                    if len(values) == 1:
                        value, value_box = values[0]
                        claims.append(
                            {
                                "id": digest(
                                    [
                                        source.id,
                                        source.sha256,
                                        index,
                                        "unit_phase_row",
                                        list(label_box),
                                    ]
                                ),
                                "field": "unit_phase_count",
                                "value": int(value),
                                "text": f"{line.strip()} {value}",
                                "kind": "printed_claim_not_approval",
                                "reading_method": "spatially_aligned_explicit_row",
                                "evidence": {
                                    "document_id": source.id,
                                    "name": source.name,
                                    "sha256": source.sha256,
                                    "page": index + 1,
                                    "bbox": list(label_box | value_box),
                                    "coordinate_space": "visible_page_points",
                                    "revision": source.revision,
                                    "role": source.role,
                                },
                            }
                        )
                coverage.append(
                    {
                        "document_id": source.id,
                        "page": index + 1,
                        "selectable_text_characters": len(text),
                        "embedded_images": len(page.get_images()),
                        "graphics_verified": False,
                    }
                )
                for field, pattern, convert in PATTERNS:
                    for match in re.finditer(pattern, text, re.IGNORECASE):
                        boxes = [
                            b
                            for start, end, b in positions
                            if start < match.end() and end > match.start()
                        ]
                        area = boxes[0]
                        for b in boxes[1:]:
                            area = area | b
                        claims.append(
                            {
                                "id": digest(
                                    [
                                        source.id,
                                        source.sha256,
                                        index,
                                        field,
                                        match.start(),
                                    ]
                                ),
                                "field": field,
                                "value": convert(match),
                                "text": match[0],
                                "kind": "printed_claim_not_approval",
                                "evidence": {
                                    "document_id": source.id,
                                    "name": source.name,
                                    "sha256": source.sha256,
                                    "page": index + 1,
                                    "bbox": list(area),
                                    "coordinate_space": "visible_page_points",
                                    "revision": source.revision,
                                    "role": source.role,
                                },
                            }
                        )
    return {"claims": claims, "coverage": coverage, "sources": manifest}


def analyze(extracted, scenario=None):
    scenario = scenario or PhaseScenario()
    claims = extracted["claims"]

    def selected(field):
        return [c for c in claims if c["field"] == field]

    def unique(field):
        rows = selected(field)
        values = {digest(c["value"]) for c in rows}
        return rows[0]["value"] if len(values) == 1 else None

    findings, comparisons, voltage_checks = [], [], []
    if any(c["value"] == 1 for c in selected("unit_phase_count")) and any(
        c["value"] == 3 for c in selected("bank_phase_count")
    ):
        findings.append(
            {
                "code": "unit_and_bank_are_different",
                "detail": "A single-phase unit and a three-phase bank are separately described. Do not treat this as proof that the existing system needs conversion.",
                "claim_ids": [
                    c["id"]
                    for c in claims
                    if c["field"]
                    in {"unit_phase_count", "bank_phase_count", "bank_unit_count"}
                ],
            }
        )
    for winding in ("primary", "secondary"):
        pair = unique(winding + "_wye_voltages")
        if not pair:
            continue
        residual = abs(
            pair["line_line_v"] / (math.sqrt(3) * pair["line_neutral_v"]) - 1
        )
        voltage_checks.append(
            {
                "winding": winding,
                **pair,
                "relative_residual": residual,
                "consistent_with_wye_ratio": residual < 0.005,
                "scope": "Arithmetic consistency only; not connection verification",
                "claim_ids": [c["id"] for c in selected(winding + "_wye_voltages")],
            }
        )
        for claim in selected(winding + "_reported_current"):
            rating = claim["value"]["rating_mva"] * 1000
            bank = current_calculation(rating, pair["line_line_v"], 3)
            per_unit = current_calculation(rating, pair["line_neutral_v"], 1)
            reported = claim["value"]["current_a"]
            comparisons.append(
                {
                    "winding": winding,
                    "printed_current_a": reported,
                    "printed_rating_mva": rating / 1000,
                    "if_rating_is_total_bank": bank,
                    "if_rating_is_each_single_phase_unit": per_unit,
                    "printed_to_total_bank_current_ratio": round(
                        reported / bank["current_a"], 6
                    ),
                    "classification": "rating_basis_needs_confirmation",
                    "engineering_error_proven": False,
                    "claim_ids": [
                        claim["id"],
                        *[c["id"] for c in selected(winding + "_wye_voltages")],
                    ],
                }
            )
    calculations, blockers = [], []
    if scenario.target == "unspecified":
        blockers.append(
            {
                "code": "target_unspecified",
                "request": "Clarify the exact requested phase configuration, terminal voltages, loading and equipment boundary.",
            }
        )
    if scenario.target == "two_phase":
        blockers.append(
            {
                "code": "two_phase_ambiguous",
                "request": "Confirm whether two-phase means split-phase, two poles or a genuine two-phase system. No automatic reinterpretation is made.",
            }
        )
    if scenario.target == "split_phase":
        blockers.append(
            {
                "code": "split_phase_loads_unknown",
                "request": "Provide center-tap/neutral arrangement and individual leg loads; total kVA alone cannot establish leg/neutral currents.",
            }
        )
    if scenario.target == "three_phase" and scenario.supply == "single_phase":
        blockers.append(
            {
                "code": "passive_transformer_cannot_create_three_phases",
                "request": "Provide a suitable three-phase supply or an engineered conversion-system design. A transformer drawing edit alone cannot create the missing phases.",
                "reference": SUPPLY_REF,
            }
        )
    if scenario.target in {"single_phase", "three_phase"}:
        if scenario.total_kva is None or scenario.terminal_voltage_v is None:
            blockers.append(
                {
                    "code": "scenario_ratings_missing",
                    "request": "Provide explicit total system kVA and terminal voltage; for three-phase use line-to-line voltage.",
                }
            )
        elif scenario.target == "three_phase" and not scenario.balanced_load:
            blockers.append(
                {
                    "code": "balanced_load_unconfirmed",
                    "request": "Supply per-phase loads or explicitly choose a balanced-load what-if assumption.",
                }
            )
        else:
            calculations.append(
                {
                    **current_calculation(
                        scenario.total_kva,
                        scenario.terminal_voltage_v,
                        1 if scenario.target == "single_phase" else 3,
                    ),
                    "basis": "Caller-supplied what-if, not an approved customer requirement",
                }
            )
    blockers += [
        {
            "code": "rating_basis_unconfirmed",
            "request": "Confirm which MVA and voltage/current ratings apply to one unit versus the complete bank, including cooling and temperature-rise basis.",
        },
        {
            "code": "design_inputs_needed",
            "request": "Provide the approved connection/vector-group and neutral/grounding diagram, load schedule, fault/protection requirements and manufacturer design rules.",
        },
        {
            "code": "native_inputs_needed",
            "request": "Provide original editable CAD with references and matching BOM/CAD reports; PDFs do not expose a verified editable terminal graph.",
        },
        {
            "code": "transformer_conversion_writer_not_implemented",
            "request": "A manufacturer-specific transformer design/connection adapter and independent checks must be implemented and validated. Supplying CAD alone does not enable conversion.",
        },
    ]
    has_native = any(
        s["role"] == "drawing" and Path(s["name"]).suffix.lower() in {".dxf", ".dwg"}
        for s in extracted["sources"]
    )
    has_bom = any(
        s["role"] == "bom" and Path(s["name"]).suffix.lower() in {".xlsx", ".csv"}
        for s in extracted["sources"]
    )
    if has_native and has_bom:
        blockers = [
            {
                "code": "native_mapping_unverified",
                "request": "CAD and BOM files are present, but their transformer/terminal mappings are unverified. Validate the manufacturer-specific adapter instead of requesting duplicate files.",
            }
            if b["code"] == "native_inputs_needed"
            else b
            for b in blockers
        ]
    impact = [
        {"area": area, "work": work, "status": "not_executed"}
        for area, work in (
            (
                "connections",
                "Determine unit/bank topology, terminal mappings, winding connections and polarity/vector group.",
            ),
            (
                "ratings",
                "Recalculate winding and line currents on the confirmed unit/bank, cooling and temperature basis.",
            ),
            (
                "protection",
                "Recheck CT/PT ratios, fault duty, protection coordination and equipment suitability.",
            ),
            (
                "drawing_and_bom",
                "Update one-line, connection diagram, nameplate schedules and affected part/quantity entries together.",
            ),
            (
                "physical_design",
                "Manufacturer validates winding/core design, impedance, thermal limits, clearances and enclosure/layout effects.",
            ),
            (
                "independent_recheck",
                "Reopen saved CAD/BOM, verify connectivity and recalculate all affected values before engineer review.",
            ),
        )
    ]
    return {
        "schema_version": VERSION,
        "fingerprint": digest([VERSION, extracted, scenario.model_dump()]),
        "status": "analysis_only_blocked_for_native_changes",
        "scenario": scenario.model_dump(),
        **extracted,
        "findings": findings,
        "voltage_checks": voltage_checks,
        "rating_comparisons": comparisons,
        "scenario_calculations": calculations,
        "blockers": blockers,
        "impact_plan": impact,
        "revised_drawing_required_to_start": False,
        "revised_drawing_use": "Optional independent benchmark, withheld from the agent when testing its proposed changes.",
        "native_execution_supported": False,
        "files_changed": [],
        "engineering_approved": False,
        "production_released": False,
        "limitations": [
            "Printed claims are not a verified equipment graph.",
            "Arithmetic checks do not select protection, size conductors, validate transformer manufacture or establish regulatory compliance.",
            "No AI extraction, CAD correction or autonomous action is performed by this prototype.",
        ],
    }
