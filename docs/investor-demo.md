# Rivet: five-minute connected workflow

This is the runbook for the fictional `DEMO-OUTLOOK-001` order. The demonstration
uses the actual Rivet application, its worker and a licensed Microsoft 365 test
mailbox. Keep this document alongside the backup recording.

## Start here

Run this from the repository root with the project virtual environment:

```sh
.venv/bin/python scripts/demo_preflight.py
```

The default check is read-only. It checks API/database health, the configured
reply model, the HTTPS workspace, one worker process, existing source bindings,
the local ERP/vault originals and the returned drawing PDF. It does not sync mail
or create, approve or send anything. A running process is not proof of a completed
email workflow; rehearse the full loop below.

If a development service is missing, use:

```sh
.venv/bin/python scripts/demo_preflight.py --start
```

This starts only missing local services and leaves existing ones running. Logs
and newly started PIDs go under `.data/runtime/demo-*`. It does not install
packages, change permissions, migrate the database or replace fixture files.
Review a failing check before the meeting; repeating `--start` cannot repair an
expired login, bad consent, missing credentials or a failed worker job.

**Do not regenerate `.data/connected-demo`.** Its existing `connections.json`
contains the live SharePoint binding. The starter fixture generator replaces that
file with a local specification service. The preflight never writes it.

Open these in the same signed-in Chrome profile:

- Workspace: `https://localhost:5179/#work`.
- Microsoft Outlook web, signed into `SrikanthSamy@rivetusa.onmicrosoft.com`.
- The private **Rivet Integration Demo** SharePoint library, if showing its origin.
- The backup recording, ready to play without network access.

Use the founder organization containing `DEMO-OUTLOOK-001`, not **Rivet · public
demo**. The public sample team has no connected Microsoft mailbox. Keep the
authenticated Rivet pane open before the meeting and check it again after a full
Outlook refresh. Do not discover a login or certificate prompt while presenting.

## The fresh email

Use a **new message**, not a reply to an existing test thread. The subject must be
unique for each rehearsal so that an existing human or Rivet draft is preserved
instead of replaced. Send only between the founder's own email and test mailbox.

**To:** `SrikanthSamy@rivetusa.onmicrosoft.com`

**Subject:** `DEMO-OUTLOOK-001 | Rev C comments attached | rehearsal 01`

**Attachment:** `output/pdf/DEMO-OUTLOOK-001-E-10-Rev-C-Returned.pdf`

Suggested body:

> Hi team,
>
> Please review the attached returned drawing for job DEMO-OUTLOOK-001, Rev C.
> Confirm which trip unit governs for CB-12: the accepted order and released
> drawing B show TU-2500-DEMO, while specification revision 2 states TU-3000-DEMO.
> Please clarify the remote indication requirement and whether the supplied
> records establish production release. Attach the relevant released drawing
> with your response.
>
> This is a fictional Rivet demonstration, not a customer order or engineering
> instruction.
>
> Rivet demo reviewer

The PDF has native review annotations; the body makes the question understandable
even before the viewer opens. For rehearsal 2 or the live meeting, change the final
subject suffix. Leave unrelated mail and old drafts untouched.

## The five-minute walkthrough

| Time | Show | Say |
| --- | --- | --- |
| 0:00–0:20 | **Your work**, connected mailbox status and recent completed work | “Rivet works in the mailbox. This page is the summary of what it read, filed and drafted, plus the few questions that need a person.” |
| 0:20–0:50 | Send the fresh fictional message from the phone or own Gmail, then click **Check inbox now** | “The customer sends a normal email and marked-up PDF. Rivet checks the inbox and attaches it to the job.” |
| 0:50–1:30 | The actual working state, followed by **Ready in Outlook** when saved | “It reads the return and checks the connected records before preparing anything. This is the real worker running.” Use the actual elapsed time; do not promise a one-minute result until measured. |
| 1:30–2:15 | Open the message in Outlook, its **Rivet: reply drafted** category and existing unsent native reply | “The response is already in this customer's thread, with the released drawing attached. I haven't asked Rivet to generate it.” |
| 2:15–3:15 | Open **Reply with Rivet**; source checks, conflict and citations | “The accepted scope and released drawing B say TU-2500-DEMO. The SharePoint specification says TU-3000-DEMO. Drawing C is still a draft, so Rivet asks which record governs. It doesn't choose the engineering answer.” |
| 3:15–3:45 | Click a citation, show the retrieved original PDF at the cited page/highlight, optionally open SharePoint origin | “Every material statement links back to evidence. We retain the version used to prepare this reply.” Only claim an exact line opens in SharePoint itself if that behavior has been verified. |
| 3:45–4:15 | Return to Outlook, edit one sentence, review the attachment and send the fictional reply | “The PM edits and sends from Outlook. Sending remains the human action.” |
| 4:15–5:00 | Check inbox again, show sent outcome and order summary after it updates | “Rivet records that I sent an edited version. The order keeps the received request, the reply and the points still open. The correction can become a same-order example under the team's policy.” |

