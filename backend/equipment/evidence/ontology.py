"""Small standards-aligned application profile, not standards certification.

CIM electrical topology and physical assembly containment are separate views.
IEC 61850 logical nodes describe functions, not physical compartments. Feature
keys are Rivet names following ETIM's typed-value pattern, not invented EF codes.
"""

from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from .schema import EDGE_KINDS, NODE_KINDS, stable_id

STANDARD_SOURCES = {
    "CIM": "https://cimug.ucaiug.org/KB/Knowledge%20Base/Use_of_the_CIM_Ontology_DistribuTech_2006.pdf",
    "CIM_modeling": "https://cimug.ucaiug.org/Model%20Manager%20Documents/Public/CIM%20Modeling%20Guide_v1.1.pdf",
    "IEC61850": "https://webstore.iec.ch/en/publication/66551",
    "IEC81346": "https://webstore.iec.ch/en/publication/64021",
    "IEEE_C37_2": "https://ieeexplore.ieee.org/document/9984140",
    "ETIM": "https://www.etim-international.com/wp-content/uploads/2019/07/ETIM-International-Guidelines-V3-2020.pdf",
}
NODE_STANDARD_MAPPING = {
    "lineup": {
        "application_class": "lineup",
        "CIM_view": "EquipmentContainer",
        "exact_normative_class": False,
    },
    "section": {
        "application_class": "physical_section",
        "CIM_view": "EquipmentContainer",
        "exact_normative_class": False,
    },
    "compartment": {
        "application_class": "physical_compartment",
        "CIM_view": "EquipmentContainer",
        "exact_normative_class": False,
    },
    "device": {
        "application_class": "device",
        "CIM_view": "ConductingEquipment only when electrically conducting; otherwise Equipment",
        "exact_normative_class": False,
    },
    "terminal": {"CIM_view": "Terminal", "exact_normative_class": False},
    "connectivity_node": {
        "CIM_view": "ConnectivityNode",
        "exact_normative_class": False,
    },
    "logical_node": {
        "IEC61850_view": "LogicalNode",
        "physical_containment": False,
        "exact_normative_class": False,
    },
    "requirement": {
        "application_class": "evidence-bound graph rule",
        "exact_normative_class": False,
    },
    "drawing_sheet": {
        "application_class": "source sheet identity; not a physical equipment container",
        "exact_normative_class": False,
    },
    "unclassified_position": {
        "application_class": "source-observed position with unknown equipment identity",
        "exact_normative_class": False,
    },
}


@dataclass(frozen=True)
class Feature:
    key: str
    value_type: str
    unit: str | None = None
    allowed_values: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    note: str = "Local application feature; no ETIM EF code asserted"


FEATURES = {
    key: Feature(key, "numeric", unit, minimum=minimum)
    for key, unit, minimum in (
        ("rated_current_a", "A", 0),
        ("trip_a", "A", 0),
        ("frame_a", "A", 0),
        ("bus_current_a", "A", 0),
        ("sensor_a", "A", 0),
        ("frequency_hz", "Hz", 0),
        ("voltage_v", "V", 0),
        ("control_voltage_v", "V", 0),
        ("interrupting_ka", "kA", 0),
        ("quantity", "each", 0),
        ("pole_count", None, 1),
        ("width_mm", "mm", 0),
        ("height_mm", "mm", 0),
    )
}
FEATURES.update(
    {
        "current_type": Feature(
            "current_type",
            "alphanumeric",
            allowed_values=("AC", "DC", "AC_DC", "unknown"),
        ),
        "position_state": Feature(
            "position_state",
            "alphanumeric",
            allowed_values=(
                "populated",
                "populated_breaker",  # Emitted by visual_reader._observations.
                "prepared_space",
                "space",
                "spare",
                "incoming_connection",
                "unknown",
            ),
        ),
        "trip_type": Feature(
            "trip_type",
            "alphanumeric",
            allowed_values=(
                "LI",
                "LSI",
                "LSIG",
                "thermal_magnetic",
                "magnetic_only",
                "electronic",
                "unknown",
            ),
        ),
        **{
            key: Feature(key, "identifier")
            for key in (
                "device_tag",
                "part_number",
                "device_frame",
                "device_type",
                "section_no",
                "circuit_no",
                "position",
                "description",
                "manufacturer",
                "product_family",
                "rating_standard",
                "item_number",
                "designation",
                "unit",
                "supplied_by",
                "location",
                "drawing_number",
                "sheet_number",
                "title",
                "date",
                "revision",
                "nameplate",
                "trip_unit",
                "pin",
                "wire_number",
                "source_lifecycle",
                "unknown",
            )
        },
        "present": Feature("present", "logical"),
        "connection": Feature("connection", "identifier"),
    }
)

