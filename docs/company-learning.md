# Private company context and the learning loop

For the current visible correction ledger, customer-group replay isolation and
portable future training architecture, see [Learning flywheel](learning-flywheel.md).
The Outlook autopilot now captures sent reply outcomes and wording changes;
older limitations below describing manual-only feedback are superseded by that
integration. Capture/retrieval does not imply weight training or correctness.

Rivet improves its access to a company's reviewed context and trains a small,
evaluated example-selection model from numeric reply outcomes. It does **not**
update foundation language-model weights or claim measured engineering accuracy
gains. Documents, rules and corrected responses remain private to the company.
Optional shared learning uses numeric features only, with explicit opt-in and
whole-company holdouts. See [Learning flywheel](learning-flywheel.md) for release,
revocation, rollback and minimum-data requirements.

## What is implemented

`backend/company_context` supplies company guidance, review feedback, and customer memory tables:

- `company_rules`: immutable content versions of human-authored workflow instructions, terminology, communication templates, or escalation guidance. Each version begins as a draft. An organization administrator approves it. Approval supersedes the previous approved version; revocation does not silently restore an older rule.
- `company_response_feedback`: the original generated response, original input, generation metadata and hash, exact source fingerprints, human accepted/edited/rejected outcome, reviewer, and reason. Recording a review does not authorize the response for future reuse.
- `company_context_events`: creation, approval, supersession, and revocation history with the actor and reason.
- `customer_profiles`: stable customer codes and IDs inside one tenant. Display names can repeat and never automatically identify or merge customers.
- `order_customer_bindings`: a PM-confirmed customer for each order, with optimistic versions, actor, time, and reason. Reassigning an order invalidates historical notes derived under the previous binding.
- `customer_memories`: immutable versions of sourced historical requirements, preferences, and process guidance, with candidate/approved/revoked states, applicability conditions, explicit conflicts, expiration, and review provenance.

## Customer memory across rotating engineers and orders

### Visible customer brief

`GET /api/company-context/orders/{id}/customer-brief` returns approved standing preferences separately from historical references. Candidates, expired or changed evidence, and explicitly conflicting context are withheld. The workspace and Outlook pane display this brief under **Customer brief**, with original source links and applicability conditions.

`POST /api/company-context/orders/{id}/customer-check` accepts 1–20 explicit document IDs from that order. The deterministic topic-text check finds related passages for human inspection; an apparent match is not proof that a drawing satisfies the preference. Missing or unreadable text and retrieval limits are disclosed. It never sends or approves anything. The UI invalidates displayed results when document fingerprints or customer context change.

`POST /api/company-context/customers/{id}/search` searches extracted source passages across explicitly linked orders for the same tenant and customer. Results retain the historical order, file, revision and location. It does not generate a new answer or infer that an old requirement applies today.

The customer profile's **Add a past request** form creates a candidate from an imported original passage. **Propose as recurring preference** creates a new version of a historical note; it preserves declared conflicts and expiration and still requires administrator approval. Standard intake handles the original files. A bulk history-import wizard remains future work.

### Linking, preparing and approving history

A PM creates or selects the actual purchasing customer and confirms the order's customer link. Rivet does not join records because two companies have similar names, matching domains, or similar PDF text. Two customers in one tenant remain separate; identical customer codes in separate tenants do not share data.

After a reviewed comment receives a recorded human response, `refresh_order_customer_candidates(session, order, limit=50)` prepares a candidate quoting that comment and response with their actual source locations. Explicitly linking an order runs this same preparation on existing reviewed history. It does not require someone to retype every previous requirement. The hook runs after the record is flushed in the human comment-save transaction; the public refresh endpoint can prepare existing reviewed history across explicitly bound orders.

This is deterministic preparation, **not model inference or weight training**. Unreviewed comments, empty/anonymous responses, missing sources, cross-order sources, and oversized excerpts are excluded. A refresh examines at most 200 comments per order and creates at most 50 candidates; customer-wide refresh examines at most 100 orders and creates at most 50 candidates overall. `partial` reports unfinished coverage. A repeat resumes within those bounds without duplicating an unchanged candidate. A changed reviewed comment creates a fresh unapproved version. An explicitly revoked candidate key stays suppressed; only a human can propose a new version for reconsideration.

