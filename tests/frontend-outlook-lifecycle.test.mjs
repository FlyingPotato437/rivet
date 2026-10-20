import test from "node:test";
import assert from "node:assert/strict";
import {
  outlookLifecycleMatches,
  outlookPackageSources,
  outlookLifecycleDecision,
  outlookPackageHandoff,
} from "../apps/web/src/outlook-lifecycle.ts";
import { lifecycleHandoffStatus } from "../apps/web/src/lifecycle-work.ts";

const hash = "a".repeat(64);
const other = "b".repeat(64);
const message = {
  mode: "outlook",
  itemKey: "outlook-item-a",
  internetMessageId: "<customer-a@example.test>",
  subject: "Revision C",
  sender: "Customer",
  canReply: true,
};
const source = {
  id: "source-a",
  document_id: "doc-a",
  document_name: "Accepted scope.pdf",
  text: "Approved scope excludes remote indication.",
  location: { page: 2 },
  hash,
  document_sha256: hash,
  current: true,
  truncated: false,
};
const packageFixture = () => ({
  id: "package-a",
  operation: "revision_review",
  title: "Revision C",
  status: "awaiting_decision",
  evidence_current: true,
  mode: "local_demo",
  fingerprint: hash,
  pending_gates: ["scope"],
  gates: ["scope"],
  allowed_gates: ["scope"],
  blockers: [],
  decisions: [],
  payload: {},
  artifacts: [],
  sources: {
    "source-a": { document_id: "doc-a", hash, document_sha256: hash },
  },
});
const fixture = () => ({
  status: "matched",
  order_id: "order-a",
  receipt_id: "receipt-a",
  revision: "C",
  version: 12,
  internet_message_id: message.internetMessageId,
  packages: [packageFixture()],
  sources: [structuredClone(source)],
});
const decide = (patch = {}) =>
  outlookLifecycleDecision({
    initial: fixture(),
    fresh: fixture(),
    expectedMessage: message,
    currentMessage: message,
    orderId: "order-a",
    packageId: "package-a",
    gate: "scope",
    decision: "approve",
    reason: "Checked the requested addition against accepted scope.",
    selectedSourceIds: ["source-a"],
    ...patch,
  });

test("only the captured selected native email and exact matched order expose decisions", () => {
  assert.equal(outlookLifecycleMatches(fixture(), message, "order-a"), true);
  for (const patch of [
    { mode: "preview" },
    { itemKey: "" },
    { internetMessageId: "" },
    { internetMessageId: "<other@example.test>" },
  ])
    assert.equal(
      outlookLifecycleMatches(fixture(), { ...message, ...patch }, "order-a"),
      false,
    );
  for (const patch of [
    { status: "needs_confirmation" },
    { status: "not_captured" },
    { order_id: "order-b" },
    { receipt_id: null },
    { version: null },
  ])
    assert.equal(
      outlookLifecycleMatches({ ...fixture(), ...patch }, message, "order-a"),
      false,
    );
  assert.equal(outlookLifecycleMatches(fixture(), message, "order-b"), false);
});

test("current exact email decisions retain order version, selected gate and explicitly reviewed sources", () => {
  assert.deepEqual(decide(), {
    expected_version: 12,
    gate: "scope",
    decision: "approve",
    reason: "Checked the requested addition against accepted scope.",
    source_ids: ["source-a"],
    internet_message_id: message.internetMessageId,
    receipt_id: "receipt-a",
    package_fingerprint: hash,
  });
  assert.equal(decide({ decision: "reject" }).decision, "reject");
});

test("other email-bound stages keep the same exact receipt and package guard", () => {
  for (const operation of [
    "kickoff",
    "revision_check",
    "purchasing",
    "release",
    "closeout",
  ]) {
    const initial = fixture();
    initial.packages[0].operation = operation;
    const body = decide({ initial, fresh: structuredClone(initial) });
    assert.equal(body.package_fingerprint, hash);
    assert.equal(body.receipt_id, initial.receipt_id);
  }
});

