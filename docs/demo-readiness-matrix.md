# Rivet demo readiness and product coverage

Audited October 15, 2026, against the current repository and read-only runtime
records. This separates the functioning pilot demonstration from the broader
quote-to-closeout product objective. It is an internal engineering assessment,
not a list of capabilities to claim as production deployments.

## Evidence labels

- **Verified live:** observed Microsoft service activity in the configured demo
  tenant, with persisted receipts and/or native Outlook evidence.
- **Reproducible local:** executable code, artifacts and tests using an explicitly
  fictional order or local adapter. It does not establish a production integration.
- **Partial:** a useful subset works, but the full requested behavior does not.
- **Missing / requires environment:** implementation or authorized access still
  needed. A schema, UI label or numeric autonomy setting is not proof of execution.

## Current evidence

- Main connected order: `24c3cffb-f4ab-4390-b77a-96121b70a10e`, job
  `DEMO-OUTLOOK-001` in the founder organization.
- Read-only runtime audit: `.data/demo-readiness/readiness-audit.json`. It records
  a current inbox checkpoint with no sync error, native Outlook draft IDs, the
  reply-drafted category, a released Rev B PDF attachment, and two manually sent
  replies detected as edited. One edited reply is approved for order-scoped
  example reuse; another correctly remains pending review.
- Rehearsal two: mailbox received the email at `2026-10-15T06:46:17Z`, capture
  completed at `06:47:50Z`, native draft existed at `06:48:11Z`. That is **21
  seconds from capture to draft**, not from sending the email or clicking the
  refresh button. The PM sent at `06:53:37Z`; the worker recorded it by
  `06:54:33.632Z`. Delivery and polling add variable time. Do not promise a
  universal under-one-minute trigger-to-ready time.
- Expanded local replay: `.data/demo-readiness/lifecycle-replay.json` identifies
  the newest order and its actual artifacts. Fresh replays include server-sealed
  commercial provenance; older rehearsal orders remain in history and should
  not be used as the current demonstration. The builder preserves existing work.
- The final commercial rehearsal uses order `201cbe91-1b4e-4166-adf0-591f7edc6397`
  (`DEMO-LIFECYCLE-1015-084345`). Its exact returned PDF arrived through the
  real mailbox. All eleven original comments remained; the transmittal stayed
  email context. No generic native reply was placed while the four decisions
  were pending. Four authenticated **fictional demo** reviews then automatically
  queued one native Outlook reply. At `2026-10-15T08:49:27.720604Z` it contained
  `response-letter.pdf` and `change-order.pdf` and the real reply-drafted category.
  It remains unsent. Proof: `.data/demo-readiness/final-commercial-handoff.json`
  and `.data/demo-rehearsal/final-commercial-*.png`. This is internal review of
  a fictional proposal, not customer acceptance or an engineering release.
- The replay uses a real returned PDF with eleven native annotations, parsed by
  the existing PDF parser; a specification PDF; native DXF and Excel originals;
  three inspectable native draft changes; and an exact existing-entity layout
  task. All are labelled fictional. No approval is fabricated.
- A later email containing identical PDF bytes at the same explicitly confirmed
  revision now reuses the existing order document, including a prior direct
  upload or server-created fixture. Its EML and attachment-arrival link remain
  separate evidence. A narrow known attachment-transmittal body does not become
  a redundant unresolved comment; unknown prose, independent technical requests
  and approval wording remain visible. A dedicated regression preserves all
  eleven original comment IDs when the rehearsal email arrives. Earlier duplicate
  history was not deleted or rewritten.
- Actual final local HTTP execution is saved in
  `.data/demo-readiness/local-http-actions-final.json`, isolated order
  `6603eeec-37b8-4a33-9956-e1d734e1e43a` in `rivet_test_actions_final`.
  Four final-approval outbox jobs produced eight network-observed effects in a
  persistent local enterprise simulator, including actual released file bytes.
  Signed order snapshots remained unchanged. This is a local integration proof,
  not a live SAP, SharePoint publishing, vault or Teams deployment.
