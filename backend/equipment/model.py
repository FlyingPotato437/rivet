"""Typed, versioned specified-versus-build state from equipment observations.

The model is a projection of immutable source observations, not an inferred
electrical design. A device tag, insert position or nearby line cannot establish
assembly membership, connectivity or physical dimensions.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class SourceAnchor(FrozenModel):
    document_id: str
    name: str
    revision: str
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    role: Literal["drawing", "bom", "specification", "markup"]
    location: dict


class EquipmentObservation(FrozenModel):
    observation_id: str
    value: str | int | float
    unit: str | None = None
    raw_value: str
    source: SourceAnchor


class EquipmentField(FrozenModel):
    decision: dict = Field(default_factory=dict)
    specified: list[EquipmentObservation] = Field(default_factory=list)
    requested: list[EquipmentObservation] = Field(default_factory=list)
    baseline: list[EquipmentObservation] = Field(default_factory=list)
    build: list[EquipmentObservation] = Field(default_factory=list)
    status: Literal["verified", "unchanged", "mismatch", "missing", "unknown"] = (
        "unknown"
    )
    agreement: Literal["consistent", "conflicting", "missing"] = "missing"


class NativeRelationship(FrozenModel):
    status: Literal["unknown", "observed"] = "unknown"
    observations: list[EquipmentObservation] = Field(default_factory=list)
    detail: str


class EquipmentDevice(FrozenModel):
    tag: str
    observed_in_current_package: bool
    fields: dict[str, EquipmentField]
    parent: NativeRelationship
    connectivity: NativeRelationship
    dimensions: NativeRelationship


class NativeTerminal(FrozenModel):
    id: str
    name: str
    position: tuple[float, float]
    source: dict


class NativeDesignDevice(FrozenModel):
    tag: str
    handle: str
    block_name: str
    position: tuple[float, float]
    bounds: tuple[float, float, float, float]
    width: float
    height: float
    terminals: list[NativeTerminal]


class NativeWire(FrozenModel):
    id: str
    handle: str
    from_terminal: str = Field(alias="from")
    to_terminal: str = Field(alias="to")
    net_id: str
    points: list[tuple[float, float]]
    actual_from: str | None = None
    actual_to: str | None = None


class NativeDesignSnapshot(FrozenModel):
    source: SourceAnchor
    units: Literal["mm"]
    status: str
    devices: list[NativeDesignDevice]
    wires: list[NativeWire]
    checks: list[dict]


class PDFDrawingSnapshot(FrozenModel):
    source: SourceAnchor
    side: Literal["baseline", "target"]
    coordinate_system: Literal["visible-page-points"] = "visible-page-points"
    physical_scale: None = None
    pages: list[dict]
    devices: list[dict]
    connections: list[dict]
    issues: list[dict]
    coverage: dict
    provenance: dict


class EquipmentModel(FrozenModel):
    schema_version: Literal["1"] = "1"
    version: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    baseline_revision: str
    target_revision: str
    revision_id: str = Field(alias="revisionId")
    observed_device_count: int = Field(ge=0)
    devices: list[EquipmentDevice]
    coverage: dict
    limitations: list[str]
    native: NativeDesignSnapshot | None = None
    vector_drawings: list[PDFDrawingSnapshot] = Field(default_factory=list)
    pdf_revision_check: dict | None = None
    company_rules: dict | None = None
    source_observations: dict = Field(default_factory=dict)
    evidence_graph: dict = Field(default_factory=dict)
    reconciliation: dict = Field(default_factory=dict)
    engineering_approved: Literal[False] = False
    production_released: Literal[False] = False


def _observation(fact):
    # Only supported provenance keys are projected. A future caller cannot
    # accidentally serialize internal storage paths in source metadata.
    allowed = {
        "page",
        "bbox",
        "coordinate_system",
        "sheet",
        "row",
        "cell",
        "column",
        "cad_handle",
        "attribute_handle",
        "attribute",
        "block_name",
        "anchor_xyz",
        "text",
        "xml_path",
    }
    return EquipmentObservation(
        observation_id=fact["id"],
        value=fact["value"],
        unit=fact.get("unit"),
        raw_value=str(fact.get("raw_value", fact["value"])),
        source=SourceAnchor(
            document_id=fact["source_id"],
            name=fact.get("source_name", "Source original"),
            revision=fact.get("revision", "Unspecified"),
            sha256=fact["sha256"],
            role=fact["role"],
            location={
                key: value
                for key, value in fact.get("evidence", {}).items()
                if key in allowed
            },
        ),
    )


def _native(facts, names):
    return [
        _observation(fact)
        for fact in facts
        if fact["field"] in names
        and fact.get("role") == "drawing"
        and fact.get("evidence", {}).get("cad_handle")
    ]


def compile_model(
    rawcomparison,
    fingerprint,
    baseline_revision,
    target_revision,
    *,
    native_snapshot=None,
    pdf_snapshots=(),
    pdf_revision_check=None,
):
    """Compile desired and actual equipment state without promoting either.

    An explicit requirement remains specified even when the actual BOM differs.
    Customer markup requests are a third state, not approved build facts.
    Conflicting observations are retained; no vote or last-upload rule chooses a
    convenient value. All current equipment observations are supported fields,
    never an independently established complete device inventory.
    """
    from .evidence.integration import decision_for, ensure_reconciled

    rawcomparison = ensure_reconciled(rawcomparison)
    baseline = [
        fact
        for fact in rawcomparison.get("baseline", {}).get("facts", [])
        if fact.get("role") in {"drawing", "bom"}
    ]
    build = [
        fact
        for fact in rawcomparison.get("target", {}).get("facts", [])
        if fact.get("role") in {"drawing", "bom"}
    ]
    specified = [
        fact
        for fact in rawcomparison.get("requirements", {}).get("facts", [])
        if fact.get("role") == "specification"
    ]
    requests = [
        fact
        for side in ("baseline", "target", "requirements")
        for fact in rawcomparison.get(side, {}).get("requested_changes", [])
    ]
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for label, observations in (
        ("specified", specified),
        ("requested", requests),
        ("baseline", baseline),
        ("build", build),
    ):
        for fact in observations:
            grouped[fact["device_tag"]][fact["field"]][label].append(fact)
    checks = {
        (check["device_tag"], check["field"]): check
        for check in rawcomparison.get("checks", [])
    }
    current_tags = {fact["device_tag"] for fact in build}
    for report in rawcomparison.get("target", {}).get("sources", []):
        if report.get("role") in {"drawing", "bom"}:
            current_tags.update(report.get("observed_device_tags", []))
    for tag in current_tags:
        grouped[tag]
    for decision in rawcomparison.get("decisions", []):
        if (
            decision.get("side") == "target"
            and decision.get("state") == "observed"
            and decision.get("device_tag") in current_tags
        ):
            grouped[decision["device_tag"]][decision["feature"]]
    devices = []
    for tag, fields in sorted(grouped.items()):
        compiled_fields = {}
        for name, groups in sorted(fields.items()):
            actual = groups.get("build", [])
            values = {
                json.dumps([fact["value"], fact.get("unit")], sort_keys=True)
                for fact in actual
            }
            agreement = (
                "missing"
                if not values
                else "consistent"
                if len(values) == 1
                else "conflicting"
            )
            status = checks.get(
                (tag, name), checks.get((tag, "device_presence"), {})
            ).get("status", "unknown")
            decision = decision_for(rawcomparison, tag, name)
            if decision:
                if decision.get("status") == "conflict" or decision.get(
                    "constraint_failures"
                ):
                    status, agreement = "mismatch", "conflicting"
                elif decision.get("needs_review", True) and status not in {
                    "mismatch",
                    "missing",
                }:
                    status = "unknown"
            compiled_fields[name] = EquipmentField(
                decision=decision or {},
                **{
                    label: [_observation(fact) for fact in groups.get(label, [])]
                    for label in ("specified", "requested", "baseline", "build")
                },
                status=status,
                agreement=agreement,
            )
        native_facts = [fact for fact in build if fact["device_tag"] == tag]
        parent = _native(native_facts, {"parent_tag", "parent_assembly"})
        connectivity = _native(
            native_facts, {"connected_to", "connectivity", "from_tag", "to_tag"}
        )
        dimensions = _native(
            native_facts, {"width_mm", "height_mm", "depth_mm", "diameter_mm"}
        )
        devices.append(
            EquipmentDevice(
                tag=tag,
                observed_in_current_package=tag in current_tags,
                fields=compiled_fields,
                parent=NativeRelationship(
                    status="observed" if parent else "unknown",
                    observations=parent,
                    detail="Only an explicit native parent attribute is retained; tags or drawing proximity do not establish parentage.",
                ),
                connectivity=NativeRelationship(
                    status="observed" if connectivity else "unknown",
                    observations=connectivity,
                    detail="No wiring graph has been reconstructed. Only explicitly supplied native connectivity facts are retained.",
                ),
                dimensions=NativeRelationship(
                    status="observed" if dimensions else "unknown",
                    observations=dimensions,
                    detail="Insert coordinates and preview geometry are not engineering dimensions. Explicit native dimensional facts are required.",
                ),
            )
        )
    coverage = {
        **rawcomparison.get("coverage", {}),
        "complete_inventory_verified": False,
        "connectivity_reconstructed": False,
        "geometry_validated": False,
        "current_source_issues": rawcomparison.get("summary", {}).get(
            "source_issues", 0
        ),
        "requirement_only_device_count": sum(
            device.tag not in current_tags for device in devices
        ),
    }
    native = None
    if native_snapshot:
        graph, source = native_snapshot
        native = NativeDesignSnapshot(
            source=SourceAnchor(
                document_id=source["id"],
                name=source["name"],
                revision=source["revision"],
                sha256=source["sha256"],
                role="drawing",
                location={"coordinate_system": "DXF modelspace / mm"},
            ),
            units=graph["units"],
            status=graph["status"],
            devices=[
                NativeDesignDevice(**{k: d[k] for k in NativeDesignDevice.model_fields})
                for d in graph["devices"]
            ],
            wires=[
                NativeWire(
                    **{
                        k: w[k]
                        for k in (
                            "id",
                            "handle",
                            "from",
                            "to",
                            "net_id",
                            "points",
                            "actual_from",
                            "actual_to",
                        )
                    }
                )
                for w in graph["wires"]
            ],
            checks=graph["checks"],
        )
        by_tag = {d.tag: d for d in native.devices}
        compiled = []
        for device in devices:
            observed = by_tag.get(device.tag)
            if not observed:
                compiled.append(device)
                continue
            incident = [
                w
                for w in native.wires
                if (w.actual_from or "").split(":")[0] == device.tag
                or (w.actual_to or "").split(":")[0] == device.tag
            ]

            def observation(key, value, handle):
                return EquipmentObservation(
                    observation_id=f"native:{source['sha256']}:{key}:{handle}",
                    value=value,
                    raw_value=str(value),
                    unit="mm" if key in {"width", "height"} else None,
                    source=SourceAnchor(
                        **{
                            **native.source.model_dump(),
                            "location": {
                                "cad_handle": handle,
                                "coordinate_system": "DXF modelspace / mm",
                            },
                        }
                    ),
                )

            connections = [
                observation("wire", f"{w.actual_from} → {w.actual_to}", w.handle)
                for w in incident
                if w.actual_from and w.actual_to
            ]
            compiled.append(
                device.model_copy(
                    update={
                        "connectivity": NativeRelationship(
                            status="observed" if connections else "unknown",
                            observations=connections,
                            detail="Actual saved conductor endpoints traced to transformed native terminals; crossings without a junction do not establish a connection.",
                        ),
                        "dimensions": NativeRelationship(
                            status="observed",
                            observations=[
                                observation("width", observed.width, observed.handle),
                                observation("height", observed.height, observed.handle),
                            ],
                            detail="Measured from the transformed native body polygon in millimetres; engineering tolerances and electrical clearances remain separate.",
                        ),
                    }
                )
            )
        devices = compiled
        coverage.update(
            connectivity_reconstructed=native.status == "verified",
            geometry_validated=native.status == "verified",
            native_observed_wire_count=len(native.wires),
            native_observed_terminal_count=sum(
                len(d.terminals) for d in native.devices
            ),
        )
    vector_drawings = []
    by_tag = {device.tag: device for device in devices}
    for report, source, side in pdf_snapshots:
        anchor = SourceAnchor(
            document_id=source["id"],
            name=source["name"],
            revision=source["revision"],
            sha256=source["sha256"],
            role="drawing",
            location={"coordinate_system": "visible-page-points"},
        )
        vector_drawings.append(
            PDFDrawingSnapshot(
                source=anchor,
                side=side,
                **{
                    key: report[key]
                    for key in (
                        "pages",
                        "devices",
                        "connections",
                        "issues",
                        "coverage",
                        "provenance",
                    )
                },
            )
        )
        if side != "target":
            continue
        for drawn in report["devices"]:
            tag = drawn["tag"]
            current_tags.add(tag)
            device = by_tag.get(tag)
            if not device:
                device = EquipmentDevice(
                    tag=tag,
                    observed_in_current_package=True,
                    fields={},
                    parent=NativeRelationship(
                        detail="A schematic symbol does not establish physical assembly membership."
                    ),
                    connectivity=NativeRelationship(
                        detail="No uniquely labelled vector connection was established."
                    ),
                    dimensions=NativeRelationship(
                        detail="PDF page points establish schematic layout, not physical equipment dimensions."
                    ),
                )
            else:
                device = device.model_copy(update={"observed_in_current_package": True})
            observations = []
            if not drawn["symbol"]["status"].startswith("unknown"):
                labelled = {port["id"] for port in drawn["ports"] if port.get("label")}
                for connection in report["connections"]:
                    if connection["page"] != drawn["page"] or not labelled.intersection(
                        connection["terminal_ids"]
                    ):
                        continue
                    evidence = connection["evidence"][0]
                    observations.append(
                        EquipmentObservation(
                            observation_id=f"pdf-net:{source['sha256']}:{connection['id']}",
                            value=" ↔ ".join(connection["terminal_ids"]),
                            raw_value=" ↔ ".join(connection["terminal_ids"]),
                            source=anchor.model_copy(
                                update={
                                    "location": {
                                        "page": evidence["page"],
                                        "bbox": evidence["normalized_bbox"],
                                        "coordinate_system": "normalized-visible-page",
                                        "path_indices": evidence.get(
                                            "path_indices", []
                                        ),
                                    }
                                }
                            ),
                        )
                    )
            # A native measured graph stays authoritative when both formats
            # are supplied. PDF observations cannot replace its physical units.
            if observations and device.connectivity.status != "observed":
                device = device.model_copy(
                    update={
                        "connectivity": NativeRelationship(
                            status="observed",
                            observations=observations,
                            detail="Labelled contacts traced through actual visible PDF vector strokes. This is bounded schematic evidence; cross-sheet and unaccounted ink remain unknown.",
                        )
                    }
                )
            by_tag[tag] = device
    devices = sorted(by_tag.values(), key=lambda device: device.tag)
    if vector_drawings:
        coverage.update(
            pdf_graph_coverage="unknown"
            if any(v.coverage["status"] == "unknown" for v in vector_drawings)
            else "bounded",
            pdf_observed_connection_count=sum(
                len(v.connections) for v in vector_drawings if v.side == "target"
            ),
            pdf_physical_dimensions_verified=False,
        )
    model = EquipmentModel(
        evidence_graph=rawcomparison.get("evidence_graph", {}),
        reconciliation=rawcomparison.get("reconciliation", {}),
        version=fingerprint,
        baseline_revision=baseline_revision or "",
        target_revision=target_revision or "",
        revisionId=target_revision or "",
        observed_device_count=len(current_tags),
        devices=devices,
        native=native,
        vector_drawings=vector_drawings,
        pdf_revision_check=pdf_revision_check,
        coverage=coverage,
        limitations=[
            "Specified values, requested changes and actual build observations are separate states; a requirement is never evidence that a drawing or BOM changed.",
            "Native terminal, wire and body geometry checks apply only to the declared CAD adapter when provided. No protection coordination, electrical-code certification, engineering approval or production release is implied."
            if native
            else "This projection checks supported literal equipment properties. No complete wiring trace, assembly graph, dimensional validation, engineering approval or production release is implied.",
            "Conflicting source observations remain visible. Missing or unparsed data stays unknown.",
        ],
    )
    return model.model_dump(mode="json", by_alias=True)
