# Rivet's evidence-bound learning loop

Rivet should improve along two different curves: become more useful inside each
company immediately, and improve the shared task models only after new behavior
has passed an independent evaluation. More usage creates opportunities to learn;
it does not guarantee better answers.

The product-specific mechanism is **evidence-bound correction replay**. This is
an implementation design, not a claim of novel research, a patent, or measured
model improvement.

## What ships in this change

- A Company learning view on Your work: observed reviews and edits, current
  approved context, approved evaluation cases, and replies whose saved inputs
  actually selected memory. The history opens in place.
- Literal before/after corrections are included with approved response examples
  in generation context. The model sees what a person changed and the recorded
  reason, rather than just the final answer. Existing tenant, topic, equipment,
  order-scope, approval, expiry and source-freshness gates still apply.
- Changes to numeric identifiers, quantities or explicit approval/negation terms
  are recorded but withheld from positive response-example reuse. This is a
  conservative token-difference check, not a semantic correctness classifier.
  A technical edit should be reviewed against its sources and represented by a
  separately approved sourced requirement when appropriate. Sending or granting
  wording reuse alone does not establish that its changed facts are correct.
- Each reply exposes a compact summary of its actual frozen company context.
  Outlook and the order record can show which examples, customer preferences and
  company rules were selected. Selection is observable; benefit is not inferred.
- The existing paired replay now withholds every other job of an explicitly
  linked evaluation customer, as well as all evaluation orders. This reduces
  answer leakage through a customer's past requests.
- An administrator can prepare a metadata-only training plan from explicitly
  selected feedback IDs. Whole customers stay in one split, or whole orders when
  no customer is linked. Identical input duplicated across groups is excluded.
  No training text is exported, no provider is called and no consent is inferred.

Migration `0020_example_learning` adds explicit learning policies and versioned
numeric example-ranker artifacts. Feedback, frozen replies, source hashes,
customer links, approved memories and approval events persist each episode.

## Automatic example-ranker training

`backend/improvement/adaptive.py` now trains a small logistic regression model
locally every five minutes in the existing worker. This is actual parameter
training for example selection; it does not change the foundation LLM's weights.

Each frozen reply identifies the approved historical examples it actually used.
Its later sent-unchanged/edited outcome provides an editing-effort proxy. The
fitter receives six numeric features: bias, query overlap, topic overlap,
same-order indicator, whether the historical example was corrected, and bounded
response length. No source text, drawings or email bodies enter its artifacts.
Several examples in one reply count as one episode, not several independent
approvals. Fact-changing corrections are excluded from positive-example training.

Private models require at least 40 episodes across six customer/order groups,
25 training episodes, ten held-out episodes from at least two groups, and both
outcome classes in both splits. Whole customer/order groups remain in one split.
A candidate activates only if its held-out Brier loss improves over the constant
training-mean baseline by at least 0.01. This measures outcome prediction, not
engineering accuracy or a proven causal reduction in editing.

The selected model adjusts the existing lexical relevance score by at most
15 percent. It cannot make an ineligible example available, waive evidence
freshness, promote customer requirements or authorize a send. The exact model
ID/scope is saved in generation context. Independent LLM replay disables trained
ranking because the ranker might have observed the held-out orders.

An administrator can pause training or roll back the latest private version in
Settings. Revoking a contributing example, changing its evidence or changing
learning permission invalidates dependent models immediately. The system falls
back to the deterministic selector. A new insufficient/failed candidate does
not silently reactivate an earlier rolled-back model.

Shared numeric learning is off by default. Explicitly opted-in organizations
can contribute numeric features/labels, never their document or reply text.
Company identity is the held-out group for shared fitting, so the same fit gates
require at least six participating companies and two held-out companies with
sufficient outcomes. Contribution revocation withdraws dependent shared releases.
This is not authorization to train a shared language model on company content.

The live workspace currently has too few eligible episodes to activate a learned
ranker. Synthetic regression tests demonstrate fitting and activation; they are
not customer-accuracy evidence. Approved private retrieval continues immediately.

## The unit of learning

A correction episode connects these existing records:

```
incoming email -> job/customer/revision -> retrieved sources and hashes
              -> generated reply + model/context fingerprint
              -> sent/reviewed reply + person + observed outcome
              -> literal edit + reusable scope + review decision
              -> later reply inputs that selected this example
```

The Outlook send reconciliation captures real edits without a separate feedback
form. Sending records acceptance of communication; it does not prove engineering
correctness. A deletion is not automatically a negative engineering label. A
rejection is a failure case, not a positive training target.

The graph is an auditable relationship between existing records, not a new graph
database or a claim that a model's reasoning is mathematically verified.

## Three levels of improvement

| Level | Learns | Boundary | Current status |
|---|---|---|---|
| Job | Reviewed wording, resolutions, current source references | Same order; source/version checks | Active through existing Outlook feedback policy |
| Company/customer | Recurring requirements, workflow rules, reviewed examples | Explicit customer identity and approved applicability | Approved context retrieval active; literal correction context added |
| Example selection | Numeric features from examples used in sent replies | Private by default; group holdouts, evaluated release and rollback | Implemented; activates only with sufficient qualifying data |
| Shared example selector | Numeric task features and observed edit outcomes | Explicit opt-in, whole-company holdouts and revocation | Implemented; inactive until enough opted-in companies contribute |
| Foundation language model | General extraction and response behavior | Separate content license, expert labels and independent tests | Future work; no foundation weight updates |

Facts that change belong in retrieval: current BOM, price, approved revision,
scope, release status, lead time and customer-specific requirements. Fine-tuning
should teach task behavior, such as locating the governing source, preserving a
revision identifier, recognizing uncertainty and writing a concise clarification.
It should not turn an old customer's breaker choice into the next job's default.

