import test from "node:test";
import assert from "node:assert/strict";
import {
  equipmentCounts,
  equipmentChecks,
  equipmentEvidencePath,
  equipmentLocation,
  equipmentPair,
  equipmentStatus,
  equipmentSources,
  equipmentValue,
  equipmentVerdict,
} from "../apps/web/src/equipment-types.ts";

test("equipment counts require explicit source-backed verified results, not a positive summary", () => {
  const comparison = {
    summary: { verified: 99, remaining: 0, cannot_verify: 0 },
    checks: [
      { status: "verified" },
      { status: "remaining" },
      { status: "unreadable" },
      { status: "unknown" },
      { status: "unchanged" },
      { status: "missing" },
    ],
  };
  assert.deepEqual(equipmentCounts(comparison), {
    verified: 1,
    remaining: 2,
    cannot_verify: 2,
    unchanged: 1,
  });
  assert.deepEqual(equipmentCounts({ ...comparison, stale: true }), {
    verified: 0,
    remaining: 0,
    cannot_verify: 6,
    unchanged: 0,
  });
});
test("an unknown or stale equipment status cannot silently become Verified", () => {
  for (const status of ["approved", "complete", "matched", "", "new_status"])
    assert.equal(equipmentStatus(status), "cannot_verify");
  assert.equal(equipmentStatus("verified", true), "cannot_verify");
  assert.equal(equipmentStatus("conflict"), "remaining");
  assert.equal(equipmentStatus("unchanged"), "unchanged");
});
test("a source failure prevents a positive package headline even if the known fields match", () => {
  const comparison = {
    summary: { outcome: "cannot_verify", source_issues: 1 },
    checks: [{ status: "verified" }],
  };
  assert.equal(equipmentVerdict(comparison), "Some changes cannot be verified");
  assert.equal(
    equipmentVerdict({
      ...comparison,
      summary: { outcome: "supported_fields_match" },
    }),
    "Supported equipment fields match",
  );
  assert.equal(
    equipmentVerdict({ ...comparison, checks: [{ status: "missing" }] }),
    "Changes remain in the revised files",
  );
  assert.equal(
    equipmentVerdict({ ...comparison, stale: true }),
    "Re-check the updated files",
  );
});
test("missing and conflicting actual fields stay visible before neutral unchanged fields without hiding any checks", () => {
  const checks = [
    { id: "a", device_tag: "CB-12", field: "trip_unit", status: "unchanged" },
    { id: "b", device_tag: "CB-12", field: "quantity", status: "verified" },
    {
      id: "c",
      device_tag: "CB-14",
      field: "device_presence",
      status: "missing",
    },
    { id: "d", device_tag: "CB-12", field: "trip_unit", status: "mismatch" },
  ];
  assert.deepEqual(
    equipmentChecks({ checks }).map((c) => c.id),
    ["d", "c", "b", "a"],
  );
  assert.deepEqual(
    checks.map((c) => c.id),
    ["a", "b", "c", "d"],
  );
  assert.equal(
    equipmentValue("120 / 800 / 65 / TU-600", "device_presence"),
    "Present",
  );
  assert.equal(equipmentValue(null, "device_presence"), "Not located");
});
test("device presence shows each original once while conflicting field observations remain available", () => {
  const evidence = [
    { document_id: "drawing", excerpt: "CB-12 trip TU-2500" },
    { document_id: "drawing", excerpt: "CB-12 frame 2500 A" },
    { document_id: "bom", cell: "D2", excerpt: "TU-2500" },
  ];
  assert.equal(equipmentSources({ evidence }, "device_presence").length, 2);
  assert.equal(equipmentSources({ evidence }, "trip_unit").length, 3);
});
test("absent evidence stays Not found while zero and false values remain visible", () => {
  assert.equal(equipmentValue(null), "Not found");
  assert.equal(equipmentValue([]), "Not found");
  assert.equal(equipmentValue(0), "0");
  assert.equal(equipmentValue(false), "No");
  assert.equal(equipmentValue(["TU-2500", "LSIG"]), "TU-2500 · LSIG");
  assert.equal(equipmentValue("2500", "trip_a"), "2500 A");
  assert.equal(equipmentValue("65", "interrupting_ka"), "65 kA");
  assert.equal(equipmentValue("unknown", "control_voltage_v"), "unknown");
  assert.equal(equipmentValue(null, "trip_a"), "Not found");
});
test("equipment evidence identifies the PDF page, spreadsheet cell and CAD element", () => {
  assert.equal(
    equipmentLocation({
      sheet: "E-10",
      page: 2,
      row: 17,
      cell: "G17",
      cad_handle: "A72",
    }),
    "Sheet E-10 · p. 2 · row 17 · cell G17 · CAD element A72",
  );
});
test("source preview URLs cannot send workspace credentials to an external or other order URL", () => {
  const evidence = {
    document_id: "doc-1",
    page: 2,
    crop_url: "https://example.org/steal",
  };
  assert.equal(
    equipmentEvidencePath("order-1", evidence),
    "/api/orders/order-1/equipment/evidence?document_id=doc-1&page=2",
  );
  assert.equal(
    equipmentEvidencePath("order-1", {
      ...evidence,
      crop_url:
        "/api/orders/order-2/equipment/evidence?document_id=doc-1&fact_id=f1",
    }),
    "/api/orders/order-1/equipment/evidence?document_id=doc-1&page=2",
  );
  assert.equal(
    equipmentEvidencePath("order-1", {
      ...evidence,
      crop_url:
        "/orders/order-1/equipment/evidence?document_id=doc-1&fact_id=f1",
    }),
    "/api/orders/order-1/equipment/evidence?document_id=doc-1&fact_id=f1",
  );
  assert.equal(
    equipmentEvidencePath("order-1", { document_id: "doc-1" }),
    null,
  );
});
test("the saved comparison retains its exact named revisions instead of switching to a newer package", () => {
  const packages = ["B", "C", "D"].map((revision) => ({
    revision,
    documents: [],
  }));
  assert.deepEqual(
    equipmentPair({
      packages,
      comparison: { baseline_revision: "B", target_revision: "C" },
    }),
    ["B", "C"],
  );
  assert.deepEqual(equipmentPair({ packages, comparison: null }), ["C", "D"]);
});
