import test from "node:test";
import assert from "node:assert/strict";
import {
  manufacturerProfileStatus,
  manufacturerProposal,
  manufacturerRuleDescription,
  manufacturerSourceLocation,
  manufacturerSourcePath,
} from "../apps/web/src/manufacturer-profile-types.ts";

test("revocation remains visible even when the immutable manifest was approved", () => {
  const approved = { manifest: { status: "approved" } };
  assert.equal(manufacturerProfileStatus(approved), "approved");
  assert.equal(
    manufacturerProfileStatus({
      ...approved,
      revocation: { reason: "Superseded" },
    }),
    "revoked",
  );
  assert.equal(
    manufacturerProfileStatus({ manifest: { status: "future-status" } }),
    "proposed",
  );
});

test("uploaded approval claims never survive as active rules or invented reviewers", () => {
  const raw = {
    version: "old-content-hash",
    name: "Panel guide",
    status: "approved",
    review: { reviewer_id: "forged", reviewed_at: "2026-10-18T12:00:00Z" },
    sources: [{ document_id: "owned-guide" }],
    rules: [{ rule_id: "r1", status: "approved" }],
    aliases: [{ alias_id: "a1", tags: ["CB-12", "BKR12"], status: "approved" }],
  };
  const proposal = manufacturerProposal(JSON.stringify(raw));
  assert.equal(proposal.status, "proposed");
  assert.equal(proposal.review, null);
  assert.equal("version" in proposal, false);
  assert.equal(proposal.rules[0].status, "proposed");
  assert.equal(proposal.aliases[0].status, "proposed");
  assert.equal(raw.status, "approved");
  for (const input of [
    "not json",
    "[]",
    "null",
    JSON.stringify({ name: "No sources", sources: [], rules: [], aliases: [] }),
  ])
    assert.throws(() => manufacturerProposal(input));
});

test("source documents use only protected server-issued order paths", () => {
  assert.equal(
    manufacturerSourcePath("/orders/order-1/equipment/files/doc-1"),
    "/api/orders/order-1/equipment/files/doc-1",
  );
  assert.equal(
    manufacturerSourcePath("/api/orders/order-1/equipment/files/doc-1"),
    "/api/orders/order-1/equipment/files/doc-1",
  );
  for (const url of [
    undefined,
    "https://attacker.test/source.pdf",
    "//attacker.test/",
    "javascript:alert(1)",
    "/api/../secret",
    "/api/company-settings",
  ])
    assert.equal(manufacturerSourcePath(url), null);
  assert.equal(
    manufacturerSourceLocation({
      location: { page: 8, sheet: "E-10", row: 3, cell: "C3" },
    }),
    "p. 8 · sheet E-10 · cell C3",
  );
});

test("rule review describes finite constraints and preserves terminal identity", () => {
  const describe = (constraint) => manufacturerRuleDescription({ constraint });
  assert.match(
    describe({
      op: "allowed_set",
      field: "trip_unit",
      allowed: ["TU-2500", "TU-3000"],
    }),
    /trip unit.*TU-2500, TU-3000/,
  );
  assert.match(
    describe({
      op: "range",
      field: "frame_a",
      minimum: 100,
      maximum: 2500,
      unit: "A",
    }),
    /at least 100 A; at most 2500 A/,
  );
  assert.match(
    describe({ op: "dimension_bounds", x_min: 0, width_max: 150, unit: "mm" }),
    /x ≥ 0 mm.*width ≤ 150 mm/,
  );
  assert.match(
    describe({
      op: "terminal_pair",
      wire_id: "W102",
      from_terminal: "BKR12:T1",
      to_terminal: "K86:12",
    }),
    /W102: BKR12:T1 ↔ K86:12/,
  );
  assert.match(
    describe({ op: "requires", required_device_tag: "KA-1" }),
    /complete, cited inventory/,
  );
  assert.match(describe({ op: "execute" }), /Unsupported rule/);
});
