import test from "node:test";
import assert from "node:assert/strict";
import {
  followAutomaticPreparation,
  initialReplyAttachments,
  nativeDraftOwnsReply,
  nativeDraftNotice,
  safeOutlookUrl,
  editableNativeDraftUrl,
  workspaceSourceUrl,
} from "../apps/web/src/outlook-workflow.ts";

test("saved marketing-origin citations open the exact evidence on the private workspace", () => {
  const old =
    "http://127.0.0.1:5178/#order/order-a?tab=work&source=source-b&comment=comment-c";
  const expected = { orderId: "order-a", sourceId: "source-b" };
  assert.equal(
    workspaceSourceUrl(old, "https://localhost:5179/#settings", expected),
    "https://localhost:5179/#order/order-a?tab=work&source=source-b&comment=comment-c",
  );
  assert.equal(
    workspaceSourceUrl(
      "https://rivet-usa.com/#order/order-a?source=source-b",
      "https://workspace.rivet-usa.com/",
      expected,
    ),
    "https://workspace.rivet-usa.com/#order/order-a?tab=work&source=source-b",
  );
  assert.equal(
    workspaceSourceUrl(
      "#order/order-a?tab=comments&source=source-b",
      "https://localhost:5179/#settings",
      expected,
    ),
    "https://localhost:5179/#order/order-a?tab=work&source=source-b",
  );
});

test("citation normalization rejects unrelated external destinations and mismatched evidence", () => {
  const base = "https://localhost:5179/";
  const expected = { orderId: "order-a", sourceId: "source-b" };
  for (const value of [
    "https://tenant.sharepoint.com/sites/demo/spec.pdf",
    "https://unrelated.example/#order/order-a?source=source-b",
    "https://rivet-usa.com.evil.test/#order/order-a?source=source-b",
    "https://user:secret@rivet-usa.com/#order/order-a?source=source-b",
    "javascript:alert(1)",
    "https://rivet-usa.com/#order/other-order?source=source-b",
    "https://rivet-usa.com/#order/order-a?source=other-source",
    "https://rivet-usa.com/#order/order-a?source=source-b&source=other-source",
    "https://rivet-usa.com/#order/order-a?source=..%2F..%2Fadmin",
    "https://rivet-usa.com/#order/order-a",
    "https://rivet-usa.com/#settings",
  ])
    assert.equal(workspaceSourceUrl(value, base, expected), null, value);
  assert.equal(
    workspaceSourceUrl(
      "#order/order-a?source=source-b",
      "javascript:alert(1)",
      expected,
    ),
    null,
  );
});

test("verified Microsoft draft links open the same draft directly in compose", () => {
  assert.equal(
    editableNativeDraftUrl(
      "https://outlook.office365.com/owa/?ItemID=AAk%2B%2Fdraft%3D&exvsurl=1",
    ),
    "https://outlook.cloud.microsoft/mail/compose/AAk%2B%2Fdraft%3D",
  );
  assert.equal(
    editableNativeDraftUrl(
      "https://outlook.office365.com/owa/?ItemID=legacy",
      "AAk+/draft=",
    ),
    "https://outlook.cloud.microsoft/mail/compose/AAk%2B%2Fdraft%3D",
  );
  assert.equal(
    editableNativeDraftUrl(
      "https://outlook.cloud.microsoft/mail/deeplink/read/draft-id",
    ),
    "https://outlook.cloud.microsoft/mail/compose/draft-id",
  );
  assert.equal(
    editableNativeDraftUrl(
      "https://outlook.office365.com/mail/deeplink/read/draft-id?ItemID=draft-id&exvsurl=1",
    ),
    "https://outlook.cloud.microsoft/mail/compose/draft-id",
  );
  assert.equal(
    editableNativeDraftUrl("https://outlook.office.com/mail/inbox/id/email-id"),
    "https://outlook.office.com/mail/inbox/id/email-id",
  );
  assert.equal(
    editableNativeDraftUrl(
      "https://outlook.office.com.attacker.test/mail/deeplink/read/id",
    ),
    null,
  );
  assert.equal(
    editableNativeDraftUrl(
      "https://user:pass@outlook.office.com/mail/deeplink/read/id",
    ),
    null,
  );
});

