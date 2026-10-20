import test from "node:test";
import assert from "node:assert/strict";
import {
  pmSentence,
  eventSentence,
  orderStory,
  decisionItems,
  decideItem,
  liveWork,
  rivetToday,
  atRisk,
  todayReplies,
  activityActor,
} from "../apps/web/src/work-presentation.ts";
const date = "2026-10-15T10:00:00Z";
const order = {
  id: "order-1",
  number: "24118",
  name: "Meridian switchboard",
  status: "ready",
  version: 7,
  updated_at: date,
  open_points: [],
  last_sent_at: date,
  communications: [],
};
const event = (id, kind, title, detail = "", at = date) => ({
  id,
  kind,
  title,
  detail,
  at,
  order_id: "order-1",
  number: "24118",
  outlook_url: "",
});
const pack = {
  id: "p1",
  title: "New breaker requested by the customer",
  prepared_at: date,
  status: "awaiting_decision",
  evidence_current: true,
  blockers: [],
  pending_gates: ["money", "scope"],
  fingerprint: "current-evidence",
  sources: {},
  payload: { total: "1200", currency: "USD" },
  artifacts: [],
  receipts: [],
};
test("Today surfaces ready replies without turning watching or working orders into decisions", () => {
  assert.deepEqual(
    todayReplies([
      order,
      { ...order, id: "2", status: "handling" },
      { ...order, id: "3", status: "watching" },
      { ...order, id: "4", status: "prepared" },
    ]).map((x) => x.id),
    ["order-1", "4"],
  );
});
test("each approval requests one specific authority, preserves price, and excludes stale or blocked work", () => {
  const items = decisionItems(
    [
      pack,
      { ...pack, id: "stale", evidence_current: false },
      { ...pack, id: "blocked", blockers: ["Missing evidence"] },
      { ...pack, id: "approved", status: "approved" },
    ],
    order,
  );
  assert.equal(items.length, 2);
  assert.equal(items[0].title, "Approve $1,200 change order");
  assert.equal(items[1].gate, "scope");
});
test("Today uses the existing approval card path, with no send, publish or cross-authority action", async () => {
  const item = decisionItems([pack], order)[0],
    calls = [];
  await decideItem(item, "approve", async (path, body) => {
    calls.push([path, body]);
    return { id: "card-1", order_version: 7, fingerprint: "current-evidence" };
  });
  assert.deepEqual(calls, [
    [
      "/orders/order-1/approval-cards",
      { package_id: "p1", gate: "money", channel: "outlook" },
    ],
    ["/approval-cards/card-1/decision", { decision: "approve" }],
  ]);
});
test("an expired, unauthorized or stale approval stays an error instead of becoming a local approval", async () => {
  const item = decisionItems([pack], order)[0];
  for (const message of ["expired", "403 named approver", "409 order changed"])
    await assert.rejects(
      decideItem(item, "approve", async (path) => {
        if (path.endsWith("/decision")) throw new Error(message);
        return {
          id: "card",
          order_version: 7,
          fingerprint: "current-evidence",
        };
      }),
      new RegExp(message),
    );
});
test("timeline is chronological, deduplicates sent confirmations and describes prepared work without claiming publication", () => {
  const emails = [
    {
      id: "sent1",
      direction: "sent",
      at: "2026-10-15T12:00:00Z",
      sender: "PM",
      subject: "Reply",
      body: "Thanks",
    },
  ];
  const rows = orderStory(
    { ...order, communications: emails },
    [
      event(
        "late",
        "received",
        "File received · Rev C.pdf",
        "",
        "2026-10-15T11:00:00Z",
      ),
      event("sent1", "sent", "Reply sent"),
      event(
        "early",
        "work",
        "Prepared",
        "Answered 7 comments.",
        "2026-10-15T09:00:00Z",
      ),
    ],
    [pack],
  );
  assert.deepEqual(
    rows.slice(0, 4).map((x) => x.id),
    ["event:work:early", "prepared:p1", "event:received:late", "email:sent1"],
  );
  assert.equal(rows.at(-2).state, "current");
  assert.equal(rows.at(-1).state, "upcoming");
  assert.equal(
    rows.filter((x) => x.email?.id === "sent1" || x.raw?.id === "sent1").length,
    1,
  );
  assert.ok(!rows.some((x) => /published|released in SAP/i.test(x.title)));
});
test("technical labels never leak onto the PM story; the original remains available in its drawer", () => {
  for (const word of [
    "receipt",
    "hash",
    "snapshot",
    "gate",
    "adapter",
    "provenance",
    "Approved in test",
  ])
    assert.equal(pmSentence(`Created ${word}`), "Rivet updated the order.");
  const raw = event(
    "1",
    "event",
    "Execution receipts and source versions",
    "raw hash: 123",
  );
  const row = orderStory(order, [raw])[0];
  assert.equal(row.raw.detail, "raw hash: 123");
  assert.equal(row.title, "Details");
  assert.equal(
    eventSentence(event("2", "attention", "Insufficient token allowance")),
    "Rivet paused this task before finishing. Open it to see what’s missing.",
  );
});
test("live progress comes only from active runs and actual completed comment counts", () => {
  assert.equal(
    liveWork([
      {
        status: "completed",
        comment_progress: { total: 11, prepared: 11, failed: 0, working: 0 },
      },
    ]),
    null,
  );
  assert.deepEqual(
    liveWork([
      {
        status: "running",
        comment_progress: { total: 11, prepared: 7, failed: 1, working: 3 },
      },
    ]),
    {
      text: "Rivet is working: 7 of 11 comments prepared · 1 need another look.",
      total: 11,
      completed: 7,
    },
  );
});
test("Rivet today groups repeated actions in one order, never people or failed tasks or old work", () => {
  const work = event("1", "work", "Completed", "Answered seven comments.");
  const groups = rivetToday(
    [
      work,
      { ...work, id: "2" },
      { ...work, id: "old", at: "2025-01-01T00:00:00Z" },
      event("s", "sent", "Sent"),
      event("fail", "attention", "Failed"),
    ],
    Date.parse(date),
  );
  assert.equal(groups.length, 1);
  assert.equal(groups[0].count, 2);
  assert.equal(activityActor(work), "rivet");
});
test("At risk requires an actual due timer for the currently waiting reply, not merely waiting status", () => {
  const waiting = { ...order, status: "waiting_customer" },
    timer = {
      id: "t",
      order_id: "order-1",
      sent_at: date,
      due_at: "2026-10-18T10:00:00Z",
      status: "drafted",
      outlook_url: "",
    },
    now = Date.parse("2026-10-20T10:00:00Z");
  assert.equal(atRisk([waiting], [], now).length, 0);
  assert.equal(atRisk([waiting], [timer], Date.parse(date)).length, 0);
  assert.equal(atRisk([waiting], [timer], now)[0].drafted, true);
  assert.equal(atRisk([waiting], [timer], now)[0].days, 5);
  assert.equal(
    atRisk([{ ...waiting, last_sent_at: "2026-10-19T10:00:00Z" }], [timer], now)
      .length,
    0,
  );
  assert.equal(atRisk([order], [timer], now).length, 0);
});

