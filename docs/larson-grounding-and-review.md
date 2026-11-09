# Larson source grounding and engineer review

Local verification: November 8, 2026. This is a development evaluation on Larson's supplied package, not an unseen accuracy benchmark or an engineering approval.

## Verified on the supplied files

| Check | Result |
| --- | --- |
| Saved written comments linked to the original annotation | 28 / 28 exact anchors: 23 drawing, 5 specification |
| Graphical marks retained with original locations | 21 / 21; grouped supporting marks, unidentified square still open |
| GPT-6 Astra page assessments | Drawing pages 1, 5, 7; specification pages 2, 5 |
| Readable vision transcriptions on those five pages | 25; all matched existing native comments; no additional actionable findings |
| New native CAD edits | 10: seven DETC labels, three bounded tertiary-current cells |
| Independent saved-file readback | 10 / 10 records and placements; original unchanged |
| Requests already documented in the native drawing | 3 |
| Requests still requiring engineering input | 25, including two requests with partial edits |
| Full engineering approval | Blocked; no approval or release performed |

Vision reported no unreadable regions on those five page images. That is a model assessment, not measured recall for every possible handwritten or graphical instruction. The native unidentified square remains unresolved. Exact annotation grounding uses original file hashes, annotation geometry and text; no nearest-text guessing.

The three current-cell corrections are 1263 → 1281 A, 1326 → 1332 A, and 1396 → 1406 A in the supported 17.5 MVA column. A separate 29.16 versus 29.167 MVA source conflict remains blocked. Physical equipment changes are not established by these numeric and label edits.

## Connected workflow

1. **Customer requests:** open a row, then its source. The PDF opens on the correct page, highlights the inner callout text box, zooms to it, and displays the extracted comment. Graphical marks are individually locatable from Source coverage.
2. **Rivet's changes:** inspect saved before/after excerpts, download the DXF and review package, then record Accept change or Needs changes. The decision binds to the saved artifact and source hashes. It is not whole-drawing approval.
3. **Engineer review:** see remaining requests, edit the DXF in a CAD application, and use Upload & recheck. The original is retained. Supported prior edits are checked by native locator, placement and value; unresolved engineering requests remain open.
4. **Outlook reply:** use Open preserved draft to inspect the existing response without regenerating or overwriting it. Rivet checks its current draft state through read-only Microsoft calls. Existing Outlook edits are preserved when project evidence changes; a stale draft must be checked against the latest package before sending. Live browser verification opened the Larson draft with its four prior attachments. This update does not claim the new 193 MiB DXF was attached to the preserved Outlook draft.

The revised-file path accepts ASCII DXF up to 200 MiB. DWG must be exported to DXF first. No browser CAD editor was added. Full drawing approval remains a distinct, guarded human decision; newly discovered visual instructions also block approval.

## Limits and manufacturer inputs

Fan relocation, ladder removal, cabinet/bushing/arrester placement, internal tap connections and CT configuration require trustworthy component boundaries, assembly definitions, mounting/clearance constraints and manufacturer engineering rules. Nitrogen headspace, oil volume and weights need design and BOM data. The present package does not establish these changes as correct.

The upload → recheck → approval lifecycle was tested on isolated synthetic fixtures. Larson has not supplied a completed engineer revision to validate that lifecycle on their real revised CAD.

## Validation and usage

- 156 targeted backend checks passed: 83 covering source/artifact drift, cross-order isolation, per-edit decisions, revision upload, recheck and late visual-evidence approval blocking; 73 covering project communication and Outlook, including preserved draft access.
- 221 frontend checks passed. Workspace and Outlook builds passed.
- Live browser: exact comment highlight, grouped graphical mark navigation, saved before/after review and engineer revision controls checked.
- Five Astra calls: 14,500 input tokens and 11,695 output tokens. Captures and usage are private under `.data/larson-grounding-20261108/`.
- Local API and worker now use `RIVET_VISION_MODEL=gpt-6-astra`, stored in private configuration. No credits purchased, cloud capacity provisioned or custom neural network trained.
- Prepared run: `13bba918-58e3-4702-9499-fb5858b2b976`. Source-current saved package remains inspectable after code updates; full approval also requires current checking implementation.
