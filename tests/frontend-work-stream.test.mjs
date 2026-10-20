import test from "node:test";
import assert from "node:assert/strict";
import { buildWorkStream } from "../apps/web/src/work-stream.ts";

const order = {
  id: "order-a",
  number: "24-118",
  title: "Westbridge switchboard",
  updated_at: "2026-10-14T01:00:00Z",
};
const mail = {
  receipt_id: "receipt-a",
  order_id: order.id,
  order: order.number,
  subject: "Returned drawings, Rev C",
  sender: "reviewer@example.com",
  outlook_url: "https://outlook.office.com/mail/inbox/id/a",
  internet_message_id: "<a@example.com>",
};
const activity = (patch = {}) => ({
  summary: { emails_read: 1, needs_you: 0 },
  feed: [],
  waiting_in_outlook: [],
  needs_you: [],
  ...patch,
});
const item = (id, patch = {}) => ({
  id,
  order_id: order.id,
  kind: "proposal",
  proposal_id: id,
  updated_at: "2026-10-14T02:00:00Z",
  status: "ready",
  origin: "ai",
  title: "Prepared work",
  detail: "Drafts saved",
  review_required: false,
  prepared_outputs: 3,
  ...patch,
});

test("one email is one outcome despite arrival, preparation, and recorded drafts", () => {
  const rows = buildWorkStream(
    activity({
      feed: [
        {
          ...mail,
          kind: "read",
          at: "2026-10-14T01:00:00Z",
          detail: "Filed",
          preparation: { status: "ready" },
        },
        {
          ...mail,
          kind: "drafted",
          at: "2026-10-14T02:00:00Z",
          detail: "Drafted",
          draft_status: "created",
        },
      ],
      waiting_in_outlook: [
        {
          ...mail,
          drafted_at: "2026-10-14T02:00:00Z",
          attachments: ["E-10 Rev B.pdf"],
          origin: "rivet",
        },
      ],
    }),
    [item("p")],
    [order],
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].nativeDraft, true);
  assert.equal(rows[0].preparedOutputs, 3);
  assert.equal(rows[0].state, "ready");
});
test("identical subjects cannot merge different incoming emails", () => {
  const rows = buildWorkStream(
    activity({
      waiting_in_outlook: [
        { ...mail, drafted_at: null, attachments: [] },
        {
          ...mail,
          receipt_id: "receipt-b",
          internet_message_id: "<b@example.com>",
          drafted_at: null,
          attachments: [],
        },
      ],
    }),
    [],
    [order],
  );
  assert.equal(rows.length, 2);
});
test("a real decision stays visible alongside the prepared native reply", () => {
  const rows = buildWorkStream(
    activity({
      waiting_in_outlook: [{ ...mail, drafted_at: null, attachments: [] }],
    }),
    [
      item("p"),
      item("decision", {
        kind: "task",
        task_id: "scope",
        prepared_outputs: 0,
        status: "needs_review",
        review_required: true,
        detail: "Choose which approved scope applies.",
      }),
    ],
    [order],
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].state, "attention");
  assert.equal(rows[0].nativeDraft, true);
  assert.match(rows[0].detail, /approved scope/);
});

