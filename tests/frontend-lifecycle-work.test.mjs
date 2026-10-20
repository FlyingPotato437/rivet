import test from "node:test";
import assert from "node:assert/strict";
import {
  lifecycleArtifactPath,
  lifecycleDecisionRequest,
  lifecycleStatus,
  draftBundles,
  currentLifecyclePackages,
  lifecycleGateEvidence,
  lifecycleHandoffStatus,
  lifecycleEvidenceFields,
  lifecycleCommercialStatus,
  lifecycleActorLabel,
  primaryLifecyclePackage,
} from "../apps/web/src/lifecycle-work.ts";

const hash = "a".repeat(64);
const artifact = {
  name: "E10 Rev D.dxf",
  sha256: hash,
  media_type: "application/dxf",
  url: "/api/orders/order-a/lifecycle/drafting/bundle-a/E10 Rev D.dxf",
};
const pkg = (patch = {}) => ({
  id: "package-a",
  operation: "revision_review",
  status: "awaiting_decision",
  fingerprint: hash,
  evidence_current: true,
  blockers: [],
  gates: ["engineering", "customer_commitment"],
  pending_gates: ["engineering", "customer_commitment"],
  decisions: [],
  sources: {
    "source-a": { hash, document_id: "document-a", document_sha256: hash },
  },
  payload: {},
  ...patch,
});
const decide = (p, overrides = {}) =>
  lifecycleDecisionRequest(
    p,
    11,
    overrides.fingerprint ?? hash,
    overrides.gate ?? "engineering",
    overrides.decision ?? "approve",
    overrides.reason ??
      "Checked the marked-up drawing against the saved changes.",
    overrides.sourceIds ?? ["source-a"],
    overrides.available ?? ["source-a"],
  );

test("authenticated file paths stay inside the manifest's order and preserve filenames", () => {
  assert.equal(
    lifecycleArtifactPath("order-a", "drafting", "bundle-a", artifact),
    "/api/orders/order-a/lifecycle/drafting/bundle-a/E10%20Rev%20D.dxf",
  );
  assert.equal(
    lifecycleArtifactPath("order-a", "drafting", "bundle-a", {
      ...artifact,
      url: artifact.url.replaceAll(" ", "%20"),
    }),
    "/api/orders/order-a/lifecycle/drafting/bundle-a/E10%20Rev%20D.dxf",
  );
});

test("file links reject foreign URLs, cross-order targets, query strings and traversal", () => {
  for (const url of [
    "https://example.com/leak",
    artifact.url.replace("order-a", "order-b"),
    artifact.url + "?download=1",
    artifact.url + "#x",
    "/api/orders/order-a/lifecycle/drafting/bundle-a/../secret",
    "%",
  ])
    assert.equal(
      lifecycleArtifactPath("order-a", "drafting", "bundle-a", {
        ...artifact,
        url,
      }),
      null,
    );
  for (const name of ["../secret", "nested/file.dxf", "..", "a\\b"])
    assert.equal(
      lifecycleArtifactPath("order-a", "drafting", "bundle-a", {
        ...artifact,
        name,
      }),
      null,
    );
  assert.equal(
    lifecycleArtifactPath("order-a", "drafting", "bundle-a", {
      ...artifact,
      sha256: "",
    }),
    null,
  );
});

test("a decision carries the exact order version, explicit gate, reason and reviewed evidence", () => {
  const body = decide(pkg(), { sourceIds: ["source-a", "source-a"] });
  assert.deepEqual(body, {
    expected_version: 11,
    gate: "engineering",
    decision: "approve",
    reason: "Checked the marked-up drawing against the saved changes.",
    source_ids: ["source-a"],
  });
});

test("reject is package specific and also requires a review reason and cited evidence", () => {
  assert.equal(decide(pkg(), { decision: "reject" }).decision, "reject");
  assert.throws(
    () => decide(pkg(), { decision: "reject", reason: "No" }),
    /short reason/,
  );
  assert.throws(
    () => decide(pkg(), { decision: "reject", sourceIds: [] }),
    /source evidence/,
  );
});

test("no approval if the source or package changed while the decision form was open", () => {
  assert.throws(
    () => decide(pkg({ evidence_current: false })),
    /changed or is blocked/,
  );
  assert.throws(
    () => decide(pkg({ fingerprint: "b".repeat(64) })),
    /changed or is blocked/,
  );
  assert.throws(
    () => decide(pkg({ blockers: ["The PO price differs from the quote."] })),
    /changed or is blocked/,
  );
});

