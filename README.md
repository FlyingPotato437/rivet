# Rivet

Rivet builds a comment and change record for custom equipment orders. The PM reviews extracted comments, links the correct drawing location, records the team's response, and preserves who requested or approved each change. Customer and production teams can receive the same reviewed record.

The default workspace starts with a **Work queue** for evidence-linked suggestions, assignments, activity and readiness. **Comment log**, **Changes & history**, **Documents**, and **Approved record** preserve the detailed record. It supports any custom equipment category; the retained engineering checker remains scoped to its electrical attributes and is outside the primary flow. No engineering fix suggestions or automatic sending are part of this version.

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

Deploy the repository root (`./`) to Vercel as **Vite**, with `VITE_API_URL` set to the HTTPS origin
of your hosted API. Run FastAPI, the durable worker, and PostgreSQL using
`compose.production.yml` on a server. The API and worker share a persistent
document volume; Caddy provides HTTPS. The image includes Chromium for PDF
exports. Production requires a production Clerk instance, exact allowed
frontend origins, and Organizations; development demo access stays disabled.

See [deployment instructions](docs/deployment.md) for the environment template,
Vercel settings, migration/startup commands, backups, and verification. The code
and configuration are included; server provisioning, DNS, credentials, and
production sign-in/email checks remain deployment steps.

## The order workflow

Open `/#orders` for the order feed. `/#decisions` filters orders with comments needing review. Each order opens its **Work queue**; the underlying comments, changes, documents and approved record remain one tab away.

1. Create an order in any equipment category. Add marked-up PDFs, drawings, or saved EML emails with attachments. Specify the revision you know; unknown values remain unconfirmed.
2. Review the queue's proposed source links and clarification drafts. Exact, unambiguous references can be linked internally with an explanation and guarded undo; unclear cases remain for review. Native PDF annotations retain their author, source date, page, and region. The source page and the referenced drawing are separate fields. Scanned areas require transcription.
3. Correct the extracted text, link the actual drawing location, record a response and its author, and set open/responded/closed explicitly. Each save retains the original text, actor, reason, and before/after values.
4. Compare two source documents, then record confirmed changes with before/after values, linked comments, requester, approver, and date. Text comparison does not inspect drawing geometry.
5. Open the Rivet sidebar with **Rivet** or **Cmd/Ctrl+J** to ask questions while reviewing the order. Answers cite supplied sources and records. Conversation and Activity stay available as you switch order tabs. Source links open the document in the main workspace; **Back to record** returns to your work. Activity shows recorded actions, and **Check record** refreshes coordination. Questions themselves never modify the record or send messages.
6. Once each comment is reviewed, approve a frozen record. Create a read-only local link or export Excel/PDF. Future edits remain in the working version.
7. Add customer, manufacturer, and production recipients. Relevant updates create notice drafts. Download a draft or explicitly review and send it through Resend after configuring a verified sender.
8. Set role owners and a customer due date. Review internal assignments and readiness checks, and optionally enable weekly status drafts. Completing a task does not approve engineering work; accepting a draft does not send it.

The prior quote tools remain under **Quote tools**. The engineering engine, obligation ledger, release checks, and synthetic Larkspur fixtures are retained for later stages; see [the earlier engine architecture](docs/order-architecture.md). They are not the current PM workflow.

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
- `backend/records/assistant.py`: read-only questions with validated source/comment/change references and no mutation tools.
- `backend/records/api.py`: application endpoints and saved-email attachment intake.
- `backend/records/exports.py`: Excel comment/change logs and PDF response matrices.
- `backend/ingestion/parser.py`: durable parsing of immutable originals, source areas, annotations and email metadata.
- `apps/web/src/RecordFeed.tsx`, `RecordWorkspace.tsx`, and `OrderEvidence.tsx`: the current workspace and PDF source review.
- `apps/web/src/RecordCoordination.tsx`: suggestions, assignments, activity, source navigation and coordination settings.

For a repeatable connected-workflow walkthrough, use [the fictional connected review](examples/connected-review/README.md). It uses the normal ingestion pipeline, including ambiguous and missing references, with a separate revision to upload later.

## Model connection and integration boundary

The existing server-side connection uses `OPENAI_API_KEY` and `OPENAI_MODEL` in `.env`. Restart the API and worker after changing them. Keys never belong in frontend code. Comment extraction, manual review, source inspection, text comparisons, and exports work without a model key. Questions in the Rivet sidebar use the configured model and send bounded order context with `store: false`. No additional key is required when this connection is already configured.

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
