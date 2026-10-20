import test from "node:test";
import assert from "node:assert/strict";
import {
  currentReviewItems,
  workOverview,
  workInStatus,
} from "../apps/web/src/review-queue.ts";
const row = (id, extra = {}) => ({
  id,
  order_id: "order-a",
  kind: "comment_review",
  comment_id: "comment-a",
  updated_at: "2026-10-13T00:00:00Z",
  ...extra,
});
test("a comment has one current review action while distinct comments and orders survive", () => {
  const input = [
    row("old"),
    row("new", { kind: "response", updated_at: "2026-10-13T01:00:00Z" }),
    row("other-comment", { comment_id: "comment-b" }),
    row("other-order", { order_id: "order-b" }),
  ];
  assert.deepEqual(
    currentReviewItems(input).map((r) => r.id),
    ["new", "other-comment", "other-order"],
  );
});
test("only explicitly superseded runs leave the queue; ambiguous paused/failed/manual tasks stay", () => {
  const input = [
    row("stale", { kind: "run", run_id: "run-old", superseded_by: "run-new" }),
    row("manual", { kind: "run", run_id: "run-manual" }),
    row("new", { kind: "run", run_id: "run-new" }),
    row("task", { kind: "task", task_id: "task-a" }),
  ];
  assert.deepEqual(
    currentReviewItems(input).map((r) => r.id),
    ["manual", "new", "task"],
  );
});
test("distinct proposals for one comment remain distinct, even with identical wording", () => {
  const input = [
    row("one", { kind: "proposal", proposal_id: "p1", title: "Same words" }),
    row("two", { kind: "proposal", proposal_id: "p2", title: "Same words" }),
  ];
  assert.equal(currentReviewItems(input).length, 2);
});

const work = (id, extra = {}) =>
  row(id, {
    status: "needs_review",
    origin: "imported",
    title: "Comment",
    detail: "Source",
    ...extra,
  });
test("captured comments remain visible as records without becoming individual decisions", () => {
  const input = [work("one"), work("two", { comment_id: "comment-b" })];
  const groups = workOverview(input);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].status, "ready");
  assert.equal(groups[0].recordedOnly, true);
  assert.equal(groups[0].items.length, 2);
  assert.equal(
    input[0].status,
    "needs_review",
    "presentation must not alter the source review state",
  );
});
test("genuine human tasks, unreadable sources and agent questions stay visible", () => {
  const groups = workOverview([
    work("capture"),
    work("manual", { kind: "task", task_id: "t1", origin: "human" }),
    work("unreadable", { comment_id: "bad-scan", origin: "automatic" }),
    work("question", {
      kind: "run",
      run_id: "r1",
      origin: "ai",
      run_status: "waiting_for_input",
    }),
    work("failed", {
      kind: "run",
      run_id: "r2",
      origin: "ai",
      run_status: "failed",
    }),
  ]);
  assert.equal(
    groups.length,
    1,
    "an order's source record is reachable from its work without a duplicate card",
  );
  assert.equal(groups[0].status, "needs_review");
  assert.deepEqual(
    groups[0].items.map((item) => item.id),
    ["manual", "unreadable", "question", "failed"],
  );
});
test("finished prepared work leaves decisions while stale work never looks ready", () => {
  const groups = workOverview([
    work("ready", {
      kind: "run",
      run_id: "r1",
      origin: "ai",
      run_status: "waiting_for_review",
    }),
    work("stale", {
      kind: "run",
      run_id: "r2",
      origin: "ai",
      run_status: "waiting_for_review",
      needs_refresh: true,
    }),
    work("other", {
      kind: "task",
      task_id: "t2",
      origin: "human",
      order_id: "order-b",
    }),
  ]);
  assert.equal(groups.length, 2);
  assert.deepEqual(
    groups.map((g) => [g.lead.id, g.status]),
    [
      ["stale", "needs_review"],
      ["other", "needs_review"],
    ],
  );
});

test("one order result retains its decisions and saved work without a second review card", () => {
  const groups = workOverview([
    work("capture"),
    work("draft", {
      kind: "proposal",
      proposal_id: "draft",
      origin: "ai",
      status: "ready",
      review_required: false,
    }),
    work("decision", {
      kind: "proposal",
      proposal_id: "link",
      origin: "ai",
      status: "ready",
      review_required: true,
    }),
    work("running", {
      kind: "run",
      run_id: "run",
      origin: "ai",
      status: "waiting",
      run_status: "running",
    }),
  ]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].lead.id, "decision");
  assert.equal(groups[0].status, "needs_review");
  assert.deepEqual(
    groups[0].items.map((item) => item.id),
    ["draft", "decision", "running"],
  );
});

test("finished drafts remain in Results while the order's real decision stays in Needs you", () => {
  const [group] = workOverview([
    work("draft", {
      kind: "proposal",
      proposal_id: "draft",
      origin: "ai",
      status: "ready",
      prepared_outputs: 3,
    }),
    work("decision", {
      kind: "task",
      task_id: "t",
      origin: "human",
      status: "needs_review",
    }),
  ]);
  assert.equal(workInStatus(group, "ready"), true);
  assert.equal(workInStatus(group, "needs_review"), true);
  assert.equal(workInStatus(group, "waiting"), false);
});
