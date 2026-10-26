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

The supplied manufacturer package does not yet establish those inputs. The
real-data run verifies terminology edits and extracts a partial schedule; it
does not validate physical layout, bushing relocation, CT selection, grounding,
tap connections, enclosure compliance, corrected BOMs or production release.
The existing fictional native editing tests remain software controls only.
