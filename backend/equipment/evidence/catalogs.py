"""Provenance-bound catalog facts, not a product selector or an OCR autocorrector.

Only complete numbers explicitly present in the curated primary-source tables decode.
The lookup returns constraints/claims; it never edits a drawing value or approves it.
"""

from __future__ import annotations

import copy
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    return json.loads(Path(__file__).with_name("catalogs.json").read_text())


def _manufacturer(value: str | None) -> str | None:
    token = " ".join((value or "").casefold().replace("™", "").split())
    return {
        "schneider": "Schneider Electric",
        "schneider electric": "Schneider Electric",
        "square d": "Schneider Electric",
        "square-d": "Schneider Electric",
        "eaton": "Eaton",
        "siemens": "Siemens",
    }.get(token)


def _family(value: str | None) -> str | None:
    token = " ".join((value or "").casefold().replace("™", "").split())
    return {
        "powerpact": "PowerPacT",
        "powerpact h/j/l": "PowerPacT",
        "series c f": "Series C F",
        "series c f-frame": "Series C F",
        "bqd": "BQD",
        "cqd": "CQD",
        "bqd6": "BQD6",
        "cqd6": "CQD6",
    }.get(token)


def _citation(product: dict[str, Any], section: str | None = None) -> dict[str, Any]:
    source = copy.deepcopy(_catalog()["sources"][product["source_id"]])
    source["source_id"] = product["source_id"]
    if product.get("page") is not None:
        source["page"] = product["page"]  # Physical one-based PDF page.
    source["section"] = section or product.get("section") or source.get("section")
    return source


def _claim(
    feature: str,
    value: Any,
    product: dict[str, Any],
    *,
    unit: str | None = None,
    conditions: dict[str, Any] | None = None,
    section: str | None = None,
) -> dict[str, Any]:
    return {
        "feature": feature,
        "value": value,
        "unit": unit,
        "source_citation": _citation(product, section),
        "extractor": "curated_primary_catalog_v1",
        "conditions": conditions or {},
        "evidence_role": "catalog_constraint",
        "engineering_approved": False,
    }


def _voltage(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)
    except (ValueError, TypeError):
        return None
    return numeric if numeric > 0 and math.isfinite(numeric) else None