The response should explicitly cover the trip-unit conflict, remote indication
scope conflict and lack of production-release evidence. The automatic attachment
must be released drawing B. A draft drawing or an internal ERP scope document must
not be automatically attached. Do not label a drawing release as a production
release.

## Describe the integrations accurately

| Part | What this demo establishes |
| --- | --- |
| Outlook | Real Microsoft 365 inbox capture, native unsent threaded reply, category and PDF attachment. The PM sends in Outlook. |
| SharePoint | A real Microsoft Graph read from the private demo site using the selected-site grant. The source itself is fictional demo data. |
| ERP order scope | An independent read-only local adapter returning the fictional accepted order. It is not the target customer's live ERP. |
| Drawing vault | An independent read-only local adapter holding released B and draft C. It is not a live commercial CAD vault. |
| Optional SAP sandbox | A separate real API connectivity check against generic SAP sandbox data, only after its verification succeeds. It is not the breaker order or evidence for its trip-unit conflict. |
| Customer learning | Saved sent/edited outcomes and eligible same-order examples, controlled by team policy. It is not shared model training across customers. |
| Quotation and PO comparison | Real RFQ capture, automatic proposal, three explicit fictional reviews in Outlook, a native quote PDF and a manual send have been observed. Three actual PO emails produced native unsent price/scope clarifications and a scope-corrected kickoff awaiting human review. Prices come from a fictional sealed catalog. |
| Broader enterprise actions | Eight actual HTTP effects in a persistent local simulator, after explicit fictional human decisions. They are not writes to Microsoft, SAP or a commercial vault. |

If asked how deep it goes: “The mailbox and SharePoint are live here. The order
and vault adapters use the same evidence path with local fictional sources. A
pilot gives us the company's ERP and vault products, access rules and approved
fields; we bind those sources to its orders. We check freshness and retain the
evidence used for each reply.”

For optional SAP setup and its isolated verification, use [SAP sandbox](sap-sandbox.md).
Do not spend the main five minutes navigating an unrelated generic sales order.

## Optional second demonstration: RFQ to quotation to PO

The native quote rehearsal has passed; the tested drawing workflow above remains
the primary fallback. The current founder order is
`f13415f5-9e99-4575-a059-c4cc2e733eeb` / `DEMO-QUOTE-1015-092316`.
Its real incoming RFQ PDF automatically produced a USD 3,200 quote and a remote
indication exception. The model, quantity and requested features come from the
original PDF; prices and margin come from a sealed fictional company catalog.
This is a bounded low-voltage circuit-breaker example, not a claim that arbitrary
specifications can already be designed and priced automatically.

1. Open the actual RFQ message in Outlook and **Reply with Rivet**. Show the
   prepared quote, USD 1,600 unit price, quantity two and the explicit exception
   that remote indication was requested but is not included in the catalog offer.
2. Inspect the source page and the actual quote PDF. Say: “Rivet prepares the
   quotation and makes the commercial exceptions visible. The authorized sales
   person still decides price, scope and customer commitment.”
3. Record the three **fictional demo** decisions in the Outlook pane. Each action
   is bound to the selected email and exact current evidence. Wait for the native
   draft, then inspect its `quote.pdf` attachment. Leave it unsent unless the PM
   explicitly chooses to send the fictional proposal.
4. In a separate own-mail thread, submit the fixture PO revision 1 at USD 1,500
   per unit. Show its clarification against the USD 1,600 quotation. The reply
   acknowledges receipt and requests a correction; it does not accept the order.
5. Submit the same PO with revision 2 at USD 1,600. Show that the price now matches,
   while remote indication is still requested and excluded by the quoted catalog.
   Rivet keeps kickoff blocked and drafts a specific scope clarification.
6. Submit revision 3, which explicitly says remote indication is not included and
   acknowledges the fictional quote exceptions. Show the matching comparison and
   kickoff awaiting scope/engineering review. Rivet does not infer customer
   acceptance from numeric matching. Broader SAP/SharePoint actions are a separate
   local demonstration below.