test("an Outlook item switch or changed email identity cannot authorize the old package", () => {
  for (const patch of [
    { itemKey: "outlook-item-b" },
    { internetMessageId: "<other@example.test>" },
    { mode: "preview" },
  ])
    assert.throws(
      () => decide({ currentMessage: { ...message, ...patch } }),
      /selected email/,
    );
  assert.throws(
    () => decide({ fresh: { ...fixture(), order_id: "order-b" } }),
    /selected email/,
  );
  assert.throws(
    () => decide({ fresh: { ...fixture(), status: "needs_confirmation" } }),
    /selected email/,
  );
});

test("a fresh preflight cannot silently change the receipt, revision or order version", () => {
  for (const patch of [
    { receipt_id: "receipt-b" },
    { revision: "D" },
    { version: 13 },
  ])
    assert.throws(
      () => decide({ fresh: { ...fixture(), ...patch } }),
      /order changed/,
    );
});

test("package replacement or lost approver authority blocks the decision", () => {
  for (const patch of [
    { fingerprint: other },
    { allowed_gates: [] },
    { operation: "release" },
    { id: "package-b" },
  ]) {
    const fresh = fixture();
    fresh.packages[0] = { ...fresh.packages[0], ...patch };
    assert.throws(
      () => decide({ fresh }),
      /package changed|authorized approver/,
    );
  }
});

test("only package-owned, hash-matched current source excerpts can be reviewed", () => {
  const view = fixture();
  for (const patch of [
    { id: "source-other" },
    { hash: other },
    { document_sha256: other },
    { document_id: "doc-b" },
    { current: false },
  ]) {
    assert.deepEqual(
      outlookPackageSources(
        { ...view, sources: [{ ...source, ...patch }] },
        view.packages[0],
      ),
      [],
    );
  }
  assert.deepEqual(
    outlookPackageSources(view, view.packages[0]).map((s) => s.id),
    ["source-a"],
  );
});

test("changed or missing source bytes between review and submit invalidate selected evidence", () => {
  for (const patch of [
    { hash: other },
    { document_sha256: other },
    { current: false },
  ]) {
    const fresh = fixture();
    fresh.sources[0] = { ...source, ...patch };
    assert.throws(() => decide({ fresh }), /source evidence/);
  }
  const fresh = fixture();
  fresh.sources = [];
  assert.throws(() => decide({ fresh }), /source evidence/);
  assert.throws(() => decide({ selectedSourceIds: [] }), /source evidence/);
  assert.throws(
    () => decide({ selectedSourceIds: ["source-other"] }),
    /source evidence/,
  );
});

test("stale, blocked and already recorded work stays unapprovable even with nominal allowed gates", () => {
  for (const patch of [
    { evidence_current: false },
    { blockers: ["Released drawing changed"] },
    { status: "rejected" },
    { status: "superseded" },
  ]) {
    const fresh = fixture();
    fresh.packages[0] = { ...fresh.packages[0], ...patch };
    assert.throws(() => decide({ fresh }), /changed or is blocked/);
  }
  const fresh = fixture();
  fresh.packages[0].decisions = [{ gate: "scope", decision: "approve" }];
  assert.throws(() => decide({ fresh }), /already been recorded/);
  assert.throws(() => decide({ reason: "ok" }), /short reason/);
});

test("a native draft from another captured email is never represented as this email's draft", () => {
  const view = fixture();
  const item = {
    ...view.packages[0],
    outlook_handoff: {
      status: "created",
      receipt_id: view.receipt_id,
      internet_message_id: view.internet_message_id,
    },
  };
  assert.equal(outlookPackageHandoff(item, view).status, "created");
  assert.equal(
    outlookPackageHandoff(
      {
        ...item,
        outlook_handoff: { ...item.outlook_handoff, receipt_id: "receipt-b" },
      },
      view,
    ).status,
    "not_requested",
  );
  assert.equal(
    outlookPackageHandoff(
      {
        ...item,
        outlook_handoff: {
          ...item.outlook_handoff,
          internet_message_id: "<other@example.test>",
        },
      },
      view,
    ).status,
    "not_requested",
  );
  const otherThread = { ...item, outlook_handoff: { status: "other_thread" } };
  assert.equal(
    lifecycleHandoffStatus({
      ...otherThread,
      outlook_handoff: outlookPackageHandoff(otherThread, view),
    }).canOpen,
    false,
  );
  assert.match(lifecycleHandoffStatus(otherThread).title, /another thread/);
});