test("decisions cannot be repeated, inferred for another gate, or made on terminal work", () => {
  for (const status of [
    "approved",
    "rejected",
    "stale",
    "superseded",
    "blocked",
  ])
    assert.throws(() => decide(pkg({ status })), /changed or is blocked/);
  assert.throws(() => decide(pkg(), { gate: "money" }), /not required/);
  assert.throws(
    () =>
      decide(
        pkg({ decisions: [{ gate: "engineering", decision: "approve" }] }),
      ),
    /already been recorded/,
  );
});

test("approval evidence must belong to this exact package and be available for review", () => {
  assert.throws(
    () => decide(pkg(), { sourceIds: ["source-other"] }),
    /source evidence/,
  );
  assert.throws(() => decide(pkg(), { available: [] }), /source evidence/);
  assert.throws(
    () => decide(pkg(), { sourceIds: ["source-a", "source-other"] }),
    /source evidence/,
  );
});

test("three edits to one drawing render one actual before/after bundle", () => {
  const bundle = {
    bundle_id: "bundle-a",
    artifacts: [artifact],
    changes: [{ before: "TU-2500", after: "TU-3000" }],
  };
  assert.deepEqual(
    draftBundles(
      pkg({
        payload: {
          comments: [
            { draft_bundle: bundle },
            { draft_bundle: bundle },
            { draft_bundle: bundle },
            { kind: "answer" },
          ],
        },
      }),
    ),
    [bundle],
  );
});

test("prepared work and test approval are not mislabeled as a production release", () => {
  assert.equal(lifecycleStatus(pkg()), "Prepared · needs approval");
  assert.equal(
    lifecycleStatus(pkg({ status: "approved" })),
    "Approval recorded",
  );
  assert.equal(
    lifecycleStatus(pkg({ status: "approved", evidence_current: false })),
    "Sources changed",
  );
  assert.deepEqual(
    currentLifecyclePackages([
      pkg(),
      pkg({ id: "old", status: "superseded" }),
      pkg({ id: "no", status: "rejected" }),
    ]).map((p) => p.id),
    ["package-a"],
  );
});

test("each approval starts with its own saved evidence and retains all other package sources", () => {
  const ids = ["price", "contract", "drawing", "layout", "answer", "extra"];
  const p = pkg({
    sources: Object.fromEntries(ids.map((id) => [id, { hash }])),
    payload: {
      change_order: {
        lines: [
          { source_ids: ["price"], billability_source_ids: ["contract"] },
        ],
      },
      comments: [
        { kind: "answer", source_ids: ["answer"] },
        { kind: "text_edit", source_ids: ["drawing"] },
        { kind: "layout_task", source_ids: ["layout"] },
      ],
      email_draft: { source_ids: ["answer", "drawing", "layout"] },
    },
  });
  const expected = {
    money: ["price", "contract"],
    scope: ["contract"],
    engineering: ["drawing", "layout"],
    customer_commitment: ["answer", "drawing", "layout", "price"],
  };
  for (const [gate, relevant] of Object.entries(expected)) {
    const grouped = lifecycleGateEvidence(p, gate, ids);
    assert.deepEqual(grouped.relevant, relevant);
    assert.deepEqual(
      new Set([...grouped.relevant, ...grouped.additional]),
      new Set(ids),
    );
    assert.equal(
      new Set([...grouped.relevant, ...grouped.additional]).size,
      ids.length,
    );
  }
});

test("evidence ordering never imports foreign IDs, assumes review, or hides unmapped sources", () => {
  const p = pkg({
    payload: { lines: [{ source_ids: ["foreign", "source-a", "source-a"] }] },
  });
  assert.deepEqual(lifecycleGateEvidence(p, "money", ["source-a", "foreign"]), {
    relevant: ["source-a"],
    additional: [],
  });
  assert.deepEqual(lifecycleGateEvidence(p, "money", []), {
    relevant: [],
    additional: [],
  });
  assert.deepEqual(lifecycleGateEvidence(pkg(), "scope", ["source-a"]), {
    relevant: [],
    additional: ["source-a"],
  });
  assert.throws(() => decide(p, { sourceIds: [] }), /source evidence/);
});

