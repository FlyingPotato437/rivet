"""Project existing native, visual and CAD observations into evidence claims.

No source observation is adjudicated here. An adapter may normalize a printed
unit, but it never fills a blank, turns a product family into a device identity,
uses revision order as approval, or calls a model confidence calibrated truth.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .identity import describe_identity, entity_identity, validate_alias
from .ontology import (
    FEATURES,
    make_edge,
    make_node,
    make_requirement_rule,
    normalize_feature_value,
    validate_topology,
)
from .schema import make_claim, new_graph, source_ref, stable_id


def _plain(value: Any) -> dict:
    return (
        value.model_dump(exclude={"path"})
        if hasattr(value, "model_dump")
        else {k: deepcopy(v) for k, v in value.items() if k != "path"}
    )


def _many(value: Any) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _raw_feature(field: str | None, header: str | None = None) -> str:
    if field and field != "unknown":
        return field
    from ..bom_rows import HEADERS

    canonical = HEADERS.get(re.sub(r"[^a-z0-9]+", "", str(header or "").casefold()))
    if canonical:
        return "circuit_no" if canonical == "circuit_number" else canonical
    label = re.sub(r"[^a-z0-9]+", "_", str(header or "unclassified").casefold()).strip(
        "_"
    )
    return "unknown:" + (label or "unclassified")


class _Builder:
    def __init__(
        self, scope_id, source_metadata, source_context, verified_identities, metadata
    ):
        self.graph = new_graph(scope_id, metadata=metadata)
        self.scope_id = scope_id
        self.sources = {
            str(source.get("id", source.get("source_id"))): source
            for source in source_metadata
        }
        self.context = source_context or {}
        self.verified = verified_identities or []
        self.indexes = {key: {} for key in ("nodes", "edges", "claims", "aliases")}
        self.entity_map = {}
        self.errors = []

    def put(self, collection, item):
        old = self.indexes[collection].get(item["id"])
        if old is not None and old != item:
            # Node observations for a verified identity can add source anchors.
            # Neither claim value nor conflicting identity metadata is replaced.
            if (
                collection == "nodes"
                and old["kind"] == item["kind"]
                and old["scope_key"] == item["scope_key"]
            ):
                for ref in item["source_refs"]:
                    if ref not in old["source_refs"]:
                        old["source_refs"].append(deepcopy(ref))
                return old
            raise ValueError(f"Evidence ID collision in {collection}")
        if old is None:
            self.indexes[collection][item["id"]] = deepcopy(item)
            self.graph[collection].append(self.indexes[collection][item["id"]])
        return self.indexes[collection][item["id"]]

    def ref(self, source_id, location, fallback_hash=None):
        base = {**self.sources.get(str(source_id), {}), "source_id": str(source_id)}
        if base.get("sha256") and fallback_hash and base["sha256"].lower() != fallback_hash.lower():
            raise ValueError("Observation hash disagrees with its bound source metadata")
        if not base.get("sha256") and fallback_hash:
            base["sha256"] = fallback_hash
        return source_ref(base, **{k: v for k, v in location.items() if v is not None})

    def entity(
        self,
        source_id,
        local_id,
        ref,
        *,
        tag=None,
        section=None,
        circuit=None,
        device_type=None,
        kind=None,
    ):
        context = self.context.get(str(source_id), {})
        node_id, identity_meta = entity_identity(
            self.scope_id,
            str(source_id),
            str(local_id),
            tag=tag,
            section=section,
            circuit=circuit,
            verified_identities=self.verified,
            source_context=context,
        )
        identity = describe_identity(tag)
        if kind is None:
            kind = "device" if tag else "unclassified_position"
        lineup = None
        # Containment itself needs a caller-verified assembly identity; a nearby
        # title or row index is not a reason to combine two manufacturer's jobs.
        if (
            context.get("lineup_verified") is True
            and context.get("lineup_id")
            and context.get("lineup_evidence")
        ):
            lineup = stable_id("lineup", self.scope_id, context["lineup_id"])
            self.put(
                "nodes",
                make_node(
                    lineup,
                    "lineup",
                    self.scope_id,
                    identifiers={"raw_tag": context["lineup_id"]},
                    source_refs=context["lineup_evidence"],
                    metadata={
                        "identity_verified": True,
                        "identity_key": context["lineup_id"],
                        "source_local": False,
                    },
                ),
            )
        node = self.put(
            "nodes",
            make_node(
                node_id,
                kind,
                self.scope_id,
                lineup_id=lineup,
                identifiers=identity,
                source_refs=[ref],
                metadata={
                    **identity_meta,
                    "source_id": source_id,
                    "section": section,
                    "circuit": circuit,
                    "device_type": device_type,
                    "source_local_id": str(local_id),
                },
            ),
        )
        self.entity_map[(str(source_id), str(local_id))] = node_id
        if tag:
            self.entity_map.setdefault(
                (str(source_id), "tag:" + identity["normalized_tag"]), node_id
            )
        if lineup and node_id != lineup:
            parent = lineup
            if section is not None:
                section_id = stable_id("section", self.scope_id, lineup, str(section))
                self.put(
                    "nodes",
                    make_node(
                        section_id,
                        "section",
                        self.scope_id,
                        lineup_id=lineup,
                        identifiers={"raw_tag": str(section)},
                        source_refs=[ref],
                        metadata={"identity_verified": False, "source_local": True},
                    ),
                )
                self.put(
                    "edges",
                    make_edge(
                        "contains",
                        lineup,
                        section_id,
                        metadata={"basis": "verified_lineup_and_source_section"},
                    ),
                )
                parent = section_id
            self.put(
                "edges",
                make_edge(
                    "contains",
                    parent,
                    node_id,
                    metadata={"basis": "verified_lineup_and_observed_position"},
                ),
            )
        return node

    def claim(
        self,
        node,
        feature,
        value,
        ref,
        extractor,
        *,
        raw_value=None,
        raw_confidence=None,
        unit=None,
        conditions=None,
        derived_from=None,
        state="observed",
        independence_group=None,
    ):
        normalized = normalize_feature_value(feature, value, unit)
        context = self.context.get(str(ref["source_id"]), {})
        original_hash = context.get("original_sha256")
        original_page = context.get("page_map", {}).get(
            str(ref.get("page")), ref.get("page")
        )
        if original_hash:
            group = stable_id(
                "original-page-ancestry",
                original_hash,
                original_page,
                ref.get("sheet"),
                None if ref.get("page") else ref.get("entity", ref.get("xml_path")),
            )
        else:
            group = independence_group or context.get("independence_group")
        claim = make_claim(
            node["id"],
            feature,
            normalized["value"],
            source=ref,
            extractor=extractor,
            raw_value=value if raw_value is None else raw_value,
            raw_confidence=raw_confidence,
            unit=normalized["unit"],
            state=state,
            conditions=conditions,
            derived_from=derived_from,
            independence_group=group,
            normalization={
                "valid": normalized["valid"],
                "reason": normalized["reason"],
                "input_unit": unit,
            },
        )
        return self.put("claims", claim)

    def finish(self):
        self.graph["coverage"].update(
            {
                "source_observation_count": len(self.graph["claims"]),
                "entity_observation_count": sum(
                    n["kind"] in {"device", "unclassified_position"}
                    for n in self.graph["nodes"]
                ),
                "unresolved_claim_count": sum(
                    c["value"] is None for c in self.graph["claims"]
                ),
                "complete": False,
                "identity_complete": False,
                "connectivity_complete": False,
            }
        )
        self.graph["metadata"]["adapter_errors"] = self.errors
        self.graph["metadata"]["topology_issues"] = validate_topology(self.graph)
        self.graph["metadata"]["claim_immutability"] = (
            "Append evidence; replace or delete no claim. Decisions are separate projections."
        )
        return self.graph


def _conditions(builder, source_id, **more):
    source = builder.sources.get(str(source_id), {})
    context = builder.context.get(str(source_id), {})
    requested_side = more.get("side", context.get("side", source.get("side", "observed")))
    role = context.get("role", source.get("role"))
    if role == "specification":
        more = {**more, "declared_side": requested_side, "side": "requirements"}
    return {
        k: v
        for k, v in {
            "revision": source.get("revision"),
            "role": context.get("role", source.get("role")),
            "source_role": context.get("role", source.get("role")),
            "side": context.get("side", source.get("side", "observed")),
            "document_lifecycle": source.get("lifecycle"),
            **more,
        }.items()
        if v is not None
    }


def _native(builder, rawcomparison):
    analyses = [
        (side, rawcomparison.get(side, {}))
        for side in ("baseline", "target", "requirements")
    ]
    if "facts" in rawcomparison:
        analyses = [("observed", rawcomparison)]
    for side, analysis in analyses:
        for record, requested in [(x, False) for x in analysis.get("facts", [])] + [
            (x, True) for x in analysis.get("requested_changes", [])
        ]:
            sid = str(record["source_id"])
            location = {
                **record.get("evidence", {}),
                "entity": "tag:"
                + str(record.get("device_tag", record.get("id", "unknown"))),
            }
            ref = builder.ref(sid, location, record.get("sha256"))
            tag = record.get("device_tag")
            node = builder.entity(
                sid, "tag:" + str(tag) if tag else str(record.get("id")), ref, tag=tag
            )
            claim = builder.claim(
                node,
                str(record.get("field", "unknown")),
                record.get("value"),
                ref,
                {
                    "name": record.get("extractor", "native_literal"),
                    "version": "existing",
                },
                raw_value=record.get("raw_value", record.get("value")),
                raw_confidence=record.get("confidence", "literal_unverified"),
                unit=record.get("unit"),
                conditions=_conditions(builder, sid, side=side, request=requested),
                state="proposed" if requested else "observed",
            )
            if (
                (
                    side == "requirements"
                    or claim["conditions"].get("role") == "specification"
                )
                and claim["feature"] in FEATURES
                and claim["value"] is not None
            ):
                builder.graph["rules"].append(
                    make_requirement_rule(
                        stable_id("requirement", claim["id"]),
                        node["id"],
                        claim["feature"],
                        "eq",
                        target={"value": claim["value"], "unit": claim["unit"]},
                        source_claim_ids=[claim["id"]],
                        state=claim["state"],
                    )
                )
        for report in analysis.get("sources", []):
            sid = str(report.get("id", report.get("source_id")))
            for uncertain in report.get("uncertain_fields", []):
                tag = uncertain.get("device_tag")
                location = {**uncertain.get("evidence", {}), "entity": "tag:" + str(tag)}
                try:
                    ref = builder.ref(sid, location, report.get("sha256"))
                except ValueError:
                    builder.errors.append({"source_id": sid, "reason": "uncertain_field_has_no_source_location"})
                    continue
                node = builder.entity(sid, "tag:" + str(tag), ref, tag=tag)
                builder.claim(node, str(uncertain.get("field", "unknown")), None, ref,
                              {"name": "native_literal", "version": "existing"},
                              raw_value=uncertain.get("raw_value"), raw_confidence=None,
                              conditions=_conditions(builder, sid, side=side,
                                                     uncertainty_reason=uncertain.get("reason")))
            for tag in report.get("observed_device_tags", []):
                ref = builder.ref(
                    sid, {"entity": "tag:" + str(tag)}, report.get("sha256")
                )
                builder.entity(sid, "tag:" + str(tag), ref, tag=tag)
            builder.graph["scope_coverage"].append(
                {
                    "source_id": report.get("id", report.get("source_id")),
                    "reader": "native",
                    "status": report.get("status"),
                    "inventory_complete": False,
                    "connectivity_complete": False,
                    "warnings": deepcopy(report.get("warnings", [])),
                }
            )


def _producer_position_state(builder, node, row, fields, ref, extractor, cell_claims):
    """Retain the visual producer's classification as an explicit inference.

    N/P = No is not evidence of population. The producer separately interprets
    frame/trip/PS text, so that derived output needs its own sourced parents and
    must not disappear merely because an N/P cell shares its field name.
    """
    requested = fields.get("position_state")
    if requested not in {"populated_breaker", "prepared_space", "incoming_connection"} or not (
        row.get("requires_human_verification") is True and row.get("normalization_notes")
    ):
        return False
    by_feature = {}
    for claim in cell_claims:
        by_feature.setdefault(claim["feature"], []).append(claim)

    def unique(feature):
        claims = by_feature.get(feature, [])
        normalized = normalize_feature_value(feature, fields.get(feature))
        return claims[0] if (
            len(claims) == 1 and claims[0].get("value") is not None
            and normalized["valid"] and claims[0]["value"] == normalized["value"]
        ) else None

    frame, trip = unique("device_frame"), unique("trip_a")
    expected, basis, parents = None, "Producer classification lacks unique source-cell support.", []
    if frame and isinstance(frame["value"], str):
        text = frame["value"]
        if re.search(r"\bPS\b", text.replace("(", " ").replace(")", " "), re.IGNORECASE):
            expected, parents = "prepared_space", [frame]
            basis = "Printed PS abbreviation in the frame cell; prepared-space interpretation remains unverified."
        elif "incoming" in text.casefold():
            expected, parents = "incoming_connection", [frame]
            basis = "Printed incoming-connection frame text; no installed breaker is inferred."
        elif text and trip:
            expected, parents = "populated_breaker", [frame, trip]
            basis = "Printed frame and trip without a PS indication; physical population remains unverified."
    supported = requested == expected
    if not supported:
        # Retain the producer assertion as unknown rather than invent a physical
        # interpretation or copy a normalized value with no printed parents.
        parents = list(cell_claims)
        basis = "Producer classification does not follow uniquely retained frame/trip/PS source cells."
    source_text = [
        {"claim_id": parent["id"], "feature": parent["feature"],
         "raw_text": deepcopy(parent.get("raw_value")), "source": deepcopy(parent["source"])}
        for parent in parents
    ]
    builder.claim(
        node, "position_state", requested if supported else None,
        {**ref, "text": "; ".join(str(p["raw_text"]) for p in source_text)},
        {**extractor, "name": "visual_position_state_interpretation", "version": "rivet.visual-position-interpretation.v2.2"},
        raw_value={"producer_value": requested, "source_cells": source_text},
        raw_confidence=row.get("field_confidence", {}).get("position_state"),
        derived_from=[parent["id"] for parent in parents],
        independence_group=parents[0]["independence_group"] if parents else None,
        conditions=_conditions(
            builder, ref["source_id"], observation_kind="producer_derived_position_state",
            inferred=True, candidate_only=True, requires_human_verification=True,
            inference_basis=basis, source_parent_ids=[p["id"] for p in parents],
            producer_normalization_notes=deepcopy(row.get("normalization_notes", [])),
            model_confidence_is_authority=False, physical_population_verified=False,
        ),
    )
    return True


def _visual(builder, prediction):
    producer = prediction.get("producer", {})
    if isinstance(producer, str):
        producer = {"name": producer}
    extractor = {
        "name": producer.get("name", "visual_equipment"),
        "version": producer.get(
            "version", prediction.get("schema_version", "unspecified")
        ),
        "run_id": producer.get("run_id"),
    }
    rows = prediction.get("observed_bom_rows", prediction.get("bom_rows", []))
    row_nodes = {}
    for row in rows:
        sid = str(row["source_id"])
        local = str(
            row.get("id")
            or f"page-{row.get('page')}-table-{row.get('table')}-row-{row.get('row')}"
        )
        fields = row.get("fields", {})
        ref = builder.ref(
            sid,
            {**row, "entity": local},
            row.get("sha256", prediction.get("source_hashes", {}).get(sid)),
        )
        node = builder.entity(
            sid,
            local,
            ref,
            tag=row.get("device_tag"),
            section=fields.get("section_no"),
            circuit=fields.get("circuit_no"),
            device_type=fields.get("device_type", fields.get("device_frame")),
        )
        row_nodes[(sid, local)] = node
        seen = set()
        cell_claims = []
        for cell in row.get("cells", []):
            field = _raw_feature(cell.get("field"), cell.get("header"))
            cell_ref = builder.ref(
                sid,
                {**row, **cell, "cell": cell.get("column"), "entity": local},
                ref["sha256"],
            )
            value = cell.get("value")
            if (
                cell.get("field") in {None, "unknown"}
                and not field.startswith("unknown:")
                and cell.get("confidence") not in {"low", "unknown"}
            ):
                value = cell.get("raw_text", cell.get("text"))
            # Even an unresolved/unsupported header keeps the original raw cell.
            if field.startswith("unknown:") or cell.get("confidence") in {
                "low",
                "unknown",
            }:
                value = None
            claim = builder.claim(
                node,
                field,
                value,
                cell_ref,
                extractor,
                raw_value=cell.get("raw_text", cell.get("text")),
                raw_confidence=cell.get("confidence", cell.get("value_confidence")),
                conditions=_conditions(
                    builder,
                    sid,
                    observation_kind="schedule_cell",
                    raw_header=cell.get("header"),
                ),
            )
            cell_claims.append(claim)
            seen.add(field)
        for field, value in fields.items():
            if field == "position_state" and _producer_position_state(
                builder, node, row, fields, ref, extractor, cell_claims
            ):
                continue
            if field not in seen:
                builder.claim(
                    node,
                    field,
                    value,
                    ref,
                    {**extractor, "name": extractor["name"] + ":row_projection"},
                    raw_confidence=row.get("field_confidence", {}).get(field),
                    conditions=_conditions(
                        builder, sid, observation_kind="row_projection"
                    ),
                )
    for device in prediction.get("devices", []):
        sid = str(device["source_id"])
        local = str(
            device.get("id")
            or device.get("source_local_id")
            or stable_id(sid, device.get("page"), device.get("bbox"), device.get("tag"))
        )
        # Schedule devices are a projection of the exact same row observations.
        # Keep their shared node, without counting a second set of cell votes.
        if (sid, local) in row_nodes:
            row_nodes[(sid, local)]["metadata"]["device_projection"] = deepcopy(
                {
                    k: device.get(k)
                    for k in ("source_local_id", "tag", "device_type", "position_state")
                }
            )
            continue
        ref = builder.ref(
            sid,
            {**device, "entity": device.get("source_local_id", local)},
            prediction.get("source_hashes", {}).get(sid),
        )
        node = builder.entity(
            sid,
            local,
            ref,
            tag=device.get("tag"),
            section=device.get("section"),
            circuit=device.get("circuit_no"),
            device_type=device.get("device_type"),
        )
        for field, value, confidence in (
            ("device_tag", device.get("tag"), device.get("tag_confidence")),
            ("device_type", device.get("device_type"), None),
            ("section_no", device.get("section"), None),
            ("position", device.get("position"), None),
        ):
            if value is not None or field == "device_tag":
                builder.claim(
                    node,
                    field,
                    value,
                    ref,
                    extractor,
                    raw_value=device.get("raw_tag", value)
                    if field == "device_tag"
                    else value,
                    raw_confidence=confidence,
                    conditions=_conditions(
                        builder, sid, observation_kind="drawing_device"
                    ),
                )
        properties = device.get("normalized_properties", device.get("properties", []))
        if isinstance(properties, dict):
            properties = [
                {
                    "field": k,
                    "value": v,
                    "raw_text": v,
                    "confidence": device.get("field_confidence", {}).get(k),
                }
                for k, v in properties.items()
            ]
        for prop in properties:
            field = _raw_feature(
                prop.get("field"), prop.get("header", prop.get("basis"))
            )
            value = prop.get("value", prop.get("raw_text"))
            if field.startswith("unknown:") or prop.get("confidence") in {
                "low",
                "unknown",
            }:
                value = None
            prop_ref = builder.ref(
                sid,
                {**ref, **prop, "entity": device.get("source_local_id", local)},
                ref["sha256"],
            )
            builder.claim(
                node,
                field,
                value,
                prop_ref,
                extractor,
                raw_value=prop.get("raw_text", value),
                raw_confidence=prop.get("confidence"),
                conditions=_conditions(
                    builder,
                    sid,
                    observation_kind="drawing_property",
                    extraction_basis=prop.get("basis"),
                ),
            )
    for identity in prediction.get("sheet_identities", []):
        sid = str(identity["source_id"])
        local = "sheet-page:" + str(identity.get("page"))
        ref = builder.ref(
            sid,
            {**identity, "entity": local},
            prediction.get("source_hashes", {}).get(sid),
        )
        node = builder.entity(sid, local, ref, kind="drawing_sheet")
        field = identity.get("field", "unknown:sheet_identity")
        builder.claim(
            node,
            field if field in FEATURES else "unknown:sheet_identity:" + field,
            identity.get("text"),
            ref,
            extractor,
            raw_value=identity.get("text"),
            raw_confidence=identity.get("confidence"),
            conditions=_conditions(builder, sid, observation_kind="sheet_identity"),
        )
    for page in prediction.get("pages", []):
        builder.graph["scope_coverage"].append(
            {
                "source_id": page.get("source_id"),
                "page": page.get("page"),
                "reader": "visual_equipment",
                "status": page.get("status"),
                "inventory_complete": False,
                "connectivity_complete": False,
                "unknown_regions": deepcopy(page.get("unknown_regions", [])),
                "inspection_complete_is_not_inventory_complete": True,
            }
        )
    # Ambiguous endpoints remain declarations/claims, never invented terminal
    # nodes attached to guessed equipment.
    for connection in prediction.get("connections", []):
        sid = str(connection["source_id"])
        local = str(connection.get("id") or stable_id("visual-connection", connection))
        ref = builder.ref(
            sid,
            {**connection, "entity": local},
            prediction.get("source_hashes", {}).get(sid),
        )
        node = builder.entity(
            sid, "connection:" + local, ref, kind="unclassified_position"
        )
        builder.claim(
            node,
            "connection",
            connection.get("raw_label")
            or {"from": connection.get("from_label"), "to": connection.get("to_label")},
            ref,
            extractor,
            raw_confidence=connection.get("confidence"),
            conditions=_conditions(
                builder,
                sid,
                endpoint_identity_verified=False,
                observation_kind="visual_connection",
            ),
        )


def _cad(builder, report):
    sid = str(report["source_id"])
    extractor = {
        "name": "cad_report",
        "version": report.get("schema_version", "unspecified"),
    }
    for component in report.get("components", []):
        local = str(component.get("id") or stable_id("cad-component", component))
        ref = builder.ref(
            sid, {**component.get("source", {}), "entity": local}, report.get("sha256")
        )
        node = builder.entity(
            sid,
            local,
            ref,
            tag=component.get("tag"),
            device_type=component.get("properties", {}).get("device_type"),
        )
        props = component.get("properties", {})
        for field, value in props.items():
            if field == "raw_properties":
                for raw_key, raw_value in value.items():
                    builder.claim(
                        node,
                        "unknown:cad_property:" + str(raw_key),
                        None,
                        ref,
                        extractor,
                        raw_value=raw_value,
                        raw_confidence=component.get("confidence"),
                        conditions=_conditions(builder, sid, raw_property=raw_key),
                    )
            else:
                builder.claim(
                    node,
                    field
                    if field in FEATURES
                    else "unknown:cad_property:" + str(field),
                    value,
                    ref,
                    extractor,
                    raw_value=value,
                    raw_confidence=component.get("confidence"),
                    conditions=_conditions(
                        builder, sid, observation_kind="cad_component"
                    ),
                )
        if component.get("tag"):
            builder.claim(
                node,
                "device_tag",
                component["tag"],
                ref,
                extractor,
                raw_value=component.get("raw_tag", component["tag"]),
                raw_confidence=component.get("confidence"),
                conditions=_conditions(builder, sid, observation_kind="cad_component"),
            )
        for pin in component.get("pins", []):
            label = str(pin.get("pin", pin.get("name", "")))
            if not label:
                continue
            terminal_id = stable_id(
                "terminal", builder.scope_id, sid, local, label, pin.get("source_index")
            )
            terminal = make_node(
                terminal_id,
                "terminal",
                builder.scope_id,
                identifiers={"raw_tag": label},
                source_refs=[ref],
                metadata={
                    "source_local": True,
                    "identity_verified": False,
                    "reported_pin": True,
                    "parent_device_id": node["id"],
                    "parent_raw_tag": component.get("raw_tag", component.get("tag")),
                },
            )
            builder.put("nodes", terminal)
            pin_claim = builder.claim(
                terminal,
                "pin",
                label,
                ref,
                extractor,
                raw_confidence="explicit_report",
                conditions=_conditions(builder, sid, declaration_only=True),
            )
            builder.put(
                "edges",
                make_edge(
                    "equipment_terminal",
                    node["id"],
                    terminal_id,
                    claim_ids=[pin_claim["id"]],
                    metadata={"reported_not_geometry_verified": True},
                ),
            )
            builder.entity_map.setdefault(
                (sid, "pin:" + str(component.get("tag")) + ":" + label), []
            ).append(terminal_id)
    for connection in report.get("connections", []):
        local = str(connection.get("id") or stable_id("cad-connection", connection))
        ref = builder.ref(
            sid, {**connection.get("source", {}), "entity": local}, report.get("sha256")
        )
        endpoints = []
        for endpoint in (connection.get("from", {}), connection.get("to", {})):
            matches = builder.entity_map.get(
                (
                    sid,
                    "pin:"
                    + str(endpoint.get("device_tag"))
                    + ":"
                    + str(endpoint.get("pin")),
                ),
                [],
            )
            endpoints.append(matches[0] if len(matches) == 1 else None)
        connectivity_id = stable_id(
            "reported-connectivity-node", builder.scope_id, sid, local
        )
        connectivity = builder.put(
            "nodes",
            make_node(
                connectivity_id,
                "connectivity_node",
                builder.scope_id,
                identifiers={"raw_tag": connection.get("wire_number")},
                source_refs=[ref],
                metadata={
                    "source_local": True,
                    "declaration_only": True,
                    "geometry_verified": False,
                    "unresolved_endpoints": endpoints.count(None),
                },
            ),
        )
        connection_claim = builder.claim(
            connectivity,
            "connection",
            {"from": connection.get("from"), "to": connection.get("to")},
            ref,
            extractor,
            raw_value={
                "wire_number": connection.get("wire_number"),
                "from": connection.get("from"),
                "to": connection.get("to"),
            },
            raw_confidence="explicit_report",
            conditions=_conditions(builder, sid, declaration_only=True),
        )
        for terminal_id in endpoints:
            if terminal_id:
                builder.put(
                    "edges",
                    make_edge(
                        "terminal_connectivity_node",
                        terminal_id,
                        connectivity_id,
                        claim_ids=[connection_claim["id"]],
                        metadata={"reported_not_geometry_verified": True},
                    ),
                )
    if report.get("observed_bom_rows"):
        _visual(
            builder,
            {
                "schema_version": report.get("schema_version", "cad-report"),
                "source_hashes": {sid: report["sha256"]},
                "producer": {"name": "cad_report_rows"},
                "observed_bom_rows": report["observed_bom_rows"],
            },
        )
    builder.graph["scope_coverage"].append(
        {
            "source_id": sid,
            "reader": "cad_report",
            "status": report.get("status"),
            "source_row_counts": deepcopy(report.get("source_row_counts", {})),
            "imported_counts": deepcopy(report.get("imported_counts", {})),
            "inventory_complete": False,
            "connectivity_complete": False,
            "warnings": deepcopy(report.get("warnings", [])),
        }
    )


def adapt_sources(
    *,
    sources=None,
    rawcomparison=None,
    visual_predictions=None,
    cad_reports=None,
    scope_id: str,
    verified_identities=None,
    aliases=None,
    source_context=None,
    metadata=None,
) -> dict:
    """Build an additive graph with no authority to approve or release anything.

    ``source_context`` supplies caller-verified lineup / original-page ancestry.
    ``verified_identities`` is an explicit, cited identity link table. The default
    retains all entities source-locally, including exact tags on separate files.
    """
    source_metadata = [_plain(source) for source in _many(sources)]
    if rawcomparison:
        for analysis in [
            rawcomparison,
            *(rawcomparison.get(x, {}) for x in ("baseline", "target", "requirements")),
        ]:
            source_metadata.extend(
                _plain(source) for source in analysis.get("sources", [])
            )
    for prediction in _many(visual_predictions):
        source_metadata.extend(
            {"id": sid, "sha256": digest}
            for sid, digest in prediction.get("source_hashes", {}).items()
        )
    for report in _many(cad_reports):
        source_metadata.append({"id": report["source_id"], "sha256": report["sha256"]})
    merged = {}
    for source in source_metadata:
        sid = str(source.get("id", source.get("source_id")))
        if (
            sid in merged
            and source.get("sha256")
            and merged[sid].get("sha256")
            and source["sha256"].lower() != merged[sid]["sha256"].lower()
        ):
            raise ValueError("A source ID cannot identify different original bytes")
        merged[sid] = {**merged.get(sid, {}), **source}
    builder = _Builder(
        scope_id, list(merged.values()), source_context, verified_identities, metadata
    )
    if rawcomparison:
        _native(builder, rawcomparison)
    for prediction in _many(visual_predictions):
        _visual(builder, prediction)
    for report in _many(cad_reports):
        _cad(builder, report)
    for alias in aliases or []:
        if not validate_alias(alias, scope_id):
            builder.errors.append(
                {
                    "code": "unverified_or_cross_scope_alias_rejected",
                    "from": alias.get("from"),
                    "to": alias.get("to"),
                }
            )
            continue
        builder.put(
            "aliases", {"id": stable_id("alias", scope_id, alias), **deepcopy(alias)}
        )
    return builder.finish()


def from_comparison(comparison: dict, *, scope_id: str, **kwargs) -> dict:
    return adapt_sources(rawcomparison=comparison, scope_id=scope_id, **kwargs)


def from_visual_predictions(
    predictions: dict | list[dict], *, scope_id: str, **kwargs
) -> dict:
    return adapt_sources(visual_predictions=predictions, scope_id=scope_id, **kwargs)


def from_cad_reports(reports: dict | list[dict], *, scope_id: str, **kwargs) -> dict:
    return adapt_sources(cad_reports=reports, scope_id=scope_id, **kwargs)


def from_equipment_model(model: dict, *, scope_id: str, **kwargs) -> dict:
    """Adapt the existing compiled model while retaining all original sources.

    Old status labels such as 'verified' remain metadata; they are not an
    approval event and do not make the underlying extractor independent.
    """
    if hasattr(model, "model_dump"):
        model = model.model_dump(by_alias=True)
    comparison = {
        side: {"facts": [], "requested_changes": [], "sources": []}
        for side in ("baseline", "target", "requirements")
    }
    for device in model.get("devices", []):
        for feature, field in device.get("fields", {}).items():
            for category, side in (
                ("specified", "requirements"),
                ("requested", "target"),
                ("baseline", "baseline"),
                ("build", "target"),
            ):
                for observation in field.get(category, []):
                    source = observation["source"]
                    fact = {
                        "id": observation.get("observation_id"),
                        "source_id": source["document_id"],
                        "sha256": source["sha256"],
                        "device_tag": device["tag"],
                        "field": feature,
                        "value": observation.get("value"),
                        "raw_value": observation.get("raw_value"),
                        "unit": observation.get("unit"),
                        "evidence": source.get("location", {}),
                        "revision": source.get("revision"),
                        "role": source.get("role"),
                    }
                    comparison[side][
                        "requested_changes" if category == "requested" else "facts"
                    ].append(fact)
                    meta = {
                        "id": source["document_id"],
                        "sha256": source["sha256"],
                        "revision": source.get("revision"),
                        "role": source.get("role"),
                        "name": source.get("name"),
                    }
                    if meta not in comparison[side]["sources"]:
                        comparison[side]["sources"].append(meta)
    observations = model.get("source_observations", {})
    inferred_sources = []
    for snapshot in model.get("vector_drawings", []):
        source = snapshot.get("source", {})
        if source.get("document_id") and source.get("sha256"):
            inferred_sources.append(
                {
                    "id": source["document_id"],
                    "sha256": source["sha256"],
                    "role": source.get("role"),
                    "revision": source.get("revision"),
                    "side": snapshot.get("side"),
                }
            )
    for row in observations.get("visual_schedule_rows", []) + observations.get(
        "native_schedule_rows", []
    ):
        if row.get("source_id") and row.get("sha256"):
            inferred_sources.append(
                {
                    "id": row["source_id"],
                    "sha256": row["sha256"],
                    "role": row.get("role"),
                    "revision": row.get("revision"),
                }
            )
    inferred_sources.extend(
        _plain(source) for source in _many(kwargs.pop("sources", None))
    )
    hashes = {source["id"]: source["sha256"] for source in inferred_sources}
    for side in comparison.values():
        hashes.update({source["id"]: source["sha256"] for source in side["sources"]})
    visual = {
        "schema_version": "existing-model-source-observations",
        "source_hashes": hashes,
        "producer": {"name": "existing_model_visual_projection"},
        "observed_bom_rows": observations.get("visual_schedule_rows", []),
        "devices": observations.get("visual_devices", []),
        "connections": observations.get("visual_connections", []),
        "sheet_identities": observations.get("sheet_identities", []),
    }
    native_rows = {
        "schema_version": "existing-model-native-schedule-rows",
        "source_hashes": hashes,
        "producer": {"name": "existing_model_native_schedule_projection"},
        "observed_bom_rows": observations.get("native_schedule_rows", []),
    }
    return adapt_sources(
        rawcomparison=comparison,
        sources=inferred_sources,
        visual_predictions=[native_rows, visual],
        cad_reports=observations.get("cad_reports", []),
        scope_id=scope_id,
        **kwargs,
    )