Current verification boundary: the final quote package
`fc7db285-6636-441f-849f-682814629328` was reviewed in Outlook and its 25,075-byte
`quote.pdf` appeared in a native draft. The founder manually sent it at `09:46:51Z`
on October 15; the worker recorded the accepted outcome at `09:47:18Z`.
Proof: `.data/demo-readiness/final-quote-native.json`. The first two live PO
scenarios also passed: revision 1 produced the price-and-scope clarification and
revision 2 produced the remaining scope clarification. Both native replies remain
unsent; neither needed a human order-match confirmation. PO1 used the ordinary
reinspection service after a filename revision-token fix. Proofs:
`.data/demo-readiness/final-po1-rehearsal.json` and
`.data/demo-readiness/final-po2-rehearsal.json`. Revision 3 also passed: its native
unsent acknowledgment appeared at `10:19:32Z`, and current kickoff package
`fc49cd06-d1af-4674-9675-fce55b32a920` has no comparison blockers and awaits scope
and engineering decisions. It required no filing confirmation and inferred no
engineering approval or customer acceptance. Proof:
`.data/demo-readiness/final-po3-rehearsal.json`. None of the three PO replies was
sent by Rivet. For presentation readability, the source register was compacted
by PDF page before review, then the same inputs were re-prepared through the
ordinary service. The original automatic package remains in superseded history.
Do not portray this formatting re-preparation as another independent quotation.