- Initial-quotation capture has now run through the real mailbox: order
  `f13415f5-9e99-4575-a059-c4cc2e733eeb`, job `DEMO-QUOTE-1015-092316`.
  The incoming RFQ PDF automatically produced a USD 3,200 proposal and the
  requested-remote-indication exception using the fictional sealed catalog.
  Its original automatic package (`941038…`) remains in superseded history.
  Before approval, source-register formatting was improved and the same inputs
  were prepared again through the ordinary service. Final package
  `fc7db285-6636-441f-849f-682814629328` received three authenticated fictional
  reviews in the real selected-message Outlook pane. Its native threaded draft
  contained the actual `quote.pdf` (25,075 bytes) at `09:42:43Z`. The founder
  manually sent it to the founder's own account at `09:46:51Z`; the worker detected
  the send at `09:47:18Z` as accepted, with an approved order-scoped example.
  Proof: `.data/demo-readiness/final-quote-native.json` and
  `.data/demo-rehearsal/final-quote-sent-confirmed.png`. This proves native quote
  preparation and the human send, not real customer acceptance of the quote.
- The first two PO scenarios now have live native-Outlook evidence on that same
  founder order. PO revision 1 was re-inspected through the ordinary intake
  service after correcting a filename revision-token bug; it matched without a
  human confirmation. Its native unsent reply at `10:12:22Z` identified both the
  USD 1,500 versus USD 1,600 unit-price difference and the remote-indication scope
  conflict. Revision 2 arrived as a fresh email and automatically produced an
  unsent reply at `10:15:31Z`: prices match, but the same scope conflict remains.
  Both replies retain the quotation caveats and do not accept the order.
  Proof: `.data/demo-readiness/final-po1-rehearsal.json` and
  `.data/demo-readiness/final-po2-rehearsal.json`.
- Revision 3 also arrived through the real mailbox and matched without a human
  filing confirmation. It explicitly corrected the scope and produced a native
  unsent reply at `10:19:32Z`, acknowledging the recorded match while retaining
  quotation caveats. Current kickoff package
  `fc49cd06-d1af-4674-9675-fce55b32a920` has no comparison blockers and awaits
  scope and engineering decisions. No approval, order acceptance, production
  release, delivery promise or customer send was inferred. Proof:
  `.data/demo-readiness/final-po3-rehearsal.json` and
  `.data/demo-rehearsal/final-po3-kickoff-ready.png`.

## Goal coverage