test("new email preparation stays visibly working while an older order conflict remains open", () => {
  for (const status of ["queued", "running"]) {
    const [row] = buildWorkStream(
      activity({
        feed: [
          {
            ...mail,
            kind: "read",
            at: "2026-10-15T06:47:50Z",
            detail: "Filed to order.",
            preparation: {
              status,
              detail:
                "Checking the connected sources before preparing this reply.",
            },
          },
        ],
      }),
      [
        item("previous-reply", {
          kind: "prepared_reply",
          prepared_outputs: 1,
          detail: "Previous response prepared.",
        }),
        item("scope", {
          kind: "task",
          status: "needs_review",
          review_required: true,
          prepared_outputs: 0,
          title: "Sources disagree",
          detail: "Confirm which specification governs.",
        }),
      ],
      [order],
    );
    assert.equal(row.state, "working");
    assert.equal(row.nativeDraft, false);
    assert.equal(row.preparedOutputs, 0);
    assert.equal(row.outcomeDetail, undefined);
    assert.match(row.detail, /Checking the connected sources/);
    assert.match(row.openPoint, /which specification governs/);
  }
});
test("the PM's existing draft is never described as written by AI", () => {
  const [row] = buildWorkStream(
    activity({
      waiting_in_outlook: [
        { ...mail, drafted_at: null, attachments: [], origin: "existing" },
      ],
    }),
    [],
    [order],
  );
  assert.equal(row.existingDraft, true);
  assert.equal(row.origin, "human");
  assert.match(row.detail, /preserved/);
});
test("match questions appear inline elsewhere without a duplicate feed task", () => {
  assert.equal(
    buildWorkStream(
      activity({
        needs_you: [
          {
            ...mail,
            order_id: null,
            order: null,
            kind: "which_job",
            detail: "Which order?",
          },
        ],
      }),
      [],
      [order],
    ).length,
    0,
  );
});
test("raw imported comments do not generate individual dashboard tasks", () => {
  assert.equal(
    buildWorkStream(
      undefined,
      [
        item("capture-a", {
          kind: "comment_review",
          comment_id: "c1",
          origin: "imported",
          prepared_outputs: 0,
        }),
        item("capture-b", {
          kind: "comment_review",
          comment_id: "c2",
          origin: "imported",
          prepared_outputs: 0,
        }),
      ],
      [order],
    ).length,
    0,
  );
});
test("sent or discarded drafts leave active work; processing appears only for real queued work", () => {
  const rows = buildWorkStream(
    activity({
      feed: [
        {
          ...mail,
          kind: "drafted",
          at: "2026-10-14T01:00:00Z",
          detail: "Old draft",
          draft_status: "discarded",
        },
        {
          ...mail,
          receipt_id: "receipt-b",
          kind: "read",
          at: "2026-10-14T02:00:00Z",
          detail: "Parsing",
          preparation: { status: "queued" },
        },
      ],
    }),
    [],
    [order],
  );
  assert.equal(rows.find((row) => row.id === "receipt-a").state, "done");
  assert.equal(rows.find((row) => row.id === "receipt-b").state, "working");
});
test("sent outcome and example approval come from explicit server facts", () => {
  const [row] = buildWorkStream(
    activity({
      feed: [
        {
          ...mail,
          kind: "sent",
          at: "2026-10-15T01:00:00Z",
          detail: "Sent after edits.",
          outcome: "edited",
          learning_status: "approved",
        },
      ],
    }),
    [],
    [order],
  );
  assert.equal(row.state, "done");
  assert.equal(row.origin, "human");
  assert.equal(row.outcome, "edited");
  assert.equal(row.learningStatus, "approved");
  const [unknown] = buildWorkStream(
    activity({
      feed: [
        {
          ...mail,
          kind: "sent",
          at: "2026-10-15T01:00:00Z",
          detail: "Sent after edits; learned.",
        },
      ],
    }),
    [],
    [order],
  );
  assert.equal(unknown.outcome, undefined);
  assert.equal(unknown.learningStatus, undefined);
});

test("mixed UTC and offset timestamps preserve the actual later Outlook send", () => {
  const [row] = buildWorkStream(
    activity({
      feed: [
        {
          ...mail,
          kind: "sent",
          at: "2026-10-14T23:53:37-07:00",
          detail: "Sent after edits.",
          outcome: "edited",
          learning_status: "approved",
        },
        {
          ...mail,
          kind: "read",
          at: "2026-10-15T06:46:17Z",
          detail: "Filed.",
          preparation: { status: "ready" },
        },
        {
          ...mail,
          kind: "drafted",
          at: "2026-10-14T23:48:11-07:00",
          detail: "Draft created.",
          draft_status: "sent",
        },
      ],
    }),
    [],
    [order],
  );
  assert.equal(row.state, "done");
  assert.equal(row.outcome, "edited");
  assert.equal(row.learningStatus, "approved");
  assert.equal(row.at, "2026-10-14T23:53:37-07:00");
});