The fresh-fixture command and exact four PDF inputs are documented in
[Lifecycle workflow](lifecycle-work.md#receive-an-rfq-before-preparing-the-quote).
Creating the fixture alone does not create a quote or prove mailbox capture.

## If asked about the rest of the order

Show the actual files and receipts, with their environment labels:

- Native draft copies: DXF attributes and a BOM cell can be changed through the
  normal agent tools using exact requested literals and original evidence. The
  native inputs must already be registered and authorized. A located layout task
  still requires a drafter to move geometry and an engineer to approve it.
- Full local completion: isolated order
  `6603eeec-37b8-4a33-9956-e1d734e1e43a` is recorded in
  `.data/demo-readiness/local-http-actions-final.json`. Explicit simulated PM,
  engineering and buyer reviews led to 11/11 verified comments, four successful
  outbox jobs and eight actual HTTP effects in the persistent local enterprise
  simulator. The target was `rivet_test_actions_final`; no vendor systems or
  customer mailbox were changed.
- Closeout: actual approved/released files are bundled with a handover index and
  hash manifest. The native unsent handover path is implemented and fake-Graph
  tested, including rejection after physical source changes. It has not been
  shown live in Outlook yet.
- Routine status: narrowly classified questions can receive a deterministic
  draft from exact, reviewed, unexpired facts. Optional sending exists only in a
  default-off local simulator with an exact recipient allowlist. No live customer
  sending or Graph sending permission is present.

The isolated completion uses real files and service calls with explicit fictional
human decisions. It does not show a model independently completing an order or
granting itself approval authority. The normal CAD tool regression uses a scripted
provider; live model reliability still requires representative customer trials.

## Rehearse twice

1. Run the preflight and verify both Outlook and the Rivet pane are signed in.
2. Start a timer when clicking **Check inbox now**, after the new email is visible
   in Outlook. Record the time to a saved native draft, not just an AI completion.
3. Verify the exact thread, intended recipient, cited source text, correct released
   PDF and absence of a duplicate draft.
4. Edit one sentence, send only the fictional reply to the founder's own sender
   account, then verify the recorded sent/edited outcome. Do not edit a source to
   force a desired answer.
5. Repeat in a second new thread with another unique suffix. Record the second
   loop as the backup and keep the first as a real completed example.

The live email for the meeting should be a third new thread. It can be prepared
as an unsent message on the phone with the PDF already attached, ready to send.
Do not send it early: the product will correctly process it before the meeting.

## If the live trigger is slow

- If the email has not reached Outlook, this is mail delivery; do not repeatedly
  trigger the worker or claim Rivet is reading an email it has not received.
- If inbox checks fail, show the actual diagnostic and switch to the saved
  completed example or backup clip. Do not replace the integration with a hidden
  precomputed response.
- If an existing thread already has a human draft, Rivet preserves it. Use the
  fresh meeting thread; do not delete somebody's draft to make the demo work.
- If evidence changes or a source is unavailable, explain that generation waits
  for a current source check. Do not bypass the check or present stale work as new.
- If the sent outcome is not updated immediately, show the sent message in
  Outlook and return to the summary after the next successful check. Do not call
  a pending outcome “learned.”

## Evidence from this build

Three manually sent fictional rehearsal replies were observed in Outlook and
then recorded by the worker as edited. The final rehearsal created its receipt
at `2026-10-15T07:46:03.423774Z` and its native draft at
`07:46:24.199619Z`: **20.8 seconds from capture to native draft**. This excludes
email delivery and the mailbox polling interval. The PM sent at `07:50:57Z`;
the worker recorded the outcome as `sent`, `edited`, with order-scoped example
reuse `approved`. This is retrieval feedback, not training model weights.

The final reply has the released `Drawing-E-10-Rev-B.pdf` attached. Its SharePoint
citation opens the retrieved specification on page 1 and highlights
`Required trip unit: TU-3000-DEMO`, rather than a disclaimer. The full-size
original can also open through the authenticated evidence viewer. Existing
human drafts in the older thread were preserved.

Proof captures are in `.data/demo-rehearsal/final-*.png`. The captioned fallback
is `output/video/Rivet — Outlook Workflow Backup.mp4` (71 seconds, 1920×1080).
It is an **edited screenshot walkthrough of actual product states**, labelled
as a fictional scenario. It has no voiceover and is not a continuous screen
recording. Its source screenshots, SHA-256 hashes, captions and media metadata
are retained in `output/video/walkthrough-evidence.json`.

A fresh, unsent meeting email with the marked-up PDF is saved in the connected
Berkeley Gmail account, addressed to the Microsoft test mailbox:
`DEMO-OUTLOOK-001 | Rev C comments attached | Live demo`. Do not send the
incomplete older browser draft in the separate Gmail account. A new thread
is required because existing human drafts are deliberately preserved.

For the expanded eleven-comment local drawing workflow, use the newest order in
`.data/demo-readiness/lifecycle-replay.json`. That demonstration produces actual
DXF and Excel copies and parsed PDF annotations. It is distinct from the live
Outlook/SharePoint rehearsal. Commercial decisions, engineering approval and the
layout task remain explicit until supported by their own evidence.

The expanded commercial handoff has also been verified live. In the founder
workspace, open `https://localhost:5179/#order/201cbe91-1b4e-4166-adf0-591f7edc6397`.
The eleven-comment returned PDF arrived through Outlook without duplicating its
already-owned annotations. Rivet held its customer proposal until the four
explicit fictional demo reviews were recorded, then automatically created one
unsent reply with `response-letter.pdf` and `change-order.pdf`. The native thread
is `DEMO-LIFECYCLE-1015-084345 | Rev C returned | Final commercial rehearsal`.
Both attachments and the actual category were inspected in Outlook. The dashboard
shows **Ready in Outlook**. Leave this draft unsent as an inspection example.
These reviews do not resolve the outstanding layout task or release production.

An inspectable document pack is saved at
`output/demo/Rivet-Demo-Documents.zip`: the actual source PDFs, editable drawing
and BOM, prepared drawing copies and output PDFs. Every file is fictional and
commercial pricing is from the sealed local test pricebook.

SAP sandbox connectivity is now verified: the real GET of sales order `1`
succeeded on October 15 at `08:04:39Z` in 1.015 seconds. Credential-free proof
is `.data/sap-sandbox/verification.json`. Its separate `SAP-SANDBOX-1` order
shows generic SAP sample data. The connector is read-only; those sample records
are not evidence for this breaker order. Production SAP writes, SharePoint
writes, vault publication, Teams delivery and routine automatic customer sending
are not established by this video. See `docs/demo-readiness-matrix.md` for the
full goal coverage and remaining work.

The expanded fallback is `output/video/expanded/Rivet — Outlook Workflow Backup.mp4` (95 seconds, 1920×1080, captioned, no soundtrack). It preserves the verified Outlook/SharePoint loop and adds clearly separated captures of the eleven-comment local order, native CAD draft, actual commercial-PDF handoff and generic SAP sandbox read. It is an edited screenshot walkthrough, not continuous footage. The 71-second inbox-only version remains available unchanged.

### Recommended complete backup

Use `output/video/complete/Rivet — Complete Demo Backup.mp4` for the current
quote-to-closeout demonstration: 2:35, 1080p, captioned, silent. Its 19 scenes
include the actual quotation review/PDF/native reply, all three live PO
comparison replies, the separate eleven-comment/CAD scenario, closeout downloads,
eight local HTTP execution receipts, and the separate read-only SAP sandbox
connection. It is an edited walkthrough of real captures, not one continuous
recording or one production order. Scope and engineering decisions remain human.
Its evidence manifest, subtitles, contact sheet and verification report are in
the same folder. The earlier 71- and 95-second videos are preserved.

`output/demo/Rivet-Complete-Demo-Documents.zip` contains 23 fictional source and
output files plus a hash manifest and guide. It separates the quotation, drawing
review and isolated completion scenarios. It contains no original emails,
credentials or databases. Opening the supplied PO fixtures does not establish
customer acceptance or production approval.
