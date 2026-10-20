# Switchboard engineering workflow

Rivet’s main order workspace is Requirements → Configure → Drawings → Review → Release. Today remains the home screen. This is a proposed-design workflow for one cited Square D QED-2 subset in the UL 891 product category, not engineering certification or a production drafting system.

## One model and existing review tools

Each order carries an `engineering` snapshot in the existing versioned order state. Engineering decisions create `OrderRevision` and `OrderEvent` records; evidence claims append to the existing insert-only equipment evidence tables. The model ID, model version, requirement provenance and selected rule-pack hash travel into the generated files. Earlier packages and originals are retained.

The five visible stages include precheck in Drawings. Existing source/comment/tool links open Review, retaining the old review workspace. Generated files register as an existing equipment package with revision `GEN-{model_version}`. The one-line is the primary editable DXF; the elevation and assembled PDF are supporting generated references. The correction executor renders its checked preview from the saved native drawing. Model edits regenerate the package. Repeating generation without a model edit reuses the same hash-checked package, avoiding duplicate revisions. A reviewed customer comment can now update its cited typed requirement and selected model, regenerate all files, run the existing revision checker/precheck, and pass four exact-byte artifacts through the existing unsent Outlook draft transport. Direct file-only corrections of these generated files are blocked so the model cannot silently diverge. Independent legacy native/BOM correction remains available for ordinary uploaded packages. Arbitrary manufacturer CAD editing remains unproven.

## Inputs and requirements

Source roles must be explicit: customer specification, engineer’s one-line, customer addendum, quote exception, or purchase order. Manufacturer output is comparison-only; component cut sheets are not customer requirements. Source originals are hash-checked. Native text observations, bounded semantic text reading and the existing image drawing reader produce proposed claims with physical page/region references. Conflicting values and exclusions are retained. A PO does not silently override a specification.

Semantic text reading validates the span ID and supporting quotation against the actual input batch. A numeric candidate without its printed quantity is withheld. Raw claims retain original values and units; typed requirement items normalize compatible units before comparison. Exact quote validation constrains provenance; it does not prove semantic correctness. Image-reader confidence is an uncalibrated observation, not a probability of accuracy.

Engineer confirmations/corrections append derived claims and a new snapshot. Unknown or unhandled engineering clauses, failed readers, unreadable scans and deferred batches stay visible and block approval. The extraction signature is captured before and after reading so concurrent source updates cannot be attached to stale observations.

## Catalog constraints and priors

The versioned JSON pack is `backend/engineering/rules/schneider-qed2-ul891-2026.07-v1.json`. It cites Schneider’s 2700CT1101 catalog and the limited adopted-code/application sources for its trigger checks. It distinguishes assembly/table SCCR from a device’s individual interrupting rating. Unknown exact product, assembly listing, frequency applicability, mounting datum, metering/SPD geometry and AHJ adoption remain questions. It does not invent dimensions or claim a complete UL/NEC check.

The configurator returns up to three proposals which pass its known constraints, or explains why no such proposal exists. Every edit recomputes checks from structural content and current source/rule hashes; caller-supplied pass flags have no authority. A partial proposal is labelled incomplete, never manufacturing-ready.

Company-local design priors are explainable counts over genuine recorded engineer-approved model snapshots. Synthetic, foreign, unapproved or hash-mismatched jobs cannot contribute. Section preferences only rank feasible options. Grouping, explicit metering positions and spare-space habits remain observations, not engineering rules or model-weight training. With no approved company history the prior list is empty.

## Files, precheck and export

The generator produces a named-block one-line DXF, front-elevation DXF, BOM XLSX/CSV, nameplate CSV, assembled submittal PDF, model snapshot and hash manifest. All contain one proposed model version. Missing physical geometry yields a clearly labelled unscaled schematic; production elevations require manufacturer dimensions/templates. Verified cut-sheet pages may be included only when explicitly mapped to supplied components and matching their identity. Shop block/title-block substitution is an explicit library contract. Wiring diagrams and plan views are outside v1.

Precheck revalidates the proposed model and independently reads saved DXF/BOM files with the existing inventory checker. Customer memory is retrieved for engineer review. The source-conformance pass checks supported typed source requirements against the selected model and independently reread saved files. Pinned source drawings can supply bounded direct-connection comparisons; approved typed customer-memory bindings can supply narrow checks. Full consultant topology, physical engineering, and prose preference compliance remain unverified. Matching unknown cells can establish saved-file representation consistency only; they remain unknown engineering values. A precheck is bound to current inputs, rules, implementation bytes, model and actual artifact hashes. An engineer-recorded approval and release export recheck that binding. The default public catalog subset leaves unresolved manufacturing questions, so it does not currently enable a production-approved design.

Release is an export stub: BOM and nameplate data only, after a current recorded engineer decision and the required gates. It never writes to an ERP, sends a customer email, approves engineering automatically or releases production.

## Model processing

The user authorized prioritizing accuracy over model cost. Engineering semantic text and image reading default to `gpt-6.1-sol` through `RIVET_ENGINEERING_TEXT_MODEL` and `RIVET_ENGINEERING_VISION_MODEL`. The existing economical reader remains available for measured comparisons. GPT-6 Astra remains excluded by the existing model guard. No new account or subscription was required.

Scanned engineering drawing reading uses `RIVET_ENGINEERING_VISION_ENABLED=true` (falling back to the existing `RIVET_VISION_ENABLED` policy when unset). It is enabled in the local engineering environment. This does not change inbox background behavior. Dense-page inspection uses direct high-resolution PDF crops; text/drawing transport waits default to 900 seconds and are explicitly bounded to 30–1800 seconds. Model failures and budget deferrals are recorded rather than treated as empty pages. Source/model/prompt-bound caches preserve provenance. Confidential pilot processing still follows the agreed upload/model-processing policy.

