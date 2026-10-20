import test from "node:test";
import assert from "node:assert/strict";
import { actionExecutionRows } from "../apps/web/src/action-execution.ts";

const attempt = (patch = {}) => ({
  id: "event-1",
  action_id: "action-1",
  action: "sap.draft_sales_order",
  package_id: "package-1",
  at: "2026-10-15T09:10:00Z",
  status: "applied_local_simulation",
  receipt: {
    mode: "local_http_demo",
    status: "applied_local_simulation",
    action_id: "action-1",
    remote_version: 1,
    files: [],
  },
  ...patch,
});

test("duplicate reconciliation displays one effect and preserves the real receipt", () => {
  const older = attempt();
  const newer = attempt({
    id: "event-2",
    at: "2026-10-15T09:11:00Z",
    reconciled: true,
  });
  const rows = actionExecutionRows([older, newer]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].attempt.id, "event-2");
  assert.equal(rows[0].applied, true);
});

test("a status label alone cannot claim execution without its matching local receipt", () => {
  for (const receipt of [
    undefined,
    { mode: "live", status: "applied_local_simulation", action_id: "action-1" },
    {
      mode: "local_http_demo",
      status: "applied_local_simulation",
      action_id: "another-action",
    },
  ]) {
    assert.equal(actionExecutionRows([attempt({ receipt })])[0].applied, false);
  }
});

test("latest failed intent stays visible rather than resurrecting an earlier success", () => {
  const rows = actionExecutionRows([
    attempt(),
    attempt({
      id: "event-3",
      action_id: "action-2",
      at: "2026-10-15T09:12:00Z",
      status: "receipt_unknown",
      receipt: undefined,
    }),
  ]);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].applied, false);
  assert.equal(rows[0].waiting, true);
  assert.equal(rows[0].attempt.action_id, "action-2");
});

test("no actions produces no empty proof rows", () => {
  assert.deepEqual(actionExecutionRows([]), []);
});
