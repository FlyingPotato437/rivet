# Rivet: customer approvals and customer history

Updated October 13, 2026. This is the current product direction; the status labels below distinguish implemented local features from pilot work.

## Positioning

Rivet handles the customer drawing-approval loop inside Outlook, carrying each customer's reviewed preferences across jobs and engineers.

The initial user is a project manager or applications engineer at a custom switchgear/switchboard manufacturer. A returned drawing or customer question triggers evidence retrieval and draft preparation. The PM reviews the reply in Outlook; the workspace holds the original markups, revision history and detailed evidence. The first pilot should remain one manufacturer's returned-drawing loop, not a general procurement platform.

## Competitive context

Applied Kinetics is a close competitor. Its [YC profile](https://www.ycombinator.com/companies/applied-kinetics) lists Fall 2026 and equipment coordination after the purchase order. Its [site](https://appliedkinetics.ai/) describes supplier follow-through and engineering-change coordination across enterprise tools, with updates in Teams or Slack. Both sources were checked October 13, 2026. Its public example is illustrative.

Our chosen emphasis is the manufacturer's customer approval desk: returned drawings, traceable answers, customer history and potential customer scope changes. This is a focus distinction, not proof of an exclusive market or durable moat. Their public positioning does not establish that they lack customer memory, drawing capabilities or paying users. They could expand into the same workflow. Avoid claims about undisclosed traction or capabilities, and do not say they copied Rivet.

The next proof is a customer demonstrating that retaining reviewed history reduces repeated clarification and response effort across real projects.

## Implemented local customer-history release

- A customer brief inside the order workspace and Outlook reply pane.
- Kickoff preferences from explicitly linked customers, with original-source links and applicability conditions.
- A pre-send topic/text check of selected documents. It finds related passages or reports that none were found. It does not certify design compliance, interpret every visual detail or decide whether a requirement is satisfied.
- Search over original extracted passages from that customer's linked orders, retaining file, revision and page/line details.
- A form to propose a past request from an imported source. A separate administrator review is required before reuse. An approved historical reference can be proposed as an ongoing preference through a new version.
- Existing automatic candidate preparation from reviewed comments and recorded responses. Bulk history import is not a completed one-click workflow: import documents through the normal intake, confirm the customer, then review prepared or manually proposed candidates.
- Approved notes with changed evidence, expiry or declared conflicts are withheld. Tenants and distinct customers remain separate. This is retrieval of reviewed context, not model-weight training or a measured accuracy claim.

A repeatable fictional fixture is available through `scripts/seed_customer_memory_demo.py --organization-id YOUR_UUID`. It creates two source-backed sample orders and two explicitly fictional approvals, leaves other orders untouched, and preserves an existing fixture on repeat runs. Run with `PYTHONPATH=.` from the repository root. The fixture uses plain-text correspondence and a drawing-notes extract; it is not evidence of accuracy on engineering drawings.

## Roadmap and acceptance gates

| Priority | Work | Completion evidence |
|---|---|---|
| 0 | Hosted private API, worker, database and durable documents; Outlook session continuity | Authenticated end-to-end replay after restart; reconnect without losing work; backup restoration |
| 0 | Agent/citation reliability and mailbox coverage | Each answer has the correct original revision/location; failed and quarantined mail is visible; reconciliation catches missed notifications; no unapproved sends |
| 1 | Real drawing corpus and customer-history onboarding | Completed order supplied with permission; exact annotation/source matching; baseline versus assisted review time; missed-item and incorrect-memory review |
| 1 | Customer memory available in daily work | Local release now available; validate with two engineers using the same customer's next job and collect corrections |
| 2 | Customer change-order preparation | Explicit scope evidence; reviewed quantities and price inputs; a customer-facing draft; commercial approval before commitment. Existing scope flags and quote tools are components, not an integrated finished feature |
| 3 | Lightweight internal handoffs | Approved summary with source links to the chosen internal channel; no supplier chasing or automated ERP PO changes in this roadmap |

## Investor demo

1. Show the real Microsoft 365 mailbox and Reply with Rivet pane.
2. Show the retrieved specification from the real selected SharePoint site, alongside clearly labeled local ERP/vault adapters.
3. Inspect a conflict and open its original evidence. Create an unsent Outlook reply with allowed attachments.
4. Switch to the fictional returning-customer example. Open Customer brief, inspect the kickoff requests, run Before sending and follow an original source.
5. Search past orders for a customer request. Explain what requires approval before that history can guide a new job.

Do not imply a customer deployment, full SAP/vault connection, measured time saving, guaranteed inbox completeness or universal PDF understanding. Real Outlook and SharePoint integration is local-demo evidence; confidential production readiness still needs the gates above.

## Pilot measures

Track time to prepare and review each reply, missed extracted comments, incorrect source/revision links, incorrect customer matches, edits required, repeated questions caught, and mailbox reconciliation lag. Report denominators and the source set. Revenue recovery is a later outcome to measure after a customer confirms billability; a scope-language flag alone is not recovered revenue.