Engineering image reading processes each physical page separately. A rejected page remains a recorded failure and cannot erase accepted observations from another page. The engineering output budgets are configurable with `RIVET_ENGINEERING_DRAWING_OUTPUT_TOKENS` (60,000) and `RIVET_ENGINEERING_TEXT_OUTPUT_TOKENS` (32,000), bounded to 1,024–128,000 tokens. These are maximum allowances, not guaranteed completeness or accuracy. Rejected/incomplete responses preserve bounded private diagnostics with source/request fingerprints, provider status, incomplete reason and usage. Partial JSON is never accepted as an equipment graph. Completed source/model/prompt-bound caches remain reusable.

An experimental two-view blind rating rereader is retained for evaluation, with the original and projected reports kept separately. It is not the default extractor or an approval gate: the development experiment withheld six correct spare ratings. A separate source-only adjudication corrected an answer-key association error; all original scores and the correction are preserved. Same-model agreement is not independent factual verification. Optional projections withhold discrepancies rather than silently substituting a favored answer.

Circuit numerals are display labels, not enterprise identities. Each drawing observation keeps a source-hash/page/object identity so two different sections labelled 7 cannot merge. Prepared positions retain their source state. Multiple main depictions require explicit identity review rather than an implicit cross-sheet merge. Oversized semantic spans remain deferred coverage and block approval.

## Real evaluation and missing pilot inputs

The reconstruction harness pins customer-side source PDFs before reading labels and separates requirement extraction, source device extraction, generated device compliance and approved-package reconstruction. Software controls are not real accuracy. Current public source auditing found no eligible pair with both original customer input and documented manufacturer-approved output. The Kansas maker quote and drawings cannot be relabelled as original customer requirements. Ellington’s later public specs cannot be assumed to be its earlier procurement inputs.

TCTC Anderson is a genuine customer-input development case, with a specification and a scanned consultant procurement one-line. Its generated artifacts are diagnostic proposals, not a successful approved reconstruction. Frozen failures remain in `.data/switchboard-reconstruction-v1`; stronger-model/rendering experiments must write a new directory. A fresh test source must stay unseen until implementation is frozen.

Ask the pilot for the original specification, customer one-line/schedules, addenda, accepted quote exceptions and PO; the complete manufacturer approval drawing/BOM revision set and review disposition; exact product selections/listed combinations; native CAD plus component/BOM/wire exports; mounting/section rules; shop blocks and title block; drafting conventions/nameplate formats; and a manufacturer engineer’s reviewed answer key. Wiring templates are needed for a later wiring scope. These missing inputs are engineering dependencies, not values to fill with a model guess.

## First-day intake

The existing real-package manifest may additionally classify `engineering_inputs` inside a package:

```json
"engineering_inputs": [
  {"source_id": "customer-spec", "role": "specification", "pages": [12, 13, 14]},
  {"source_id": "customer-one-line", "role": "customer_one_line", "pages": [1]}
]
```

IDs must refer to pinned original sources in that package. Page numbers are original one-based PDF pages. Manufacturer output remains in the original comparison roles and is never promoted to customer input by filename. These design stages currently require PDF exports alongside any CAD/BOM originals; the original native/report readers still process the CAD/BOM in the equipment audit.

Run the same first-day command after redaction/sharing review, with remote reading only under the agreed processing policy:

```sh
uv run python -m scripts.pilot_first24 \
  --manifest .data/pilot-intake/package.json \
  --package pilot-package \
  --output-dir .data/pilot-runs/first-pass-001 \
  --vision --vision-model gpt-6.1-sol \
  --drawing-page-budget 20 --timeout-seconds 7200
```

This produces the proposed requirements/model/files before the existing equipment/comment/revision/scoring report. Sources, reports and private outputs stay local under ignored `.data` storage. The shared deadline and remaining unread pages are explicit. Without a separate reviewed key, accuracy stays blank; proposed counts are not accuracy. No mail is sent, CAD originals changed or production approved by this command.

## Engineer decisions and asset mapping

Source equipment roles are decided by exact source hash, physical page and observed object ID. An engineer may identify a main/feeder or mark an object as reference; a bare current never becomes a guessed frame/trip or purchase. A separate Add missing requirement action records an explicit human entry with original page, actor and reason; it is never credited as extraction accuracy.

Configure previews are read-only, debounce edits and show current rule failures before a deliberate save. Confirmed system scalars survive into the model, DXF metadata and the XLSX System requirements sheet. A service-current requirement does not establish a discrete through-bus selection.

The application accepts drawing libraries and cut sheets by owned document ID, not a filesystem path. Asset roles, original hashes, exact component identity, supplied status, selected PDF pages and engineer review are checked before generation. Unverified, foreign, changed or incompatible assets are rejected. Source-bound unknown product data remains a question; it does not authorize selecting a convenient cut sheet.

## Final verification boundary, 19 October 2026

The final offline run passed 588 backend and 196 frontend controls plus typecheck and both workspace/Outlook builds on unchanged code. A separate nine-case API control exercised original-source comment evidence → reviewed requirement/model edit → seven regenerated artifacts → independent existing revision checker → unsent draft with four exact-byte attachments, including stale/tampered/wrong-order failures. The mailbox in that control is fake. These counts measure software behavior and never real design accuracy. Full live delivery and any later real-document metrics have separate receipts under `.data/switchboard-reconstruction-v1/`.
