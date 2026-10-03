# Rivet record workflow

The October validation call changes the default workflow to a PM-owned comment and change record for custom and first-time equipment orders. It does not depend on the electrical attribute registry or the Larkspur fixture. The previous engineering engine remains available in code/API for later stages; it is no longer the default dashboard.

## Persistence and authority

- `OrderRecord` owns an independent version and durable JSON record: comments, changes, subscribers, notice drafts, approvals, and before/after edit events. Originals remain immutable `Document` blobs and `Span` citations. Migration `0004` adds the records without replacing existing orders.
- A durable parse worker imports numbered comments and PDF annotation objects. Unknown author/date/location remain explicitly unconfirmed. An unstructured email becomes one reviewable row, not invented separate issues. No AI-generated fix, response, or compliance determination is applied.
- `original_text` remains immutable when a PM corrects `text`. Every write requires a reason, optimistic version, and idempotency key. In authenticated workspaces the server records the verified user ID rather than trusting a submitted actor name. Local development keeps named actors. Target drawing citations and email links must belong to the same order.
- Source-page and target-location are separate fields. All imported rows need PM review. Confidence is qualitative (`Needs review` / `PM reviewed`), never a fabricated model probability.
- Open/responded/closed are explicit communication statuses. Responded/closed require a response and its author; closed also requires review. A new source never silently closes comments or discards older rounds.
- Changes are human-confirmed records with from/to values, revision labels, linked comments, requester, and recorded approval. Deterministic text comparison provides source-linked candidates; geometry and scanned content are not interpreted.

## Approvals and shared records

A record approval freezes the comments, responses, changes, and source references, even when some comments remain open. It certifies a reviewed record, not equipment compliance or production release. Later edits remain in the working version. Read-only links contain a random token; only its SHA-256 hash is persisted. Each expires after 30 days and can be revoked. The projection excludes subscribers, internal edit events, and unrelated inbox sources. Referenced original documents are included in full, with token-scoped download checks.

The running development app retains its localhost boundary. Clerk authenticates workspace requests; each database query and write uses the verified active organization. Public record links authorize only their frozen snapshot and included source documents. A link on localhost still works only on this computer. Hosting and production Clerk/domain setup remain separate. See [accounts and email setup](accounts-email-setup.md).

## Email and notice boundary

`POST /api/orders/{id}/record/email` accepts a saved `.eml`, preserves its headers/body and imports supported attachments through the existing document queue. Byte-identical messages are deduplicated within an order. Unsupported attachments produce an explicit error. The same intake service handles Resend deliveries. A configured receiving domain enables random per-order addresses. Verified webhook events queue durable work; the worker rechecks the envelope recipient, downloads the original MIME email, and imports supported attachments. Unsupported attachments in forwarded messages remain in the immutable original and are listed in metadata.

Recipients are grouped as Manufacturer, Customer, or Production. Comment changes, new intake, recorded changes, and approvals append a durable notice draft with a recipient snapshot. `.eml` drafts have `X-Unsent: 1` and can be reviewed and sent from an email app. After a verified sender is configured, a user can explicitly review and send a notice. A durable delivery record freezes recipients and content, and per-recipient Resend idempotency keys prevent duplicate sends inside the provider retry window. Provider acceptance is recorded as sent; inbox delivery is not inferred. No notice is sent automatically.

## Rivet questions

`POST /api/orders/{id}/record/ask` answers from a bounded snapshot of this order. The model receives read-only context, a strict answer schema, and no mutation tools. References outside the supplied source/comment/change sets are rejected. The UI shows the record version used for the answer and opens its citations. The previous compact question-bar design and Rivet mark are retained.

`OPENAI_API_KEY` and `OPENAI_MODEL` remain server-only. Without them the record workflow remains usable and questions return a clear connection error. The assistant does not draft fixes, close comments, approve records, or send notices.

## Interface and validation

`RecordFeed` shows comment totals, open work, and review counts. `RecordWorkspace` has Comment log, Changes & history, Documents, and Approved record. `SharedRecord` is a separate read-only projection. The homepage preview is explicitly fictional and does not modify customer records.

Run `uv run pytest tests/test_records.py -q` against the dedicated `rivet_test` database. Tests cover a non-electrical custom order, persistence across rounds, original text and edits, stale writes, source isolation, status gates, immutable approval links, revocation, EML attachments/deduplication, notice drafts, Excel exports, and document comparisons. Existing engineering tests remain regression coverage for retained functionality.

Public-source regression coverage and known extraction limits are recorded in [public-document-validation.md](public-document-validation.md). Uploads reject PDFs exceeding 150 pages or files exceeding 20 MB before persisting an unusable document.

## Explicit record relationships

`backend/records/connections.py` derives a deterministic relationship index from persisted IDs: comment → source document/annotation, confirmed drawing area, imported email, change, edit event, and the latest approval snapshot. It never guesses associations from similar text. The original markup location and the referenced drawing remain separate; the PM must explicitly link them.

The UI uses this index for source links, reciprocal comment/change navigation, and focused history. The assistant receives the same bounded relationships with the record context. An approval comparison checks saved content against the frozen snapshot; a later response, change, or new document is surfaced as an update after record approval. This does not imply production has begun, a design has been approved, or a customer has been informed. Recipient setup is visible, while notice drafts remain unsent until a person explicitly sends them.

Review next and Save & next advance through pending reviews without checking the review box for the user. PDF zoom and publisher page mapping make verification easier. The real-data regression walks all 13 public annotations through review, recorded response, a linked test change, record approval, Excel/PDF exports, and a frozen shared snapshot; a subsequent edit changes the working record only.