| Requested stage | Status | What actually exists | Remaining work / limits |
| --- | --- | --- | --- |
| Watch Outlook automatically | Verified live | Durable Microsoft Graph delta checkpoints, scoped receipts, retry/lease handling, recurring inbox checks and immediate reconcile control | Hosting, observability and deployment operations still need a production pilot. No claim that an unconfigured folder or mailbox is watched. |
| Ignore unrelated mail and match order mail | Verified live for existing demo order; reproducible local for broader routing cases | Existing incoming test mail was attached to the correct job; exclusions and uncertain matching have tested paths | Run a representative customer mailbox evaluation. Ambiguity should ask one question; arbitrary mail must not flood the review queue. |
| Create a new order from a new job number | Reproducible local / tested | Existing intake service can create the order and retain email plus attachments | A fresh live new-job rehearsal is distinct from a new thread on the existing demo job. |
| Spec package to quote and exceptions | Verified live capture/preparation for the bounded fictional RFQ; broader coverage partial | Original PDF fields identify the low-voltage circuit-breaker category, exact catalog model and quantity. The agent joins a trusted server catalog, uses Decimal gross-margin pricing and records explicit requested-versus-offered exceptions; produces a source-linked PDF | This supports explicitly labelled inputs for one category, not general spec-to-equipment design. Unknown models, ambiguous quantities and missing trusted pricing require clarification. Tax/freight, availability and delivery commitments are not invented. |
| Quote and exceptions in sales' Outlook drafts | Verified live in the fictional test mailbox | After the actual price, scope and customer-commitment gates were reviewed in Outlook, Rivet placed the quote PDF in a native threaded draft. The founder then sent it manually; Rivet recorded the accepted outcome | Formatting-only re-preparation preceded the final reviews; the original automatic package is preserved. An ambiguous or missing thread needs explicit selection. No customer sending authority or real customer acceptance is implied. |
| Purchase order checked against quote | Verified live for all three fictional PO scenarios | Explicit PO number/revision/model/quantity/currency/unit price and bounded equipment requirements are read from original spans. Actual PO1 and PO2 generated native unsent clarifications; Rev 3 generated a matching acknowledgment and a source-current kickoff awaiting scope/engineering decisions. Each matched without human filing confirmation | Rev 2 corrects price but remains blocked because remote indication is still requested; Rev 3 explicitly removes that feature. Quote exceptions remain visible; matching does not establish their acceptance, approve engineering or promise delivery. Arbitrary PO formats need evaluation. |
| Draft sales order in SAP | Reproducible local HTTP execution | An approved kickoff creates a persistent draft sales-order resource over actual HTTP, with source-bound receipts and reconciliation | The target is an explicit local simulator. No writable SAP deployment exists. Need the customer's SAP environment, communication arrangement, object mapping and approved write scope. |
| Job folder in SharePoint | Reproducible local HTTP execution | An approved kickoff creates the local simulator's job-folder resource over HTTP | The real SharePoint permission is read-only. No real SharePoint folder was created by this lifecycle service. |
| Customer-history kickoff brief | Reproducible local / existing customer-context service | Explicit customer binding and approved standing preferences/historical sources, with provenance, expiry/conflict handling | An unbound customer is not guessed. The new lifecycle replay intentionally does not pretend a real customer's history exists. |
| Check first drawings against history | Reproducible local, partial | Approved-preference topic/text checks point to passages and report incomplete/scanned coverage | This is retrieval and preflight assistance, not visual engineering compliance or proof every preference is satisfied. |
| Read all eleven returned comments | Reproducible local with realistic PDF | Eleven native PDF annotations survive extraction with actual page/annotation locations; seven answers cite parsed specification passages | General scanned handwriting/cloud interpretation needs a broader evaluated corpus. Do not treat the deliberately readable native fixture as handwriting validation. |
| Seven cited answers | Reproducible local; native cited replies verified live on connected demo | Replay answers cover indoor enclosure/access, cable entry, control voltage, CT terminals, remote indication exception, finish and test documents | Replay answer text is a reproducible scenario, not proof of universal autonomous answer quality. The connected live model flow is a separate proof. |
| Three CAD/data edits | Reproducible local; ordinary agent-tool wiring implemented | Real DXF INSERT attribute changes for trip unit/nameplate and XLSX/CSV BOM cell changes, exact source hashes and before/after previews. Normal agent tools inspect owned native entities and prepare literal, source-supported copies; saved bundle IDs feed revision review | The new scheduled-agent regression uses a scripted provider; it is not live-model evidence. Editable DXF/structured BOM only, with bounded fields. No DWG, proprietary CAD vault, broad parametric editing or geometric redesign. Copies remain pending engineering review. |
| Move a breaker / layout change | Reproducible local task, no geometry edit | Validated existing entity anchor/bounds produce a located drafting task; arbitrary proposed coordinates are rejected | A drafter still checks clearances/routing and performs the layout change. The product must not imply geometry was moved automatically. |
| Added breaker priced as billable scope | Reproducible local | Fixture has an explicitly trusted scope exception and pricebook; $900 cost at 25% gross margin yields a $1,200 draft addition | No billability inferred from a customer request alone. Real accepted scope, pricing rules and commercial authority must be connected and reviewed. |
| Customer reply appears natively in Outlook | Verified live | Actual unsent threaded reply, category, released drawing attachment, duplicate/stale guards and preserved human edits | Existing human drafts are deliberately preserved. Later evidence can make a prepared draft stale; the PM must see that state. |
| PM sends reply and change order | Reply sent live; approved commercial native draft verified live | PM edited and sent the drawing-response test; worker recorded the edit. A separate fictional lifecycle package, after explicit demo reviews, automatically produced one native reply with the exact response-letter and change-order PDFs | The commercial proposal remains unsent for inspection. Draft CAD and internal drafting lists are excluded. Rivet does not send customer mail. |
| Revision D check: 11/11 addressed | Reproducible local logic, **not completed in current eleven-comment replay** | Coverage, evidence-backed resolutions, BOM equality, released outbound documents and exact engineering approval gates are implemented and tested | The default live-demo order still has pending work. A separate isolated completion scenario now fails on unchanged layout, then verifies real drafter-produced Rev D DXF/BOM, recorded PM answers and exact approvals before reaching 11/11. Do not transfer that simulated completion to a real customer order. |
| No unreleased customer attachments | Verified live for released Rev B selection; local release-check tests | Draft Rev C is excluded from automatic native attachments; the revision-check package blocks unreleased files | This controls Rivet's prepared outputs. It cannot prevent a person attaching a file outside Rivet. |
| Purchasing and six-week schedule impact | Reproducible local HTTP execution | Trusted lead-time preparation plus the exact configured buyer's approval posts a local channel handoff and updates an internal material milestone. The worker executes after final human gates, without another dashboard click | No live Teams or customer scheduling system is connected. The milestone is not a promised ship date; production, test and transit durations remain unresolved inputs. |
| Customer approval to production release and SAP update | Reproducible local HTTP execution | Exact customer/engineering hash-bound signoffs and completed revision evidence permit actual file publication to the local SharePoint/vault simulator, followed by its local SAP release state | No actual vendor production release. The real Rivet Order remains unchanged. Proposed commercial changes are separate from customer price acceptance and never authorize billing. |
| Automatic routine status replies | Deterministic draft and local send simulator implemented; production sending unavailable | Narrow status-only requests use exact, human-reviewed facts with current source hashes and expiry. Default is an unsent draft. Explicit opt-in plus exact recipient allowlist can exercise a synthetic-order local outbox with duplicate/uncertain-outcome guards | No Graph send path or Mail.Send grant. Live customer sending remains unavailable. Ambiguous, late, technical or commercial requests escalate. No broad schedule-system integration or convenient status-fact setup UI is claimed. See `docs/routine-status.md`. |
| Closeout | Reproducible local including native-draft API tests | Exact approved/released physical test reports, as-built revision and supporting files become a PDF index and ZIP with a manifest. Release/check dependencies, human gates and hashes remain current. Approved handover can enter the captured customer thread as an unsent native draft | The Outlook path is fake-Graph tested, not yet live-rehearsed. Missing request provenance never selects an arbitrary thread. No ERP shipment trigger, independent test certification or comprehensive document-completeness claim. |
| Learn from each order | Partial, real tenant-scoped retrieval feedback | Native sent/edited reply outcome recorded; source-current examples may be reused within the order; approved customer history can be retrieved across explicitly bound orders | Authorized engineering and commercial approvals now create structured, unapproved historical customer-memory candidates. A separate history review is required before a later bound order retrieves them. There is no cross-company foundation-model training or automatic standing-preference promotion. |
| Dashboard control room | Partial, working | Main work feed, real native draft/sent status, source detail, lifecycle prepared outputs, decision gates, blockers and artifact links | End-to-end order bottleneck analytics, authoritative recovered revenue and production cross-system reconciliation are not complete. Draft proposed scope value is not revenue recovered. |

