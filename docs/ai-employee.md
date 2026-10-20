# Rivet as an AI employee

Updated October 14, 2026. Rivet does the coordination work on its own and stops only where a person has to commit to something. People work in Outlook; the Rivet workspace is a read-only view of what happened plus a few team settings. The primary navigation is Your work and Orders.

## Principles

1. **Act first, ask rarely.** Anything unambiguous and reversible happens without a question. Ambiguity gets one question, in the place the person already is.
2. **The person's normal action is the decision.** Sending the drafted reply is the approval. Editing it is the correction. Deleting it means it wasn't needed. There are no review checkboxes in the main loop.
3. **Never commit for the company.** Rivet never sends mail to a customer and never approves engineering, scope or price. Those stay human, with the evidence in front of them.
4. **Everything Rivet does is visible and cited.** Each action is recorded with its sources and shows up in the Autopilot view.

## The workflow

| Step | Who | What happens |
|---|---|---|
| 1. Email arrives | Rivet | The connected mailbox is reconciled every 2 minutes; every message gets a durable receipt ([mailbox reliability](mailbox-reliability.md)). |
| 2. Triage | Rivet | Routine mail (invoices, newsletters) is set aside. Order mail is filed to its job by job number or thread; a new job number creates the order. Only a genuinely unclear match asks one Yes/No question. |
| 3. Understand | Rivet | Attachments and markups are parsed; connected sources (SharePoint live; ERP and drawing vault demo services) are re-read; conflicts and customer history are checked. |
| 4. Prepare | Rivet | A cited reply is drafted from the order evidence, with open questions instead of guesses ([automatic preparation](outlook-pilot-workflow.md)). |
| 5. Deliver | Rivet | **New:** the reply is placed as an unsent draft in the customer's own Outlook thread, with cited released drawings attached, and the email gets the "Rivet: reply drafted" category. |
| 6. Decide | Person | The PM opens the draft in Outlook, edits if needed, and clicks Send. Nothing else to click. |
| 7. Learn | Rivet | **New:** Rivet sees the sent reply, records whether it went out as drafted or edited, and (by team policy) reuses it as an example for future replies on the same order. Broader customer or company guidance requires administrator approval. |
| 8. Look | Person, optional | **New:** the Your work page shows what Rivet read, drafted and learned, what's waiting in Outlook, and what needs a person. |

The Outlook add-in remains available for closer inspection of sources and conflicts. When Rivet has placed the draft, **Open draft in Outlook** opens that existing native reply: its thread subject, editable body, and attachments are in Outlook itself. This does not create a second reply or replace the PM's edits. The pane's copy is labelled as the prepared version.

If a person already started a reply in that thread, Rivet preserves it and labels it **Your Outlook draft**. Rivet’s prepared text remains a reference; the system does not claim that it wrote the existing draft or use that manual text as an automatically approved Rivet example.

Background preparation produces a complete unsent draft. It does not simulate typing. The current add-in activates on received messages; writing progressively into an already-open compose form would require separate compose-mode activation and a protected hand-off. Repeated whole-body writes are deliberately avoided because they can overwrite a person's edits. Microsoft documents [opening an existing message](https://learn.microsoft.com/en-us/javascript/api/outlook/office.mailbox?view=outlook-js-1.9#outlook-office-mailbox-displaymessageformasync-member(2)) and [compose-only body insertion](https://learn.microsoft.com/en-us/office/dev/add-ins/outlook/insert-data-in-the-body).

## What a person still does

- Send, edit or delete the reply in Outlook.
- Answer the rare "which job is this?" question.
- Grant Microsoft permissions once, as a tenant administrator.
- Decide anything commercial or engineering, using the evidence Rivet links.

## Straightforward workspace

`Your work` is the home screen (`#work`). Each customer email has one outcome: a ready Outlook reply, work in progress, or an actual open point. Incoming order-match questions are inline Yes/No controls. Sending and editing happen in Outlook; opening the workspace does not create another reply. Unrelated mail is absent, raw imported comments do not become tasks, and completed correspondence is behind Show completed history. Receipt IDs keep different emails with the same subject separate.

The Orders list is searchable without status tabs. An order opens on a single page with the prepared reply, saved supporting work, and recent history. A citation opens the exact source alongside that page, with its recorded location highlighted. Closing the source preserves the order and reply. Comments, files, connected context, customer memory, and exports are available within labelled sections. Record-editing, approvals, sharing, and comparisons remain explicit secondary tools; the ordinary correspondence loop does not require them.

