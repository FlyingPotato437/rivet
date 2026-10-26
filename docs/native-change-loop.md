# Native change loop: equipment evidence and saved-file verification

The existing private drawing-redesign job now also extracts component schedules
from placed native CAD geometry and independently reparses saved corrections.
It remains a bounded draft-authoring workflow. No UI, sending, release or
engineering-approval authority was added.

## What was already present

- An equipment model with separate specified, requested and observed states.
- DXF attribute/BOM edits and structural checks for supported native conventions.
- A large-file ASCII DXF reader and byte-preserving terminology editor.
- Comment intake, response exports, protected artifacts and Outlook draft paths.

The manufacturer drawing path previously exposed text and geometry snapshots,
but did not use its embedded component schedule. Its after previews substituted
the planned strings into the before snapshot, although the underlying writer
already checked saved text and untouched bytes. These are different guarantees.

## What changed

`backend/engineering/cad_evidence.py` follows native rectangular table cells
under explicit No/Symbol/Name headers. Every cell keeps its bounding box, native
locator, hash, literal text and observed/blank/ambiguous status. Blank column
headings remain unlabelled. A column of numbers is not assumed to be quantity.
Finite printed tag ranges can expand; open-ended ranges such as KM1-KMn cannot.
Matching labels elsewhere produce review candidates, not merged physical
devices, verified connections or a complete production inventory.

The equipment reader uses this bounded streaming path for drawings between
30 MiB and 512 MiB. It returns partial coverage, source-linked schedule rows
and observed tag candidates. It does not bypass the native editor's limits or
invent ratings and part numbers. Snapshot limits and a separate schedule time
budget remain in force; failures stay visible.

The redraw job now reopens the saved DXF with a parser separate from the writer,
checks each affected placement, verifies unchanged supported geometry/units,
and generates after crops from that saved snapshot. Every observed placement
of an edited block definition gets evidence. Incomplete placement coverage
withholds a pass for shared-definition edits. The existing byte-level check
also proves that bytes outside the targeted text values were preserved,
including geometry the reference renderer does not support.

Non-text PDF annotations now remain in an explicit visual-review register.
A prepared naming clause does not close the other clauses of a comment.
A generic weight-related request no longer causes an unevidenced assertion
that drawing totals disagree.

## Drawing calculations in the native job

The next bounded operator corrects whole-ampere values in an explicitly
single-phase tertiary current table. It requires the customer's current-update
request and a voltage list on the same source page, exact native cell boundaries,
voltage/current unit headers, an explicit phase count, and MVA headers that
agree with the nameplate capacity stages. Conflicting capacity columns remain
unchanged. Duplicate values, ambiguous cells, missing boundaries, shared numeric
definitions and unsupported phase/unit conventions stop the calculation.

This is intentionally narrow: the recognized request is “Update amp values to
reflect correct voltages,” paired with a list of voltages. It is not a general
electrical design planner. It neither infers transformer ratings nor changes
windings, protective devices, connections or physical geometry.

The writer changes only the selected native text fields in a copy. The second
parser reopens that copy, and a separate exact-fraction calculation recomputes
each result from the saved inputs. Incorrect saved arithmetic withholds the
review ZIP even if the text writer reports success. The background job retains
the input evidence, capacity conflicts and saved checks. Numerical success does
not mark the engineering request approved or the drawing released.

Before editing, compare the supplied native file with the marked drawing: a
manufacturer may already have incorporated some comments. Existing changes
must not be redone or attributed to Rivet. An observed placement in a partial
reference render is not proof of clearance, mounting, connections or compliance.

## BOMs remain part of the normal workflow

A project BOM is an expected input when available, alongside native drawings
and returned comments. The existing drawing/BOM correction and independent
readback paths remain supported and regression-tested. One package without a
BOM does not change the product's data model or imply the manufacturer does not
normally maintain one. An embedded drawing schedule remains source evidence;
it is not promoted to a production BOM or used to invent parts and quantities.

## Private evaluation

Create a private manifest containing `sources` with `id`, `name`, `path`,
`sha256` and `role`, plus `native_source_id`. Keep customer originals and keys
under ignored `.data/` storage. Run from the repository root:

```sh
.venv/bin/python -m scripts.evaluate_native_change_loop \
  .data/private-evaluation/manifest.json \
  --output .data/private-evaluation/new-run \
  --answer-key .data/private-evaluation/development-key.json
```

The destination must be new. No models, external uploads, mailbox actions or
payments run. Outputs include the proposed native copy, before/after views,
equipment evidence, raw schedule export, comment register and evaluation JSON.
Keys bind to the original hash and identify a table by its header locator,
row and field. Report populated-cell agreement separately from preserved blank
cells. A key reviewed against our own CAD renderer is a development regression
control, not independent extraction accuracy or an unseen test.

## Still requires manufacturer input and an engineer

For physical/electrical edits, obtain the matching BOM/component export,
terminal/connection report, device/block mapping and governing design rules.
Use an engineer-completed next revision as a held-out expected result. Agree
one recurring change family and measure requested changes caught, missed,
incorrect changes and collateral edits independently of extraction coverage.

The supplied manufacturer package does not yet establish all those inputs. The
real-data run verifies terminology edits, bounded current-table arithmetic and
a partial schedule; it does not validate physical layout, bushing relocation,
CT selection, grounding, tap connections, enclosure compliance, corrected BOMs
or production release. Some requested physical changes are already visible in
the supplied native file; they remain unverified engineering outcomes.
The existing fictional native editing tests remain software controls only.
