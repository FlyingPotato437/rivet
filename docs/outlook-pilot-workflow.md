# Rivet's Outlook pilot

Rivet prepares coordination work from returned equipment drawings and project email. The PM works in Outlook; Rivet keeps the source record, revision history, prepared work and review evidence behind the scenes. The web workspace is available when a question needs closer inspection.

## The daily experience

1. A message arrives in an explicitly connected Microsoft 365 mailbox/folder. Scheduled Graph delta reconciliation records it durably, including its immutable message ID. The original email and supported attachments enter the intake pipeline.
2. Rivet matches the job and revision using the source information. A clear existing job or thread routes automatically; a missing revision is recorded as Unspecified. Routine account, billing and newsletter emails are retained in capture history outside Review. A plausible but uncertain order email asks one Yes/No question beside the subject and readable contents; only conflicting identifiers require a job or revision choice. Set-aside mail can be restored. When internal updates are enabled, the PM receives a confirmation request linking directly to that intake item. If it is already resolved, the pending request is suppressed.
3. Parsing creates source-linked comments and exposes unavailable or unsupported content. The event-driven agent examines the order, traces references, compares available revision text and stages coordination work. For a returned edit owned by an opted-in native drawing package, the receipt reconciler queues the bounded correction worker only after the attachment is read and the current plan is supported. The worker independently rechecks saved drawing/BOM copies and binds any unsent Outlook draft to that originating receipt; a newer message in that same thread blocks the handoff. Unsupported or ambiguous edits stay visible for review. This path does not turn a generic reply into a claim that the customer markup was corrected. Approved company instructions and examples guide wording and procedure.
4. An optional internal email brief reports a ready automatically prepared reply with its order, source/open-point counts and validated Outlook/record links. It omits the reply body and confidential source text. The same draft/evidence pair generates one notice, and sending rechecks freshness, the latest message in its thread and the configured recipient. Stale or already-handled drafts suppress unsent notices; other actual coordination work retains the existing brief path. It goes only to a server-configured internal recipient when delivery has been enabled. Customer replies are separate.
5. In a returned email, the PM opens **Reply with Rivet**. The Outlook task pane matches that email's durable receipt to its order. A durable background worker has already queued a response using the captured message, attachments, recorded responses and bounded source excerpts; opening the pane is not the trigger. The pane follows actual queued, running, ready or blocked status. Relevant approved customer memory supplies historical context with its original order and sources; it never silently becomes the current drawing specification.
6. The PM sees the editable response, source links and unresolved questions. Relevant cited released drawings are suggested; the PM can change the selection. A single **Insert draft in Outlook** action validates the current message and evidence, saves the review and opens Outlook's native reply composer. These checks repeat across asynchronous boundaries so switching emails cannot insert an old reply into a new thread. Private attachment bytes pass through an authenticated compose request; permanent public document URLs are never created. Outlook keeps the thread; the PM checks recipients and sends. Opening a draft is not recorded as sending it. Clients without the attachment API show a clear limitation instead of quietly dropping files.
7. The review records the original draft and the accepted, edited or rejected outcome. An administrator can explicitly approve a response as a future example. Changed documents, withdrawn guidance and revoked examples invalidate affected context.

## Build and live setup

| Area | Implemented | Live acceptance still required |
|---|---|---|
| Mail capture | Per-folder checkpoints, full pagination, duplicate prevention, receipt ledger, leases, retries and recovery | Authorized Microsoft 365 tenant, licensed mailbox, app identity and correctly scoped mailbox permission |
| Order work | Intake, extraction, review flags, saved agent steps and staged proposals | Representative customer documents and ground-truth review |
| Outlook reply | Add-in manifest and task pane, authenticated generation, evidence links, editing, review and native reply API | HTTPS host, Clerk login inside Outlook, tenant installation and real threaded reply test |
| Updates | Default-off internal outbox, frozen payloads, provider idempotency and acceptance status | Verified sender, explicit internal recipient and a live receipt/brief loop |
| Company learning | Versioned rules, approved examples, customer memory, review provenance, revocation and paired replay with a human rubric | Sufficient reviewed examples and measured improvement on cases withheld from retrieval |

The local task-pane preview uses real order data and the configured AI provider. It is labeled as a preview: it does not prove a connected mailbox or a successful Office-hosted reply.