## How deep integration feeds the loop

Outlook supplies the incoming request, thread and actual final communication.
SharePoint supplies versioned specifications; ERP and vault connectors supply
accepted scope and released drawing evidence. A correction remains tied to the
retrieved snapshot. Subsequent changes can invalidate its reuse eligibility.

Tool outcomes are another kind of evidence: a proposed action is different from
an executed action, and an executed action requires the saved external/local
receipt. A future tool-policy learner should train from verified receipts,
not the agent's narrative that a tool succeeded.

Live Outlook/SharePoint reads remain distinct from local ERP/vault action
simulations. The learning view does not add production integrations, training
permission or customer sending authority.

## Candidate -> replay -> release

1. Capture immutable correction episodes from normal work.
2. Find recurring correction topics across independent jobs. A repeated topic
   is a sampling signal, not proof that identical wording or policy applies.
3. Review a candidate improvement: an example selection change, workflow rule,
   task prompt, extractor or trained adapter. Record its version and provenance.
4. Freeze a representative evaluation set with expert labels. Keep whole
   customers/jobs out of development and retrieval. Also use chronological
   holdouts and exact/near-duplicate checks before a real training run.
5. Replay the same frozen evidence with the current behavior and candidate.
   Randomize A/B display and grade source support, question coverage, customer
   applicability and unsupported commitments. Evaluate extraction omissions,
   wrong source/version selection and tool mistakes separately.
6. Measure paired changes, review effort, cost and latency. A sent-as-drafted
   rate is an adoption signal, not accuracy. Report counts and uncertainty.
7. Block regressions; require a human release decision. Canary new versions on
   a limited set of jobs and retain the last version for rollback.

The existing replay gate requires at least 10 cases from 3 orders, at least one
improvement, no rubric regression, valid citations/shape, and passing critical
criteria for every candidate case. This is an initial screening gate, not
statistical proof, a production certification or an automatic deployment.
There are no graded model results yet for this change.

## Portable fine-tuning architecture

Keep a provider-independent Episode -> Label -> DatasetVersion -> TrainingRun ->
EvaluationRun -> Release pipeline for future language-model tuning. The local
ranker implements numeric dataset fingerprints, fitted parameters, evaluation,
release and rollback now. The separate text-training plan remains metadata-only
and does not start a provider fine-tuning job.

**Supervised tuning:** inputs contain the task and evidence supplied at inference;
targets are expert-verified outputs. Train behavior with a loss of the form
`-sum(log p(target_token | input, previous_target_tokens))`. Train separate small
tasks first: markup extraction, order matching, source selection or reply format.

**Preference tuning:** use original/edited pairs only after an expert confirms
which behavior is preferred and why. An edit is not automatically a preference
for every changed fact. For DPO-style training, compare the policy's log-odds of
preferred versus rejected outputs against a fixed reference model. Keep
technical correctness labels distinct from wording preference labels.

**Policy/tool learning:** successful tool traces need validated permissions,
exact input versions and execution receipts. Replay in a sandbox before using a
new tool policy. A model score never overrides release or sending gates.

Do not make production viability depend on one vendor's training endpoint.
OpenAI's model-optimization documentation, checked October 15, 2026, states that
fine-tuning is being wound down and is unavailable to new users. Use retrieval,
reviewed examples and evaluated prompt/task changes now; select an available
training provider/model only once a specific task has enough labels and an
observed failure that training is likely to fix.

## Company privacy and the network flywheel

Company learning is private by default. More users at the same company can
contribute reviewed examples within that company's policy. Wider adoption can
reveal recurring task failure categories. That does not authorize pooling their
documents or replies.

Shared-model training requires separate company permission, an explicit data
license, review of text/attachments/tool traces for identifying or proprietary
content, and an auditable dataset manifest. Removing company names is not
sufficient anonymization for unique drawings or contract terms. Consent and
revocation handling must be designed before exporting training text. Nothing
in the current API shares examples across tenants.

## Endpoints and validation

- `GET /api/company-context/learning`: admin-only private learning ledger.
- `GET /api/company-context/orders/{id}/learning`: authorized order view.
- `POST /api/company-context/learning/training-plan`: admin-only metadata plan;
  body contains 1–200 `feedback_ids`. It creates no provider job or file export.
- `GET /api/company-context/learning/model`: admin-only active/latest model and policy.
- `PUT /api/company-context/learning/policy`: private training and explicit numeric sharing.
- `POST /api/company-context/learning/train`: check the current private dataset now.
- `POST /api/company-context/learning/models/{id}/rollback`: withdraw a private release.
- Existing `scripts/evaluate_company_replies.py`: prepare, paired replay and
  human scoring; provider calls require its explicit flag.

Counters are bounded recent samples (up to 200 records per type), not lifetime
totals. Partial coverage is disclosed. Word-edit distance describes text change,
not correctness. Stored context selection describes actual input, not causal
attribution of a model's answer.

## How to describe this to Daniel

“Rivet learns from the work people already do. When a PM corrects a reply, we keep
the correction with the exact source versions and the final outcome. It can guide
the next relevant reply inside that company. Before changing behavior more
widely, we replay it against separate customer jobs and check for regressions.
Over time, those reviewed episodes can support task-specific model training,
with company permission. Today the private memory and feedback loop are built;
an evaluated example selector trains automatically once enough replies accumulate.
Shared numeric learning is opt-in. Foundation-model tuning and measured
engineering-accuracy gains are still ahead.”

Sources: [OpenAI model optimization](https://developers.openai.com/api/docs/guides/model-optimization)
and [Optimizing LLM accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy).