test("approval cannot use a newer card to silently approve work the PM did not see", async () => {
  const item = decisionItems([pack], order)[0];
  let posts = 0;
  for (const card of [
    { id: "new", order_version: 8, fingerprint: "current-evidence" },
    { id: "new", order_version: 7, fingerprint: "other-evidence" },
  ]) {
    await assert.rejects(
      decideItem(item, "approve", async (path) => {
        if (path.endsWith("/decision")) posts++;
        return card;
      }),
      /order changed/,
    );
  }
  assert.equal(posts, 0);
});

test("connected work is one story entry and a sandbox never becomes a claimed vendor write", () => {
  const attempt = {
    id: "a",
    at: date,
    action_id: "action-a",
    action: "sharepoint.publish_release",
    status: "applied_local_simulation",
    receipt: {
      mode: "local_http_demo",
      status: "applied_local_simulation",
      action_id: "action-a",
      files: [],
    },
  };
  const execution = {
    mode: "local_http_demo",
    vendor_writes: false,
    order_id: "order-1",
    attempts: [attempt],
  };
  const events = [
    {
      ...event(
        "w",
        "event",
        "sharepoint.publish_release: applied_local_simulation",
      ),
      event_type: "workflow_action",
    },
  ];
  const rows = orderStory(order, events, [], execution),
    work = rows.find((r) => r.id === "connected-work");
  assert.ok(work.title.includes("rehearsed 1 update"));
  assert.equal(work.execution, execution);
  assert.equal(
    rows.filter((r) => r.raw?.event_type === "workflow_action").length,
    0,
  );
  assert.equal(work.state, "done");
  assert.deepEqual(work.relatedIds, ["event:event:w"]);
  assert.ok(!work.title.includes("Updated SharePoint"));
});

test("a blocked file publication is attention, never a completed action or production release", () => {
  const blocked = {
    ...event("pub", "event", "Enterprise publication: needs_reconciliation"),
    event_type: "enterprise_publication",
    actor: "rivet",
  };
  assert.equal(orderStory(order, [blocked])[0].state, "attention");
  assert.deepEqual(rivetToday([blocked], Date.parse(date)), []);
  const done = {
    ...blocked,
    id: "done",
    title: "Enterprise publication: published_copy",
  };
  assert.equal(eventSentence(done), "Rivet published the approved file copy.");
  assert.ok(!eventSentence(done).includes("production release"));
});