The October 12 local acceptance test also exercised the actual Outlook web host:
scheduled email and attachment capture, automatic order creation and coordination
drafts, a grounded reply, human review, and native unsent reply insertion with an
exact original attachment. See [the bounded acceptance record](outlook-addin.md#local-outlook-web-acceptance--october-12-2026).

### Customer onboarding versus development setup

Local certificate installation, browser trust troubleshooting, development keys,
and creating Rivet's own test tenant are development tasks. Customers should receive
an already hosted HTTPS add-in and an operating backend; they should not repeat
those steps.

The intended customer path is administrator approval, selection of the permitted
mailbox and folders, team sign-in, then one labeled test message proving capture,
matching, review and draft creation. Background sync health and a clear last-sync
time must be visible. Existing mail rules and permissions need review because they
determine actual coverage. IT approval and Microsoft app rollout can add elapsed
time even when the setup interaction is short.

Today this is an operator-assisted, single-Microsoft-tenant pilot setup. It is not
yet a self-service, multi-company onboarding flow or a verified five-minute setup.
Before selling that experience, host the worker and private surfaces, package
per-tenant consent and scope checks, prove persistent Outlook authentication, and
provide a repeatable acceptance/recovery check. Internal update emails need their
own configured sender, destination and delivery test.

## Reliability

The goal is accountable processing of every accessible message in the agreed scope. Each captured item has a receipt and visible status. A separate polling loop continues independently of long AI runs. Failed imports remain recoverable; reconciliation does not depend on a notification arriving.

This is not a claim that no email can ever be missed. Messages moved outside selected folders, deleted before capture, inaccessible due to permissions, or unavailable during provider outages require operational controls. Setup must inspect mailbox rules, define included folders, monitor stale synchronization timestamps, and test recovery. A provider accepting an outgoing brief is not proof of delivery, reading or acknowledgement.

## Company-specific improvement

Rivet retrieves current documents as evidence and separately retrieves approved company procedure and relevant reviewed responses. A historical example's price, specification, person or approval is never a fact for the current order.

Accepted/edited/rejected counts show review behavior, not accuracy. Evaluation separates cases by source order; held-out orders must be excluded from example retrieval. Measure comment recall, source/location correctness, unsupported statements, draft edit effort and PM time. Keep model and prompt versions with each result.

This release does not train model weights or share one company's documents with another. It provides a controlled learning mechanism that can be tested, corrected and revoked. [OpenAI's accuracy guidance](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy) distinguishes supplying missing context from changing model behavior; neither should be called an improvement without evaluation.

## Customer continuity and the smaller workspace

An administrator or PM explicitly links an order to a customer. Rivet records the binding and proposes historical notes from comments that a person has reviewed and answered. These candidates retain the original wording, source location, responder and project. A person must approve reuse. A historical reference and a standing preference are different types; both have applicability conditions, and stale, expired, revoked or conflicting notes cannot silently supply technical requirements.

This answers Nikhil's rotating-engineer problem: the next engineer can find what the customer previously requested and how the team answered, rather than asking the customer to repeat everything. After approval, the reply assistant distinguishes the approved record from later amendments. It does not infer that a closed comment proves the drawing changed.

The dashboard has three primary destinations: **Review**, **Orders**, and **Customers**. Review is a flat list of decisions and prepared work. Its filters separate **Needs you**, **Rivet working**, and **Results**. Captured comments are grouped as recorded context, without changing their source review state. Explicit coordination tasks, unreadable sources, failed work and agent questions remain actionable. Each grouped item is still directly accessible. Unmatched files lead directly to the one order-matching decision. Uploading files is secondary; routine intake belongs in Outlook. Sources, detailed history and administrative integrations remain available when needed.

## Integration depth and a measurable flywheel

Integration should deepen along a repeatable sequence:

1. Agree the selected mailbox/folders and prove durable capture, retries and an understandable health status.
2. Connect the company's job/customer identifiers to order context; require confirmation for ambiguity.
3. Add approved recurring customer preferences and company response practices with provenance.
4. Capture edits and rejected drafts as review outcomes. Reuse only explicitly approved examples; withhold evaluation orders entirely from example and customer-memory retrieval.
5. Replay the same original inputs with memory off and on, using a fixed model and prompt. A human grades source support, responsiveness, customer context and unsupported commitments. Deploy changes only after review of results.
6. Deepen source access while preserving permissions and approval boundaries. The current demo uses real Outlook and read-only access to a selected SharePoint site. ERP and drawing-vault sources use independent local demo adapters; company-specific vendor and collaboration connectors remain pilot work. See [the connected demo and verified boundaries](connected-demo.md).

