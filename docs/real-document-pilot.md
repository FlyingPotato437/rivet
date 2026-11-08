# Real-document pilot intake and the first 24 hours

Rivet's native equipment model, revision checker, correction pipeline and unsent Outlook draft workflow remain in place. This work adds measurement and real-file hardening, not a new autonomous workflow or UI redesign.

## Private upload

The authenticated API now has a separate staging endpoint, `POST /api/pilot-packages`. It uses the existing Clerk organization boundary; every source and manifest is readable only within that organization. It requires a redaction confirmation and an explicit name, role, side and revision for every file. It does not guess a revision sequence or start background work.

Accepted originals are PDF, DXF, XLSX, CSV/TSV and supported electrical CAD report XML. PDF limits are 200 MB / 500 pages per file, processed in bounded 25-page batches with original SHA-256 and page numbers. Other formats retain their 20 MB upload bound. A package is limited to 20 files and 400 MB. A native DWG receives a clear DXF/PDF export request; no unlicensed converter has been installed.

The upload receipt provides organization-scoped manifest and original-download routes. Files use opaque storage names and restrictive filesystem permissions. Private originals and reports stay under ignored `.data/`; none belong in git. This is access-controlled storage, not a claim of application-level encryption at rest.

**Deployment limitation:** the public rivet-usa.com deployment is still marketing-only. This private endpoint is implemented and locally tested, but it is not yet a customer-shareable hosted upload link. To use this endpoint for customer uploads, first deploy the private authenticated backend and persistent storage behind HTTPS, invite the authorized pilot account into its organization, and verify access and cross-organization denial. Do not expose the local development server or send confidential files through an unauthenticated tunnel. The private SharePoint folder below is the interim hosted option.