def lookup_product(
    manufacturer: str | None = None,
    family: str | None = None,
    part_number: str | None = None,
    frame: str | None = None,
    voltage_v: float | None = None,
    current_type: str = "AC",
    *,
    connection_system: str | None = None,
    rating_standard: str = "UL489",
) -> dict[str, Any]:
    """Return evidence and conditional constraints, retaining every input verbatim.

    Missing manufacturer/family cannot bind a bare frame letter to a catalog.
    A complete exact supported part number can establish its product family. A
    supplied conflicting manufacturer/family blocks that binding. Unsupported
    suffixes, DC variants, and standards remain unknown; no nearest-part match.
    """
    data = _catalog()
    raw = dict(
        manufacturer=manufacturer,
        family=family,
        part_number=part_number,
        frame=frame,
        voltage_v=voltage_v,
        current_type=current_type,
        connection_system=connection_system,
        rating_standard=rating_standard,
    )
    result: dict[str, Any] = {
        "schema": data["schema"],
        "input": raw,
        "claims": [],
        "constraints": [],
        "applicability": {"product_bound": False, "operating_rating_bound": False},
        "unknowns": [],
        "conflicts": [],
    }
    claims, constraints, unknowns = (
        result["claims"],
        result["constraints"],
        result["unknowns"],
    )
    maker, fam = _manufacturer(manufacturer), _family(family)
    part = (part_number or "").strip().upper()
    # Do not strip suffixes, punctuation, internal spaces or leading brand letters.
    product = data["products"].get(part) if part else None
    if product:
        if manufacturer and maker != product["manufacturer"]:
            result["conflicts"].append(
                "Supplied manufacturer disagrees with the exact catalog product."
            )
            return result
        if family and fam != product["family"]:
            result["conflicts"].append(
                "Supplied family disagrees with the exact catalog product."
            )
            return result
        maker, fam = product["manufacturer"], product["family"]
        result["applicability"].update(
            product_bound=True, manufacturer=maker, family=fam, catalog_number=part
        )
    elif part:
        unknowns.append(
            "Complete catalog number or suffix is outside this verified subset; nothing was decoded from its prefix."
        )
    if manufacturer and maker is None:
        unknowns.append("Manufacturer is not in the verified catalog subset.")
    if family and fam is None:
        unknowns.append("Product family is not in the verified catalog subset.")

    mode = (current_type or "").upper()
    if mode not in {"AC", "DC"}:
        unknowns.append("Current type must be AC or DC to bind a rating.")
    requested_frame = (frame or "").strip().upper()
    if maker == "Schneider Electric" and fam == "PowerPacT":
        frame_key = requested_frame
        if product:
            frame_key = part[0] if part[0] != "L" else "L600"
        choices = [
            x
            for x in data["frame_constraints"]
            if x["frame"] == frame_key and x["current_type"] == mode
        ]
        if frame_key == "L" and mode == "AC":
            choices = [
                x for x in data["frame_constraints"] if x["frame"] in {"L400", "L600"}
            ]
            unknowns.append(
                "L alone does not identify the 400 A or 600 A frame variant."
            )
        for row in choices:
            constraints.append(
                {
                    "kind": "conditional_range",
                    "feature": "rated_current_a",
                    "minimum": row["minimum_a"],
                    "maximum": row["maximum_a"],
                    "unit": "A",
                    "frame_capacity_a": row["capacity_a"],
                    "applies_when": {
                        "manufacturer": maker,
                        "family": fam,
                        "frame_variant": row["frame"],
                        "current_type": "AC",
                    },
                    "source_citation": _citation(
                        row, "General characteristics / frame ratings"
                    ),
                }
            )
        if requested_frame and requested_frame not in {"H", "J", "L", "L400", "L600"}:
            unknowns.append(
                "Legacy/unknown frame text is preserved; no PowerPacT frame alias was inferred."
            )
    elif requested_frame and not product:
        unknowns.append(
            "Bare frame text requires a verified manufacturer and product-family binding."
        )
    if not product:
        return result

    claims.extend(
        [
            _claim("manufacturer", maker, product),
            _claim("family", fam, product),
            _claim("part_number", part, product),
        ]
    )
    if product.get("trip_type"):
        claims.append(_claim("trip_type", product["trip_type"], product))
    decoded_frame: str
    if maker == "Schneider Electric":
        decoded_frame = part[0]
        capacity = {"H": 150, "J": 250, "L": 600}[decoded_frame]
        poles, current = int(part[3]), int(part[5:8])
        current_feature = (
            "sensor_a" if product["trip_type"] == "electronic" else "rated_current_a"
        )
        claims.extend(
            [
                _claim("device_frame", decoded_frame, product),
                _claim("frame_a", capacity, product, unit="A"),
                _claim("pole_count", poles, product),
                _claim(current_feature, current, product, unit="A"),
            ]
        )
        if current_feature == "sensor_a":
            unknowns.append(
                "Electronic-trip sensor rating does not establish the installed long-time pickup setting."
            )
        rating_product = dict(product, page=27 if decoded_frame in {"H", "J"} else 29)
        voltage = _voltage(voltage_v)
        if (
            mode == "AC"
            and rating_standard.upper().replace(" ", "") == "UL489"
            and voltage in data["schneider_air_voltage_columns"]
        ):
            values = data["schneider_air_ul489_ac"][decoded_frame].get(part[1])
            if values:
                ka = values[data["schneider_air_voltage_columns"].index(voltage)]
                claims.append(
                    _claim(
                        "interrupting_ka",
                        ka,
                        rating_product,
                        unit="kA",
                        conditions={
                            "voltage_v": voltage,
                            "current_type": "AC",
                            "rating_standard": "UL489",
                            "frequency_hz": [50, 60],
                        },
                        section="UL 489 circuit breaker AIR ratings",
                    )
                )
                result["applicability"]["operating_rating_bound"] = True
        else:
            unknowns.append(
                "Interrupting capacity is unknown for missing/unlisted voltage, DC variant or non-UL489 standard; no voltage interpolation."
            )
    elif maker == "Eaton":
        decoded_frame = product["device_frame"]
        for feature, unit in [
            ("device_frame", None),
            ("frame_a", "A"),
            ("rated_current_a", "A"),
            ("pole_count", None),
        ]:
            claims.append(
                _claim(
                    feature,
                    product[feature],
                    (
                        dict(
                            product,
                            source_id="eaton-f-frame-ratings",
                            section="Series C FD/HFD frame current range",
                        )
                        if feature == "frame_a"
                        else product
                    ),
                    unit=unit,
                )
            )
        ka = (
            product["interrupting_ka"]
            .get(mode, {})
            .get(str(int(_voltage(voltage_v) or -1)))
        )
        if (
            ka is not None
            and _voltage(voltage_v) in {240, 480}
            and rating_standard.upper().replace(" ", "") == "UL489"
        ):
            claims.append(
                _claim(
                    "interrupting_ka",
                    ka,
                    product,
                    unit="kA",
                    conditions={
                        "voltage_v": _voltage(voltage_v),
                        "current_type": "AC",
                        "rating_standard": "UL489",
                        "frequency_hz": [50, 60],
                    },
                )
            )
            result["applicability"]["operating_rating_bound"] = True
        else:
            unknowns.append(
                "This exact Eaton product source supports only the stored 240/480 Vac interrupting ratings; DC/600 Vac kA remain unknown."
            )
    else:
        is_six = part[3] == "6"
        decoded_frame = fam
        offset = 4 if is_six else 3
        poles, amps = int(part[offset]), int(part[offset + 1 :])
        claims.extend(
            [
                _claim("device_frame", decoded_frame, product),
                _claim("pole_count", poles, product),
                _claim("rated_current_a", amps, product, unit="A"),
            ]
        )
        voltage = _voltage(voltage_v)
        standard = rating_standard.upper().replace(" ", "")
        connection = (connection_system or "").upper().replace(" ", "")
        ka = None
        if mode == "AC" and not is_six and standard == "UL489":
            if voltage in {120, 240}:
                ka = 65
            elif voltage == 277 and poles == 1:
                ka = 14
            elif (
                voltage == 480
                and poles in {2, 3}
                and connection in {"480Y/277", "480Y/277V", "GROUNDED_WYE_480_277"}
            ):
                ka = 14
        if mode == "AC" and is_six and standard in {"CSA", "CSA489"}:
            if voltage == 347 and poles == 1:
                ka = 10
            elif (
                voltage == 600
                and poles in {2, 3}
                and connection in {"600Y/347", "600Y/347V", "GROUNDED_WYE_600_347"}
            ):
                ka = 10
        if ka is not None:
            claims.append(
                _claim(
                    "interrupting_ka",
                    ka,
                    dict(product, page=1),
                    unit="kA",
                    conditions={
                        "voltage_v": voltage,
                        "current_type": "AC",
                        "rating_standard": rating_standard,
                        "connection_system": connection or None,
                        "frequency_hz": [50, 60],
                    },
                    section="UL/CSA AIR ratings and product voltage columns",
                )
            )
            result["applicability"]["operating_rating_bound"] = True
        else:
            unknowns.append(
                "Siemens kA requires the listed voltage, pole count, standard and grounded-wye system where slash-rated; DC is unsupported here."
            )
        unknowns.append(
            "No independent frame-ampacity claim was curated for this Siemens family."
        )

    if requested_frame and requested_frame not in {
        decoded_frame,
        f"{decoded_frame}{capacity}"
        if maker == "Schneider Electric"
        else decoded_frame,
    }:
        result["conflicts"].append(
            "Observed frame text disagrees with the exact bound catalog product; original observation must remain available."
        )
    constraints.append(
        {
            "kind": "less_than_or_equal",
            "feature": "trip_a",
            "other_feature": "frame_a",
            "unit": "A",
            "applies_when": {"same_device": True, "both_values_known": True},
            "source_citation": _citation(
                product,
                "Product current/sensor rating: does not establish actual trip setting",
            ),
            "reason": "Installed pickup cannot be inferred from catalog capacity; an observed value above that capacity requires review.",
        }
    )
    return result
