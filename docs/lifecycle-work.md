# Order lifecycle work

Rivet's Outlook drawing-approval loop is the live integration. The lifecycle
service extends the same tenant-scoped order with executable local preparation
for quoting, kickoff, returned comments, purchasing, release and closeout. Prepared
adapter receipts are labelled `local_demo`; separately authorized execution
against the persistent local enterprise harness produces `local_http_demo`
receipts. Neither creates production SAP records, SharePoint folders or Teams
messages. Approved customer artifacts can use the actual native Outlook draft
lane; that separate receipt records whether the unsent draft really exists.

The service stores work inside `Order.state.lifecycle`. The existing order lock,
optimistic version, idempotency record, `OrderRevision` and `OrderEvent` machinery
protect each change. Artifacts are real immutable files under
`.data/lifecycle/{organization}/{order}/{package}`. Native CAD bundles live under
`.data/drafting/{organization}/{order}/{bundle}` and retain their original hashes.

## Agent tools

Mount `backend.lifecycle.api.router` in the authenticated API. The tool surface is:

- `GET /api/orders/{id}/lifecycle`: concise completed work, decisions, blockers,
  source-currentness, full packages and artifact download links.
- `POST /api/orders/{id}/lifecycle/prepare`: one typed operation, with
  `expected_version` and an `Idempotency-Key` header.
- `POST /api/orders/{id}/lifecycle/decisions/{package_id}`: approve or reject a
  protected gate on the exact prepared content, using the authenticated actor.
- `GET /api/reply-assistant/lifecycle?internet_message_id=...`: only lifecycle
  work actually bound to the selected captured Outlook email and its evidence.
- `POST /api/reply-assistant/lifecycle/decisions/{package_id}`: the Outlook pane's
  decision route additionally binds the receipt, message ID, package fingerprint
  and current source membership under the order lock.
- `POST /api/orders/{id}/lifecycle/policy`: administrator-only per-company
  autonomy policy, recorded as an append-only approved `CompanyRule` version.
- `GET /api/orders/{id}/lifecycle/artifacts/{package_id}/{name}` and
  `/lifecycle/drafting/{bundle_id}/{name}`: tenant and hash-checked artifacts.

The `operation` discriminator selects its strict schema:

| Operation | Work produced | Decisions retained |
| --- | --- | --- |
| `quote` | Decimal-priced lines, explicit spec exceptions, an unsent quote artifact | Money, scope, customer commitment |
| `kickoff` | PO-to-quote differences, clarification, real existing customer brief, preflight text search, local SAP/folder preparation | Scope, engineering; mismatches block acceptance |
| `revision_review` | Every current revision comment, cited responses, real native draft-edit references, exact layout tasks, priced extra-scope proposal | Engineering, customer commitment; money/scope for extra scope |
| `revision_check` | Recorded resolution evidence, approved-baseline vs revision BOM comparison, outbound document release check | Engineering |
| `purchasing` | Supplier/buyer change draft and earliest-material-availability schedule proposal | Money, customer commitment |
| `release` | Local release package referencing approved revision check and classified customer/engineering approval documents | Engineering, scope, customer commitment |
| `closeout` | Classified test reports, as-built drawings and an unsent handover artifact | Engineering, customer commitment |

These schemas are for an agent to call after gathering the sources. They are not
a seven-page form the PM must fill in. The order page can show `completed_work`,
`decisions`, `blockers` and the relevant artifacts without adding navigation tabs.

The ordinary record agent also exposes `inspect_order_cad` and
`prepare_order_cad`. These inspect already registered, authorized DXF/BOM
originals and produce actual source-grounded draft copies or located layout
tasks. Saved bundles are visible to lifecycle preparation. The scheduled-agent
verification uses a scripted provider with actual native files; it is not proof
of live model reliability on arbitrary drawings. General native CAD upload and
proprietary DWG/PDM editing are not part of this bounded capability.

## Evidence and calculation rules

Every cited span must belong to this tenant and this order. A saved source
snapshot includes the original document SHA-256, metadata, source text and
location. Any changed source, superseded prerequisite or changed native CAD
artifact invalidates downstream approval. A previous signature never approves a
new version implicitly.

Release also requires classified customer and engineering approval records whose
`approved_revision` and `approved_document_sha256` metadata identify the exact
checked outbound files. A generic "approved" message from an earlier revision
does not pass. Returned-comment decisions and outstanding money/scope gates must
be complete before a local release package can be prepared.

