import test from "node:test";
import assert from "node:assert/strict";
import {
  orderOutcome,
  replyPreview,
  todayWorkSummary,
} from "../apps/web/src/workspace-outcomes.ts";

test("a prepared response does not become a native Outlook draft without a confirmed ready state", () => {
  const order = {
    status: "prepared",
    outlook: { native: true },
    last_sent_at: null,
  };
  assert.equal(orderOutcome(order).label, "Response prepared");
  assert.equal(
    orderOutcome({ ...order, status: "ready" }).label,
    "Ready in Outlook",
  );
  assert.equal(
    orderOutcome({ ...order, status: "ready", outlook: { native: false } })
      .label,
    "Response prepared",
  );
});
test("waiting status cannot claim a reply was sent without a sent timestamp", () => {
  const order = {
    status: "waiting_customer",
    outlook: { native: false },
    last_sent_at: null,
  };
  assert.equal(orderOutcome(order).label, "Watching for updates");
  assert.equal(
    orderOutcome({ ...order, last_sent_at: "2026-10-17T10:00:00Z" }).label,
    "Waiting for customer",
  );
});
test("prepared work stays distinct from decisions and no activity is invented for an empty workspace", () => {
  assert.equal(
    todayWorkSummary({ ready: 2, prepared: 1, decisions: 0, working: 0 }),
    "2 reply drafts ready in Outlook · 1 response prepared from files.",
  );
  assert.equal(
    todayWorkSummary({ ready: 0, prepared: 0, decisions: 1, working: 0 }),
    "1 decision needs you.",
  );
  assert.equal(
    todayWorkSummary({ ready: 0, prepared: 0, decisions: 0, working: 0 }),
    "Nothing needs your attention right now.",
  );
});
test("reply previews come from actual prepared text and remain bounded", () => {
  assert.equal(replyPreview({ reply_preparation: null }), "");
  const order = {
    reply_preparation: {
      prepared_reply: {
        body: "Hello,\n\nThe [BOM](https://example.com) still lists TU-2500. **Please confirm.**",
      },
    },
  };
  assert.equal(
    replyPreview(order),
    "The BOM still lists TU-2500. Please confirm.",
  );
  assert.ok(replyPreview(order, 20).length <= 21);
});