## Autonomy levels

| Level | Actual state |
| --- | --- |
| 1: prepare; people send/sign off | Native Outlook reply flow is live; broader lifecycle operations are local preparation with protected decision gates. |
| 2: routine work | Automatic capture, filing and reply preparation work. Deterministic status drafts and a default-off synthetic send simulator exist; production customer auto-send and live Teams handoffs remain unavailable. |
| 3: approved edits and enterprise writes | Native CAD text/BOM copies and eight source-bound local HTTP actions are implemented and tested. Final human gates can queue their worker execution. Actual SAP/SharePoint/vault/Teams writes still need real scoped adapters and permissions. |
| 4: whole orders with exceptions | Policy can record a level but does not activate this capability. Whole-order autonomous execution remains missing. |

Numeric autonomy never grants engineering, price, scope or customer commitment
authority. Synthetic local-demo decisions require a real authenticated team
administrator. Non-synthetic work requires an administrator-configured exact
user ID in the appropriate `gate_approvers` mapping. An ordinary writer is not
automatically an engineer or commercial signatory.

## Integration and safety audit

The lifecycle router is mounted in `backend/api/main.py` and uses the existing
workspace boundary. It is not a public endpoint exception. Reads are tenant
scoped; writes require the workspace header, allowed origin and authenticated
writer; individual approvals add gate authority. The same order lock,
optimistic version and idempotency implementation protects mutations. Artifact
downloads resolve only manifest names under server-derived UUID paths and verify
their hashes. CAD HTML/SVG responses have a restrictive sandbox policy.

