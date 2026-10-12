# Rivet

Rivet builds a comment and change record for custom equipment orders. The PM reviews extracted comments, links the correct drawing location, records the team's response, and preserves who requested or approved each change. Customer and production teams can receive the same reviewed record.

The **Inbox** begins with a returned PDF or forwarded/saved email. Rivet matches explicit job and revision identifiers, asks for confirmation when uncertain, and opens a spreadsheet-style **Comment log** with evidence-linked revision checks. Click a comment to review the source and response together. Export a response letter, Excel log, drafting list, or email brief. Existing durable agents investigate references and prepare cited internal changes for human review. Engineering and commercial decisions remain with people.

The public production website is **marketing-only**. `/pilot` embeds the founder’s Cal.com calendar. Private app code and authentication are excluded from the public build, and production API workspace access is closed by default. See [public pilot release](docs/public-pilot-release.md) and [inbox-first adoption](docs/inbox-first-adoption.md).

[Current comment-record architecture and boundaries](docs/comment-record-architecture.md).

## Run locally

The app runs locally with Clerk sign-in and organization-scoped workspaces when keys are configured. Without Clerk, explicit local development remains available behind the loopback boundary. Its PostgreSQL cluster is separate from your other projects. See [accounts and email setup](docs/accounts-email-setup.md).

```sh
cd /Users/srikanthsamy1/Desktop/CodeProjects/rivet
uv sync
npm ci
# First run on another machine, with PostgreSQL binaries installed:
mkdir -p .data
initdb -D .data/postgres --auth=trust --encoding=UTF8 --locale=C
pg_ctl -D .data/postgres -l .data/postgres.log -o '-p 55432 -h 127.0.0.1' start
createdb -h 127.0.0.1 -p 55432 rivet
uv run alembic upgrade head
RIVET_AUTH_MODE=local uv run python -m backend.seed
uv run python scripts/dev.py
```

After pulling updates, run `uv run alembic upgrade head` before starting the API and worker.

On this machine the database is already initialized. After a reboot, start that cluster with the `pg_ctl` command, then run `uv run python scripts/dev.py`.

Open **http://127.0.0.1:5178**. API documentation is at **http://127.0.0.1:8787/docs**. The launcher starts the web UI, FastAPI, and a separate durable worker; Ctrl-C stops them. Database shutdown is explicit: `pg_ctl -D .data/postgres stop`.

## Deploy

Deploy the repository root (`./`) to Vercel as **Vite** with `npm run build` and Node 24. This builds the public product site and embedded pilot calendar without an API dependency or sign-in entry point.

For a future private pilot deployment, FastAPI, the durable worker, PostgreSQL, persistent document storage, and production Clerk must be configured separately. Production APIs remain closed unless deliberately enabled. The current public frontend cannot open the app by changing a URL or environment variable.

See [public release boundaries](docs/public-pilot-release.md) and [infrastructure deployment instructions](docs/deployment.md).

## The order workflow

In local development, open `/#decisions` for the **Inbox** or `/#orders` for the order directory. Each order starts with its comment log, actual revision flags, and export actions. A comment opens its source and editable response in one step.

1. Drop in a returned PDF, saved EML email, or text comment log. Explicit job/revision identifiers match an existing order or create a new one. Uncertain or conflicting identifiers ask for confirmation. With verified Resend receiving configured, a team forwarding address feeds the same intake; no order setup is required first.
2. Review the queue's proposed source links and clarification drafts. Exact, unambiguous references can be linked internally with an explanation and guarded undo; unclear cases remain for review. Native PDF annotations retain their author, source date, page, and region. The source page and the referenced drawing are separate fields. Scanned areas require transcription.
3. Correct the extracted text, link the actual drawing location, record a response and its author, and set open/responded/closed explicitly. Each save retains the original text, actor, reason, and before/after values.
4. Compare two source documents, then record confirmed changes with before/after values, linked comments, requester, approver, and date. Text comparison does not inspect drawing geometry.
5. Open **Rivet work** or use **Cmd/Ctrl+J**. **Tasks** run a saved investigation, read sources, request specific missing information, and prepare internal changes. Inspect and accept or dismiss those changes; new evidence invalidates old proposals. **Questions** provides cited answers without editing the record. **Activity** shows recorded work. Tasks and private question history persist on the server, while source links keep review in the same workbench.
6. Once each comment is reviewed, approve a frozen record. Create a read-only local link or export Excel/PDF. Future edits remain in the working version.
7. Add customer, manufacturer, and production recipients. Relevant updates create notice drafts. Download a draft or explicitly review and send it through Resend after configuring a verified sender.
8. Set role owners and a customer due date. Review internal assignments and readiness checks, and optionally enable weekly status drafts. Completing a task does not approve engineering work; accepting a draft does not send it.

Earlier quote routes, the engineering engine, obligation ledger, release checks, and synthetic Larkspur fixtures remain in the codebase for later stages; see [the earlier engine architecture](docs/order-architecture.md). They are outside the primary PM navigation.

## Test with real documents

[Public-document test report and limitations](docs/public-document-validation.md).

```sh
uv run --with pypdf python scripts/prepare_public_documents.py
RIVET_PUBLIC_DOCUMENTS=1 uv run pytest tests/test_public_documents.py -q -s
# With the local app and worker running, create two clearly labeled sample orders:
uv run python scripts/seed_public_records.py
```

Preparation verifies original SHA-256 hashes and preserves selected original pages and annotations. The manifest records original page numbers. Neither script resets existing orders or marks comments approved. Tests use the separate `rivet_test` database.

## Implemented architecture