test("latest active receipt and displayed order time compare instants across timezones", () => {
  const rows = buildWorkStream(
    activity({
      feed: [
        {
          ...mail,
          receipt_id: "old",
          kind: "read",
          at: "2026-10-15T06:46:17Z",
          detail: "Old mail",
          preparation: { status: "queued" },
        },
        {
          ...mail,
          receipt_id: "new",
          kind: "read",
          at: "2026-10-14T23:53:37-07:00",
          detail: "New mail",
          preparation: { status: "queued" },
        },
      ],
    }),
    [
      item("scope", {
        kind: "task",
        status: "needs_review",
        review_required: true,
        prepared_outputs: 0,
        detail: "Confirm the scope.",
      }),
    ],
    [order],
  );
  assert.equal(rows[0].id, "new");
  assert.match(rows[0].openPoint, /scope/);
  assert.equal(rows[1].openPoint, undefined);
  const [record] = buildWorkStream(
    undefined,
    [
      item("a", { updated_at: "2026-10-15T06:46:17Z" }),
      item("b", { updated_at: "2026-10-14T23:53:37-07:00" }),
    ],
    [order],
  );
  assert.equal(record.at, "2026-10-14T23:53:37-07:00");
});

test("invalid timestamps have stable ordering and cannot erase a confirmed send", () => {
  const rows = buildWorkStream(
    activity({
      feed: [
        {
          ...mail,
          receipt_id: "a",
          kind: "sent",
          at: null,
          detail: "Sent.",
          outcome: "edited",
        },
        {
          ...mail,
          receipt_id: "b",
          kind: "sent",
          at: "invalid",
          detail: "Sent.",
          outcome: "accepted",
        },
        {
          ...mail,
          receipt_id: "a",
          kind: "read",
          at: "invalid",
          detail: "Old mail",
        },
      ],
    }),
    [],
    [order],
  );
  assert.deepEqual(
    rows.map((row) => row.id),
    ["a", "b"],
  );
  assert.equal(rows[0].outcome, "edited");
});

test("a completed Outlook send cannot be overwritten by an older receipt preparation warning", () => {
  const [row] = buildWorkStream(
    activity({
      feed: [
        {
          ...mail,
          kind: "sent",
          at: "2026-10-15T06:47:52Z",
          detail: "Sent after edits.",
          outcome: "edited",
          learning_status: "pending_review",
        },
      ],
      needs_you: [
        {
          ...mail,
          kind: "blocked",
          detail: "Sources disagree and need a governing specification.",
        },
      ],
    }),
    [],
    [order],
  );
  assert.equal(row.state, "done");
  assert.equal(row.outcome, "edited");
  assert.equal(row.learningStatus, "pending_review");
  assert.equal(row.detail, "Sent after edits.");
  assert.match(row.openPoint, /governing specification/);
});
test("a placement failure overrides readiness and does not discard the order link", () => {
  const [row] = buildWorkStream(
    activity({
      waiting_in_outlook: [{ ...mail, drafted_at: null, attachments: [] }],
      needs_you: [
        {
          ...mail,
          kind: "failed",
          detail: "The evidence changed before placement.",
        },
      ],
    }),
    [],
    [order],
  );
  assert.equal(row.state, "attention");
  assert.equal(row.orderId, order.id);
  assert.match(row.detail, /evidence changed/);
});

test("a saved order reply does not become new active work after the PM sends it", () => {
  const sent = {
    ...mail,
    kind: "sent",
    at: "2026-10-15T01:00:00Z",
    detail: "Sent after edits.",
    outcome: "edited",
    learning_status: "approved",
  };
  const prepared = item("reply", {
    kind: "prepared_reply",
    prepared_outputs: 1,
  });
  const rows = buildWorkStream(activity({ feed: [sent] }), [prepared], [order]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].state, "done");
  const withDecision = buildWorkStream(
    activity({ feed: [sent] }),
    [
      prepared,
      item("decision", {
        kind: "task",
        status: "needs_review",
        review_required: true,
        prepared_outputs: 0,
        detail: "Confirm the governing contract scope.",
      }),
    ],
    [order],
  );
  assert.equal(withDecision.length, 2);
  assert.equal(
    withDecision.some((row) => row.state === "attention"),
    true,
  );
});

test("finished response is the outcome while an interrupted supporting task remains visible", () => {
  const [row] = buildWorkStream(
    undefined,
    [
      item("response", {
        kind: "prepared_reply",
        prepared_outputs: 1,
        detail: "Response prepared from 2 files and 9 comments.",
      }),
      item("old-task", {
        kind: "run",
        run_status: "failed",
        title:
          "SYSTEM: inspect every supplied source, process the event, call the tools, return structured JSON and never fabricate a citation.",
        prepared_outputs: 0,
        status: "needs_review",
        review_required: true,
        detail: "One supporting investigation stopped before completion.",
      }),
    ],
    [order],
  );
  assert.equal(row.state, "ready");
  assert.match(row.outcomeDetail, /9 comments/);
  assert.match(row.openPoint, /stopped/);
  assert.equal(row.openPointTitle, "Additional preparation needs retry");
  assert.doesNotMatch(row.openPointTitle, /SYSTEM|structured JSON/);
});