test("reply history uses the actual draft time, not the order update time", () => {
  const reply = {
    created_at: "2026-10-15T10:00:00Z",
    coverage: { documents_read: 2 },
    unresolved: [],
    source_refs: [],
  };
  const p = {
    ...order,
    updated_at: "2026-10-15T08:00:00Z",
    reply_preparation: { prepared_reply: reply },
  };
  const rows = orderStory(p, [
    event(
      "later",
      "work",
      "Work",
      "Checked the revised drawing.",
      "2026-10-15T11:00:00Z",
    ),
  ]);
  assert.equal(rows[0].id, "prepared-response");
  assert.equal(rows[1].id, "event:work:later");
  assert.equal(rows.find((r) => r.id === "current-reply").at, "");
});
test("legacy runs that say no work was prepared do not turn into green completed work", () => {
  const failed = event(
    "legacy",
    "work",
    "Rivet prepared the order work",
    "The prior staging attempts failed validation, so no change set was prepared.",
  );
  assert.deepEqual(rivetToday([failed], Date.parse(date)), []);
  assert.equal(
    orderStory({ ...order, status: "handling" }, [failed])[0].state,
    "attention",
  );
});

test("Rivet's work reads as specific sentences from server facts", async () => {
  const { preparedSentence, workingSentence } = await import(
    "../apps/web/src/work-presentation.ts"
  );
  const run = {
    ...event("r", "work", "Rivet prepared the order work", "Prepared 11 of 11."),
    facts: { comments_total: 11, comments_prepared: 11, sources: 6 },
  };
  assert.equal(
    eventSentence(run),
    "Rivet drafted answers to 11 of 11 comments using 6 sources.",
  );
  const review = {
    operation: "revision_review",
    counts: { answer: 7, text_edit: 3, layout_task: 1 },
    change_order_total: "1200.00",
  };
  assert.equal(
    preparedSentence(review, "11 comments processed"),
    "Rivet drafted answers to 7 comments, edited 3 drawing copies, sent 1 layout change to a drafter and priced the extra scope at $1,200.",
  );
  assert.equal(
    preparedSentence({ operation: "kickoff", purchase_order_number: "PO-77", mismatches_count: 2 }, "x"),
    "Rivet checked PO-77 against the quote and found 2 differences.",
  );
  assert.equal(
    preparedSentence(
      { operation: "purchasing", part_number: "CB-EXTRA", lead_time_days: 42, current_ship_date: "2026-11-04", proposed_ship_date: "2026-11-26" },
      "x",
    ),
    "Rivet flagged CB-EXTRA: a 42-day lead time moves the ship date from Nov 4 to Nov 26.",
  );
  const rehearsal = {
    ...event("w", "event", "sap.draft_sales_order: applied_local_simulation (local HTTP simulation)"),
    event_type: "workflow_action",
    facts: { action: "sap.draft_sales_order", mode: "local_http_demo" },
  };
  assert.equal(
    eventSentence(rehearsal),
    "Rivet rehearsed a step in ERP: draft order saved.",
  );
  const live = workingSentence([
    { order_id: "a", number: "24118", status: "running", comments_total: 11, comments_prepared: 6 },
    { order_id: "b", number: "24119", status: "queued", comments_total: 0, comments_prepared: 0 },
  ]);
  assert.equal(live.text, "Rivet is working on 24118: 6 of 11 comments prepared · 1 more order.");
  assert.equal(live.total, 11);
  assert.equal(workingSentence([], 0), null);
});

test("decision titles name the customer, price and what changed", () => {
  const review = {
    ...pack,
    operation: "revision_review",
    pending_gates: ["money", "engineering", "customer_commitment"],
    payload: {
      counts: { answer: 7, text_edit: 3, layout_task: 1 },
      change_order: { total: "1200.00", lines: [{ description: "Extra breaker" }] },
    },
  };
  const items = decisionItems([review], { ...order, customer: "Meridian" });
  assert.deepEqual(
    items.map((i) => i.title),
    [
      "Approve $1,200 change order for Meridian",
      "Check 3 drawing edits for Meridian",
      "Approve the reply to Meridian",
    ],
  );
  const quote = decisionItems(
    [{ ...pack, operation: "quote", pending_gates: ["money"], payload: { total: "1600", currency: "USD" } }],
    order,
  );
  assert.equal(quote[0].title, "Approve the $1,600 quote");
});
