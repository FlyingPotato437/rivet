# CAD evidence and technical model routing

## Research attribution and implementation boundary

Chengwei Ma et al., *Beyond Pixels: A Vector-to-Graph Framework for Reliable
Schematic Auditing*, ICASSP 2026, [arXiv:2602.11678v2](https://arxiv.org/abs/2602.11678v2).
Author implementation: [V2G-Audit](https://github.com/gm-embodied/V2G-Audit),
reviewed at commit `70a18683d5f2635f25d8b8761a483233179f2986`.

Rivet independently implements source-bound terminal graphs and deterministic
typed checks. No author code or benchmark data is bundled. At the reviewed
commit the repository has no root LICENSE or explicit code license headers.
The public method and repository are credited; this is not an exact reproduction
or a claim to the paper's accuracy.

The prototype verifier derives edges only from established named terminals and
conductors whose saved geometry physically lands on them. Visual crossings do
not establish junctions. Operators are `connected`, `isolated` and
`one_grounding_point`. The last requires an explicit circuit boundary and physical
bond-point identities; repeated PE symbols do not count as independent grounds.
It is not applied to Larson's general-arrangement drawing. Unknown incidence
withholds a passing audit. A positively evidenced short is still reported when
other parts are incomplete. Cycle rank is descriptive, not a fault verdict.

## Large manufacturer DXF intake

```sh
.venv/bin/python -m scripts.inspect_cad_snapshot \
  '/path/to/manufacturer.dxf' --output-dir .data/private-cad-run
```

This read-only command streams ASCII DXF instead of raising the existing native
writer's size/entity limits. It hashes the original, indexes bounded supported
records and follows placed block references with base-point, translation,
rotation and planar scaling. Unused block-library text is not located drawing
evidence. Handles, effective layers and root placement are retained.

The supported snapshot covers LINE, ARC, CIRCLE, LWPOLYLINE, TEXT, MTEXT, ATTRIB
and planar INSERT records. Curves retain their native parameters and are sampled
within a bounded geometric error for reference views. A Z scale other than one
does not invalidate a block whose geometry lies at Z=0. Nonplanar content remains
explicitly unsupported.
Unsupported geometry, paper-space content, external references, arrays, nonplanar
transforms, text OCS, caps and malformed input are explicit. It does not identify
devices from loose labels or turn mechanical lines into electrical wires. A
complete supported-record snapshot would still not establish electrical coverage.
This command does not bypass the bounded native editing adapter or automatically
upload data, create a BOM or modify a manufacturer file.

The supplied 202,791,677-byte Larson DXF was processed locally in 9.726 seconds
with parser version 2: 1,234 located text items, 9,216 placed lines, 277 INSERT
locators and 9,766 sampled paths (178,308 points). Coverage remains partial:
hatches, splines, ellipses, dimensions, legacy polylines, images and other
unsupported records remain explicit. Counts are extraction observations,
not labelled accuracy. The private snapshot records full counts and omissions;
no manufacturer electrical audit or automatic correction is claimed.

The actual Larson comment-redesign adapter is separate from the graph verifier.
Its current real-file result is seven tap-changer terminology replacements,
two documentation requests already present and 26 of 28 requests still open.
The graph verifier is exercised by the fictional PDU path, not an established
Larson electrical graph. No research benchmark result transfers to this package.
Manufacturer-specific component mapping, design constraints and independently
verified edit operators remain necessary for physical/electrical changes. See
`manufacturer-demo-runbook.md` for the current demonstration boundaries.

## Astra belongs in Rivet's API workflow

The server may select different models for routine preparation and technical
review. Defaults stay economical; no running worker or local secret/config file
was changed by this implementation.

```dotenv
RIVET_AGENT_MODEL=gpt-6-luna
RIVET_AGENT_PROFILE=routine
RIVET_TECHNICAL_REVIEW_MODEL=gpt-6-astra
```

An authenticated request to the existing order-agent run endpoint may include
`"model_profile": "technical_review"`; the default stays routine. Server operators
can instead choose the default profile through `RIVET_AGENT_PROFILE`. Text inside
an email, drawing, playbook or prompt cannot choose a profile. Technical model and
reasoning effort are pinned in the durable run and returned under `model_route`.
Routine per-comment children use the separately pinned routine model. The
coordinator uses Astra for an explicitly selected technical review; it retains
the same tools, source validation, budgets and approval restrictions.

Engineering text/vision readers also accept an explicit `gpt-6-astra` model via
their existing model arguments or `RIVET_ENGINEERING_TEXT_MODEL` and
`RIVET_ENGINEERING_VISION_MODEL`. Blind rereads accept explicit model lists.
The old Astra-specific bans were removed. Astra requests use high reasoning,
structured outputs where applicable, Responses, and `store=false`.

Official API references: [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)
and [reasoning](https://developers.openai.com/api/docs/guides/reasoning).
Configuration does not establish this account's model access. No paid API call
was made for this change; transport and routing were tested with fake responses.
Provider errors remain visible; there is no silent downgrade. A stronger model
does not approve engineering or make incomplete CAD evidence complete.

## Outputs and validation

The regenerated fictional review package is in
`output/pdu-phase-prototype-reviewed/`. The three-sheet drawing PDFs are rendered
from saved native CAD, including the new aligned cell/grid schedules. The review
summary uses two deliberate pages; the design brief uses one. The existing
spreadsheet builder reopens and compares both 21-row BOM workbooks. The unsent
`.eml` carries the revised files; the live mailbox is unchanged.

Real manufacturer CAD/BOM edits still need a convention-specific adapter, the
actual BOM/connection reports and an engineer-completed held-back change for
validation. No synthetic check contributes to real-document accuracy.
