import test from "node:test";
import assert from "node:assert/strict";
import {
  openOutlookReply,
  readOutlookContext,
  replyHtml,
  requireCapturedOutlookMessage,
  canAttachOutlookFiles,
  canOpenSavedOutlookDraft,
  openSavedOutlookDraft,
  openSavedOutlookLink,
} from "../apps/web/src/outlook-bridge.ts";

function runtime(reply, itemKey = "message-A") {
  return {
    onReady: async () => ({ host: "Outlook" }),
    context: {
      requirements: {
        isSetSupported: (name, version) =>
          name === "Mailbox" && version === "1.9",
      },
      mailbox: {
        item: {
          itemId: itemKey,
          internetMessageId: "<a@example.com>",
          subject: "Returned drawing",
          from: { displayName: "Reviewer" },
          displayReplyFormAsync: reply,
        },
      },
    },
  };
}

test("browser preview cannot claim access to an Outlook message", () => {
  assert.deepEqual(readOutlookContext(), {
    mode: "preview",
    itemKey: "",
    internetMessageId: "",
    subject: "",
    sender: "",
    canReply: false,
  });
});

test("native reply keeps selected item and safely inserts text as HTML", async () => {
  let value;
  const office = runtime((html, callback) => {
    value = html;
    callback({ status: "succeeded" });
  });
  assert.equal(readOutlookContext(office).canReply, true);
  await openOutlookReply(
    office,
    "message-A",
    'Hello,\nKeep <script> and "quotes" as text.',
  );
  assert.equal(
    value,
    "<div>Hello,<br>Keep &lt;script&gt; and &quot;quotes&quot; as text.</div>",
  );
});

test("switching messages blocks insertion into the wrong thread", async () => {
  let called = false;
  await assert.rejects(
    openOutlookReply(
      runtime(() => {
        called = true;
      }, "message-B"),
      "message-A",
      "Draft",
    ),
    /selected email changed/,
  );
  assert.equal(called, false);
});

test("unsupported client cannot open a reply", async () => {
  const office = runtime(() => {
    throw new Error("must not call");
  });
  office.context.requirements.isSetSupported = () => false;
  await assert.rejects(
    openOutlookReply(office, "message-A", "Draft"),
    /supported Outlook message/,
  );
});

test("reply length considers UTF-8 and escaping before Outlook limit", async () => {
  const office = runtime(() => {
    throw new Error("must not call");
  });
  await assert.rejects(
    openOutlookReply(office, "message-A", "<".repeat(8100)),
    /too long/,
  );
  await assert.rejects(
    openOutlookReply(office, "message-A", "😀".repeat(8100)),
    /too long/,
  );
});

test("Outlook failure remains an error rather than a sent confirmation", async () => {
  const office = runtime((_, done) =>
    done({ status: "failed", error: { message: "Mailbox unavailable" } }),
  );
  await assert.rejects(
    openOutlookReply(office, "message-A", "Draft"),
    /Mailbox unavailable/,
  );
});

test("HTML escaping preserves newlines and does not create links or tags", () => {
  assert.equal(
    replyHtml("A & B\r\n<a href='x'>"),
    "<div>A &amp; B<br>&lt;a href=&#39;x&#39;&gt;</div>",
  );
});

test("native generation requires message identity and its durably matched order", () => {
  const current = readOutlookContext(runtime(() => {}));
  assert.equal(
    requireCapturedOutlookMessage(current, current, "order-a", "order-a"),
    "<a@example.com>",
  );
  for (const [message, matched] of [
    [{ ...current, internetMessageId: "" }, "order-a"],
    [{ ...current, itemKey: "" }, "order-a"],
    [readOutlookContext(), "order-a"],
    [current, null],
    [current, "other-order"],
  ]) {
    assert.throws(() =>
      requireCapturedOutlookMessage(message, current, matched, "order-a"),
    );
  }
});

test("generation revalidation catches selected message changing before API request", () => {
  const expected = readOutlookContext(runtime(() => {}));
  assert.throws(
    () =>
      requireCapturedOutlookMessage(
        { ...expected, itemKey: "message-B" },
        expected,
        "order-a",
        "order-a",
      ),
    /selected email changed/,
  );
  assert.throws(
    () =>
      requireCapturedOutlookMessage(
        { ...expected, internetMessageId: "<b@example.com>" },
        expected,
        "order-a",
        "order-a",
      ),
    /selected email changed/,
  );
});

test("selected documents are inserted as authenticated Base64 files, never public links", async () => {
  let reply;
  const office = runtime((payload, done) => {
    reply = payload;
    done({ status: "succeeded" });
  });
  office.context.requirements.isSetSupported = (name, version) =>
    name === "Mailbox" && ["1.9", "1.15"].includes(version);
  assert.equal(canAttachOutlookFiles(office), true);
  await openOutlookReply(office, "message-A", "See the reviewed document.", [
    { name: "drawing.pdf", base64: "JVBERi0xLjQ=" },
  ]);
  assert.deepEqual(reply, {
    htmlBody: "<div>See the reviewed document.</div>",
    attachments: [
      {
        name: "drawing.pdf",
        type: "base64",
        base64file: "JVBERi0xLjQ=",
        inLine: false,
      },
    ],
  });
});

