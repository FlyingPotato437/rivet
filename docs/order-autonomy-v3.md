# Rivet order autonomy: implementation and live setup

Verification: **937 backend tests passed, 4 skipped; 135 frontend checks passed; all three builds succeeded.** A final 123-test composition/integration pass also succeeded after connecting per-comment work to Outlook composition. The worker is running the updated code locally.

The daily interface is **Today → Order → Outlook draft**. Orders hold all files,
comments, completed work and decisions. Activity is the audit trail. Settings
contains learning and integration status; legacy quote/bid/catalog routes require
`VITE_ENABLE_LEGACY_ENGINES=true`. Existing data and dependent engines are retained.

| Area | Implemented | Current live limit |
| --- | --- | --- |
| Agent | Default 40 steps, 8,000 output tokens, 64,000 context allowance, 2 million cumulative run tokens; bounded regeneration on incomplete JSON | Configurable policy limits remain; no unbounded runs |
| Context | Real BPE sizing, whole-observation compaction, complete original steps retained | Unknown new model names use o200k BPE; provider-reported usage is authoritative |
| Parallel work | One durable task per comment for returns with 6+ comments, four worker lanes, validated merge in batches of five, live completion counts | A 2 million-token allowance conservatively reserves up to 40 large comment tasks; larger returns need a larger allowance or drawing sections |
| Follow-through | After merging responses, the coordinator can continue supported CAD/lifecycle work; current per-comment drafts also feed Outlook reply composition | Existing typed evidence and approval constraints still apply |
| Scans | Page rendering, located visual markup transcription, uncertainty records, durable per-page checkpoints | Default 40 visual pages/document; deferred pages explicitly reported. Review-markup transcription is not full technical OCR |
| Quality | 35 pinned public originals, real ingestion benchmark, CI gate and reviewed two-page vision smoke check | Overall answer accuracy, handwriting recall and PM edit rate are not yet measured |
| Decisions | Expiring, evidence/version-bound cards reuse existing named-approver logic; signed-in review page; replay protection | Native Outlook Action.Http needs provider registration, Entra configuration and public HTTPS. Teams currently opens authenticated review; a native bot invoke is not implemented |
| Internal updates | Durable Teams workflow outbox, decision notices and a morning brief in each configured local timezone | No Teams endpoint configured. Provider acceptance is distinct from confirmed rendering or phone delivery |
| Triggers | Microsoft subscription registration/renewal and authenticated notification wake-up for mailbox and configured SharePoint drives | Public callback and subscriptions not registered. Checkpointed delta polling remains authoritative and recovers missed events |
| Waiting | Three-business-day timers after a confirmed send; optional unsent follow-up in the existing Outlook thread; cancels when a customer replies | Drafting off by default. Extracting arbitrary promised dates and supplier chasing are not implemented |
| SharePoint writes | Approved immutable artifacts, explicit target allowlist, Graph upload session with conflict=fail, credential isolation, receipts | Selected-site write consent and destination not configured. Uncertain upload outcomes use deterministic-path read-back and hash checks; no blind re-upload |
| SAP / vault writes | Explicit manufacturer bridge protocol with idempotency key, read-before-write receipt reconciliation and exact artifact/order fingerprint checks | No manufacturer bridge installed or authorized. This is not native SAP write integration, and the public SAP sandbox remains read-only |
| Learning | Existing private reviewed/sent-example retrieval and versioned example ranker remain in use | No 12-month historical mailbox importer, automatic base-model fine-tuning, cross-tenant document pooling or earned email-send permission is implemented |

## Worker and database

Migrations 0022–0027 add durable comment tasks, approval cards, Microsoft
subscriptions, waiting timers, publication receipts and internal notification
outbox. API changes reload locally; the worker must restart after code updates.
Four long-work lanes do not occupy the mail capture/reply lane.

Every partial comment result is retained. Failed/exhausted children wake the
coordinator; an incomplete return cannot be labelled complete. Stale generations
cannot stage against changed evidence. Retrying legacy runs adopts current
configured limits while preserving historical usage.

## Required live setup

1. Choose the authenticated hosted API/workspace and publicly reachable HTTPS
   callback. Configure `RIVET_PUBLIC_WORKSPACE_URL` and
   `RIVET_NOTIFICATION_BASE_URL` on that host. Do not expose a development server.
2. Register mailbox notifications through `POST /api/mailboxes/{id}/notifications`.
   For an existing, exact SharePoint binding, use
   `POST /api/orders/{id}/source-notifications` with its connection and binding key.
3. Register Outlook actionable-message originator and Entra API scope. Map each
   allowed Microsoft tenant/object ID to its Rivet user and organization. The
   callback verifies signature, issuer, audience, delegated scope and mapped
   identity, then checks the company's named gate approver. No actor from the
   card body is trusted.
4. Configure a Teams workflow endpoint in the internal channel config. Its host
   is allowlisted and its secret URL stays in an environment variable. Deploying
   a Teams bot with native in-message approvals is a separate step.
5. Publication targets with both `enabled` and `auto_publish` are queued after all required gates approve the exact package. Synthetic orders additionally require `allow_synthetic` on an explicit test target. Configure the exact SharePoint output folder and grant selected-site write
   consent before enabling its write target. SAP/vault require the actual system
   names, approved test environment and installed bridge contract.
6. Pilot routine sending only after defining action/customer policy, earning
   criteria and explicit mailbox-scoped Mail.Send consent. Current Rivet still
   saves unsent customer drafts; money, scope and engineering stay gated.

No new Mail.Send permission or broader Microsoft consent was added. No customer
email was sent in this update. Morning brief and approval card generation are
implemented, but no unconfigured Teams/Outlook delivery is represented as live.

## Relevant endpoints

- `GET /api/orders/{id}/timeline`, `GET /api/order-activity`
- `GET /api/daily-brief`, `GET /api/automation-readiness`
- `POST /api/orders/{id}/approval-cards`
- `POST /api/approval-cards/{id}/decision` (signed-in workspace)
- `POST /api/actions/outlook/{id}` (registered Entra-authenticated callback)
- `POST /api/orders/{id}/publish` (exact approved artifact and enabled target)

Microsoft references: [actionable-message security](https://learn.microsoft.com/outlook/actionable-messages/security-requirements),
[Entra setup](https://learn.microsoft.com/en-us/outlook/actionable-messages/enable-entra-token-for-actionable-messages),
[Graph upload sessions](https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession?view=graph-rest-1.0).
