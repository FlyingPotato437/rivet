# Transformer phase-change prototype

The prototype adds a read-only engineering preflight to an existing order. It is
not a native transformer converter. The previous drawing/BOM correction and
Outlook workflow are unchanged.

## Implemented

`backend/engineering/phase_change.py` reads explicit printed phase, winding,
voltage and rating claims from hash-checked PDF originals. Page rectangles are
in visible-page points, including rotated CAD text. FreeText reviewer overlays
are excluded from printed equipment facts. Raster equipment remains unverified.

Unit phase, bank phase, bank unit count and winding count are separate fields.
Different printed values are retained; ambiguous voltage values are not selected
for calculations. No tag, topology or engineering approval is inferred.

Deterministic calculations cover single-phase terminal current and balanced
three-phase line current. An inverse squared-power check verifies the arithmetic.
Reported winding currents are compared under explicit alternative unit/bank
rating interpretations; differences are not declared proven engineering errors.
These formulas do not size conductors/protection or validate transformer design.

An explicit caller what-if can supply target, supply, total kVA, terminal voltage
and a balanced-load assumption. Two-phase terminology remains unresolved;
split-phase needs leg loads/neutral context. A passive transformer is not treated
as a single-to-three-phase power converter. All scenarios remain blocked for
native execution, engineering approval and release.

## Access and execution

- `GET /api/orders/{id}/engineering/phase-change`: read claims and preflight.
- `POST /api/orders/{id}/engineering/phase-change/analyze`: writer-only what-if;
  requires the current `expected_version`, rejects paths and extra write flags.
- Both use the authenticated order's documents and tenant scope. No new table,
  worker, external call, model, email action or document mutation is introduced.

For the explicitly selected private local evaluation:

```sh
.venv/bin/python -m scripts.phase_change_prototype \
  --state .data/larson-intake-20261021/state.json \
  --out .data/larson-intake-20261021/phase-prototype
```

The state file supplies the operator's authorized tenant/user/order identity.
Private outputs include source hashes/rectangles, claims, four scenario reports,
a comparison CSV and a readable HTML report. They must remain ignored by Git.

## What blocks a real conversion

The exact requested equipment boundary and target configuration are needed first.
Then obtain editable CAD and references, matching BOM and connection reports,
approved unit/bank ratings, vector group/grounding, loads, fault/protection data
and manufacturer design rules. CAD/BOM presence is not a verified mapping.

A manufacturer-specific transformer design/connection writer and independent
engineering validation still need implementation. The existing bounded native
adapter cannot be treated as a general transformer redesign engine. A completed
engineer revision is useful as a withheld benchmark, not required to start.

## Validation

Tests cover published arithmetic examples, invalid/nonfinite/ambiguous inputs,
unit/bank/winding separation, rotated text, reviewer overlays, source tampering,
scenario assumptions, tenant isolation, version conflicts and unchanged orders.
Synthetic controls are not real-document accuracy measurements. The Larson
package is development evidence, not an unseen test or an engineering answer key.

Formula reference: https://www.se.com/us/en/faqs/FA101694/
Supply constraint: https://www.se.com/us/en/faqs/FA91854/