test("a prepared response cannot conceal a genuine commercial decision", () => {
  const [row] = buildWorkStream(
    undefined,
    [
      item("response", {
        kind: "prepared_reply",
        prepared_outputs: 1,
        detail: "Response prepared from current evidence.",
      }),
      item("scope", {
        kind: "task",
        prepared_outputs: 0,
        status: "needs_review",
        review_required: true,
        detail: "Commercial approval is still required.",
      }),
    ],
    [order],
  );
  assert.equal(row.state, "attention");
  assert.match(row.outcomeDetail, /Response prepared/);
  assert.match(row.openPoint, /Commercial/);
});

const lifecycleResult = () =>
  item("lifecycle", {
    kind: "lifecycle",
    origin: "automatic",
    prepared_outputs: 4,
    detail:
      "7 answers drafted · 3 drawing/BOM fields edited · 1 located drafting task · $1,200.00 proposed scope addition.",
    lifecycle: {
      version: 12,
      mode: "local_demo",
      prepared_label: "4 work packages · Local test adapters",
      open_point:
        "PO price differs. Approval needed: engineering, price, scope.",
      packages: [],
    },
  });

test("actual lifecycle work replaces the generic draft count without concealing a failed supporting task", () => {
  const [row] = buildWorkStream(
    undefined,
    [
      lifecycleResult(),
      item("response", {
        kind: "prepared_reply",
        prepared_outputs: 1,
        detail: "Response prepared from 6 files.",
      }),
      item("generic-drafts", { prepared_outputs: 8 }),
      item("failed", {
        kind: "run",
        run_status: "failed",
        status: "needs_review",
        prepared_outputs: 0,
        detail: "Additional preparation did not finish.",
      }),
    ],
    [order],
  );
  assert.equal(row.state, "ready");
  assert.match(row.outcomeDetail, /7 answers.*3 drawing.*\$1,200/);
  assert.equal(row.preparedOutputs, 4);
  assert.equal(row.preparedLabel, "4 work packages · Local test adapters");
  assert.match(row.openPoint, /PO price differs/);
  assert.match(row.openPoint, /did not finish/);
});

test("lifecycle results never overwrite actual Outlook sent and working states", () => {
  const sent = buildWorkStream(
    activity({
      feed: [
        {
          ...mail,
          at: "2026-10-15T00:00:00Z",
          kind: "sent",
          draft_status: "sent",
          outcome: "edited",
          learning_status: "approved",
          detail: "Sent after edits.",
        },
      ],
    }),
    [lifecycleResult()],
    [order],
  ).find((row) => row.source === "mail");
  assert.equal(sent.state, "done");
  assert.equal(sent.outcome, "edited");
  assert.equal(sent.outcomeDetail, undefined);
  const working = buildWorkStream(
    activity({
      feed: [
        {
          ...mail,
          at: "2026-10-15T01:00:00Z",
          kind: "read",
          detail: "Filed",
          preparation: { status: "running", detail: "Reading the new return." },
        },
      ],
    }),
    [lifecycleResult()],
    [order],
  );
  assert.equal(working.length, 1);
  assert.equal(working[0].state, "working");
  assert.equal(working[0].detail, "Reading the new return.");
  assert.equal(working[0].outcomeDetail, undefined);
});

test("lifecycle outcome leaves a real native draft ready in Outlook with its actual attachment", () => {
  const [row] = buildWorkStream(
    activity({
      waiting_in_outlook: [
        {
          ...mail,
          drafted_at: "2026-10-15T01:00:00Z",
          attachments: ["Released E-10 Rev B.pdf"],
        },
      ],
    }),
    [lifecycleResult()],
    [order],
  );
  assert.equal(row.nativeDraft, true);
  assert.equal(row.state, "ready");
  assert.deepEqual(row.attachments, ["Released E-10 Rev B.pdf"]);
  assert.match(row.outcomeDetail, /7 answers/);
});