Prices require a structured source span containing the exact `tag`, `currency`,
`unit_cost` and `gross_margin_percent` values. The calculation reuses
`backend.domain.pricing.gross_margin` and rounds money with Decimal. An unrelated
paragraph cannot be used to justify a fabricated price. An extra quantity is a
proposed scope change, not proof of billability. Contract-confirmed billability
requires a structured cited contract record with `billable_scope_tag` and
`billable: true`; otherwise it stays a blocker for the commercial owner.

Commercial sources must also have server-owned trust. A synthetic replay source
is sealed to its order, organization, original SHA-256 and allowed purpose by
the backend fixture builder. Production commercial sources require an active,
current administrator-controlled ERP binding with `commercial_fact_purposes`
explicitly including `pricing`, `lead_time` or `commercial_scope`. Uploaded
customer JSON cannot declare its own cost, margin, lead time or billability trust.

The approved BOM must have recorded approval metadata and cited structured
`tag`, `quantity` and `part_number` rows. The revised BOM must cite the values
actually checked. Every comment in the returned revision must be present. A
drafted answer is not a resolved comment. The revision check reports documented
resolution evidence while `addressed_comments` remains zero until engineering
approves the exact package. A layout task remains work for the drafter, and an
unreleased attachment blocks the outbound revision package. This controls work
prepared by Rivet; it does not control emails a person sends outside Rivet.

Native edits use the real `backend.drafting` adapter. Each lifecycle `text_edit`
identifies `draft_bundle_id` and a distinct `draft_change_id`; the change must
carry the actual cited markup's span ID. Original DXF/BOM hashes must be registered
to this order, and every downloaded output is re-hashed. The adapter changes
allowed text/data fields in copies, never geometry. Its pending-engineering state
does not establish release.

## Autonomy

Company policy records levels 1 through 4 and named routine operations. A numeric
level does not grant authority over money, scope, engineering or customer
commitments. This implementation never sends customer mail, never writes
real SAP, SharePoint, vault or Teams records, and never marks the existing production `Order` released
on the strength of a local adapter receipt. A future connected adapter must have
its own explicit permission scope, reconciliation and idempotency.

After the last required human decision, a customer quote/response/change-order
PDF package can be queued to the existing unsent Outlook draft mechanism only
when source provenance identifies exactly one current captured customer email,
or a returned-revision package matches exactly one confirmed intake for that
order and revision. A quote always requires source provenance. Ambiguous or
absent matches require explicit thread selection. The native
handoff is an operational record outside the signed order snapshot; it never
attaches draft CAD or sends a message.

A current priced revision package owns the matching email response while its
decisions are pending. Rivet shows the prepared work and required decisions,
instead of generating a competing generic reply. The worker rechecks this rule
before an Outlook write, so an earlier generated reply cannot bypass the gate.
Unrelated revisions and ordinary source-only replies continue normally. Existing
native Outlook drafts and the PM's edits are preserved.

Synthetic local-demo decisions require a team administrator. For non-synthetic
orders an administrator must configure exact authenticated actor IDs in the
appropriate `gate_approvers` mapping. A general writer role alone does not grant
money, scope or engineering approval authority. CAD layout tasks require a saved
bundle's exact task ID; coordinates are derived from its validated entity anchor,
and a caller's conflicting coordinates are rejected.

Historical customer preferences come from the existing approved customer-memory
service. Accepted edits, prices and human decisions are separate evidence events;
they are not cross-company base-model training or a claim of automatic model
improvement.

## Reproduce the local eleven-comment workflow

```sh
export RIVET_PDF_PYTHON=/path/to/artifact/python/with/reportlab-and-pypdf
.venv/bin/python -m backend.lifecycle.replay \
  --organization-id 6151b990-82f1-57d4-a559-df777cb047af \
  --output .data/demo-readiness/lifecycle-replay.json
```

This creates a **new fictional order** with a professional returned PDF containing
eleven native annotations, a companion specification PDF, real source DXF and Excel files,
three inspectable draft changes, seven cited answers, one layout task, a priced
extra-breaker proposal, PO clarification and a 42-day material lead-time impact.
It leaves all required decisions pending. It sends no email, changes no external
service and fabricates no approvals. Re-running creates another new order; it
does not overwrite the Outlook demo thread or existing engineering artifacts.
The existing PDF parser reads the actual drawing annotations and specification
text into source spans. The seven response examples concern enclosure/access,
cable entry, control power, CT terminals, scope exceptions, paint and test reports.
They are cited to those parsed PDF passages, not to unsupported CAD interpretations.
The PDF generator runs in a separate artifact runtime; ReportLab is not an API or
worker dependency.

Verification: `tests/test_lifecycle.py` covers deterministic pricing, persistence,
real artifact hashes, conflicting PO, omitted comments, stale source approvals,
unreleased files, BOM disagreement, engineering-gated resolution counts,
classified approvals, closeout inputs, schedule proposals and tenant isolation.