Team autonomy controls and integration setup live in Workspace settings. Older `#autopilot`, `#review`, `#inbox`, and `#decisions` links resolve to the same work screen. Existing comment/source links remain valid. The Open reply in Outlook action resolves the saved native draft through Microsoft on demand. It does not create, replace, or send a draft. Existing manual Outlook drafts are labelled and preserved; the prepared AI wording is shown separately.

The combined work feed never hides a failed preparation or record decision underneath a ready draft. Preparation and acceptance of engineering/commercial commitments remain distinct. The frontend grouping is a presentation layer over durable mailbox receipts, reply drafts, and record work; it does not modify approval state.

## Prepared work without a checklist

The normal loop is one prepared Outlook reply: check it, edit if needed, send. Imported comments stay in the source log but are not individual PM decisions. The workspace groups each order into one result, with supporting work collapsed underneath. An explicit question, human assignment, stale evidence, or failure keeps that order in Needs you; it is never hidden by a saved draft.

Rivet now persists evidence-linked clarification and summary drafts automatically in its work record (`prepared`). They are available immediately after preparation without a separate acceptance step. Existing current draft-only results adopt the same behavior. These saved drafts do not mark comments reviewed, create a response attributed to a person, alter engineering facts, or send correspondence. The Outlook reply is prepared by the existing automatic reply pipeline; internal coordination drafts are supporting work, not a second sending gate.

Proposed drawing-reference changes and human assignments still require a single **Looks good · save** check for the group. The note is optional; the actor and decision are audited. New evidence invalidates both pending changes and prepared drafts. Failed or budget-limited runs remain visibly incomplete even if they saved partial work. The agent reserves processing capacity for writing and finishing rather than spending its full allowance on more reads. Old budget interruptions are shown as incomplete work, not a request for a fictional human answer.

The agent is instructed to do the work it can do itself, combine related clarification questions, and ask the PM only for a blocking choice. It must still expose material evidence conflicts. This reduces unnecessary questions; it is not a guarantee that every real drawing can be interpreted without a person.

## Team settings (Autopilot page)

| Setting | Default | Effect |
|---|---|---|
| Put prepared replies in Outlook | On | Unsent reply in the thread, with released drawings attached |
| Label emails Rivet has handled | On | Adds the "Rivet: reply drafted" category |
| Learn from replies your team sends | On | A sent reply with current source evidence becomes an example for that same order. Off: an administrator approves reuse. Broader customer/company guidance is always reviewed |

Only team administrators change these. There is deliberately no setting that sends mail to customers.

## Implementation

- `backend/autopilot/service.py`: `deliver()` places drafts for ready automatic replies; `sync_sent()` checks each placed draft and records sent, edited or deleted; `activity()` feeds the Autopilot page. Both run in the worker's mailbox loop.
- `backend/autopilot/outlook.py`: Graph `createReply`, body update, file attachments and categories. There is no send call.
- Safety checks before placing a draft: the reply is still unreviewed, the order evidence hasn't changed, no newer customer email arrived in the thread, and the mailbox connection is enabled. Draft versions for the same captured message share a placement guard. The native draft ID is persisted immediately, interrupted work resumes that draft, and existing edits are preserved. If Microsoft does not confirm whether createReply succeeded, Rivet stops for inspection rather than issuing a potentially duplicate creation request.
- Attachments follow the existing rule: only cited, released drawing-vault files are attached automatically. SharePoint and internal ERP records still need the PM's choice. Files over 3 MB are noted for manual attachment.
- Sent detection uses the draft's immutable Graph ID, which Microsoft documents as finding the copy in Sent Items after sending ([immutable IDs](https://learn.microsoft.com/en-us/graph/outlook-immutable-id), checked October 14, 2026). A draft that can't be read is only treated as deleted after 10 minutes, because a just-sent message is briefly unavailable. The text above Outlook's quoted original is compared with Rivet's draft.
- Tables: `autopilot_settings`, `outlook_drafts` (migration 0018). Tests: `tests/test_autopilot.py` (fake mailbox; it fails if any send endpoint is called).

## Microsoft permission

