# Project-led Outlook workflow

Rivet groups confirmed order emails into projects, prepares work in the background, and places unsent replies in Outlook. The dashboard shows the result and communication history. Opening the dashboard does not start a model run.

## Daily use

1. A relevant email arrives in a monitored Outlook folder. The durable mailbox worker captures the original and attachments, identifies the job and revision, and creates or matches the order. Unrelated mail stays out of the project view. An ambiguous match asks one short question.
2. Rivet reads the documents, checks configured connected sources, retrieves permitted customer history, and prepares a cited response. Commercial or engineering uncertainty remains explicit; it is not silently resolved by the model.
3. The response appears as an unsent reply in Outlook with eligible released attachments. An existing human draft is preserved. Evidence changes and newer thread messages are checked before placement.
4. The PM edits and sends through Outlook. Rivet confirms the send from Microsoft, records the communication, and watches for the next customer message. Removing a draft is not proof that it was sent.
5. A later customer request resumes preparation automatically. Each email conversation has its own state; sending in one conversation cannot conceal unanswered mail in another.

## Dashboard

- **Projects** is the workspace home, replacing the separate task and order entry points. Each row shows progress and the last received or sent communication.
- Project states: **Rivet is working**, **Ready in Outlook**, **Response prepared**, **Waiting for customer**, **Needs your decision**, **Watching for updates**, and **Up to date**.
- A project opens to its current result, prepared reply, and email history. A conversation selector separates independent threads on the same job. Files, cited comments, exports and detailed tools remain one step away.
- Project names are editable. The canonical job number and evidence identity remain unchanged. Renames use optimistic version checks and preserve the prior name, actor and timestamp.
- Import-only sample projects show their available evidence; they do not claim that Outlook is connected or that an email was sent.

## Implementation

`backend/project_flow/` provides tenant-scoped read projections and versioned naming. Migration `0021_project_flow` adds display metadata, confirmed thread links, and Sent Items receipts. The communication worker runs independently of document parsing, model preparation and mailbox capture.

Sent tracking reads only confirmed project conversations in the authorized mailbox. It validates provider pagination, ignores drafts and internal forwards, and keeps confirmed history when synchronization fails. A thread attached to two jobs pauses automatic sent filing instead of merging them. Confirmed self-addressed sent copies are retained without creating order-match questions or another reply loop.

The project view currently shows at most 200 projects, 300 incoming receipts per project and 100 recent sent records. Partial history is indicated; full mail remains accessible in Outlook. Sent excerpts use Microsoft's body preview and may be shortened. A delayed provider sync is shown without inventing a send.

The existing learning pipeline records sent responses and permitted corrections. Quoted original messages are excluded, including Outlook's flattened plain-text quote format. Reuse follows the team's policy and evidence checks. This is private, evidence-linked example learning and an evaluated example ranker; it is not automatic foundation-model retraining.

## Verified locally — October 15, 2026

Three authorized fictional messages were sent only within `SrikanthSamy@rivetusa.onmicrosoft.com`: a returned PDF, Rivet's prepared response, and a new customer follow-up on the same job.

- The returned PDF was automatically captured and matched to `DEMO-OUTLOOK-001`.
- Rivet checked the live SharePoint specification and local demo ERP/vault records, identified conflicting trip units and remote-indication scope, and left production release unconfirmed.
- Outlook received a native unsent reply with released `Drawing-E-10-Rev-B.pdf`, rather than the draft Rev C drawing.
- Sending through Outlook produced a confirmed Sent entry and **Waiting for customer** for that conversation.
- The follow-up was automatically filed to the same project and received a fresh native reply with the released drawing.
- Renaming the project in the UI persisted and updated the sidebar.

The own-mailbox follow-up uses a new email conversation because a self-addressed message in the same conversation cannot reliably distinguish the PM from an external customer. Same-conversation external-customer resumption, multiple conversations, deleted drafts, tenant isolation, stale versions, invalid provider pages and outgoing-copy suppression are covered by automated tests.

SharePoint and Outlook in this rehearsal are live. The breaker ERP and drawing vault are labelled local test adapters. The separate SAP sandbox connector does not turn those breaker records into a production SAP integration. Hosted pilot deployment and manufacturer-specific source access remain separate setup work.

## File-only projects and review polish

A project started from uploaded documents can save its prepared response as a new, unaddressed native Outlook draft. **Save to Outlook Drafts** is an explicit action; viewing the project creates nothing. One enabled mailbox is selected automatically; multiple mailboxes require a choice. The PM chooses the recipient and attachments in Outlook. Existing captured customer emails continue to use their actual reply thread and released-attachment policy.

The save action preserves the native draft and any human edits when reopened. Unknown provider creation outcomes require attention rather than a blind retry that could make duplicates. Mail.ReadWrite is sufficient; no sending permission is added. File-only saves are excluded from the background email-reply retry queue. The project and reply card use the same freshness check, including when context returns to an earlier fingerprint and the worker reuses its saved response.

Project files and outputs are linked directly below the project title. Response memory and its review history live in workspace settings, with a link from the project list. Comment logs use unique, stable `C-001` display IDs while preserving original annotation numbers and revision references. Recognized attachment cover notes stay with the original email and source history, outside the actionable comment log. Reviewed or edited comments are never hidden by that classification. Display IDs do not invalidate model evidence fingerprints.

## Simple record browsing

The routine review path is now **Project → Comments or Files**. The Comments view opens the latest return, with one list and the selected comment beside it. Reading a comment, its recorded response and original PDF stays on this page; it does not open an agent workspace. The full prepared response remains on the project page and in Outlook. A per-comment response is shown only when one was actually recorded.

Revision selection is behind **Filter**, exports are under **Downloads**, and record editing, assignments and other advanced controls are under **More tools**. File citations open a dedicated file preview in the same two-view browser. Both views return directly to the project. Existing advanced URLs remain available through `view=advanced`.

An optional inline response-note editor preserves the source fields, workflow and review status. It saves against the version at which editing began, retains text after a conflicting save, and warns before leaving the editor through the record browser. Saving a note does not send mail, mark a review complete or approve an engineering change. The normal path remains reviewing Rivet's completed work and sending through Outlook.
