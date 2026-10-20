# Proposed drawing package export

`backend.engineering.generation.generate_package(selected_design, output_dir=...)`
exports one explicitly selected, versioned configuration. It does not choose an
option, approve engineering, send mail, or release equipment.

The output contains an editable one-line DXF, schematic front-elevation DXF,
equipment BOM XLSX and CSV, nameplate CSV, assembled submittal PDF, immutable
model snapshot and hash manifest. The PDF drawing pages are rendered from the
saved DXFs using the existing ezdxf renderer. The BOM retains numeric cells and
explicit unknowns. Every generated file records the same model version and
proposed status. A changed snapshot cannot overwrite an existing package.

## Model and drawing semantics

- Pass one configurator configuration, or a wrapper with `configuration`,
  `model_id`, and `model_version`. An option list is rejected rather than chosen
  silently. Device and section IDs must be distinct.
- One BOM row represents one proposed physical device instance. Customer-owned
  items and unknown supply status remain explicit; neither establishes a
  supplied component.
- `interrupting_ka` is the device interrupting rating. Conditional catalog
  `catalog_sccr_ka` is retained in a separate XLSX/CSV field. It is never copied
  into device kAIC or promoted into a verified assembly rating.
- A connected one-line requires the configurator's explicit proposed topology:
  one main load side, one common bus, and explicit feeder line-side branches.
  Dangling, duplicate, unsupported or unaccounted connections are rejected.
  Without topology, symbols remain disconnected and confirmation is requested.
- Logical connections carry `RIVET_GENERATION` metadata. They are not native
  terminal/wire assertions. No physical terminal geometry or wiring is invented.
- DXF device INSERTs retain `EQUIPMENT_TAG`, `FRAME_A`, `TRIP_A`, `TRIP_UNIT`,
  `DEVICE_FRAME` and the existing equipment reader's other attribute names.
  Numeric ampere attributes use supported `A` units. XLSX/CSV uses the native equipment table contract; the CAD report importer recognizes that table before considering component-report formats.
- Front elevation is **schematic and not to scale**. Equal section boxes are
  plotting geometry. Known model dimensions are labelled; missing dimensions
  remain `UNKNOWN` and their regions are crossed. Dense assignments continue in
  additional views of the same section rather than omitting devices or creating
  extra physical sections. Production-elevation requests are refused.
- S1/S2 and P1/P2 are proposed drawing reference labels, with full model-ID
  mappings in `drawing_reference_labels`. They do not assert customer sheet IDs.

## Shop assets

Supply a template such as:

```python
template = {
    "id": "manufacturer-template",
    "version": "2026-01",
    "library_path": "/authorized/local/shop-blocks.dxf",
    "library_sha256": "<exact source SHA256>",
    "blocks": {"breaker": "SHOP_BREAKER", "title_block": "SHOP_TITLE"},
    "elevation_mode": "schematic",
}
```

Named blocks are imported from the pinned library. Existing block artwork is
retained while the exporter attaches model attributes. V1 uses sheet/plot
millimetres. The shop must supply appropriately sized schematic blocks; this
does not adapt arbitrary production mechanical geometry automatically.

## Cut sheets

No catalogs are fetched or guessed. A cut-sheet declaration supplies `path`,
`sha256`, explicit 1-based physical `pages`, `component_ids`, `verified: true`,
`source_ref`, and `identity`. All mapped model devices must be supplied. Known
exact part numbers must match; if the part number is unknown, the cited
manufacturer/family/frame identity must match. A family page remains a family
reference and does not become an exact selected SKU.

Missing, ambiguous, unrelated, hash-mismatched or by-others declarations are
excluded with a reason. Source bytes are rechecked before assembly. Selected
original PDF pages receive a separate proposed/model-version footer margin,
without obscuring their content. The pure library caller is responsible for verifying the component/source mapping and authorizing local source paths. The application API resolves owned document IDs to server paths and requires an explicit engineer review; clients cannot submit paths.

## Validation boundary

The generation controls use synthetic models only and do **not** measure real
design accuracy. Real reconstruction must begin from customer-side requirements
and be scored against a separately held manufacturer package. Export consistency
is distinct from engineering validity, manufacturer's drafting standards,
terminal wiring, clearances, mechanical fit and approval.

To match a pilot's production style we still need its title block, named symbol
library and conventions, exact bill-of-material export schema, part-selection
rules, mounting/clearance rules, and examples of approved drawing packages.

## Independent saved-file readback

System scalars are serialized into hidden version-bound DXF attributes and a literal-JSON XLSX system sheet, then reread from those saved files. Untagged model devices use an opaque model-local native identity bound to the exact model ID/version/device ID; that internal key never becomes a source equipment tag. Source labels remain unknown where not printed. Duplicate or foreign bindings are rejected.

For matching proposed generated objects only, a blank spreadsheet cell and DXF `UNKNOWN` can be representation-consistent. The result explicitly says `engineering_value_known=false`; no known equipment fact is emitted, no source unknown is resolved, and no engineering approval follows. Known-versus-unknown and ordinary unbound unknown comparisons still fail closed.
