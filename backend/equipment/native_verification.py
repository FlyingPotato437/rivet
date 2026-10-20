"""Independent saved-package closure for structural native drawing changes."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .comparison import analyze_package, compare_packages
from .native_bom import verify_inventory
from .readers import HEADER_MAP, _key, normalize_value
from .rules import evaluate


def _inventory_checks(comparison, requests):
    """Close only explicitly requested inventory changes against real files."""
    result = deepcopy(comparison)
    removed = {r["device_tag"]: r for r in requests if r["kind"] == "remove_device"}
    added = {r["device_tag"]: r for r in requests if r["kind"] == "add_device"}
    for check in result["checks"]:
        tag, field = check["device_tag"], check["field"]
        if tag in removed:
            # A live specification for a removed device is still a conflict.
            specified = [
                f for f in result["requirements"]["facts"] if f["device_tag"] == tag
            ] + [
                f
                for side in ("baseline", "target", "requirements")
                for f in result[side]["requested_changes"]
                if f["device_tag"] == tag
            ]
            actual = [f for f in result["target"]["facts"] if f["device_tag"] == tag]
            observed = any(
                tag in s["observed_device_tags"] for s in result["target"]["sources"]
            )
            if not specified and not actual and not observed:
                check.update(
                    status="verified",
                    reason="Explicit removal is reflected in both saved native drawing and BOM; no governing retained-device requirement exists.",
                    native_request=removed[tag]["evidence"],
                )
        if tag in added and field != "device_presence":
            attrs = {
                HEADER_MAP.get(_key(k)): v for k, v in added[tag]["attributes"].items()
            }
            if field not in attrs:
                continue
            desired, unit, error = normalize_value(field, attrs[field])
            observed = [
                *check["target_drawing"]["evidence"],
                *check["target_bom"]["evidence"],
            ]
            roles = {f["role"] for f in observed}
            if (
                not error
                and roles == {"drawing", "bom"}
                and all(
                    (f["value"], f.get("unit")) == (desired, unit) for f in observed
                )
                and not check["required"]["evidence"]
            ):
                check.update(
                    status="verified",
                    reason="The added device's actual native and BOM values match the explicitly requested part and attributes.",
                    native_request=added[tag]["evidence"],
                )
                check["requested"] = {
                    "value": desired,
                    "unit": unit,
                    "values": [{"value": desired, "unit": unit}],
                    "evidence": [added[tag]["evidence"]],
                }
    return result


def _close_inherited_manufacturer(comparison, requests, original_bom_analysis):
    """Close only a source-proved BOM product label, never invented intent."""
    result = deepcopy(comparison)
    additions = {r["device_tag"]: r for r in requests if r["kind"] == "add_device"}
    reports = original_bom_analysis.get("sources", [])
    if not reports or any(
        s.get("role") != "bom" or s.get("status") != "parsed" for s in reports
    ):
        return result
    for check in result["checks"]:
        tag = check["device_tag"]
        if (
            tag not in additions
            or check["field"] != "manufacturer"
            or check["status"] != "unknown"
            or check["required"]["evidence"]
            or check.get("requested", {}).get("evidence")
            or not any(
                c["device_tag"] == tag
                and c["field"] == "part_number"
                and c["status"] == "verified"
                for c in result["checks"]
            )
        ):
            continue
        attributes = {}
        for key, value in additions[tag].get("attributes", {}).items():
            attributes.setdefault(HEADER_MAP.get(_key(key), _key(key)), []).append(
                value
            )
        if "manufacturer" in attributes or len(attributes.get("part_number", [])) != 1:
            continue
        part = attributes["part_number"][0]
        if normalize_value("part_number", part)[2]:
            continue
        rows, maker_values, invalid = [], set(), False
        for report in reports:
            for row in report.get("observed_bom_rows", []):
                cells = {}
                for cell in row.get("cells", []):
                    key = HEADER_MAP.get(
                        _key(cell.get("header")), _key(cell.get("header"))
                    )
                    cells.setdefault(key, []).append(cell)
                part_cells = cells.get("part_number", [])
                if not any(c.get("text") == part for c in part_cells):
                    continue
                makers = cells.get("manufacturer", [])
                row_tags = [c.get("text") for c in cells.get("device_tag", [])]
                if (
                    len(part_cells) != 1
                    or len(makers) != 1
                    or any(t in additions for t in row_tags)
                    or not str(makers[0].get("text") or "").strip()
                    or any(c.get("merged_range") for c in [*part_cells, *makers])
                    or row.get("source_id") != report.get("id")
                    or row.get("sha256") != report.get("sha256")
                    or row.get("role") != "bom"
                ):
                    invalid = True
                    break
                maker_values.add(makers[0]["text"])
                rows.append(deepcopy(row))
            if invalid:
                break
        if invalid or not rows or len(maker_values) != 1:
            continue
        maker = next(iter(maker_values))
        expected, unit, error = normalize_value("manufacturer", maker)
        if error:
            continue
        target_makers = check["target_bom"]["evidence"]
        target_parts = [
            f
            for f in result["target"]["facts"]
            if f["device_tag"] == tag
            and f["field"] == "part_number"
            and f["role"] == "bom"
        ]
        actual_makers = [*target_makers, *check["target_drawing"]["evidence"]]
        if (
            not target_makers
            or not target_parts
            or any(
                f.get("device_tag") != tag
                or f.get("role") != "bom"
                or f.get("field") != "manufacturer"
                for f in target_makers
            )
            or any(f.get("raw_value", f["value"]) != part for f in target_parts)
            or any(
                (f["value"], f.get("unit")) != (expected, unit) for f in actual_makers
            )
            or any(
                normalize_value("manufacturer", f.get("raw_value", f["value"]))
                != (expected, unit, None)
                for f in target_makers
            )
        ):
            continue
        check.update(
            status="verified",
            reason="The proposed BOM manufacturer label preserves unanimous original BOM rows for the exact explicitly added part. This is source-derived product identity, not a new customer requirement or observed drawing manufacturer.",
            source_product_identity_lineage={
                "kind": "original_same_part_bom_label",
                "part_number_literal": part,
                "manufacturer_literal": maker,
                "original_rows": rows,
                "added_part_request": deepcopy(additions[tag]["evidence"]),
                "customer_intent_inferred": False,
                "drawing_manufacturer_inferred": False,
                "engineering_approved": False,
            },
        )
    return result


def recheck_package(
    baseline,
    outputs,
    requirements,
    *,
    original_target=None,
    native_plan=None,
    literal_edits=None,
    identity_profile=None,
    identity_source_catalog=None,
):
    identity_options = (
        {
            "identity_profile": identity_profile,
            "identity_source_catalog": identity_source_catalog,
        }
        if identity_profile is not None
        else {}
    )
    result = compare_packages(baseline, outputs, requirements, **identity_options)
    result["data_rules"] = evaluate(result["target"])
    if not native_plan:
        return result
    from .native_edits import verify_native_changes
    from .native_model import inspect_native

    def value(item, key):
        return item[key] if isinstance(item, dict) else getattr(item, key)

    source = next(
        s
        for s in original_target
        if value(s, "role") == "drawing"
        and Path(value(s, "name")).suffix.lower() == ".dxf"
    )
    drawing = next(
        s
        for s in outputs
        if value(s, "role") == "drawing"
        and Path(value(s, "name")).suffix.lower() == ".dxf"
    )
    bom = next(s for s in outputs if value(s, "role") == "bom")
    before = inspect_native(
        value(source, "path"), expected_sha256=value(source, "sha256")
    )
    after = inspect_native(
        value(drawing, "path"), expected_sha256=value(drawing, "sha256")
    )
    allowed = [
        {
            "device_tag": target["equipment_tag"],
            "attribute": target["attribute_tag"],
            "before": target["expected_value"],
            "after": target["replacement_value"],
        }
        for edit in (literal_edits or [])
        for target in edit.get("targets", [])
        if target["kind"] == "cad_attribute"
    ]
    native = verify_native_changes(
        before, after, native_plan, allowed_attribute_changes=allowed
    )
    inventory = verify_inventory(
        value(drawing, "path"), value(bom, "path"), **identity_options
    )
    native["checks"] = [*native.get("checks", []), *inventory["checks"]]
    native["inventory_consistency_scope"] = inventory.get("scope")
    native["unknown_inventory_fields"] = inventory.get("unknown_fields", [])
    if inventory["status"] != "verified":
        native["status"] = "blocked"
    if Path(value(bom, "name")).suffix.lower() == ".xlsx":
        from .native_schedules import verify_native_schedules

        schedules = verify_native_schedules(
            value(bom, "path"), after, value(bom, "revision")
        )
        native["checks"].extend(schedules["checks"])
        if schedules["status"] != "verified":
            native["status"] = "blocked"
    from .identity import IdentityContext

    context = IdentityContext(identity_profile, identity_source_catalog)
    logical_requests = deepcopy(native_plan["requests"])
    for request in logical_requests:
        if "device_tag" in request:
            request["device_tag"] = context.canonical(request["device_tag"])
    result = _inventory_checks(result, logical_requests)
    if any(r["kind"] == "add_device" for r in logical_requests):
        original_bom = analyze_package(
            [s for s in original_target if value(s, "role") == "bom"]
        )
        result = _close_inherited_manufacturer(result, logical_requests, original_bom)
    from collections import Counter

    counts = Counter(c["status"] for c in result["checks"])
    result["summary"].update(
        verified=counts["verified"],
        unchanged=counts["unchanged"],
        mismatch=counts["mismatch"],
        missing=counts["missing"],
        unknown=counts["unknown"],
        remaining=counts["mismatch"] + counts["missing"],
        cannot_verify=counts["unknown"],
    )
    passed = (
        native["status"] == "verified"
        and all(c["status"] == "pass" for c in native["checks"])
        and bool(result["checks"])
        and not any(counts[s] for s in ("missing", "mismatch", "unknown"))
        and not result["summary"]["source_issues"]
        and result["coverage"]["baseline_comparison_available"]
        and not result["data_rules"]["failed"]
        and not result["data_rules"]["unknown"]
    )
    result["summary"].update(
        outcome="supported_fields_match" if passed else "changes_remaining",
        revision_changes_verified=passed,
        native_changes_verified=passed,
        native_check_count=len(native["checks"]),
    )
    result["native_verification"] = native
    return result