test("Outlook status distinguishes a queued handoff, an actual native draft and an existing human draft", () => {
  const state = (outlook_handoff) =>
    lifecycleHandoffStatus(pkg({ outlook_handoff }));
  assert.equal(
    state({ status: "queued" }).title,
    "Preparing the Outlook draft",
  );
  assert.equal(state({ status: "queued" }).canOpen, false);
  const ready = state({
    status: "created",
    internet_message_id: "<mail@example.test>",
    draft: { attachments: [{ name: "quote.pdf" }] },
  });
  assert.equal(ready.title, "Ready in Outlook");
  assert.match(ready.detail, /unsent reply.*1 attachment/);
  assert.equal(ready.canOpen, true);
  assert.equal(state({ status: "created" }).canOpen, false);
  assert.equal(
    state({ status: "existing_draft" }).title,
    "Your Outlook draft is preserved",
  );
  assert.match(state({ status: "existing_draft" }).detail, /not replaced/);
});

test("missing threads, failed placement and sent outcomes cannot look like ready unsent drafts", () => {
  const state = (outlook_handoff) =>
    lifecycleHandoffStatus(pkg({ outlook_handoff }));
  assert.match(
    state({ status: "needs_thread", candidate_count: 2 }).detail,
    /More than one email thread/,
  );
  assert.match(
    state({ status: "needs_thread", candidate_count: 0 }).detail,
    /not linked/,
  );
  assert.equal(
    state({ status: "failed", draft: { error: "Microsoft unavailable" } })
      .detail,
    "Microsoft unavailable",
  );
  assert.equal(
    state({ status: "permission_required" }).title,
    "Outlook permission needed",
  );
  assert.equal(
    state({ status: "sent", draft: { outcome: "edited" } }).title,
    "Sent after edits",
  );
  assert.equal(
    state({ status: "sent", draft: { outcome: "accepted" } }).title,
    "Sent as drafted",
  );
  for (const status of [
    "needs_thread",
    "failed",
    "permission_required",
    "sent",
    "discarded",
    "superseded",
    "not_requested",
    "unexpected",
  ])
    assert.equal(state({ status }).canOpen, false);
  assert.match(
    state({ status: "not_requested" }).detail,
    /approvals are still pending/,
  );
});

test("source changes remain visible beside saved drafts without erasing a confirmed send", () => {
  const changed = pkg({
    evidence_current: false,
    outlook_handoff: { status: "created", draft: { attachments: [] } },
  });
  assert.equal(lifecycleHandoffStatus(changed).tone, "attention");
  assert.match(
    lifecycleHandoffStatus(changed).detail,
    /Source records have changed/,
  );
  changed.outlook_handoff = { status: "sent", draft: { outcome: "edited" } };
  assert.equal(lifecycleHandoffStatus(changed).title, "Sent after edits");
  assert.equal(lifecycleHandoffStatus(changed).tone, "done");
});

test("flat commercial source facts are readable without inferred values or decimal rounding", () => {
  assert.deepEqual(
    lifecycleEvidenceFields(
      JSON.stringify({
        part_number: "CB-12",
        unit_cost: "1200.00",
        currency: "USD",
        gross_margin_percent: "25.0",
        lead_time_days: 14,
        baseline_quantity: 1,
        billable: true,
        delivery_confirmed: false,
      }),
    ),
    [
      { label: "Part number", value: "CB-12" },
      { label: "Unit cost", value: "USD 1,200.00" },
      { label: "Currency", value: "USD" },
      { label: "Gross margin", value: "25.0%" },
      { label: "Lead time (days)", value: "14" },
      { label: "Baseline quantity", value: "1" },
      { label: "Billable", value: "Yes" },
      { label: "Delivery confirmed", value: "No" },
    ],
  );
  assert.equal(
    lifecycleEvidenceFields(
      '{"unit_cost":"9007199254740993.12345","currency":"USD"}',
    )[0].value,
    "USD 9,007,199,254,740,993.12345",
  );
  assert.equal(
    lifecycleEvidenceFields('{"unit_cost":"1200.00"}')[0].value,
    "1200.00",
  );
  assert.deepEqual(
    lifecycleEvidenceFields(
      '{"exceptions":["Freight excluded","Delivery requires confirmation"]}',
    ),
    [
      {
        label: "Exceptions",
        value: "Freight excluded; Delivery requires confirmation",
      },
    ],
  );
  for (const text of [
    "Customer asked for NEMA 3R.",
    "{invalid",
    '["a"]',
    "null",
    '{"nested":{"price":1}}',
    '{"unit_cost":9007199254740993}',
    "{}",
  ])
    assert.equal(lifecycleEvidenceFields(text), null);
});

