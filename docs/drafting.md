# Draft drawing and BOM edits

Rivet can prepare a new editable **DXF** and **Excel BOM** revision from an exact,
evidence-linked edit plan. CSV BOMs are supported too. The adapter is implemented
in `backend/drafting/`; it is a local file integration, not a connection to
AutoCAD, SolidWorks, a proprietary CAD API, or a production file vault.

## What is implemented

- Change a selected DXF `INSERT` block's single-line `ATTRIB` value. The block
  handle, block name, unique `EQUIPMENT_TAG`, attribute tag, source value and
  source SHA-256 must all match.
- Change one exact Excel/CSV BOM cell selected by a unique row key and column.
  Existing Excel formatting, the other cells and the other worksheets survive.
  Formula cells and formula-like replacements are rejected.
- Prepare a new drawing and BOM copy with draft revision metadata. A new DXF
  paper layout records `DRAFT REV D - ENGINEERING APPROVAL REQUIRED`; the
  company's source titleblock is not silently interpreted or overwritten.
- Create a drafting task for a layout change, tied to the block's actual DXF
  insertion point and bounding box. No breaker or other geometry is moved.
- Produce highlighted before/after SVGs, a readable `review.html`, and a hashed
  `report.json` linking each change to its source evidence and decision record.
- Reopen the serialized DXF and BOM and verify the requested values before
  publishing a bundle. Source files are checked again before publication.

Every artifact is **pending engineering approval**. Preparing or downloading a
bundle never approves the edit or releases a drawing to production.

## Reproduce the local demo

From the repository root:

```sh
uv sync --frozen
.venv/bin/python -m scripts.build_drafting_demo --output .data/drafting-demo-new
```

Use a new output directory each time. Existing inputs and bundles are never
overwritten. The generator creates:

```text
originals/E-10-Rev-C.dxf
originals/BOM-Rev-C.xlsx
edit-plan.json
draft-Rev-D/E-10-draft-Rev-D.dxf
draft-Rev-D/BOM-draft-Rev-D.xlsx
draft-Rev-D/before.svg
draft-Rev-D/after.svg
draft-Rev-D/review.html
draft-Rev-D/report.json
bundle.json
```

The fixture is labelled **fictional local demo**. It contains three proposed
changes: `CB-12.TRIP_UNIT`, `CB-12.NAMEPLATE`, and BOM line `10.part_number`.
One layout request is handed to the drafter at the source block's exact
coordinates. The fixture's engineer confirmation is fictional and must not be
presented as an actual customer or engineering approval.

The trip-unit edit assumes an explicit upstream decision about the governing
specification. A conflicting source alone does not authorize selecting a new
trip unit. Plans with `decision_status="needs_clarification"` are rejected.

## Server integration

The caller must authenticate the user, establish organization/order ownership,
and resolve trusted source files before calling this pure file adapter. The
browser must never supply arbitrary filesystem paths or edit allowlists.

```python
from backend.drafting import prepare_revision, load_bundle, read_bundle_artifact

# request is an authenticated, validated DraftRevisionRequest; all Paths below
# are resolved by the server from trusted file records.
destination = DATA / "drafting" / str(request.organization_id) / str(request.order_id) / str(request.bundle_id)
bundle = prepare_revision(
    request,
    drawing_path=source_dxf,
    bom_path=source_bom,
    destination=destination,
)

# Save bundle.report_sha256 along with the bundle ID in the order record.
checked = load_bundle(
    destination / "report.json",
    organization_id=request.organization_id,
    order_id=request.order_id,
    expected_report_sha256=bundle.report_sha256,
)

# API routes return bytes from exact manifest membership, not a browser path.
contents, artifact = read_bundle_artifact(
    destination / "report.json",
    artifact_name="E-10-draft-Rev-D.dxf",
    organization_id=request.organization_id,
    order_id=request.order_id,
    expected_report_sha256=bundle.report_sha256,
)
```

Bundle fields: `bundle_id`, `organization_id`, `order_id`, `status`,
`source_revision`, `target_revision`, `report_path`, `report_sha256`, `artifacts`,
`changes`, `layout_tasks`. Artifact entries provide `name`, `role`, `media_type`
and `sha256`. A lifecycle record can reference a specific `changes[].id` and
must match its `evidence_id`; merely pointing at a bundle does not establish
that an arbitrary comment was addressed.

## Limits and approval

- Editable UTF-8 DXF only. DWG, PDF-to-CAD conversion, geometry editing,
  proprietary CAD automation and external vault write-back are not included.
- Supported edits are simple text/data values on known block attributes. Arrays,
  ambiguous tags, constant attributes, multiline attributes and XREF targets are
  rejected. An unresolved value must be clarified before preparing an edit.
- The SVG is a review view rendered from the actual DXF primitives. It supports
  straight lines/polylines, circles, text and block instances. Unsupported
  primitive counts are disclosed. The native DXF is the engineering artifact.
- No engineering validation, clearance check, electrical calculation, drawing
  release or external system modification is performed by this adapter.
- Lifecycle approval is separate and must record the authorized engineer's
  decision against the exact bundle hash. Further CAD edits invalidate that
  approval and require a new bundle.

## Verification

```sh
.venv/bin/python -m pytest --noconftest tests/test_drafting_revision.py -q
.venv/bin/ruff check backend/drafting scripts/build_drafting_demo.py tests/test_drafting_revision.py
```

These are pure file tests and do not touch or reset a database. They cover native
DXF and Excel output values, unchanged source bytes and geometry, stale source
hashes, missing/ambiguous targets, rejected formulas, all-or-nothing failures,
released-input copying, pending approval, tenant checks, tampered artifacts and
unsupported DWG input.

Implementation references: [ezdxf block attributes](https://ezdxf.readthedocs.io/en/stable/tutorials/blocks.html),
[DXF attribute entities](https://ezdxf.readthedocs.io/en/stable/blocks/attrib.html),
[saving a new DXF](https://ezdxf.readthedocs.io/en/stable/usage_for_beginners.html).
