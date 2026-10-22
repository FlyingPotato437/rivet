"""Bounded audit of extracted native DIMENSION and unit-text evidence.

This module accepts lists, tuples, or mappings of already-extracted records;
it never opens or traverses a drawing. It reports consistency between native
dimension measurements and their definition-point distances separately from
physical unit calibration. A unit label alone cannot establish a physical
scale because native values need not be expressed in the labeled display unit.
"""

from __future__ import annotations

import itertools
import math
import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

_UNIT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("millimetre", re.compile(r"(?i)(?<![\w])(?:mm|millimet(?:er|re)s?)(?![\w])")),
    ("centimetre", re.compile(r"(?i)(?<![\w])(?:cm|centimet(?:er|re)s?)(?![\w])")),
    ("metre", re.compile(r"(?i)(?<![\w])(?:m|met(?:er|re)s?)(?![\w])")),
    # Bare "in" is intentionally excluded: it is too common in English.
    ("inch", re.compile(r'(?i)(?<![\w])(?:inches?|\")(?![\w])')),
    ("inch", re.compile(r"(?i)(?<![\w])in\.(?![\w])")),
    ("inch", re.compile(r"(?i)\bunits?\s*[:=]\s*in\b")),
    ("foot", re.compile(r"(?i)(?<![\w])(?:ft|feet|foot|')(?![\w])")),
)


def _items(value: Any, limit: int, *, kind: str) -> tuple[list[Mapping[str, Any]], bool]:
    """Bound a list/dict without materializing all mapping entries."""
    if isinstance(value, Mapping):
        # A single extracted record is accepted, as is handle -> record data.
        single_keys = {"handle", "text", "string"}
        single_keys.add("actual_measurement" if kind == "dimensions" else "value")
        if any(key in value for key in single_keys):
            return [value], False
        sampled = list(itertools.islice(value.items(), limit + 1))
        truncated = len(sampled) > limit
        result = []
        for key, record in sampled[:limit]:
            if isinstance(record, Mapping):
                item = dict(record)
                # A mapping key can be a real CAD handle, but never synthesize
                # an identity when the caller supplied an anonymous list item.
                item.setdefault("handle", str(key))
                result.append(item)
        return result, truncated
    if isinstance(value, (list, tuple)):
        truncated = len(value) > limit
        return [item for item in value[:limit] if isinstance(item, Mapping)], truncated
    return [], False


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _unit_matches(text: str) -> list[str]:
    return sorted({name for name, pattern in _UNIT_PATTERNS if pattern.search(text)})