A live interim [Microsoft 365 intake folder](https://rivetusa.sharepoint.com/sites/RivetIntegrationDemo/Shared%20Documents/Forms/AllItems.aspx?id=%2Fsites%2FRivetIntegrationDemo%2FShared%20Documents%2FPilot%20intake%20%2D%20redacted%20packages&viewid=136a02fd%2D7fcd%2D42b0%2Dbd09%2D86e2ee1f8850) is now prepared in the existing private Rivet SharePoint site. Its redaction checklist was uploaded and verified. The site currently shows one member; no external access or anonymous sharing link was created. Grant access to the manufacturer's named account only after confirming the recipient and permission scope. This is a hosted upload location, not a claim that a new hosted Rivet backend has been deployed. The operator can download the redacted files and stage their explicit source manifest for the first-day command.

`GET /api/pilot-packages/checklist` returns the checklist. The public pilot-booking form is not a document-upload route.

### External AI authorization is separate from upload permission

Private intake defaults `external_ai_authorized=false`. To record customer permission, the operator supplies `external_ai_authorized=true` and a nonempty `processing_authorization_reference` (at most 500 characters; a reference to the agreed policy/approval, not a pasted email body). The API records operator, time and the exact source hashes in that package's `model_processing` receipt. This records a permission decision; it does not itself establish that an NDA or customer contract permits processing.

The first-day runner rejects `--vision` and `--drawing-vision` on private packages without that receipt, before creating output or invoking any reader. Added or changed source files require a new receipt covering the new exact set. Existing private manifests with only a redaction confirmation must be updated by the authorized operator after permission is established; do not silently enable them. Local extraction and replay/scoring of saved predictions remain available without a new model request. This gate covers the private pilot path, not every legacy workspace or mailbox ingestion route; use this path for the initial SCC evaluation.

## Redaction checklist

- Remove company/customer names, site addresses, personal names, phone numbers and email addresses, including title blocks, comments, document properties and hidden spreadsheet sheets.
- Apply permanent redaction, not a black rectangle over selectable text. Reopen the result and test search/copy. Retain a private unredacted original at the manufacturer.
- Keep device tags, ratings, quantities, dimensions, drawing/sheet numbers, revision identifiers and all reviewer comments with their locations.
- Use consistent customer/order aliases across every revision, specification and BOM. Keep revision dates and sequence.
- Include the prior issue, the returned marked issue, the revised issue and the matching BOM when available. List unavailable files explicitly.
- Confirm sharing permission, retention and whether the agreed workflow permits model-assisted page reading.

## One command

Run from the repository with a staged package manifest:

```sh
uv run python -m scripts.pilot_first24 \
  --manifest .data/pilot-intake/ORG_ID/PACKAGE_ID/package.json \
  --package PACKAGE_ID \
  --output-dir .data/pilot-runs/first-pass
```

The manifest is `rivet.real-packages.v1`, with immutable source hashes and explicit `baseline`, `target` and `requirements` sides. The separate private upload creates it. Standalone local packages can use the reviewable public examples in `benchmarks/real-packages/packages.json`; private manifests must also include a confirmed redaction/sharing receipt.

For page-image equipment extraction, revision comparison and comment reading, add `--vision --vision-model gpt-6-luna --drawing-page-budget 500 --timeout-seconds 86400` under the agreed processing policy. Every source hash and explicit revision pair is checked before model requests. Native text/table/CAD readers run first; an extracted native table row does not suppress image reading of the rest of that page. Equipment reading uses batches of up to eight pages, with a 500-page image-pass limit; deferred pages remain listed for another pass. Candidate scanned/marked pages also receive a separate comment-reading pass. The independent drawing graph reader uses bounded page jobs across issues, checks conductor ink/endpoints and keeps unverified topology provisional. Raw cells/transcripts, confidence, ambiguous regions and failures are saved under the private run directory. No source page is silently considered verified because it has a selectable footer. Saved page reads are reusable only when the source, model and prompt hash match.

`--timeout-seconds` sets a shared preparation-and-scoring budget (default one hour, maximum 24 hours). Individual preparation readers run in bounded child processes; timeouts retain checkpoints and deferred-page records. A large package may need further bounded passes. This is a processing bound, not a promise to finish an arbitrary 500-page binder within one hour.

For already-generated, hash-bound transcripts, use `--predictions /absolute/path/predictions.json`. Vision processing time is separate from a later cached scoring run; a fraction-of-a-second replay is not the original end-to-end processing time.

Outputs include:

- `equipment-model.json`: the actual current equipment projection, including unknown coverage.
- `source-observations.json`: image-read device/position observations, schedule cells, declared CAD connections, and their unverified status, separate from deterministic equipment geometry.
- `comment-log.json` and `comment-log.csv`: extracted review notes and retained source marks, confidence and page provenance.
- `revision-checks.json`: actual equipment and explicitly paired PDF comparisons.
- `source-diagnostics.json`: attempted/readable/unreadable pages, unsupported content and engine errors.
- `report.json`: extraction and checking scores, missed items, false alarms, source/reference hashes and timing.
- `findings-summary.md`: a short first-pass findings draft for review before sharing.
- `run-receipt.json`: source hashes, completion time and the fact that no customer email was sent.

The `evidence/` folder holds the authoritative reconciled graph, append-only claim archive, source-table correspondence, source-accounting residuals, drawing-stage/deferred-page reports, and separate provisional graph changes with scored and wider viewing-context crops. The legacy native equipment model and visual change scores remain separate artifacts. An all-review result is not autonomous accuracy, and residual ink is not equipment recall.

The `source-reading/` folder contains checkpointed preparation diagnostics and revision overlays with before/after crops. Sheet matching uses printed identity and location evidence, not page order. Ambiguous matches, possible renumbering, unproven added/removed-sheet absences and deferred comparisons are called out for human verification. Only uniquely overlapping native/image table observations are merged for scoring; conflicting values stay unknown and both raw observations remain available.

Without an independently reviewed answer key, accuracy fields are null. Never turn “all extracted items checked” into extraction recall. A package with no baseline cannot establish that a requested drawing change was implemented.

## Reference labeling and evaluation

Public reference keys are in `benchmarks/real-packages/keys/`. They are preliminary assistant visual transcriptions, not independent electrical-engineer approval. The Kansas H3 inventory distinguishes four populated breakers from four prepared spaces and an incoming connection. Its six-page scope is linked to the unchanged 266-page parent, not counted as another independent project.

Before scoring a customer pilot, a manufacturer PM/engineer should review the extracted note list, label missed notes and devices against the original pages, and review a sample of correct and incorrect comparisons. Preserve this key separately from model output. Include zero-comment pages and ambiguous/unreadable regions. Record exact source hashes and the reviewed page scope. Do not mark a whole binder “complete” after labeling one sheet.

Run the scored corpus without mail or database mutations:

```sh
uv run python -m scripts.evaluate_real_packages \
  --manifest benchmarks/real-packages/packages.json \
  --output-dir .data/real-evaluations/latest
```

Supply saved vision with repeatable `--predictions PACKAGE_ID=/absolute/path/predictions.json`. The harness writes equipment extraction, native annotation transport, visual comment detection/transcription, BOM row/cell accuracy, issue detection and revision-check accuracy separately. Synthetic controls are never pooled into real-public or real-private accuracy.

## First-day operating sequence

1. Verify the redaction receipt, original hashes and explicit revision sequence. Record what was not supplied.
2. Run ingestion and the equipment/checking pipeline. Run vision on scanned/marked pages under the agreed policy.
3. Inspect coverage failures before interpreting a comparison. Unknown extraction blocks an engineering conclusion; it is not a pass.
4. Independently review and label the package, then rerun scoring. Keep extraction misses separate from comparison misses.
5. Review the generated findings summary and send the observed counts, uncertainty and missing inputs. Do not promise engineering release, corrected CAD support or accuracy that the actual files did not demonstrate.

Reserve hours 0–2 for the intake/redaction check and revision mapping, hours 2–8 for extraction plus a separate visual reference pass, hours 8–18 for missed-item review and a scored rerun, and hours 18–24 for checking the findings with the founder and returning the reviewed summary. A processing limit or unreadable source is itself a finding; do not spend the entire first day hiding it behind repeated model retries. The customer should receive the observed coverage and outstanding questions within the first day even when the package cannot be verified.

The pipeline has run on real public files. A complete private manufacturer package has not yet been validated. The public corpus includes bids, addenda, issued approval drawings and reference records; it does not prove a complete customer-return-to-corrected-release cycle.

## Ask the manufacturer for

1. Original PDFs for each issue, with dates/revision sequence, drawing numbers and all sheets, plus the returned marked-up PDF/comments. Include a known unchanged sheet and tell us which revision is actually released.
2. The actual BOM workbook, preserving all sheets, units, formulas and headers. Include a PDF snapshot of the same BOM if one exists.
3. AutoCAD Electrical component, BOM and wire From/To reports, or EPLAN device/article and connection exports, in CSV/XLSX/XML as available. Include report headers, pin names, wire numbers, catalogue identifiers, units and an example of a missing/not-applicable value. We have measured an authentic Autodesk harness XML import; real EPLAN and manufacturer report variants still need validation.
4. DXF plus matching PDF if CAD geometry checking is expected; DWG alone cannot be processed here. Preserve block attributes and the original units.
5. A short tag/alias and symbol legend, populated-versus-prepared-space conventions, and a PM/engineer who can review the answer key. Keep tags, ratings and comments while redacting identities consistently.

Do not claim real-package CAD correction/release or complete wiring correctness from extraction alone. The measured equipment-v2 split, results and limitations are in `output/real-document-readiness/equipment-v2-20261018.md`.