- `backend/records/service.py`: versioned comments, change records, audit events, frozen approvals, notices, and token-scoped shares.
- `backend/records/automation.py`: exact anchor matching, ambiguity handling, typed links, reversible internal actions, role tasks, readiness and scheduled drafts.
- `backend/records/agent_service.py`: durable, bounded model-tool runs and atomic evidence-linked proposals.
- `backend/records/graph.py`: typed relationships projected from actual order records and source anchors.
- `backend/records/inbox.py`: tenant-scoped actionable work across orders.
- `backend/records/assistant.py` and `conversations.py`: read-only cited questions and private persisted conversation history.
- `backend/records/api.py`: application endpoints and saved-email attachment intake.
- `backend/records/intake.py`: tenant-scoped intake, conservative matching, explicit confirmation, and duplicate protection.
- `backend/records/revision_review.py`: record-based repeated/open/after-approval/commercial-language checks with source evidence.
- `backend/records/delivery.py`: reviewed email brief drafts and connection readiness.
- `backend/records/exports.py`: Excel comment logs, drafting lists, response letters, and approval worksheets.
- `backend/ingestion/parser.py`: durable parsing of immutable originals, source areas, annotations and email metadata.
- `apps/web/src/RecordFeed.tsx`, `RecordWorkspace.tsx`, `RecordWorkbench.tsx`, `RecordAgent.tsx`, and `OrderEvidence.tsx`: the inbox, spreadsheet-style comment log, direct source review, task results, and PDF viewer.
- `apps/web/src/RecordCoordination.tsx`: suggestions, assignments, activity, source navigation and coordination settings.

For a repeatable connected-workflow walkthrough, use [the fictional connected review](examples/connected-review/README.md). It uses the normal ingestion pipeline, including ambiguous and missing references, with a separate revision to upload later.

## Model connection and integration boundary

The server-side connection uses `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-6-luna` for questions, and `RIVET_AGENT_MODEL=gpt-6-luna` for durable tasks. Restart the API and worker after changing them. Keys never belong in frontend code. Comment extraction, manual review, source inspection, text comparisons, and exports work without a model key. Without a key, tasks use explicitly labelled deterministic reference rules. Questions and model tasks send bounded order context with `store: false`; no additional key is required.

The main workspace is a drawing review workbench with a collapsible comment queue and one Record/Rivet inspector. Runs, steps, evidence-linked proposals, per-order instructions, and private question history persist in PostgreSQL. The Work inbox collects actionable records across orders. Automatic preparation is opt-in per order, and `RIVET_AGENT_AUTORUN=false` disables the scheduler globally. Agents prepare internal work; people review changes, record engineering responses, approve records, and send notices. Apply migrations through `0008` before starting this version.

The previous engineering workflow remains in the code and API for later stages. The current record workflow adds EML import, editable comments, source-linked text comparisons, frozen approvals, local read-only links, recipient lists, notice drafts, and Excel/PDF logs. Mailbox synchronization beyond Resend, Outlook drafts, scan/vision interpretation, CAD attribute reads/edits, ERP writes, pricing-rule integration, reviewer profiles, and learned checks are extension stages. They are not shown as connected. Clerk sign-in, organization isolation, authenticated downloads, Resend intake, and explicit notice sending are implemented. Hosting, a production Clerk instance, and receiving/sending domain configuration remain deployment steps. See [accounts and email setup](docs/accounts-email-setup.md).

The existing decimal-pricing quote workspace remains available; it has not been represented as an automated engineering change-pricing service. [Retained quote workflow](docs/quote-workflow.md).

## Verify

```sh
createdb -h 127.0.0.1 -p 55432 rivet_test  # once
uv run pytest -q
node --test tests/frontend-api-routing.test.mjs
npm run build
```

Tests rebuild only the dedicated `rivet_test` schema. Order coverage includes precedence, alias decisions, rejected/stale proposals, immutable history, conflicting sources, real PDF markup extraction, fabricated “fixed” claims, downstream evidence, signed waivers, exact-snapshot release, idempotency, exports, and cross-order evidence isolation. Prior quote/coordination tests remain in the suite.

Regenerate API types after changing routes:

```sh
uv run python scripts/schema.py
node node_modules/openapi-typescript/bin/cli.js docs/openapi.json -o apps/web/src/api-schema.d.ts
```

## Data

See [data retention](docs/data-retention.md). Originals and prior snapshots are retained. `.data` contains the local database and private uploads; do not remove it to upgrade the app. Migrations add the order tables without replacing the existing quote schema.

## Try the authenticated public-document demo

With Clerk development keys, Postgres, and the worker running:

```sh
uv run --with pypdf python scripts/prepare_public_documents.py
uv run python -m scripts.setup_demo
```

Restart Rivet and open `http://127.0.0.1:5178/#orders` → **Try the demo**. This signs into a separate Clerk development team with two real public review packages. No signup, password, or inbox code is required. Setup is repeatable and preserves edits; it uses normal document ingestion and never copies existing teams. Use this public practice team only for demo files. Personal accounts and teams remain separate.

The drawing review contains 13 native annotations; the scanned package exposes two areas for manual transcription. Use **Review next**, open the original source (with zoom and publisher page mapping), record your response, and **Save & next**. Connected records link the original annotation, PM-selected drawing, email, change, and history. All reviews remain explicit. The agent answers with citations and cannot change records or send messages.

See [demo and account setup](docs/accounts-email-setup.md), [public document results](docs/public-document-validation.md), and [record relationships](docs/comment-record-architecture.md). Live email forwarding and remote share links still require the documented domain/provider and hosting setup.