UNIT_FACTORS = {
    "Hz": {"hz": 1, "khz": 1000},
    "A": {
        "a": 1,
        "amp": 1,
        "amps": 1,
        "ampere": 1,
        "amperes": 1,
        "ka": 1000,
        "ma": 0.001,
    },
    "V": {"v": 1, "vac": 1, "vdc": 1, "volt": 1, "volts": 1, "kv": 1000},
    "kA": {"ka": 1, "kaic": 1, "a": 0.001},
    "mm": {"mm": 1, "cm": 10, "m": 1000, "in": 25.4, "inch": 25.4, "inches": 25.4},
    "each": {"each": 1, "ea": 1, "pcs": 1, "piece": 1, "pieces": 1},
}


def normalize_feature_value(feature: str, value: Any, unit: str | None = None) -> dict:
    spec = FEATURES.get(feature)
    if feature == "connection" and isinstance(value, (dict, list)):
        return {"value": deepcopy(value), "unit": None, "valid": True, "reason": None}
    if spec is None:
        return {
            "value": None,
            "unit": unit,
            "valid": False,
            "reason": "feature_not_in_application_profile",
        }
    if value is None or (
        isinstance(value, str)
        and value.strip().casefold() in {"", "-", "—", "n/a", "unknown", "tbd", "?"}
    ):
        return {
            "value": None,
            "unit": spec.unit,
            "valid": False,
            "reason": "unknown_or_unprinted",
        }
    if spec.value_type == "logical":
        if isinstance(value, bool):
            return {"value": value, "unit": None, "valid": True, "reason": None}
        return {
            "value": None,
            "unit": None,
            "valid": False,
            "reason": "logical_value_required",
        }
    if spec.value_type == "numeric":
        if isinstance(value, bool):
            return {
                "value": None,
                "unit": spec.unit,
                "valid": False,
                "reason": "boolean_is_not_numeric",
            }
        parsed_unit = unit
        numeric = value
        if isinstance(value, str):
            match = re.fullmatch(
                r"\s*([+-]?(?:\d[\d,]*)(?:\.\d+)?)\s*([A-Za-z]+)?\s*", value
            )
            if not match:
                return {
                    "value": None,
                    "unit": spec.unit,
                    "valid": False,
                    "reason": "ambiguous_numeric_text",
                }
            numeric, suffix = match.groups()
            if suffix and unit and suffix.casefold() != unit.casefold():
                return {
                    "value": None,
                    "unit": spec.unit,
                    "valid": False,
                    "reason": "conflicting_printed_and_declared_units",
                }
            parsed_unit = suffix or unit
        try:
            number = float(Decimal(str(numeric).replace(",", "")))
        except (InvalidOperation, ValueError, TypeError):
            return {
                "value": None,
                "unit": spec.unit,
                "valid": False,
                "reason": "numeric_value_required",
            }
        if not math.isfinite(number):
            return {
                "value": None,
                "unit": spec.unit,
                "valid": False,
                "reason": "non_finite_value",
            }
        if parsed_unit:
            factors = UNIT_FACTORS.get(spec.unit, {})
            if str(parsed_unit).strip().casefold() not in factors:
                return {
                    "value": None,
                    "unit": spec.unit,
                    "valid": False,
                    "reason": "incompatible_or_unknown_units",
                }
            number *= factors[str(parsed_unit).strip().casefold()]
        if spec.minimum is not None and number < spec.minimum:
            return {
                "value": None,
                "unit": spec.unit,
                "valid": False,
                "reason": "below_feature_domain",
            }
        if feature in {"pole_count", "quantity"} and not number.is_integer():
            return {
                "value": None,
                "unit": spec.unit,
                "valid": False,
                "reason": "integer_value_required",
            }
        return {
            "value": int(number) if number.is_integer() else number,
            "unit": spec.unit,
            "valid": True,
            "reason": None,
        }
    text = str(value).strip()
    if spec.allowed_values:
        if text not in spec.allowed_values:
            key = re.sub(r"[^a-z0-9]+", "", text.casefold())
            matches = [
                candidate
                for candidate in spec.allowed_values
                if re.sub(r"[^a-z0-9]+", "", candidate.casefold()) == key
            ]
            if len(matches) != 1:
                return {
                    "value": None,
                    "unit": None,
                    "valid": False,
                    "reason": "ambiguous_enum_spelling"
                    if matches
                    else "value_not_in_feature_enum",
                }
            text = matches[0]
        if text.casefold() == "unknown":
            return {
                "value": None,
                "unit": spec.unit,
                "valid": False,
                "reason": "unknown_or_unprinted",
            }
    return {"value": text, "unit": spec.unit, "valid": True, "reason": None}


