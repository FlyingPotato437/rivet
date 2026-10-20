# Real switchboard reconstruction evaluation

This benchmark keeps customer inputs, manufacturer results and labels in separate roles. It does not turn a vendor's quotation or finished schedule into a customer specification.

## Current eligibility

| Public package | Customer-side input | Manufacturer result | Approved reconstruction eligible |
|---|---|---|---|
| Kansas Docking / Square D | Original spec, consultant one-line and PO were not established in the acquired binder. Page 50 is Square D's own quotation. | Maker approval-drawing binder and returned/revised drawing pages. Approval must be assessed per subpackage. | No |
| Ellington / Windermere Eaton | Original early procurement specification/one-line were not established. Available later Phase 2 addenda postdate the submitted package. | Eaton submittal July 21, 2023; CES review August 1, 2023; construction-manager note requests separate SPD review. | No |
| TCTC Anderson Hall | Genuine college procurement spec and signed consultant procurement one-line E0.3A. | Manufacturer submittal/approval not obtained. | No; input extraction and proposed-design compliance only |

The real approved-design reconstruction denominator is **zero eligible pairs** so far. The pilot must supply customer requirements **and** the corresponding manufacturer submittal and review disposition to measure that claim. A public-file search is not evidence that those documents never existed.

## Development key

TCTC's source-only key covers 52 scalar requirements and all 13 MSB breaker symbols (one incoming main, nine loaded feeder circuits, three installed spare breakers). Downstream panel devices, administrative clauses, delivery, generic instrumentation and finish requirements are outside this bounded scalar score. It does not claim a complete engineering specification.

Labels were made by an assistant from originals and enlarged original-page renders, before viewing predictions; they need an engineer's independent review. The original PDF's ambiguous `LS16` token stays unknown. The drawing requests replacing a trip plug in an **existing** main breaker; this must not become an assumed new main. Three-phase service does not establish an explicit per-breaker pole count.

## Run and score

```sh
uv run python -m scripts.evaluate_switchboard_reconstruction \
  --manifest benchmarks/switchboard-reconstruction-v1/manifest.json \
  --package tctc-anderson-customer-input-development \
  --run-native \
  --output .data/switchboard-reconstruction-v1/a-new-run-directory
```

`--run-native` reads only selected, hash-pinned original customer pages, runs the current requirements/configurator/generator, saves its prediction, and then opens the key. Scanned input pages remain explicitly unreadable in this baseline. It neither confirms requirements nor approves a design.

Use `--prediction path/to/source-only-prediction.json` to score a run made with a different reader stage. The payload contains `requirements` (typed extraction), `design` (selected proposed design), and optionally `configuration`, `artifacts` and `elapsed_seconds`. Never create a prediction by copying the answer key into solver inputs. The canonical stage documents must remain the sources of each requirement.

Run the full source pipeline with quoted native-text reading and the page-image drawing reader:

```sh
uv run python -m scripts.evaluate_switchboard_reconstruction \
  --manifest benchmarks/switchboard-reconstruction-v1/manifest.json \
  --package tctc-anderson-customer-input-development \
  --run --read-text --vision --model gpt-6.1-sol \
  --max-drawing-pages 6 --max-text-batches 40 \
  --output .data/switchboard-reconstruction-v1/a-new-full-run-directory
```

This saves the selected original source spans, exact implementation copies and hashes, raw reader records, coverage, proposed artifacts, and a frozen prediction before opening labels. Incomplete reading stays visible. A heldout run whose implementation changes during execution stops before opening its key. Output directories containing a frozen prediction are never reused.

The scorer separately counts source-extracted devices and devices present in a generated proposal: a configurator's empty main placeholder cannot inflate extraction recall. The explicit canonical-key projection treats enclosure/enclosure_type and section_barriers/inter_section_barriers as schema aliases, and NEMA1/Type1 as designation spellings; it does not infer device identities or engineering values.

Reports separate requirement extraction from generated-device compliance, solver self-check counts, unreadable pages and approved-reference reconstruction. An input-only case reports approved-device placement and drafter-edit estimates as **not applicable**. The empty-design baseline has zero extracted requirements and zero generated devices.

For a future eligible approved pair, logical edit estimates are bounded by device insertions/removals, labelled property updates and placement moves. They are not drafter hours or CAD clicks, exclude unlabelled content, and do not prove engineering validity. Synthetic controls test only the scorer.

Original PDFs and intermediate renders/predictions live in ignored `.data/`. `label-freeze.json` pins the reviewable labels and source-role manifest. Development results must never be presented as unseen-test performance.

Identity projection version `source-bound-observation-inventory-v5` preserves source-local drawing IDs. For scoring, a printed display tag can correspond to a reference label only when exactly one subject has that literal label in the same original SHA256/page. Repeated labels remain ambiguous; ratings and row order never choose an identity. Generated devices inherit source correspondence only through an identical source-local requirement ID. The gold source identifier may be explicit when its human-readable inventory ID is functional. This evaluation-only correspondence does not merge production entities or approve an engineering identity.


For historical or information-only drawings, add `--extract-only`. This mode records source observations but leaves configuration, design and generated artifacts null. A manifest with `source_authority: historical_reference_only` is rejected without that flag. Reference drawings are not new purchase requirements or a verified current installation.

`raw_source_position_inventory` measures tagged positions in accepted original-page drawing reports, including blanks and spaces without numeric ratings. It is separate from canonical typed requirement extraction and proposed-design compliance. Literal identity requires an unambiguous printed label on the same original source/page. Explicit A/kA and kV/V conversions are arithmetic only; continuous current is not trip current and SCCR is not breaker interrupting rating.
