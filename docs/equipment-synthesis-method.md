# Equipment reconstruction and revision evidence

Rivet now has two additional diagnostics over the reconciled evidence graph. They
do not change drawings, approve engineering, or convert missing observations into
known absence.

## Analysis by synthesis

`backend/equipment/evidence/synthesis.py` accepts the graph, its resolutions, and
an exact hash-bound source page. It writes two reconstructions:

- A source-position projection of selected values and explicitly located
  connection paths, in SVG and PNG.
- A canonical equipment graph projection showing nodes, selected features, review
  flags and edges. This is labelled as a schematic projection, not a released CAD
  drawing or a geometrically faithful copy.

The source page is independently segmented into connected ink components before
any model rectangle is considered. A component can be explained by a tight,
exact native-text match to the selected value, or by a narrowly reconstructed
source-located path. Merely covering a source region with a device rectangle
cannot explain its ink. Large value boxes, unresolved values and OCR-only matches
stay tentative and do not increase strict coverage.

Native words use the existing visibility checks, so hidden and covered CAD text
cannot independently explain visible ink. Scanned packets with selectable footer
text also receive independent Tesseract OCR. OCR is a location aid, not proof of
the reading: scan text remains tentative even when it agrees. OCR timeouts and
component-budget exhaustion remain explicit and the remaining ink is unexplained.

A bounded four-rotation OCR pass returns word boxes to the original physical-page
coordinates. Repeated ruled lines can propose additional unknown table strips
after a small source-only deskew. Both axes remain possible; these proposals do
not decide which strip is a component row, and cannot explain away any ink.

Outputs include the source, reconstruction, diagnostic pixel difference, red/green
overlay, unexplained-ink mask and crops. Table rules, drawing borders, ordinary
notes, unmodelled symbols and genuinely missed devices can all remain unexplained.
The fonts and layout of the reconstruction are substitutes; pixel difference is
not an engineering-change detector.

### The metric is spatial coverage, not device recall

`spatial_ink_coverage` is explained source-ink pixels divided by all source-ink
pixels. A correct number at a matching location does not independently prove its
device association. The equipment extraction recall field stays null.

Unexplained-region recall is measured only when the caller supplies a separate
human region key frozen before the run, with labelled regions that are known to
be missing from the model. It measures whether those regions remain highlighted,
not whether Rivet correctly identifies every device. Without that independent
key, the report says that recall is unmeasured. Model-generated boxes cannot
serve as their own answer key.

Source-only region annotations support a separate measure: localization of
annotated row/symbol regions by unexplained-region proposals, using one-to-one
intersection-over-union of at least 0.2. Independent assistant annotations still
need engineer confirmation. They do not label actual model misses, every symbol
or every ink region, so they do not establish equipment recall or whole-page
proposal precision.

## Graph revision comparison

`backend/equipment/evidence/graph_diff.py` compares selected attribute assertions
and electrical relationships. It preserves before/after references and can render
crops from hash-verified physical source pages.

Default correspondence requires an exact tag, node kind and order/lineup scope.
Case and whitespace may normalize, but `52-12`, `BKR-12`, and `FDR 12` are not
silently interchangeable. Anonymous circuit positions cannot match because their
source-local identifiers happen to be equal. A source-local or renamed identity
needs an explicitly verified correspondence with provenance.

Values that remain conflicting or need review produce an unknown comparison.
Observed, proposed, approved, released and requirement values are not blended.
Multiple issue or rating-condition strata require an explicit comparison scope;
dictionary order cannot silently select the last assertion.
Unique provisional value deltas are retained separately as candidate changes,
with review required. Conflicting, invalid and tied values do not create a
candidate. Candidate precision/recall and confirmed-model precision/recall are
reported separately; the candidate list cannot silently improve the strict score.
Missing devices and connections become additions or removals only when the
opposite issue declares complete identity or connectivity coverage for the
relevant scope. Otherwise the result is a possible change that needs review.
Duplicate identities and unmapped terminals remain unknown. Containment edges
are not electrical connections.

This means incomplete PDF extraction may yield no proven structural change even
when the visual overlay clearly changed. That is an honest limitation. The
earlier Kansas visual change score must not be presented as graph-diff accuracy.
A graph assertion also does not establish that a change is approved or released.