An administrator reviews the candidate's source and applicability before reuse. `historical_reference` means the source proves what was requested on that earlier order. It never turns an old specification into a requirement on the current project. `standing_preference` is a separate human-authored, sourced version explicitly approved as ongoing guidance; its conditions still apply, and it cannot authorize a technical design, commercial commitment, or message send. Candidate approval alone does not infer that a one-off technical request is a standing customer policy.

Customer retrieval requires the current order's confirmed customer, the source order's still-valid customer binding, matching equipment category and relevant topics/terms, an approved note, unchanged source fingerprints, and an unexpired approval. Notes include the original order, file/revision/page or annotation, exact human review, applicability conditions, and pointers to recorded approval history. Source-comment edits, source changes, revocation, expiry, or customer reassignment remove a note from generation immediately. A newly received revision does not delete valid historical context: the earlier revision stays visibly labeled as history.

Declared conflicts return both available sides, even when one side is less relevant by lexical matching. The agent receives `requires_human_resolution`; it must not silently select the latest document or treat relevance as engineering correctness. This does not claim automatic detection of every possible technical contradiction. Current order sources take precedence, and unclear applicability goes back to the PM.

`assemble_context(...)` now returns `customer_memory` in addition to company rules/examples. The same deterministic `context_hash` covers confirmed customer identity, selected historical notes, explicit conflicts, and relevant approval pointers. Existing reply/agent freshness gates therefore reject work prepared with withdrawn or reassigned customer context. `exclude_order_ids` excludes held-out notes, approval pointers, and notes whose declared comparisons were derived from held-out history. The retriever must never place evaluation answers in the generation prompt.

Customer routes share `/api/company-context` and the existing authenticated tenant boundary:

| Route | Purpose | Permission |
|---|---|---|
| `GET /customers`, `GET /customers/{id}` | Profiles, confirmed orders, sourced candidate/approved/revoked notes | Workspace member |
| `POST /customers` | Explicit customer code, name, description | Member/admin |
| `GET /orders/{id}/customer` | Current customer binding and version | Workspace member |
| `PUT /orders/{id}/customer` | Confirm/change/unlink customer with `customer_id`, `expected_version`, `reason` | Member/admin |
| `POST /customers/{id}/notes/refresh` | Prepare exact reviewed-history candidates; returns counts, notes, and `partial` | Member/admin |
| `POST /customers/{id}/notes` | Propose a sourced note or new immutable version | Member/admin |
| `POST /customer-notes/{id}/approve`, `/revoke` | Explicit review with `expected_version` and `reason` | Admin |

Customer note creation requires `key`, `title`, `text`, `applicability_note`, `source_order_id`, at least one actual `source_id` in `source_ids`, and `provenance_note`. Optional fields are `expected_version` (zero initially), `kind`, `topics`, `category`, `applicability`, `conflicts_with`, and timezone-aware `expires_at`. A source order must already be confirmed for that customer. The UI can link to `source_url` to inspect the actual evidence. List/detail views expose pagination limits rather than claiming a complete archive. Migration `0016` creates these tables after `0015`.

An administrator can separately approve feedback as a positive response example, or as an evaluation case. Rejected responses cannot become positive examples. Company-wide reuse is an explicit decision, requires topic labels, and remains inside the tenant. Otherwise an example is limited to its original order. Expiration and immediate revocation are supported.

Human-authored rules can carry a provenance note and references to actual source spans. `source_order_id` identifies the evidence order; the separate optional `order_id` limits where the rule applies. This allows a company-wide policy with a cited source without making it applicable to every customer by accident: the administrator still chooses category/topic restrictions and approves the version.

## The actual flywheel

