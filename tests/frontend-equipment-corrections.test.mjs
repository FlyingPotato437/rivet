import test from "node:test";
import assert from "node:assert/strict";
import {
  correctionArtifactPath,
  correctionBefore,
  correctionIssue,
  correctedBundleReady,
  correctedDraftReady,
  correctedFiles,
  companyRulesAllowAttachment,
  newestCorrectedBundle,
} from "../apps/web/src/equipment-correction-types.ts";
const files = [
  {
    name: "E-10-D-R1.dxf",
    role: "drawing",
    sha256: "abc",
    url: "/orders/o1/equipment/corrections/b1/files/E-10-D-R1.dxf",
  },
  {
    name: "BOM-D-R1.xlsx",
    role: "bom",
    sha256: "def",
    url: "/orders/o1/equipment/corrections/b1/files/BOM-D-R1.xlsx",
  },
  {
    name: "E-10-D-R1.pdf",
    role: "preview",
    sha256: "ghi",
    url: "/orders/o1/equipment/corrections/b1/files/E-10-D-R1.pdf",
  },
  {
    name: "correction-manifest.json",
    role: "manifest",
    sha256: "xyz",
    url: "",
  },
];
const bundle = {
  id: "b1",
  status: "prepared_draft",
  target_revision: "D-R1",
  created_at: "2026-10-17T12:00:00Z",
  attachable: true,
  current: true,
  changes: [],
  artifacts: files,
  verification: {
    summary: {
      verified: 2,
      remaining: 0,
      cannot_verify: 0,
      source_issues: 0,
      outcome: "supported_fields_match",
    },
  },
};
test("company constraints can block a ready file package without turning inapplicable checks into approvals", () => {
  const rules = {
    active: true,
    status: "pass",
    profile_version: "reviewed-version",
    blockers: [],
    results: [{ rule_id: "position", status: "pass", applicable: true }],
  };
  assert.equal(correctedBundleReady({ ...bundle, company_rules: rules }), true);
  assert.equal(companyRulesAllowAttachment({ ...rules, active: false }), false);
  assert.equal(
    companyRulesAllowAttachment({
      ...rules,
      blockers: ["Source guide revoked"],
    }),
    false,
  );
  for (const status of ["fail", "unknown"]) {
    assert.equal(
      correctedBundleReady({
        ...bundle,
        company_rules: {
          ...rules,
          results: [{ rule_id: "position", status, applicable: true }],
        },
      }),
      false,
    );
  }
  assert.equal(
    companyRulesAllowAttachment({
      ...rules,
      status: "unknown",
      results: [{ rule_id: "unrelated", status: "unknown", applicable: false }],
    }),
    true,
  );
  assert.equal(
    companyRulesAllowAttachment({
      ...rules,
      status: "unknown",
      results: [{ rule_id: "unclear", status: "unknown", applicable: null }],
    }),
    false,
  );
});
test("corrected work is ready only after an independent check with no unsupported or missing evidence", () => {
  assert.equal(correctedBundleReady(bundle), true);
  for (const changed of [
    { attachable: false },
    { current: false },
    { status: "preparing" },
    {
      verification: {
        summary: {
          verified: 2,
          remaining: 0,
          cannot_verify: 1,
          outcome: "cannot_verify",
        },
      },
    },
    {
      verification: {
        summary: {
          verified: 2,
          remaining: 0,
          cannot_verify: 0,
          source_issues: 1,
          outcome: "supported_fields_match",
        },
      },
    },
  ])
    assert.equal(correctedBundleReady({ ...bundle, ...changed }), false);
});
test("an Outlook receipt cannot claim corrected files are ready when files are missing, different, or preserved in a human draft", () => {
  const receipt = {
    status: "created",
    attachments: files
      .filter((f) => f.role !== "manifest")
      .map((f) => ({ name: f.name, sha256: f.sha256 })),
  };
  assert.equal(
    correctedDraftReady({ ...bundle, outlook: { draft: receipt } }),
    true,
  );
  assert.equal(
    correctedDraftReady({
      ...bundle,
      outlook: {
        draft: { ...receipt, attachments: receipt.attachments.slice(0, 2) },
      },
    }),
    false,
  );
  assert.equal(
    correctedDraftReady({
      ...bundle,
      outlook: {
        draft: {
          ...receipt,
          attachments: receipt.attachments.map((f) => ({
            ...f,
            sha256: "changed",
          })),
        },
      },
    }),
    false,
  );
  assert.equal(
    correctedDraftReady({
      ...bundle,
      outlook: { draft: { ...receipt, status: "existing_draft" } },
    }),
    false,
  );
});
test("the customer file count excludes the internal verification manifest", () =>
  assert.deepEqual(
    correctedFiles(bundle).map((f) => f.name),
    ["E-10-D-R1.dxf", "BOM-D-R1.xlsx", "E-10-D-R1.pdf"],
  ));
test("stale correction packages cannot replace the current prepared result", () => {
  const stale = {
    ...bundle,
    id: "newer-stale",
    created_at: "2026-10-18T12:00:00Z",
    current: false,
  };
  assert.equal(newestCorrectedBundle({ bundles: [bundle, stale] }).id, "b1");
  assert.equal(newestCorrectedBundle({ bundles: [stale] }), undefined);
});
test("correction source values explain drawing/BOM differences without exposing object syntax", () => {
  assert.equal(
    correctionBefore({
      field: "trip_unit",
      before: { drawing: "TU-2500", bom: "TU-2500" },
    }),
    "TU-2500",
  );
  assert.equal(
    correctionBefore({
      field: "trip_unit",
      before: { drawing: "TU-3000", bom: "TU-2500" },
    }),
    "Drawing: TU-3000 · BOM: TU-2500",
  );
  assert.equal(
    correctionIssue({
      reason: "CB-14 is missing. An engineer must restore it.",
    }),
    "CB-14 is missing. An engineer must restore it.",
  );
});
test("corrected originals use the current order's protected API path, never an external URL", () => {
  assert.equal(
    correctionArtifactPath("o1", "b1", files[0]),
    "/api/orders/o1/equipment/corrections/b1/files/E-10-D-R1.dxf",
  );
  assert.equal(
    correctionArtifactPath("o1", "b1", {
      ...files[0],
      url: "https://example.com/steal",
    }),
    null,
  );
  assert.equal(correctionArtifactPath("o2", "b1", files[0]), null);
});