The Outlook lifecycle pane is tied to the captured message. Its mutation endpoint
rechecks the internet message ID, receipt, order version, package fingerprint,
cited source membership/currentness and authorized gate under the order lock.
Another email or a changed source cannot reuse a previously displayed approval.
Handover file-identity citations are explicitly server-authored identity records;
they prove file selection and hashes, not engineering correctness or test validity.

Commercial values now require either server-created synthetic-demo provenance or
a current administrator-controlled live ERP binding explicitly authorized for
`pricing`, `lead_time` or `commercial_scope`. Exact structured values are still
required. Customer-uploaded JSON, even if it contains trust-looking fields,
cannot supply the manufacturer's cost/margin rule. Changed source trust,
original hashes, native artifacts or synthetic/production context invalidates
prepared work. CAD layout locations are derived from validated native entities.

Production work still requires organizational signatory onboarding, reliable
artifact/object storage and backups, hosted worker operations, recovery drills,
real customer retention/access policies, and a representative evidence-quality
evaluation. These are operational requirements, not evidence that the current
demo is unsafe or that every integration must be added before a limited pilot.

## SAP and other access still needed

1. **SAP connectivity demonstration: verified.** The user signed in and the
   actual read-only sandbox GET succeeded at `2026-10-15T08:04:39.793287Z` in
   1.015 seconds. `.data/sap-sandbox/verification.json` records order `1`,
   `API_SALES_ORDER_SRV` and the source SHA-256. Generic SAP sample order `1` is a
   separate connectivity proof, never the TU-2500/TU-3000 breaker scope. No
   SAP writing permission was added and no credentials are included in reports.
2. **Real SAP writes:** authorized company sandbox, API/communication arrangement,
   named order/material/pricebook mappings, scoped draft-order/change/release
   permissions, idempotency and post-write reconciliation.
3. **SharePoint writes:** selected site/library and specifically approved create
   or update permissions. The current selected-site read grant does not authorize
   folder creation or released-document publication.
4. **Vault:** actual product and version (for example the company's chosen CAD/PDM
   system), authorized test repository, checked-in source/checkout semantics,
   version/release mapping and engineer approval identity.
5. **Teams:** target tenant/team/channel, consented app or workflow endpoint,
   internal-only recipient policy, message/draft receipt handling. No supplier
   chasing or customer sending is currently implied.

## Concrete next local work

1. The bounded lifecycle preparation bridge is implemented in
   `backend/records/lifecycle_preparation.py` and the normal record-agent tools.
   It automatically stages complete trusted quote/PO inputs and exposes all
   lifecycle preparation schemas, with no approval, production-release or sending
   tool. The real RFQ, native quote/manual send and all three PO rehearsals are
   verified for the fictional one-category example. Next, evaluate representative
   authorized customer formats. Absent facts and prerequisite decisions remain
   blockers.
2. The quote/change-order artifact-to-native-Outlook handoff is implemented with
   exact version/hash binding, current mailbox provenance and recipients. The
   revision-response/change-order path is now rehearsed in the authorized demo
   mailbox; initial quotation now has real capture, native PDF and human-send
   proof. Closeout native index
   and ZIP delivery is implemented and fake-Graph tested with physical tamper guards.
   Operational draft status is stored outside the immutable signed package; it
   does not imply a customer received a draft or approved its contents.
3. The separate isolated Rev D completion replay is implemented and verified
   in `.data/demo-readiness/lifecycle-completion-replay.json`. It demonstrates
   unfinished-layout failure, actual native drafter changes, 11/11 after explicit
   simulated engineering approval, stale customer-signature rejection, buyer
   approval, local release and closeout. Keep it separate from the live unfinished
   order. Add a real pilot drafter/customer approval set when authorized.
