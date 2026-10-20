# Native equipment correction loop

Rivet can prepare a new DXF and XLSX/CSV BOM from explicit, source-backed literal requirements. It reopens those files and checks their actual values before offering them as draft attachments. This is a bounded authoring workflow, not autonomous engineering or production release.

## Inputs and plan

`backend/equipment/corrections.py` exposes:

- `plan_corrections(baseline_sources, target_sources, requirement_sources=None)`
- `prepare_corrections(..., destination=..., expected_plan_id=..., target_revision=...)`

Inputs use the existing immutable `SourceInput` contract. The authenticated caller selects tenant-owned originals; a model cannot submit replacement values, paths, edit allowlists or CAD handles. The engine derives exact selectors from the native files and desired values from explicit specification or requested-change observations.

The initial editor supports one native DXF drawing and one CSV/XLSX BOM, exact equipment tags, explicit block attributes and literal cells. It covers frame/trip ratings, trip-unit identifiers, part numbers, quantity, nameplate, control voltage and interrupting rating. Contradictory governing values, formula cells, ambiguous device instances, missing devices, unreadable requested changes and unsupported layout/topology requests block preparation.

## Output and independent check

The destination must be new. Originals are hash-checked before and after preparation. The new bundle contains:

- A draft DXF with edited attributes and a separate draft-review layout.
- A draft BOM. Workbooks receive a separate Rivet draft sheet and visible print-header status.
- A PDF preview containing supported geometry rendered from the saved DXF and labelled equipment values exported from its actual attributes.
- A manifest with source/output hashes and exact before/after changes.

The existing comparison engine independently reopens the saved DXF, BOM and PDF together. The supported governing requirements must match, no known checks may remain unresolved, and the basic equipment-data rules must pass. A corrupted preview, stale input or incorrect serialized edit prevents publication. Original PDF snapshots remain unchanged and are not mixed into the new corrected revision.

`attachable` means the draft copies passed this bounded source check. Engineering approval, contractual acceptance, release and sending remain separate decisions. Wiring, dimensions, clearance, compatibility, protection coordination and regulatory compliance are not inferred.

## Typed equipment state

`backend/equipment/model.py` compiles a versioned Pydantic equipment model from comparison observations. Each field retains separate specified, requested, baseline and build values with source anchors. Conflicting observations remain separate. The version is the source fingerprint; device counts are observed counts, not a complete-inventory assertion.

Parent relationships, connectivity and dimensions stay unknown unless explicit native facts establish an observation. A block insertion point or a nearby line never becomes a wiring edge or physical dimension.

## Verification

Pure tests can run without the repository's database fixture:

```sh
uv run pytest --noconftest -q tests/test_equipment_corrections.py tests/test_equipment_model.py
```

These cover actual DXF attributes and workbook/CSV cells, original-byte preservation, native geometry preservation, independent PDF/native rechecking, stale hashes, formulas, ambiguous instances, missing geometry, scanned requests and impossible trip/frame combinations. Fixtures are explicitly fictional engineering packages; performance on real manufacturer packages still requires pilot validation.