test("commercial badge follows recorded scope and price decisions without implying customer acceptance", () => {
  const commercial = pkg({ pending_gates: ["money", "scope"] });
  assert.equal(
    lifecycleCommercialStatus(commercial),
    "Scope and price need approval",
  );
  commercial.pending_gates = ["scope"];
  commercial.decisions = [{ gate: "money", decision: "approve" }];
  assert.equal(
    lifecycleCommercialStatus(commercial),
    "Price reviewed · scope needs approval",
  );
  commercial.pending_gates = ["money"];
  commercial.decisions = [{ gate: "scope", decision: "approve" }];
  assert.equal(
    lifecycleCommercialStatus(commercial),
    "Scope reviewed · price needs approval",
  );
  commercial.pending_gates = [];
  commercial.decisions.push({ gate: "money", decision: "approve" });
  assert.equal(
    lifecycleCommercialStatus(commercial),
    "Internally reviewed · proposal only",
  );
  assert.equal(
    lifecycleCommercialStatus({ ...commercial, evidence_current: false }),
    "Sources changed · review required",
  );
  assert.equal(
    lifecycleCommercialStatus({
      ...commercial,
      blockers: ["Contract basis changed"],
    }),
    "On hold · conditions unresolved",
  );
  assert.equal(
    lifecycleCommercialStatus({ ...commercial, status: "rejected" }),
    "Proposal rejected",
  );
  assert.equal(
    lifecycleCommercialStatus({ ...commercial, decisions: [] }),
    "Scope and price need approval",
  );
});

test("reviewer label preserves actual names without exposing opaque provider IDs as names", () => {
  for (const value of [
    "user_2mx123",
    "usr_xyz",
    "auth0|1234",
    "clerk:user_xyz",
    "41d6a292-0907-44d2-a1e0-03618970aa74",
  ])
    assert.equal(lifecycleActorLabel(value), "Authorized team member");
  for (const value of [
    "Srikanth Samy",
    "Nikhil R. Nune",
    "Demo PM",
    "engineer@example.test",
  ])
    assert.equal(lifecycleActorLabel(value), value);
});

test("the latest current release or closeout leads while earlier drawing work stays available", () => {
  const review = pkg({
    id: "review",
    operation: "revision_review",
    prepared_at: "2026-10-15T08:00:00Z",
  });
  const release = pkg({
    id: "release",
    operation: "release",
    prepared_at: "2026-10-15T01:10:00-07:00",
  });
  const closeout = pkg({
    id: "closeout",
    operation: "closeout",
    prepared_at: "2026-10-15T08:20:00Z",
  });
  assert.equal(
    primaryLifecyclePackage([review, release, closeout]).id,
    "closeout",
  );
  assert.equal(primaryLifecyclePackage([review, release]).id, "release");
  assert.equal(
    primaryLifecyclePackage([review, { ...closeout, evidence_current: false }])
      .id,
    "review",
  );
  assert.equal(
    primaryLifecyclePackage([review, { ...closeout, status: "superseded" }]).id,
    "review",
  );
  assert.equal(
    primaryLifecyclePackage([review, { ...closeout, status: "rejected" }]).id,
    "review",
  );
  const newReturn = {
    ...review,
    id: "new-return",
    prepared_at: "2026-10-15T08:30:00Z",
  };
  assert.equal(
    primaryLifecyclePackage([review, closeout, newReturn]).id,
    "new-return",
  );
  assert.equal(
    primaryLifecyclePackage([pkg({ id: "quote", operation: "quote" })]).id,
    "quote",
  );
  assert.equal(primaryLifecyclePackage([]), undefined);
});

test("an approval hold describes prepared work without claiming an Outlook draft", () => {
  const status = lifecycleHandoffStatus(
    pkg({ outlook_handoff: { status: "awaiting_approval" } }),
  );
  assert.equal(status.title, "Ready for your decisions");
  assert.equal(status.canOpen, false);
  assert.match(status.detail, /after the required decisions/);
});