## Verification

`tests/test_evidence_synthesis.py` contains deterministic safety controls for
partial coverage, ambiguous identities, unit-aware rating changes, unresolved
values, connectivity, source hashes, whole-page boxes, and the `600A` versus `60A`
error. These controls are synthetic and never count toward real-file accuracy.

Real evaluation must retain source hashes and the frozen development/test split,
and report field accuracy, unflagged wrong values, review rate, spatial coverage,
independently measured region recall and graph revision precision/recall separately.

`scripts/evaluate_evidence_synthesis.py` evaluates saved reconciled graphs without
a database or another model call. Its index lists model paths, package IDs and
optional before/after comparisons. It verifies source hashes, can verify an
implementation freeze, writes page-level diagnostics, and uses the existing
revision scorer for declared comparison keys. Unknown comparisons are retained;
the old visual reader's changes are never substituted for graph differences.

`scripts/evaluate_reconciled_equipment.py` builds the saved graphs and before/after
value report. Its development entries fit only development labels, clustered by
the original document package. Evaluation keys are opened after their graph is
written. Stable source-row identities remain fixed; source-only OCR table rows
are scored separately rather than choosing whichever reader looks best against
the key. Reports include candidate accuracy, accepted-only accuracy, missing
values, review fraction, and wrong values that bypassed review. Zero bypassed
errors with every value still needing review is not reported as solved extraction.

## Independent drawing observations and the first-day run

`drawing_reader.py` reads each drawing issue independently. It receives original
page images only, with a labelled coordinate aid; no other issue, change list or
answer key enters the prompt. The inexpensive configured vision model remains
subject to the pilot's existing remote-processing policy. Source OCR ranks four
orientations when ordinary orientation detection fails. Every box and path is
mapped back to the original physical page.

The reader preserves sections, actual device symbols, prepared positions,
busbars, named interfaces, raw rating text and uncertain regions. A proposed
connection produces source-local terminals and a connectivity node only when its
path has independent narrow-path ink support and both endpoints lie within the
cited symbol bounds. Rejected paths remain explicit. This is provisional drawn
connectivity; a symbol, a prepared position, or a title-block word such as
APPROVAL does not establish installed equipment, engineering approval or release.
Assembly SCCR/withstand, device interrupting capability, bus ampacity and branch
ratings remain separate. Unsupported rating kinds preserve their raw evidence as
unknown rather than borrowing a different typed feature.

Cross-issue sections need a cited, explicitly supplied drawing-lineage record.
Source-local circuit positions are not automatically merged into enterprise
identities. Named connection and rating-label differences are candidate drawing
changes and are scored separately from strict confirmed graph changes. A model's
visible-section estimate never becomes proof of physical completeness.

The known Kansas C1 May15/May30 development pair was read independently after a
reader freeze. Frozen predictions were written before the reference was opened:
candidate checks found 3 of 4 grouped changes with 2 false alarms (75% recall,
60% precision); strict confirmed checks remain 0 of 4. The source-supported
section-4 transition and both INTERIOR label additions were found. The right
future-end removal was missed; inconsistent lengths of an unchanged flange
description produced the two false alarms. These are development results, not
unseen validation. No complete C1 device/terminal/wire inventory key exists, so
observed counts and pixel-path support are not extraction recall. Earlier runs,
including a corrected fallback-rendering failure, remain in the local audit.

The first-day pipeline invokes the reader with `--vision`, or with
`--drawing-vision` when reusing earlier source predictions. Its default drawing
budget is 500 pages; jobs run one page at a time in round-robin source order
within the shared deadline. Missing provider authorization, unsupported input,
timeouts and deferred pages stay visible. For an authorized full pilot pass use
`--vision --drawing-page-budget 500 --timeout-seconds 86400`. Cached development
rehearsals retain cache provenance and exclude the earlier model-call time.

`drawing_context.py` adds readable, versioned source-context crops alongside the
original tight scored boxes. Context comes from cited source sections and claims,
with recorded orientation, bounded rendering and a minimum readable-size target.
These viewing bounds are never promoted into a larger equipment association or
used to change predictions/scores. The first-day archive includes the raw drawing
observations, provisional graphs, separate candidate changes and both crop links.