test("pane follows background matching and preparation without another generation request", () => {
  for (const status of ["queued", "running"]) {
    assert.equal(followAutomaticPreparation(true, "matched", status), true);
  }
  assert.equal(
    followAutomaticPreparation(true, "not_captured", "queued"),
    true,
  );
});

test("quiet mail, a real decision, and terminal failures never masquerade as ongoing work", () => {
  for (const status of [
    "not_order_related",
    "set_aside",
    "needs_confirmation",
    "excluded_draft",
    "excluded_internal_update",
  ]) {
    assert.equal(followAutomaticPreparation(true, status, "queued"), false);
  }
  for (const status of [
    "failed",
    "blocked",
    "not_needed",
    "ready",
    "reviewed",
  ]) {
    assert.equal(followAutomaticPreparation(true, "matched", status), false);
  }
  assert.equal(followAutomaticPreparation(false, "matched", "queued"), false);
});

test("only server recommended and available files are preselected", () => {
  assert.deepEqual(
    initialReplyAttachments({
      status: "ready",
      recommended_attachment_ids: ["released", "missing"],
      attachment_candidates: [
        { document_id: "released", eligible: true },
        { document_id: "missing", eligible: false },
        { document_id: "internal-price", eligible: true },
      ],
    }),
    ["released"],
  );
});

test("restoring an approved reply preserves its explicit attachment choice including none", () => {
  const draft = {
    status: "approved",
    recommended_attachment_ids: ["released"],
    attachment_candidates: [{ document_id: "released", eligible: true }],
  };
  assert.deepEqual(initialReplyAttachments(draft), []);
  assert.deepEqual(
    initialReplyAttachments({
      ...draft,
      selected_attachments: [{ document_id: "reviewed" }],
    }),
    ["reviewed"],
  );
});

const { reviewAndInsertReply } =
  await import("../apps/web/src/outlook-workflow.ts");
function replyFlow(overrides = {}) {
  const calls = [];
  const reviewed = {
    status: "approved",
    body: "PM edited wording",
    attachment_ids: ["released"],
  };
  return {
    calls,
    options: {
      draft: { status: "ready" },
      isCurrent: () => true,
      validateMessage: () => calls.push("validate"),
      saveReview: async () => {
        calls.push("review");
        return reviewed;
      },
      onReviewed: (saved) => {
        calls.push("saved");
        assert.deepEqual(saved, reviewed);
      },
      compose: async (saved) => {
        calls.push("compose");
        return { body: saved.body, attachments: saved.attachment_ids };
      },
      insert: async (composed) => {
        calls.push("insert");
        assert.deepEqual(composed, {
          body: "PM edited wording",
          attachments: ["released"],
        });
      },
      ...overrides,
    },
  };
}

test("one insert action uses the saved PM wording/files and validates every asynchronous boundary", async () => {
  const { calls, options } = replyFlow();
  await reviewAndInsertReply(options);
  assert.deepEqual(calls, [
    "validate",
    "review",
    "validate",
    "saved",
    "compose",
    "validate",
    "insert",
  ]);
});

test("switching emails during review never composes or overwrites the newly selected message", async () => {
  let current = true;
  const { calls, options } = replyFlow({
    isCurrent: () => current,
    saveReview: async () => {
      current = false;
      return { status: "approved" };
    },
  });
  assert.equal(await reviewAndInsertReply(options), null);
  assert.deepEqual(calls, ["validate"]);
});

test("switching emails during compose never inserts into the new email", async () => {
  let current = true;
  const { calls, options } = replyFlow({
    isCurrent: () => current,
    compose: async () => {
      current = false;
      return {};
    },
  });
  assert.equal(await reviewAndInsertReply(options), null);
  assert.equal(calls.includes("insert"), false);
});

test("a stale message identity blocks insertion even if Outlook did not dispatch ItemChanged", async () => {
  let identityChecks = 0;
  const { calls, options } = replyFlow({
    validateMessage: () => {
      if (++identityChecks === 3) throw new Error("selected email changed");
    },
  });
  await assert.rejects(reviewAndInsertReply(options), /selected email changed/);
  assert.equal(calls.includes("insert"), false);
});