test("an older Outlook client never silently drops approved attachments", async () => {
  let called = false;
  const office = runtime(() => {
    called = true;
  });
  assert.equal(canAttachOutlookFiles(office), false);
  await assert.rejects(
    openOutlookReply(office, "message-A", "Draft", [
      { name: "drawing.pdf", base64: "JVBERi0xLjQ=" },
    ]),
    /cannot insert the selected files/,
  );
  assert.equal(called, false);
});

test("opening an autonomous Outlook draft displays the existing item without recreating or replacing it", async () => {
  let opened;
  const office = runtime(() => {
    throw new Error("must not create a reply");
  });
  office.context.mailbox.displayMessageFormAsync = (id, done) => {
    opened = id;
    done({ status: "succeeded" });
  };
  assert.equal(canOpenSavedOutlookDraft(office), true);
  await openSavedOutlookDraft(office, "message-A", "ews-draft-123");
  assert.equal(opened, "ews-draft-123");
});

test("saved draft opening refuses an unsupported client and does not guess its item ID", async () => {
  const office = runtime(() => {
    throw new Error("must not create a reply");
  });
  assert.equal(canOpenSavedOutlookDraft(office), false);
  await assert.rejects(
    openSavedOutlookDraft(office, "message-A", "ews-draft-123"),
    /Drafts folder/,
  );
  office.context.mailbox.displayMessageFormAsync = () => {
    throw new Error("must not open an empty ID");
  };
  await assert.rejects(
    openSavedOutlookDraft(office, "message-A", ""),
    /Drafts folder/,
  );
});

test("switching selected email blocks opening another thread’s saved draft", async () => {
  const office = runtime(() => {}, "message-B");
  office.context.mailbox.displayMessageFormAsync = () => {
    throw new Error("must not open a stale draft");
  };
  await assert.rejects(
    openSavedOutlookDraft(office, "message-A", "ews-draft-123"),
    /selected email changed/,
  );
});

test("Outlook saved-draft failure is shown without falling back to another reply", async () => {
  const office = runtime(() => {
    throw new Error("must not create a reply");
  });
  office.context.mailbox.displayMessageFormAsync = (_, done) =>
    done({ status: "failed", error: { message: "Draft was removed" } });
  await assert.rejects(
    openSavedOutlookDraft(office, "message-A", "ews-draft-123"),
    /Draft was removed/,
  );
});

test("a normal REST draft identifier is converted before native Outlook opens it", async () => {
  const calls = [];
  const office = runtime(() => {
    throw new Error("must not create a reply");
  });
  office.context.mailbox.convertToEwsId = (id, version) => {
    calls.push(["convert", id, version]);
    return "ews-draft-123";
  };
  office.context.mailbox.displayMessageFormAsync = (id, done) => {
    calls.push(["open", id]);
    done({ status: "succeeded" });
  };
  await openSavedOutlookDraft(office, "message-A", "rest-draft-123", "rest");
  assert.deepEqual(calls, [
    ["convert", "rest-draft-123", "v2.0"],
    ["open", "ews-draft-123"],
  ]);
});

test("a REST identifier is never passed raw to the native display API", async () => {
  const office = runtime(() => {});
  office.context.mailbox.displayMessageFormAsync = () => {
    throw new Error("must not open a REST ID without conversion");
  };
  await assert.rejects(
    openSavedOutlookDraft(office, "message-A", "rest-draft-123", "rest"),
    /cannot convert/,
  );
});

test("desktop Office opens only a verified Microsoft Outlook fallback link", () => {
  const opened = [];
  const office = runtime(() => {});
  office.context.requirements.isSetSupported = (name, version) =>
    name === "OpenBrowserWindowApi" && version === "1.1";
  office.context.ui = { openBrowserWindow: (url) => opened.push(url) };
  assert.equal(
    openSavedOutlookLink(
      office,
      "https://outlook.cloud.microsoft/mail/deeplink/read/saved-draft",
    ),
    true,
  );
  assert.equal(
    openSavedOutlookLink(office, "https://example.test/draft"),
    false,
  );
  assert.deepEqual(opened, [
    "https://outlook.cloud.microsoft/mail/deeplink/read/saved-draft",
  ]);
});

test("web Outlook does not claim to support a desktop browser API", () => {
  const office = runtime(() => {});
  office.context.ui = {
    openBrowserWindow: () => {
      throw new Error("must not call unsupported API");
    },
  };
  assert.equal(
    openSavedOutlookLink(office, "https://outlook.office.com/mail/id/draft"),
    false,
  );
});