## Complete the isolated Rev D scenario

```sh
RIVET_DATABASE_URL=postgresql+psycopg://localhost:55432/rivet_test_lifecycle \
RIVET_DATA_DIR=/separate/local/completion-artifacts \
RIVET_PDF_PYTHON=/path/to/artifact/python \
.venv/bin/python -m backend.lifecycle.completion_replay \
  --simulate-human-decisions \
  --organization-id 6151b990-82f1-57d4-a559-df777cb047af \
  --output .data/demo-readiness/lifecycle-completion-replay.json
```

This integration scenario refuses the live database. It creates a fresh fictional
order, explicitly simulates named human decisions through the ordinary approval
service, records the PM's seven reviewed answers and produces native drafter
Rev D DXF/BOM files. An unchanged layout fails before the actual moved-breaker
output passes. Customer approval for an obsolete hash is rejected; exact current
approval hashes permit the local release and closeout. The real live demo order
is not marked complete, no production approval is created and no message is sent.

The scenario records authorized engineering/commercial decisions as unapproved
historical customer-memory candidates. `tests/test_lifecycle_completion.py`
proves that a separate customer-history administrator review is needed before a
second bound order retrieves the example, and that changed evidence is withheld.
These are retrieval candidates, not automatic preferences or model training.

## Receive an RFQ before preparing the quote

`backend.lifecycle.quote_replay` creates one fresh fictional order with a sealed
company catalog and four real PDF inputs. It does **not** prepare a quotation,
import a customer message or send mail:

```sh
uv run python -m backend.lifecycle.quote_replay \
  --organization-id 6151b990-82f1-57d4-a559-df777cb047af \
  --output .data/demo-readiness/quote-replay.json
```

Send the returned RFQ PDF to the connected test mailbox in a new thread with its
exact job number in the subject. Normal capture and the background record agent
read its original PDF spans, match the requested model to the trusted catalog,
calculate the proposed price, and record exceptions. In this fixture the unit
price is USD 1,600, the total is USD 3,200, and requested remote indication is an
explicit exception because it is absent from the catalog offer. Customer-provided
costs and margins cannot set the price. Unknown models, conflicting quantities or
missing commercial fields stop quotation preparation with a specific question.

The selected Outlook email shows the quotation and its original cited evidence.
An authorized reviewer records each required commercial decision there. The
server rechecks the selected message, captured receipt, order version, package
fingerprint, current sources and gate authority before recording a decision.
Only then may the approved `quote.pdf` enter the customer's thread as an unsent
native reply. Existing human drafts remain untouched.

The first PO PDF intentionally says USD 1,500 per unit. Rivet records the mismatch
and prepares a cited clarification rather than accepting the order. The second
PDF explicitly advances the same PO to revision 2 at USD 1,600. Price is corrected,
but remote indication remains required while the quote excludes it: kickoff is
still blocked and the reply explains that specific conflict. The third PO is
revision 3, explicitly removes remote indication, and acknowledges the fictional
quotation exceptions. It prepares kickoff for scope and engineering review;
matching fields never establishes customer acceptance or production release.
The fixture keys are `po_mismatch`, `po_price_corrected`, and `po_scope_corrected`.
The legacy `po_corrected` key means price-only revision 2.

The core kickoff service requires the selected PO number, quantity and unit price
to appear together in one original cited row, with each field's source included.
It rejects combining values from different POs or revisions. Independently cited
requirements for the same PO remain in the comparison; an unestablished special
requirement cannot disappear just because the numeric values match. Catalog
model, currency and bounded equipment features are checked at this core boundary,
including callers that bypass the normal agent preparation bridge.

A narrow RFQ transmittal does not become a second incomplete request when its
original EML attachment links lead to a complete parsed schedule. Any repeated
quantity and equipment tag must agree. Independent instructions, missing fields,
contradictory quantities and unlinked documents still need review; the original
email and all its source spans are retained.

This is a bounded low-voltage circuit-breaker implementation using explicitly
labelled fields and a trusted, server-provided catalog. It does not yet interpret
arbitrary equipment specifications or independently choose an engineering design.
No model-weight training or production ERP write is implied.

Approved closeout work can also create an unsent handover reply with the actual
index PDF and document ZIP. Each source must be approved/released and physically
hash-verified; the ZIP is rebuilt from those exact bytes before delivery. The
handover needs an explicitly cited captured request or matching captured source
files. Without that provenance it waits for a deliberate thread choice rather
than guessing the latest email. Each Outlook attachment is limited to the native
inline-attachment size supported by the existing delivery lane.
