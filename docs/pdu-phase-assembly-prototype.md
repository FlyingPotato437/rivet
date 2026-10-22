# PDU phase assembly prototype

This offline prototype replaces a bounded assembly in actual editable DXF and
CSV BOM files. It does not redesign transformer cores or windings and is distinct
from the read-only Larson 50 MVA preflight. Customer files, existing Outlook
drafts, workers, dashboard and engineering APIs are unchanged.

## Run

```sh
.venv/bin/python -m scripts.build_phase_conversion_demo --complete
```

The timestamped output contains original/draft DXFs, three-sheet vector PDFs with
selectable text, CSV and Excel BOMs, connection reports, exact request/catalog,
change plan, independent recheck, injected-error results, an HTML review page and
an unsent `.eml` with five actual attachments. It never creates/sends mail in
Outlook. No model or paid API is used.

The Excel builder uses the existing bundled artifact-tool runtime. Native BOM
editing uses CSV; Excel files are rebuilt exports, not arbitrary manufacturer
workbook preservation. Missing runtime fails visibly without installation.

To rerun an edited fictional package, retain its `baseline/`, catalog and request:

```sh
.venv/bin/python -m scripts.build_phase_conversion_demo \
  --inputs output/pdu-phase-prototype \
  --out output/another-sample --complete
```

Only the fictional `RVT-PDU-PHASE-1` convention is supported: 30 kVA, 480 V
three-phase supply, 208Y/120 V output, 60 Hz, seven explicit circuit loads and
unity power factor. Other designs and manufacturer conventions are rejected.

## Sample change

Replace the original 30 kVA, 480 V single-phase to 120/240 V assembly with a
three-phase delta/wye assembly. Three-phase supply is an explicit assumption.
Retain six 120 V circuits and distribute them across phases. Replace the seventh
circuit's 240 V equipment and outlet with compatible 208 V three-phase equipment.

Public Larson ratings and Schneider formulas provide reference points. All part
IDs, candidate breaker choices, loads, terminal mappings and geometry are invented.
The source manifest records sources and the inaccessible full Hubbell PDF.
No manufacturer approval, assembly listing or certification is inferred.

## Verification

- Source hashes plus catalog/request hashes bind the plan. Source CAD and BOM
  inventory/fields must agree; originals remain untouched.
- Actual block bodies, terminals, polylines and visible schedules are edited in
  new files. Unowned geometry/notes/handles are checked for preservation.
- A separate verifier reopens the native DXF and CSVs. It checks inventory,
  candidate attributes, terminal sets, visible nameplate and connection report.
- Conductors must physically land on their named terminals. Actual internal panel
  bus geometry is checked against requested phase assignments.
- A separate typed terminal-graph auditor checks phase isolation and each required
  circuit phase path. It returns evidence handles, connected-component count,
  Laplacian rank and cycle rank. A cycle alone is not an electrical fault.
  Incomplete incidence cannot earn a passing audit. Grounding design is not
  inferred from repeated earth symbols.
- An independent ideal AC phasor model uses the observed bus mapping to calculate
  phase loads/currents, fundamental neutral current and VA conservation.
- Excel exports are reopened and compared row-for-row to verified data. The PDF
  text layer comes from actual saved CAD text, not the intended design.
- Seven independent corruptions must be caught: missing phase conductor, stale
  outlet BOM, wrong breaker poles, disconnected conductor, missing native H3
  terminal, old nameplate and a circuit on the wrong internal phase.

The AC model assumes balanced supply voltage, zero line impedance and unity PF.
It is not a transient, harmonic, protection, thermal or mechanical simulation.
Synthetic controls are not manufacturer accuracy or superiority to engineers.

## Research

[Beyond Pixels, ICASSP 2026](https://arxiv.org/abs/2602.11678) motivates explicit
vector-to-graph representations for schematic auditing.
[V2G-Audit](https://github.com/gm-embodied/V2G-Audit), source reviewed at commit
`70a18683d5f2635f25d8b8761a483233179f2986`, is credited alongside the paper.
[SketchGraphs](https://arxiv.org/abs/2007.08506) represents CAD with relational
constraints. [FunSearch](https://www.nature.com/articles/s41586-023-06924-6)
illustrates separating a candidate generator from its evaluator.

The prototype uses the general design-graph/writer/independent-evaluator pattern.
It does not reproduce these models, import their code, claim their benchmark
results or introduce a novel algorithm. No fine-tuning was performed.

See [the implementation notes](cad-graph-and-model-routing.md) for the actual
large-DXF import limitations and optional Astra API configuration.

## Manufacturer validation

Obtain native CAD with references; matching BOM and connection reports; exact
requested change; approved diagrams, loads and manufacturer design rules. A
completed engineer revision and calculations can be withheld as a benchmark.
Additional adapters are needed for actual manufacturer CAD conventions.

Protection coordination, SCCR, conductors, grounding, GFCI, mechanical fit, mass,
cooling and transformer design remain unverified. `engineering_approved` and
`released` stay false. These checks do not authorize fabrication or energizing.