1. Rivet generates a draft using the current order evidence and the explicitly approved context available at that time.
2. A person accepts, edits, or rejects it. Rivet retains both the original generation and the review. Sending and engineering/commercial approval are separate actions.
3. An administrator reviews whether that wording or correction is safe to reuse, chooses its scope, and approves it. Useful changes become available to the next matching generation immediately; no model-training job is required.
4. Retrieval selects only current, approved rules and relevant examples from the same workspace. The original order's facts are never evidence for the new order. The agent must obtain factual support from the current order's source tools.
5. The team exports an evaluation snapshot, replays representative cases with held-out orders excluded from retrieval, and compares the revised system against a baseline. A dataset export alone is not an evaluation or evidence of improvement.
6. Failed cases lead to a reviewed rule/example change, extraction fix, or integration fix. Only measured changes should justify broader autonomy.

This distinguishes two kinds of improvement. Company adaptation gives a specific company its own approved vocabulary, response patterns, and operating practices. Product improvement changes common extraction, routing, reliability, and prompting logic against reproducible tests. Private customer documents and labels are not a shared training asset by default.

## Retrieval and freshness

`assemble_context(session, order, query, topics=(), limit=4, exclude_order_ids=())` returns `rules`, `examples`, `guardrails`, and a deterministic `context_hash`.

Selection uses explicit category/topic/order restrictions and a transparent lexical score:

`score = 4 × matching_topic_count + matching_meaningful_term_count + same_order_bonus`

An active evaluated ranker can adjust this score by at most 15 percent without
changing any eligibility filter. Its ID/scope is frozen with the reply. Without
sufficient training data, after rollback or during held-out replay, the fixed
lexical selector remains in use.

Without an exact topic match, an example requires at least two meaningful query terms in its input/topic labels. Generic words such as "comment", "drawing", and "review" do not make every example relevant. The score is a ranking heuristic, **not an accuracy or confidence probability**. No vector database or embedding API is required for this first implementation. A future retriever must preserve the same tenant, scope, approval, freshness, and holdout filters.

Selected company context is bounded to 32,000 serialized characters. Lower-ranked examples are omitted to fit; approved rules themselves are not silently truncated. An excessive set of applicable rules blocks generation with a request to narrow their topics/scope. Inbox reply generation separately limits its source/comment selection, reports partial coverage, and enforces a final 125,000-character provider-input limit.

Each example carries source/document fingerprints and an order evidence version. A changed source, changed document hash, newly arrived revision/document, deleted source, expiration, or revocation excludes it from future context and exports. This deliberately errs toward excluding old examples. A person must review a new current response to restore reuse; Rivet does not automatically recertify historical wording. Source-backed rules similarly cease to apply when their recorded evidence changes.

Generation code must retain the returned `context_hash` and check it, along with current source dependencies, before saving or approving a draft. Rules and historical examples are prompt context; they cannot grant permissions, authorize a send, certify engineering, or override backend gates.

## Integration hooks

Import `backend.company_context.api.router` into the application. Importing the router registers the package's SQLAlchemy models with the existing `Base`; migration `0012` also imports them explicitly. Migration `0012` depends on `0011`.

For existing record-agent drafts:

```python
capture_feedback(session, order, FeedbackWrite(
    proposal_id=saved_proposal_id,
    operation_index=0,
    outcome="edited",
    human_response=reviewed_text,
    reason="The PM clarified which drawing revision is required.",
    topics=["drawing-reference"],
), request_key=idempotency_key)
```

For another persisted generator such as an inbox reply:

```python
capture_generation_feedback(
    session, order,
    request_key=review_key,
    generation={"id": saved_draft.id, "model": saved_draft.model,
                "prompt_version": saved_draft.prompt_version,
                "context_hash": saved_draft.context_hash},
    original_text=saved_draft.text,
    input_text=saved_draft.input_text,
    evidence_ids=saved_draft.evidence_ids,
    outcome="edited",
    human_response=reviewed_text,
    reason=review_reason,
    topics=["drawing-reference"],
)
```

That second function is a **trusted server hook**, not a public endpoint accepting browser-supplied model provenance. The caller must load the persisted draft, verify its original evidence is still current, and pass the saved original generation. The returned value is a feedback view dictionary with `id`, hashes, and `reuse_status: unapproved`.