Reading mail needs `Mail.Read`; placing drafts and labels needs **`Application Mail.ReadWrite`** on the connected mailbox. Prefer a mailbox-scoped Exchange Application RBAC assignment. Do not also grant tenant-wide Entra `Mail.ReadWrite`: Entra and Exchange grants are additive, so the broad grant bypasses the RBAC restriction. Existing Entra mail grants must be audited separately. `Mail.Send` is not needed and should not be granted. Without `Mail.ReadWrite`, Rivet records "an administrator must grant Mail.ReadWrite", shows it on the Autopilot page, and retries every 6 hours. Everything else keeps working through the Outlook add-in.

### Local demo verification, October 14

The demo mailbox is readable, source retrieval works, and the add-in preserves existing native replies. The Exchange Application RBAC assignment remains rejected by Microsoft. A temporary legacy Application Access Policy now restricts Graph mail access to the demo mailbox: a live read succeeds there and is denied (403) on a separate empty shared test mailbox. `Mail.ReadWrite` is now consented and live-verified: the same transport used by Autopilot created a native unsent reply in the fictional test thread, wrote its body, attached a file, and verified the attachment bytes and original conversation. Only that temporary test draft was moved to Deleted Items afterward. The exclusion test still returned 403 after consent, and the token contains no `Mail.Send`. The background worker is running with automatic draft placement enabled; the existing demo thread retains its pre-existing draft, so it is deliberately not duplicated. This permission test does not claim a new end-to-end incoming-email run. The local Outlook session survives panel reopen and full Outlook refresh. Diagnostics are in the ignored `.data/microsoft-setup/` directory. Migrate the fallback policy to Exchange Application RBAC once tenant delegation is repaired. Do not present existing manual drafts as proof of background placement.

### Autonomous preparation verification, October 14

A live model run on the existing fictional `DEMO-OUTLOOK-001` documents completed in five model calls and saved three source-linked clarification drafts without a proposal-acceptance action. The drafts distinguished the proposed trip unit from an approved configuration, requested the missing marked drawing, and requested production-release evidence. The comment record and engineering approval state were not changed. The queue retains the separate source conflict as an open point alongside the saved output. This verifies automatic saving and the workspace presentation; it is not a new incoming-email/send test.

## Not yet

- Automatic customer acknowledgements are outside the current no-send policy. There is no setting to enable them.
- The Outlook pane already supports a concise Yes/No match confirmation. Asking that question directly in the native inbox without opening the pane remains future work.
- Internal hand-offs (drafting tasks, buyer notes) to Teams or email.
- Learning from replies the PM wrote without Rivet (Sent Items sync).

## Reliability boundaries

Sending a reply records correspondence approval, not engineering or commercial approval. Reuse means retrieving a saved, source-linked example; it does not update a shared model’s weights. Replies sent against changed evidence require review before reuse. Changes to the order or thread flag an existing Outlook draft for attention rather than replacing the PM’s edits. Preparation currently considers recent actionable messages (seven days); historical records remain context. The mailbox worker must stay running, and only configured folders are monitored.

## Daily workspace

The workspace opens on **Your work**, one feed of prepared email or order outcomes. Imported comment rows and untouched routing bookkeeping stay in the source log instead of becoming individual PM approvals. Genuine engineering/commercial choices, unreadable sources and connection failures remain visible. The primary action opens the existing Outlook draft directly in compose mode; the detailed order opens on its prepared response, with exact source anchors beside it. Opening a view does not start model work or send mail.

Returned files without a captured mailbox thread also produce a response automatically. `backend/reply_assistant/order_packet.py` rotates through enabled orders in the worker, selects the latest explicit returned revision, and queues the existing cited-reply pipeline. The request key includes the evidence fingerprint and focused files, so repeated polls and restarts reuse the result. An explicit order opt-out and `RIVET_AGENT_AUTORUN=false` stop scheduling. Changed source evidence invalidates the displayed response and prepares a replacement; old wording remains retained. No synthetic Outlook message is created for these document-only responses.

Prepared responses show selected-document/comment coverage, citations, remaining uncertainty and AI attribution. They are drafts for review, not technical verification or production approval. Older investigations stay in history after validated current preparation replaces them; they are never marked completed merely to clean up the feed. A failed supporting task remains distinguishable from a finished response. The agent can batch-read original comment anchors with `read_comment_sources`, avoiding repeated searches through unrelated drawing text.
