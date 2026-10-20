# Equipment evidence reconciliation

Rivet keeps source readings and equipment decisions separate. An extracted number is a claim, not an engineering conclusion. Original facts, conflicting values, unknown cells, drawings, correction bundles and Outlook drafts remain available.

## The decision path

1. Native CAD/PDF/tabular readers, page-image reading and the source-only ruled-table reader produce cited observations. Each retains its exact source hash, physical page/cell/entity, raw wording, extractor version and raw confidence.
2. Adapters project these observations into an order/package-scoped graph. The physical hierarchy is separate from terminal/connectivity topology. A device-number function or nearby drawing symbol is not enough to establish physical identity. Requirements, proposals, actual observations, approved events and released events stay distinct.
3. Reconciliation selects a supported, feasible candidate while retaining every conflicting claim. It checks typed units/domains, bound catalog constraints, trip versus frame, explicit graph requirements and documented topology constraints. Trip/frame alternatives use a bounded joint search; equal-support alternatives stay unknown. A shared bus does not imply that the sum of branch trips must be below bus ampacity. Unapproved symmetry is a review clue; a sibling's value is never copied into a blank.
4. Identical source-page readings remain one proof family. A part-number deduction that depends on three pages is one joint proof, not three independent observations. Prior revisions, requested values and specifications cannot vote as actual installed equipment. Product bindings and operating voltage/current type/standard/frequency must be established before a conditional catalog rating can apply.
5. Raw model confidence is recorded for diagnosis, never used as the probability of correctness. Reliability calibration accepts development labels only and clusters observations by original package. It reports value accuracy and a conservative package-level confidence bound separately. A small corpus cannot establish production reliability.
6. The existing comparison, compiled equipment model, API and correction/Outlook attachment gates use the reconciled decision. Raw per-file observations remain visible as evidence. An unresolved explicit graph requirement also blocks an overall checked result.

This is bounded candidate selection and constraint propagation, not a globally optimal engineering design solver or a standards compliance certificate.

Source-local table correspondence can join two readings only with the same pinned page, an established ruled table and a unique printed circuit identifier supported by cell geometry. Ratings and row ordinals never establish identity. The source page remains one proof family. An inferred populated/prepared classification is retained separately from the original raw cell, cites its frame/trip or explicit PS/incoming evidence, and never establishes verified physical population.

## Standards and catalog scope

The graph uses CIM/IEC 61850 concepts while keeping physical containment, terminals and connectivity separate. ANSI C37.2 function numbers and IEC 81346 designation aspects assist identity; customer aliases require cited, scoped correspondence. Typed features follow ETIM's numeric/logical/identifier and unit concepts. The application does not invent ETIM feature codes or claim conformance to a full licensed ETIM/CIM model. See `equipment-standards-mapping.md`.

The curated catalog subset contains exact supported Schneider, Siemens and Eaton numbers. Unknown suffixes remain unknown. Legacy `LI`/`MJ` frame text is not automatically interpreted as modern PowerPacT. Sensor capacity is not the installed trip pickup. See `equipment-catalog-sources.md` for primary URLs, hashes, pages and limitations. Verified AC frame ranges in Schneider 0611CT1001 are H 15–150 A, J 70–250 A, L400 70–400 A and L600 200–600 A; a bare L does not select a variant.

## Immutable storage

Migration 0029 adds tenant/order-scoped claim and decision-run tables. Claims reuse identical IDs only when payloads match. The database rejects update, delete and truncate operations. Each decision run retains the complete graph plus actual policy and calibration hashes. First-day offline runs use a private, atomic append-only archive with the same claims and run receipt. Old claims are never overwritten when an extractor or policy changes.

## Source accounting and revisions

The source-accounting pass renders a canonical schedule/graph and compares anchored text and measured source ink. It produces residual masks, source crops, unresolved anchors and independently located table-strip proposals. Matching a word somewhere else on the page does not explain the selected cell. Numeric raw text that disagrees with its typed value is flagged.

Residual ink is a warning system. It is **not extraction recall**: grid lines, logos and labels also leave ink, and a self-generated mask cannot establish how many devices were missed. The evaluation reports source-only labelled-row localization separately; true missed-device-region recall stays unmeasured until an independent reviewer labels actual misses.

Graph revision checks compare explicit observed snapshots, rating-condition strata and source-backed identity correspondences. Proposed/specification values cannot overwrite target values. Incomplete extraction cannot establish that a device was added or removed. Confirmed graph changes and provisional candidate changes are separate, with hash-checked before/after crops. The drawing reader checks proposed conductor paths against source ink and endpoints; incomplete topology stays explicit. Extra context crops show the section, label and branch without changing the boxes used for scoring. Existing visual revision observations are retained but do not become verified graph changes by relabelling.

## First-day command

```sh
uv run python -m scripts.pilot_first24 \
  --manifest /private/package.json \
  --package PACKAGE_ID \
  --output-dir .data/pilot-runs/FRESH_RUN \
  --vision --drawing-page-budget 500 --timeout-seconds 86400
```

Use `--vision` only within the manufacturer's agreed processing policy. The command checks original hashes, runs existing extraction/comment/revision checks, builds the evidence graph, reads source tables and drawing topology, reconciles, accounts for each eligible page, checks explicit revision pairs, archives claims and writes a findings summary. The drawing stage attempts up to 500 pages in bounded jobs across issues; deferred/deadline pages remain visible. No email, CAD publication, approval or production release occurs.

An optional independent answer key is opened **after** evidence decisions are archived. `reconciled-scoring-report.json` reports candidate values, accepted-only values, unflagged errors and review burden separately from legacy extraction/check metrics. Without labels, accuracy is unknown.

The normal workspace's native-document comparison and release gates use reconciled decisions and invalidate obsolete cached results. The scanned table/drawing graph produced by this first-day command remains a separate offline artifact; it is not automatically substituted into the live workspace or Outlook correction workflow. Native correction and draft functionality is preserved, but arbitrary real-package correction and release remain unvalidated.

For the pilot, request PDFs of every revision, returned comments, BOM spreadsheet, and AutoCAD Electrical component/BOM/wire reports or EPLAN exports when available. Keep device tags, ratings, revision identifiers and markups while redacting customer names, addresses and unrelated personal information. External upload access remains closed until the manufacturer's authorized uploader is known.