Inbox reply requests persist their context snapshot and a `reply_draft` job atomically, then return `generating` for polling. The worker checks freshness before preparation, calls the provider outside database locks, and commits only while holding the current job lease. If a worker crashes, a replacement can retry the bounded preparation; no sending side effect exists to duplicate. Stale evidence produces `needs_context`, and exhausted worker attempts produce an explicit failed draft instead of an indefinite progress state. Reopening a reviewed draft rechecks current evidence and company guidance, including withdrawn examples.

`approve_example(session, feedback_id, ReuseReview(...))` and `revoke_example(session, feedback_id, reason)` require an administrator. A user's acceptance of a response does not imply that it should be generalized into a company policy. Worker jobs must carry the authenticated organization identity, as the existing worker does.

## Authenticated API

All routes begin `/api/company-context`. Tenant identity comes from the authenticated request, never a body/query organization ID.

| Route | Purpose | Permission |
|---|---|---|
| `GET /` | Current rules, feedback, approval and freshness status | Admin |
| `POST /rules` | New rule version with provenance | Admin |
| `POST /rules/{id}/approve` or `/revoke` | Explicit context lifecycle | Admin |
| `POST /orders/{id}/feedback` | Review a saved agent communication draft | Member/admin |
| `POST /feedback/{id}/approve` or `/revoke` | Reuse/evaluation permission | Admin |
| `POST /retrieve` | Select context for an accessible order | Authenticated workspace |
| `GET /evaluation-snapshot` | Tenant-scoped evaluation data snapshot | Admin |
| `GET /history` | Context approval audit | Admin |

The feedback route requires an `Idempotency-Key`. An identical request replays its original result; reusing the same key for different review content is rejected. Overview/history lists are capped to 200 records and are not a complete archival export.

## Evaluations before claiming improvement

The snapshot defaults to metadata and hashes. `include_content=true` includes private reviewed text, and is administrator-only. It does not transmit the export to OpenAI or another external evaluator. The snapshot reports `dataset_only_not_evaluated`.

The administrator overview also returns counts of accepted, edited, and rejected responses, current approved examples, stale/expired approved examples, and coverage by order. These are observed review decisions with an explicit `observed_review_outcomes_not_model_accuracy` label. No acceptance rate is presented as a correctness score.

All examples from an order receive the same deterministic development/holdout assignment. A replay must exclude **every held-out order**, including its examples and source-derived rules, from generation context; otherwise answer leakage makes the score meaningless. Snapshots can contain explicit rejected-output cases, but these are negative labels, not accepted target answers.

Measure at least:

- Factual claims supported by the supplied sources; unsupported commitments.
- Correct order/revision/source matching and comments missed during extraction.
- Human acceptance/edit/rejection rates, with case mix and denominators.
- Time to prepare and review a response; model and parsing cost.
- Wrong-recipient/unauthorized send rate, which is enforced by delivery gates rather than model confidence.

Mailbox completeness is a separate reliability metric: reconciliation lag, received/processed/quarantined counts, and unacknowledged failures. More response examples cannot repair a missed mailbox event or an unreadable PDF.

## Why retrieval first

OpenAI distinguishes missing/current/private context from inconsistent model behavior: retrieval supplies context, while examples and prompt changes guide behavior. It recommends evaluating the failure mode before adding complexity. See [Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy).

The [model optimization guide](https://developers.openai.com/api/docs/guides/model-optimization), checked October 12, 2026, emphasizes representative evaluations and reviewed feedback. It also says the fine-tuning platform is winding down and unavailable to new users. Rivet therefore does not make fine-tuning availability a dependency or promise that every customer's documents train a private model. Any future weight training would require a separate provider/data-consent decision and demonstrated benefit on held-out cases.

## Limits

This foundation does not automatically harvest mailbox edits, fine-tune a model, run provider eval jobs, install enterprise document connectors, or establish the accuracy of an engineering decision. Its value is auditable, immediately usable company context with safe isolation and explicit human promotion. UI controls and adoption policies should describe that precisely.