test("compose failure keeps the approved reply retryable without re-reviewing or losing edits", async () => {
  let saved;
  const { options } = replyFlow({
    onReviewed: (result) => {
      saved = result;
    },
    compose: async () => {
      throw new Error("Outlook temporarily unavailable");
    },
  });
  await assert.rejects(
    reviewAndInsertReply(options),
    /temporarily unavailable/,
  );
  assert.equal(saved.status, "approved");
  const retried = replyFlow({
    draft: saved,
    saveReview: async () => {
      throw new Error("must not re-review");
    },
  });
  assert.equal(
    (await reviewAndInsertReply(retried.options)).body,
    "PM edited wording",
  );
  assert.equal(retried.calls.includes("insert"), true);
});

test("stale evidence rejected by compose is never inserted", async () => {
  const { calls, options } = replyFlow({
    compose: async () => {
      throw new Error("Evidence changed. Prepare a new draft.");
    },
  });
  await assert.rejects(reviewAndInsertReply(options), /Evidence changed/);
  assert.equal(calls.includes("insert"), false);
});

test("a native draft placed while the pane was open prevents manual review and duplicate insertion", async () => {
  for (const status of [
    "pending",
    "creating",
    "created",
    "existing_draft",
    "sent",
    "discarded",
    "superseded",
  ]) {
    const { calls, options } = replyFlow({
      canInsert: async () => !nativeDraftOwnsReply(status),
    });
    assert.equal(await reviewAndInsertReply(options), null);
    assert.deepEqual(calls, ["validate"]);
    assert.ok(nativeDraftNotice(status));
  }
});

test("safe manual fallback remains available after a known permission or placement failure", async () => {
  for (const status of [undefined, "permission_required", "failed"]) {
    const { calls, options } = replyFlow({
      canInsert: async () => !nativeDraftOwnsReply(status),
    });
    await reviewAndInsertReply(options);
    assert.ok(calls.includes("insert"));
  }
});

test("failed placement lookup never guesses that no Outlook draft exists", async () => {
  const { calls, options } = replyFlow({
    canInsert: async () => {
      throw new Error("Mailbox check unavailable");
    },
  });
  await assert.rejects(
    reviewAndInsertReply(options),
    /Mailbox check unavailable/,
  );
  assert.deepEqual(calls, ["validate"]);
});

test("switching emails during the saved-draft lookup stops insertion", async () => {
  let current = true;
  const { calls, options } = replyFlow({
    isCurrent: () => current,
    canInsert: async () => {
      current = false;
      return true;
    },
  });
  assert.equal(await reviewAndInsertReply(options), null);
  assert.deepEqual(calls, ["validate"]);
});

test("Outlook links reject arbitrary or credentialed destinations", () => {
  assert.equal(
    safeOutlookUrl("https://outlook.cloud.microsoft/mail/deeplink/read/draft"),
    "https://outlook.cloud.microsoft/mail/deeplink/read/draft",
  );
  assert.equal(
    safeOutlookUrl("https://outlook.office.com/mail/id/draft-123"),
    "https://outlook.office.com/mail/id/draft-123",
  );
  for (const value of [
    undefined,
    "javascript:alert(1)",
    "http://outlook.office.com/mail",
    "https://outlook.office.com.evil.test/mail",
    "https://user:secret@outlook.office.com/mail",
    "https://example.test/",
  ]) {
    assert.equal(safeOutlookUrl(value), null);
  }
});

const { intakeRevisionForConfirmation } =
  await import("../apps/web/src/outlook-workflow.ts");
test("conflicting revision labels require a specific choice rather than silently using Unspecified", () => {
  for (const selected of ["", "Unspecified", "D"]) {
    assert.throws(
      () => intakeRevisionForConfirmation({ revisions: ["B", "C"] }, selected),
      /Choose the returned revision/,
    );
  }
  assert.equal(
    intakeRevisionForConfirmation({ revisions: ["B", "C"] }, "C"),
    "C",
  );
  assert.equal(
    intakeRevisionForConfirmation({ revision: "C", revisions: ["C"] }),
    "C",
  );
  assert.equal(intakeRevisionForConfirmation({}), "Unspecified");
});