const approvedQuote = (patch = {}) =>
  item("lifecycle-quote", {
    kind: "lifecycle",
    origin: "automatic",
    detail: "Quote and exceptions prepared.",
    prepared_outputs: 1,
    lifecycle: {
      version: 7,
      mode: "local_demo",
      open_point: "",
      prepared_label: "1 work package",
      packages: [
        {
          id: "quote-v1",
          status: "approved",
          fingerprint: "quote-hash",
          evidence_current: true,
        },
      ],
    },
    ...patch,
  });
const sentQuote = (patch = {}) => ({
  ...mail,
  kind: "sent",
  draft_status: "sent",
  at: "2026-10-15T03:00:00Z",
  outcome: "accepted",
  detail: "Sent as Rivet drafted it.",
  coverage: { package_id: "quote-v1" },
  ...patch,
});

test("an exact approved package sent from Outlook has one finished receipt instead of a second prepared lifecycle row", () => {
  for (const outcome of ["accepted", "edited"]) {
    const rows = buildWorkStream(
      activity({ feed: [sentQuote({ outcome })] }),
      [approvedQuote()],
      [order],
    );
    assert.equal(rows.length, 1);
    assert.equal(rows[0].id, mail.receipt_id);
    assert.equal(rows[0].state, "done");
    assert.equal(rows[0].outcome, outcome);
  }
});

test("a sent email cannot hide a different package, unverified send or existing human reply", () => {
  for (const changed of [
    { coverage: { package_id: "another-package" } },
    { coverage: undefined },
    { draft_status: "created" },
    { kind: "read" },
    { outcome: "external" },
    { order_id: "different-order" },
  ]) {
    const rows = buildWorkStream(
      activity({ feed: [sentQuote(changed)] }),
      [approvedQuote()],
      [order],
    );
    assert.equal(
      rows.some((row) => row.id === `order:${order.id}`),
      true,
    );
  }
});

test("an older sent package cannot hide newer unsent or stale lifecycle work and open decisions", () => {
  for (const changed of [
    { open_point: "PO scope still needs a decision." },
    {
      packages: [
        {
          id: "quote-v2",
          status: "approved",
          fingerprint: "new",
          evidence_current: true,
        },
      ],
    },
    {
      packages: [
        {
          id: "quote-v1",
          status: "awaiting_decision",
          fingerprint: "quote-hash",
          evidence_current: true,
        },
      ],
    },
    {
      packages: [
        {
          id: "quote-v1",
          status: "stale",
          fingerprint: "quote-hash",
          evidence_current: false,
        },
      ],
    },
    {
      packages: [
        {
          id: "quote-v1",
          status: "approved",
          fingerprint: "quote-hash",
          evidence_current: true,
        },
        {
          id: "kickoff-v1",
          status: "approved",
          fingerprint: "kickoff",
          evidence_current: true,
        },
      ],
    },
  ]) {
    const quote = approvedQuote();
    quote.lifecycle = { ...quote.lifecycle, ...changed };
    const rows = buildWorkStream(
      activity({ feed: [sentQuote()] }),
      [quote],
      [order],
    );
    assert.equal(
      rows.some((row) => row.id === `order:${order.id}`),
      true,
    );
  }
});

test("coalescing a sent package keeps another receipt's preserved human draft and a real order task", () => {
  const preserved = {
    ...mail,
    receipt_id: "manual-reply",
    internet_message_id: "<manual@example.com>",
    drafted_at: "2026-10-15T04:00:00Z",
    attachments: [],
    origin: "existing",
  };
  const manualRows = buildWorkStream(
    activity({ feed: [sentQuote()], waiting_in_outlook: [preserved] }),
    [approvedQuote()],
    [order],
  );
  assert.equal(manualRows.length, 2);
  assert.equal(
    manualRows.find((row) => row.id === "manual-reply").existingDraft,
    true,
  );
  assert.equal(
    manualRows.find((row) => row.id === "manual-reply").preparedOutputs,
    0,
  );
  const decisionRows = buildWorkStream(
    activity({ feed: [sentQuote()] }),
    [
      approvedQuote(),
      item("scope-decision", {
        kind: "task",
        status: "needs_review",
        review_required: true,
        prepared_outputs: 0,
        detail: "Resolve the PO scope exception.",
      }),
    ],
    [order],
  );
  assert.equal(decisionRows.length, 2);
  assert.match(
    decisionRows.find((row) => row.id === `order:${order.id}`).detail,
    /PO scope exception/,
  );
});
