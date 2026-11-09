import test from "node:test";
import assert from "node:assert/strict";
import {
  orderReviewFromRoute,
  orderReviewLabel,
  orderReviewRoute,
  outlookReplyStatus,
  drawingReviewNotice,
} from "../apps/web/src/order-flow.ts";

test("order workbench route selects each customer workflow view and defaults safely", () => {
  assert.equal(orderReviewFromRoute("order/ord-1"), "requests");
  assert.equal(orderReviewFromRoute("order/ord-1?review=changes"), "changes");
  assert.equal(orderReviewFromRoute("order/ord-1?review=reply"), "reply");
  assert.equal(orderReviewFromRoute("order/ord-1?review=engineer"), "engineer");
  assert.equal(orderReviewFromRoute("order/ord-1?review=unknown"), "requests");
  assert.equal(orderReviewFromRoute("order/ord-1?tab=comments"), "requests");
});

test("workbench tabs produce stable order routes and clear labels", () => {
  assert.equal(
    orderReviewRoute("ord/one", "changes"),
    "order/ord%2Fone?review=changes",
  );
  assert.equal(orderReviewLabel("requests"), "Customer requests");
  assert.equal(orderReviewLabel("changes"), "Rivet’s changes");
  assert.equal(orderReviewLabel("reply"), "Outlook reply");
  assert.equal(orderReviewLabel("engineer"), "Engineer review");
});

test("reply status distinguishes a saved Outlook draft, an email thread, and no saved draft", () => {
  assert.equal(outlookReplyStatus({ nativeDraft: true }), "Draft in Outlook");
  assert.equal(
    outlookReplyStatus({ nativeDraft: false, preservedDraft: true }),
    "Draft needs update",
  );
  assert.equal(
    outlookReplyStatus({
      nativeDraft: false,
      threadUrl: "https://outlook.test/thread",
    }),
    "Open email thread",
  );
  assert.equal(
    outlookReplyStatus({ nativeDraft: false }),
    "Draft not saved in Outlook",
  );
  assert.equal(
    outlookReplyStatus({ nativeDraft: false, lastDirection: "sent" }),
    "Reply sent",
  );
  assert.equal(
    outlookReplyStatus({
      nativeDraft: false,
      lastDirection: "received",
      waitingForCustomer: true,
      lastSentAt: "2026-11-08T00:00:00Z",
    }),
    "Reply sent",
  );
});

test("partial, stale, and failed drawing runs remain visible beside a ready order reply", () => {
  assert.equal(
    drawingReviewNotice({
      current: true,
      status: "needs_information",
      totals: { needs_information: 1 },
    }),
    "Drawing review incomplete",
  );
  assert.equal(
    drawingReviewNotice({
      current: false,
      status: "ready_for_review",
      totals: { needs_information: 0 },
    }),
    "Drawing review incomplete",
  );
  assert.equal(
    drawingReviewNotice({
      current: true,
      status: "ready_for_review",
      totals: { needs_information: 0 },
      remaining_issues: [],
    }),
    "Drawing review awaits engineer approval",
  );
});