The improvement flywheel is reviewed work → approved reusable knowledge → better retrieval and drafting → measured outcomes. Product-wide improvements can use aggregate metrics, synthetic failure cases and separately authorized evaluation data. Customer documents are not automatically pooled across tenants. Weight training is a separate future decision requiring explicit data rights, a supported provider and held-out evaluation; the current implementation does not claim it.

## Running private reply evaluations

`scripts/evaluate_company_replies.py` is an operator tool, not a public endpoint. It reads only feedback explicitly approved for evaluation and frozen inputs saved with the original draft. The human's corrected answer stays out of the generation input. All evaluation orders are withheld even when a run selects only a subset of cases. Immediately before provider calls, consent and evidence freshness are checked again.

```sh
.venv/bin/python scripts/evaluate_company_replies.py prepare \
  --organization-id <tenant-uuid> --reviewer <operator-name> \
  --output .data/evaluations/plan.json
.venv/bin/python scripts/evaluate_company_replies.py run \
  --plan .data/evaluations/plan.json \
  --output-dir .data/evaluations/run-001 --allow-provider-calls
.venv/bin/python scripts/evaluate_company_replies.py score \
  --run .data/evaluations/run-001/run-private.json \
  --grades .data/evaluations/run-001/grades.json \
  --output .data/evaluations/run-001/score.json
```

Use a fresh output directory for each run. Files are created with owner-only permissions; do not commit them. Give the reviewer `review-blinded.json` and the rubric template, retaining the answer key separately. Fill every grade with a boolean, named reviewer and evidence-specific reason. Generation and citation checks are not semantic accuracy scores. The default review gate requires at least ten cases across three orders, complete human grades, no regressions or generation failures, an observed improvement, and no candidate failures on evidence support or unsupported commitments. This is a minimum product review rule, not statistical proof of safety. The tool never trains or deploys a model.

## Focal demonstration

Use a clearly labeled sample order. Start with a returned email, show its captured receipt and the work Rivet prepared, then open the compact reply pane. Show an evidence link and one unresolved reference. Edit the response and show its review record. Finish with the internal brief and company guidance controls.

Suggested description: “We are moving the daily workflow into Outlook. Rivet captures returned documents, prepares the coordination work, and brings the PM a response to review. The deeper order record is there when needed. Each manufacturer's approved procedures and corrections improve the context for its next response, and we measure that on withheld cases.”

Do not present a standalone preview as Outlook integration proof. The authorized demo mailbox now completes the live loop with a real SharePoint specification, local ERP/vault context, a prepared response, and a reviewed native Outlook draft with supporting PDFs. The next milestones are a redacted historical order benchmark, production add-in authentication, and a supervised pilot. SAP, vendor-specific drawing vaults, Slack and Teams are not live connections in this demo.

## Setup

- [Mailbox scope and recovery](mailbox-reliability.md)
- [Outlook hosting, authentication and sideloading](outlook-addin.md)
- [Company guidance and evaluation](company-learning.md)
- [Production deployment](deployment.md)


### Automatic preparation boundaries

The worker reconciles the latest actionable message per customer thread, independently of a dashboard or pane visit. It waits for parsing and connected-source refresh. A later acknowledgement does not prevent the prior unanswered request from being prepared, and independent threads on one order remain separate. Bounded rotating batches prevent one busy order from starving the rest.

Current automatic preparation is limited to messages received within seven days. Older mail remains in the order context and supports manual reply requests, avoiding a surprise wave of historical drafts. Deterministic matching and relevance rules are inspectable; they are not a measured promise of perfect email classification. Scanned or handwritten job identifiers may still need a person.

With the Autopilot setting on and `Mail.ReadWrite` granted, Rivet also places the prepared reply as an unsent draft in the customer's Outlook thread and labels the email; sending it is the approval and Rivet learns from what was sent (see [Rivet as an AI employee](ai-employee.md)). Without that permission, an unsent Outlook reply is created only when the PM clicks Insert draft in Outlook. Rivet never sends. Internal ready notifications require the existing delivery switch, sender and recipient configuration; the local development environment keeps delivery off. Private hosting and per-company Microsoft consent remain prerequisites for customer rollout.
