import test from "node:test";
import assert from "node:assert/strict";
import {
  redesignApiPath,
  redesignCanApprove,
  redesignIsActive,
  redesignStatusLabel,
} from "../apps/web/src/drawing-redesign-types.ts";

const eligibleRun = {
  id: "run-1",
  status: "ready_for_review",
  current: true,
  approval_eligible: true,
  remaining_issues: [],
};

test("redesign approval requires a current, eligible review with no open issues", () => {
  assert.equal(redesignCanApprove(eligibleRun), true);
  assert.equal(redesignCanApprove({ ...eligibleRun, current: false }), false);
  assert.equal(
    redesignCanApprove({ ...eligibleRun, approval_eligible: false }),
    false,
  );
  assert.equal(
    redesignCanApprove({
      ...eligibleRun,
      remaining_issues: ["Confirm the rating"],
    }),
    false,
  );
  assert.equal(
    redesignCanApprove({ ...eligibleRun, status: "needs_information" }),
    false,
  );
});

test("only queued and running redesigns poll as active work", () => {
  assert.equal(redesignIsActive("queued"), true);
  assert.equal(redesignIsActive("running"), true);
  assert.equal(redesignIsActive("needs_information"), false);
  assert.equal(
    redesignStatusLabel("ready_for_review"),
    "Ready for engineer review",
  );
});

test("media and download links stay on authenticated API paths", () => {
  const order = "6cc6b87f-2e6d-4caa-a6ea-147138f6ca6e";
  const run = "edfd8eba-a3a0-4b81-86a9-3a921de34318";
  const path = `/api/orders/${order}/engineering/redesign/${run}/files/Proposed-drawing.dxf`;
  assert.equal(redesignApiPath(path), path);
  assert.equal(
    redesignApiPath(path.replace("Proposed-drawing", "Drawing%20review")),
    path.replace("Proposed-drawing", "Drawing%20review"),
  );
  assert.throws(() => redesignApiPath("https://example.com/file"));
  assert.throws(() => redesignApiPath("//example.com/file"));
  assert.throws(() =>
    redesignApiPath(path.replace("Proposed-drawing.dxf", "%2e%2e%2fsecret")),
  );
  assert.throws(() => redesignApiPath(path + "?download=1"));
  assert.throws(() => redesignApiPath(path.replace(order, "not-a-uuid")));
});