4. Structured CAD/commercial feedback candidates now exist. An integration test
   proves zero automatic reuse, explicit administrator history review, retrieval
   on a second bound order, and withholding if the historical source changes.
   Evaluate the candidate quality with the pilot company before reducing review.
5. The local HTTP enterprise harness now executes approved kickoff, proposed ERP
   changes, buyer handoff/material schedule and exact document release, with
   persistent state, partial retries and lost-response reconciliation. Replace
   individual adapter targets only after the pilot company supplies an authorized
   test environment and exact role/object mappings. Do not show a local channel
   receipt as a delivered Teams message. See `docs/local-enterprise-actions.md`.
6. Ordinary-agent CAD and routine-status guard regressions now pass. Evaluate
   representative real customer documents next. Scripted provider tests establish
   tool execution and invariants; they do not establish live model reliability.

The honest current demonstration is strong: a real email becomes a cited native
Outlook reply using live SharePoint plus labelled local ERP/vault evidence, and
the broader local order workflow now produces real native draft edits and
commercial preparation. The complete autonomous quote-to-closeout employee is
the product objective; it is not all verified live today.

## Isolated completion and verification

`backend.lifecycle.completion_replay` refuses databases other than `rivet_test`
or `rivet_test_*`. Its CLI additionally requires `--simulate-human-decisions`.
It never changes the live server, sends a message or creates production approval.
The named fictional PM, engineer and buyer decisions use the ordinary gate
service; they are not a bypass exposed to the agent or API. The actual drafter
DXF preserves the original draft, moves the located breaker and adds the
commercially approved extra device; the native BOM contains the corresponding
part. The unchanged draft fails the layout-resolution check.

The lifecycle verification passed 60 lifecycle/completion/native-drafting
and customer-memory tests, including later-order memory retrieval. Reviewed
answer records, native output hashes and exact approval hashes are required
before reporting completion. A separate 73-test intake/record regression covers
attachment reuse, the eleven-comment email arrival and conservative email-body
handling. The current live default replay remains pending human decisions.

Earlier follow-up verification passed 101 commercial handoff/automatic reply/autopilot/connected-flow tests, 73 duplicate-import/context/intake tests, 18 pure native-drafting tests, 8 PDF export tests and 111 frontend tests. A subsequent 80-test run covers the bounded incoming RFQ/PO path, selected-message lifecycle projection/mutation, approved native handover, commercial handoff and automatic replies. The selected-message tests include tenant separation, named gate authority, changed source text/hash/state, unrelated email rejection, order-version checks and idempotent decisions. The handover test uses actual generated Rev D files and rejects physical tampering.

The final native-CAD/drafting/lifecycle-agent run passed 47 tests, including 13 new normal-agent cases with a scripted provider and real DXF/XLSX outputs. The final routine-status/automatic/autopilot/worker/reply run passed 138 tests, including 29 routine cases and source expiry checks at view, review, compose and automatic placement. Scoped Ruff checks passed for those modules. See `docs/native-drafting-agent.md` and `docs/routine-status.md` for their boundaries.

These suites overlap; do not add their counts into a claimed unique scenario total. Earlier TypeScript and marketing/workspace/Outlook builds passed; the root task retains the final current-UI build and live rehearsal results. Commercial approval waiting is a deliberate decision state; unrelated source-only replies continue normally.

A subsequent 54-test commercial/lifecycle/agent/selected-message run covers the
actual RFQ transmittal body, complete versus incomplete attachment provenance,
independent instructions, inconsistent cover quantities, and the four-stage
RFQ → price-mismatched PO → price-corrected but scope-conflicting PO → explicitly
scope-corrected PO path. Original source hashes bind the added feature comparison.

The final related run passed 118 tests across commercial mail, revision intake,
mailbox sync, lifecycle-agent preparation and lifecycle core. A subsequent focused
42-test commercial-mail/lifecycle run verifies the final coherent-row guard:
the core service rejects mixed purchase-order/revision numeric evidence and keeps
separately cited requirements from the same PO. Strict transmittal parsing retains
unknown instructions, including instructions disguised as greetings/signatures.
Scoped Ruff passed. These counts overlap with each other and earlier suites.