def audit_cad_units(
    dimensions: Any,
    unit_texts: Any,
    header_insunits: Any = None,
    *,
    relative_tolerance: float = 0.03,
    max_items: int = 20_000,
) -> dict[str, Any]:
    """Audit explicit labels and native dimension/point consistency.

    A frame's measurement-to-point ratio is verified only when at least two
    distinct, handled dimensions with explicit scope and frame IDs agree. A
    numeric text override contributes no ratio evidence. Even a verified
    ratio plus a unit label does not calibrate physical displacement scale;
    the output therefore keeps physical unit calibration insufficient unless
    an independent calibration source is added in a future interface.
    """
    if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 1:
        raise ValueError("max_items must be a positive integer")
    tolerance = _finite_number(relative_tolerance)
    if tolerance is None or tolerance < 0 or tolerance >= 1:
        raise ValueError("relative_tolerance must be finite and in [0, 1)")

    raw_dimensions, dimensions_truncated = _items(dimensions, max_items, kind="dimensions")
    raw_unit_texts, unit_texts_truncated = _items(unit_texts, max_items, kind="unit_texts")
    unit_evidence: list[dict[str, Any]] = []
    unit_by_frame: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in raw_unit_texts:
        text = str(_first(record, "text", "value", "string") or "")
        matches = _unit_matches(text)
        handle_raw = _first(record, "handle")
        scope_raw = _first(record, "scope_id", "scope", "layout")
        frame_raw = _first(record, "frame_id", "view_id", "viewport_id")
        scope = str(scope_raw).strip() if scope_raw is not None else ""
        frame = str(frame_raw).strip() if frame_raw is not None else ""
        referenceable = bool(handle_raw is not None and scope and frame)
        entry = {
            "handle": handle_raw,
            "text": text[:500],
            "units": matches,
            "scope_id": scope or None,
            "frame_id": frame or None,
            "explicit": bool(matches),
            "referenceable": referenceable,
        }
        unit_evidence.append(entry)
        if matches and referenceable:
            unit_by_frame[(scope, frame)].update(matches)

    dimension_evidence: list[dict[str, Any]] = []
    eligible_by_frame: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen_handles: set[str] = set()
    for record in raw_dimensions:
        handle_raw = _first(record, "handle")
        handle = str(handle_raw).strip() if handle_raw is not None else ""
        scope_raw = _first(record, "scope_id", "scope", "layout")
        frame_raw = _first(record, "frame_id", "view_id", "viewport_id")
        scope = str(scope_raw).strip() if scope_raw is not None else ""
        frame = str(frame_raw).strip() if frame_raw is not None else ""
        measurement = _finite_number(_first(record, "actual_measurement", "measurement", "measured_value"))
        point_distance = _finite_number(_first(record, "point_distance", "definition_point_distance"))
        override = _first(record, "text_override", "override_text", "dimension_text")
        has_override = override not in (None, "", "<> ", "<>")
        display = _first(record, "display_text", "printed_value")

        reason = None
        if not handle:
            reason = "missing real dimension handle"
        elif handle in seen_handles:
            reason = "duplicate dimension handle"
        elif not scope or not frame:
            reason = "missing scope or frame identity"
        elif measurement is None or measurement <= 0:
            reason = "missing, malformed, non-finite, or non-positive native measurement"
        elif has_override:
            reason = "text override is not independent measurement evidence"
        elif point_distance is None or point_distance <= 0:
            reason = "missing, malformed, non-finite, or non-positive definition-point distance"
        seen_handles.add(handle) if handle else None
        entry = {
            "handle": handle or None,
            "actual_measurement": measurement,
            "point_distance": point_distance,
            "text_override": str(override)[:500] if override is not None else None,
            "display_text": str(display)[:500] if display is not None else None,
            "scope_id": scope or None,
            "frame_id": frame or None,
            "eligible_for_measurement_point_consistency": reason is None,
            "exclusion_reason": reason,
        }
        dimension_evidence.append(entry)
        if reason is None:
            eligible_by_frame[(scope, frame)].append(entry)

    frames: list[dict[str, Any]] = []
    for (scope, frame), items in sorted(eligible_by_frame.items()):
        ratios = [item["actual_measurement"] / item["point_distance"] for item in items]
        if not all(math.isfinite(value) and value > 0 for value in ratios):
            frames.append({
                "scope_id": scope,
                "frame_id": frame,
                "status": "insufficient",
                "ratio": None,
                "ratios": ratios,
                "dimension_handles": [item["handle"] for item in items],
                "unit_evidence_handles": [],
                "units": [],
                "reason": "non-finite measurement-to-point ratio",
            })
            continue
        mean_ratio = sum(ratios) / len(ratios)
        agrees = len(items) >= 2 and all(
            abs(value - mean_ratio) / mean_ratio <= tolerance for value in ratios
        )
        applicable_units = set(unit_by_frame.get((scope, frame), set()))
        applicable_unit_evidence = [
            entry for entry in unit_evidence
            if entry["referenceable"] and entry["explicit"]
            and (entry["scope_id"], entry["frame_id"]) == (scope, frame)
        ]
        frames.append({
            "scope_id": scope,
            "frame_id": frame,
            "status": "verified" if agrees else ("conflicting" if len(items) >= 2 else "insufficient"),
            "measurement_to_point_ratio": mean_ratio if agrees else None,
            "ratios": ratios,
            "dimension_handles": [item["handle"] for item in items],
            "unit_evidence_handles": [item["handle"] for item in applicable_unit_evidence],
            "units": sorted(applicable_units),
            "relative_tolerance": tolerance,
            "reason": (
                "independent native dimensions agree within tolerance"
                if agrees else "independent dimensions disagree beyond tolerance"
                if len(items) >= 2 else "at least two independent dimensions are required"
            ),
        })

    truncation = {
        "dimensions": dimensions_truncated,
        "unit_texts": unit_texts_truncated,
        "any": dimensions_truncated or unit_texts_truncated,
    }
    all_frames = {(item["scope_id"], item["frame_id"]) for item in dimension_evidence}
    verified_frames = [item for item in frames if item["status"] == "verified"]
    conflicting_frames = [item for item in frames if item["status"] == "conflicting"]
    if conflicting_frames:
        consistency_status = "conflicting"
        consistency_reason = "dimensions in the same view/frame disagree beyond tolerance"
    elif truncation["any"]:
        consistency_status = "insufficient"
        consistency_reason = "bounded input was truncated; omitted evidence could change the result"
    elif len(all_frames) > 1:
        consistency_status = "insufficient"
        consistency_reason = "dimensions span multiple scopes or frames; views are audited separately"
    elif len(verified_frames) == 1:
        consistency_status = "verified"
        consistency_reason = "multiple independent native measurements agree with point distances in one frame"
    else:
        consistency_status = "insufficient"
        consistency_reason = "insufficient independent, identified dimension/point evidence"

    explicit_unit_records = [entry for entry in unit_evidence if entry["explicit"]]
    explicit_units = sorted({unit for entry in explicit_unit_records for unit in entry["units"]})
    physical_reason = (
        "explicit labels identify display units but do not calibrate native geometry to physical displacement"
        if explicit_units else "no explicit unit label provides even display-unit context"
    )
    physical_calibration = {
        "status": "insufficient",
        "verified_physical_scale": None,
        "explicit_unit_labels": explicit_units,
        "unit_evidence_handles": [entry["handle"] for entry in explicit_unit_records if entry["handle"] is not None],
        "reason": physical_reason,
    }
    if consistency_status == "conflicting":
        status = "conflicting"
        reason = consistency_reason
    else:
        # Top-level status describes whether physical scale is established.
        status = "insufficient"
        reason = physical_reason if not truncation["any"] else consistency_reason

    return {
        "status": status,
        "reason": reason,
        "measurement_point_evidence": {
            "status": consistency_status,
            "reason": consistency_reason,
            "frames": frames,
        },
        "physical_unit_calibration": physical_calibration,
        "header_evidence": {
            "insunits": header_insunits,
            "authoritative": False,
            "reason": "INSUNITS is retained as context and never proves units or scale",
        },
        "dimension_evidence": dimension_evidence,
        "unit_evidence": unit_evidence,
        "scale_evidence": {
            "status": physical_calibration["status"],
            "verified_physical_scale": None,
            "reason": physical_reason,
        },
        "limits": {
            "max_items_per_input": max_items,
            "dimensions_seen": len(raw_dimensions),
            "unit_texts_seen": len(raw_unit_texts),
            "truncation": truncation,
        },
    }