def make_node(
    node_id: str,
    kind: str,
    scope_key: str,
    *,
    lineup_id: str | None = None,
    identifiers: dict | None = None,
    source_refs: list[dict] | None = None,
    metadata: dict | None = None,
) -> dict:
    if kind not in NODE_KINDS or not scope_key:
        raise ValueError("Unknown node kind or missing scope")
    return {
        "id": node_id,
        "kind": kind,
        "scope_key": scope_key,
        "lineup_id": lineup_id,
        "identifiers": identifiers or {},
        "source_refs": source_refs or [],
        "metadata": metadata or {},
        "state": "observed",
    }


def make_edge(
    kind: str,
    from_id: str,
    to_id: str,
    *,
    claim_ids: list[str] | None = None,
    metadata: dict | None = None,
) -> dict:
    if kind not in EDGE_KINDS or not from_id or not to_id:
        raise ValueError("Unknown edge type or endpoint")
    return {
        "id": stable_id("edge", kind, from_id, to_id, claim_ids or []),
        "kind": kind,
        "from": from_id,
        "to": to_id,
        "claim_ids": claim_ids or [],
        "metadata": metadata or {},
        "state": "observed",
    }


def validate_topology(graph: dict) -> list[dict]:
    nodes = {node["id"]: node for node in graph["nodes"]}
    issues = []
    allowed_contains = {
        "lineup": {"section", "device", "unclassified_position"},
        "section": {"compartment", "device", "unclassified_position"},
        "compartment": {"device", "unclassified_position"},
    }
    for edge in graph["edges"]:
        left, right = nodes.get(edge["from"]), nodes.get(edge["to"])
        reason = None
        if not left or not right:
            reason = "missing_edge_endpoint"
        elif left["scope_key"] != right["scope_key"]:
            reason = "cross_scope_edge"
        elif edge["kind"] == "contains" and right["kind"] not in allowed_contains.get(
            left["kind"], set()
        ):
            reason = "invalid_physical_containment"
        elif edge["kind"] == "equipment_terminal" and (
            left["kind"] != "device" or right["kind"] != "terminal"
        ):
            reason = "equipment_terminal_types_required"
        elif edge["kind"] == "terminal_connectivity_node" and (
            left["kind"] != "terminal" or right["kind"] != "connectivity_node"
        ):
            reason = "terminal_connectivity_node_types_required"
        elif edge["kind"] == "logical_function_of" and (
            left["kind"] != "logical_node" or right["kind"] != "device"
        ):
            reason = "logical_node_is_not_physical_device"
        if reason:
            issues.append({"edge_id": edge["id"], "code": reason})
    return issues


def make_requirement_rule(
    rule_id: str,
    subject_id: str,
    feature: str,
    operator: str,
    *,
    target: dict,
    source_claim_ids: list[str],
    state: str = "observed",
    kind: str = "attribute_constraint",
    authority: dict | None = None,
) -> dict:
    """Requirements constrain decisions; they are not measured actual values."""
    if operator not in {"eq", "ne", "le", "lt", "ge", "gt", "in", "exists"}:
        raise ValueError("Unknown graph-rule operator")
    if not source_claim_ids or not subject_id or feature not in FEATURES:
        raise ValueError("A graph requirement needs a typed feature and source claims")
    if state not in {"observed", "proposed", "approved", "released"}:
        raise ValueError("Unknown requirement lifecycle state")
    if state in {"approved", "released"} and not authority:
        raise ValueError(
            "An authoritative requirement needs verified approval evidence"
        )
    if "value" not in target and not (
        target.get("other_subject_id") and target.get("feature")
    ):
        raise ValueError("Rule target needs a literal value or another subject feature")
    return {
        "id": rule_id,
        "kind": kind,
        "subject_id": subject_id,
        "feature": feature,
        "operator": operator,
        "target": deepcopy(target),
        "source_claim_ids": list(source_claim_ids),
        "state": state,
        "authority": deepcopy(authority),
        "observed_equipment_evidence": False,
    }
