import test from "node:test";
import assert from "node:assert/strict";
import {
  mailboxCheckTargets,
  requestMailboxChecks,
  mailboxCheckProgress,
  lastMailboxCheck,
} from "../apps/web/src/mailbox-check.ts";

const before = "2026-10-15T01:00:00Z";
const after = "2026-10-15T01:00:15Z";
const mailbox = (patch = {}) => ({
  id: "mailbox-a",
  mailbox: "demo@example.com",
  enabled: true,
  status: "current",
  folders: [
    {
      id: "inbox",
      state: "current",
      last_synced_at: before,
      last_attempt_at: before,
    },
  ],
  ...patch,
});

test("check snapshots only enabled mailboxes and preserves baseline checkpoints", () => {
  const live = mailbox();
  const targets = mailboxCheckTargets([
    live,
    mailbox({ id: "paused", enabled: false }),
  ]);
  live.folders[0].last_synced_at = after;
  assert.equal(targets.length, 1);
  assert.equal(targets[0].folders[0].last_synced_at, before);
});

test("accepted scheduling is not presented as completed synchronization", async () => {
  const live = mailbox();
  const targets = mailboxCheckTargets([live]);
  const calls = [];
  const result = await requestMailboxChecks(targets, async (path) => {
    calls.push(path);
    return { status: "scheduled" };
  });
  assert.deepEqual(calls, ["/mailboxes/mailbox-a/reconcile"]);
  assert.equal(result.scheduled.length, 1);
  assert.equal(mailboxCheckProgress(result.scheduled, [live]).complete, false);
});

test("one mailbox permission failure does not hide a successfully scheduled mailbox", async () => {
  const targets = mailboxCheckTargets([
    mailbox(),
    mailbox({ id: "denied", mailbox: "blocked@example.com" }),
  ]);
  const result = await requestMailboxChecks(targets, async (path) => {
    if (path.includes("denied"))
      throw new Error("Administrator access required.");
    return { status: "scheduled" };
  });
  assert.deepEqual(
    result.scheduled.map((item) => item.id),
    ["mailbox-a"],
  );
  assert.match(result.errors[0], /blocked@example.com.*Administrator/);
});

test("every monitored folder must advance before a requested check completes", () => {
  const live = mailbox({
    folders: [
      { id: "inbox", state: "current", last_synced_at: before },
      { id: "orders", state: "current", last_synced_at: before },
    ],
  });
  const targets = mailboxCheckTargets([live]);
  live.folders[0].last_synced_at = after;
  assert.equal(mailboxCheckProgress(targets, [live]).complete, false);
  live.folders[1].last_synced_at = after;
  assert.deepEqual(mailboxCheckProgress(targets, [live]), {
    complete: true,
    errors: [],
  });
});

test("a failure in a later folder is reported while the first folder is still pending", () => {
  const live = mailbox({
    folders: [
      {
        id: "inbox",
        state: "current",
        last_synced_at: before,
        last_attempt_at: before,
      },
      {
        id: "orders",
        state: "current",
        last_synced_at: before,
        last_attempt_at: before,
      },
    ],
  });
  const targets = mailboxCheckTargets([live]);
  Object.assign(live.folders[1], {
    state: "retry",
    last_attempt_at: after,
    error: "Microsoft rejected the connection.",
  });
  const progress = mailboxCheckProgress(targets, [live]);
  assert.equal(progress.complete, false);
  assert.match(progress.errors[0], /Microsoft rejected/);
});

test("an old error does not interrupt a newly requested retry", () => {
  const live = mailbox({
    folders: [
      {
        id: "inbox",
        state: "retry",
        last_synced_at: before,
        last_attempt_at: before,
        error: "Temporary failure.",
      },
    ],
  });
  assert.deepEqual(mailboxCheckProgress(mailboxCheckTargets([live]), [live]), {
    complete: false,
    errors: [],
  });
});

test("removed or empty monitoring cannot silently claim completion", () => {
  const empty = mailbox({ folders: [] });
  assert.match(
    mailboxCheckProgress(mailboxCheckTargets([empty]), [empty]).errors[0],
    /no monitored folders/,
  );
  const targets = mailboxCheckTargets([mailbox()]);
  assert.match(
    mailboxCheckProgress(targets, [mailbox({ enabled: false })]).errors[0],
    /no longer enabled/,
  );
  assert.match(
    mailboxCheckProgress(targets, [empty]).errors[0],
    /folders changed/,
  );
  assert.equal(mailboxCheckProgress([], []).complete, false);
});

test("last checked reflects the oldest covered folder, not the newest one", () => {
  assert.equal(
    lastMailboxCheck([
      mailbox({
        folders: [
          { id: "inbox", state: "current", last_synced_at: after },
          {
            id: "orders",
            state: "current",
            last_synced_at: "2026-10-14T18:00:00-07:00",
          },
        ],
      }),
    ]),
    "2026-10-14T18:00:00-07:00",
  );
  assert.equal(
    lastMailboxCheck([
      mailbox({
        folders: [{ id: "new", state: "pending", last_synced_at: null }],
      }),
    ]),
    null,
  );
});
