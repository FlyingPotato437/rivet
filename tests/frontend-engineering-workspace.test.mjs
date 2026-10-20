import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

// Exercise the actual isolated assignment helpers without importing the app's
// browser/auth bootstrap. TypeScript strips their local type annotations.
const source = readFileSync(
  new URL("../apps/web/src/EngineeringWorkspace.tsx", import.meta.url),
  "utf8",
);
const parsed = ts.createSourceFile(
  "EngineeringWorkspace.tsx",
  source,
  ts.ScriptTarget.ESNext,
  true,
  ts.ScriptKind.TSX,
);
const names = new Set([
  "engineeringAssignmentPair",
  "swapEngineeringAssignments",
  "engineeringOutlookCanRetry",
  "engineeringReviewRequirementSupported",
]);
const functions = parsed.statements
  .filter(
    (statement) =>
      ts.isFunctionDeclaration(statement) && names.has(statement.name?.text),
  )
  .map((statement) => statement.getText(parsed));
assert.equal(functions.length, 4);
const compiled = ts.transpileModule(functions.join("\n"), {
  compilerOptions: {
    target: ts.ScriptTarget.ES2022,
    module: ts.ModuleKind.CommonJS,
  },
}).outputText;
const exports = {};
vm.runInNewContext(compiled, { exports });
const {
  engineeringAssignmentPair,
  swapEngineeringAssignments,
  engineeringOutlookCanRetry,
  engineeringReviewRequirementSupported,
} = exports;

function design() {
  return {
    devices: [
      {
        id: "F1",
        role: "feeder",
        frame: "JJ",
        section_id: "S1",
        position_id: "P1",
      },
      {
        id: "F2",
        role: "feeder",
        frame: "JJ",
        section_id: "S2",
        position_id: "P2",
      },
    ],
    sections: [
      {
        id: "S1",
        kind: "distribution",
        positions: [
          {
            id: "P1",
            device_id: "F1",
            side: "left",
            start_in: 9,
            mounting_space_in: 4.5,
            physical_datum_offset_in: null,
            source_refs: [{ page: 65 }],
          },
        ],
      },
      {
        id: "S2",
        kind: "distribution",
        positions: [
          {
            id: "P2",
            device_id: "F2",
            side: "right",
            start_in: 0,
            mounting_space_in: 4.5,
            physical_datum_offset_in: 3,
            source_refs: [{ page: 64 }],
          },
        ],
      },
    ],
  };
}

test("compatible swaps preserve mounting slots and update both device bindings without mutating the saved design", () => {
  const input = design();
  const original = structuredClone(input);
  const output = swapEngineeringAssignments(input, "F1", "F2");
  assert.notEqual(output, input);
  assert.deepEqual(input, original);
  assert.deepEqual(
    output.devices.map((device) => [
      device.id,
      device.section_id,
      device.position_id,
    ]),
    [
      ["F1", "S2", "P2"],
      ["F2", "S1", "P1"],
    ],
  );
  for (let index = 0; index < input.sections.length; index++) {
    const { device_id, ...mounting } = output.sections[index].positions[0];
    const { device_id: previous, ...originalMounting } =
      input.sections[index].positions[0];
    assert.deepEqual(mounting, originalMounting);
    assert.notEqual(device_id, previous);
  }
  assert.deepEqual(
    output.sections
      .flatMap((section) =>
        section.positions.map((position) => position.device_id),
      )
      .sort(),
    ["F1", "F2"],
  );
});

for (const change of [
  "main",
  "different-frame",
  "different-height",
  "unbound-position",
  "missing-space",
  "missing-device",
]) {
  test(`a ${change} assignment is not offered as a compatible swap`, () => {
    const input = design();
    if (change === "main") input.devices[0].role = "main";
    if (change === "different-frame") input.devices[0].frame = "LJ";
    if (change === "different-height")
      input.sections[0].positions[0].mounting_space_in = 6;
    if (change === "unbound-position") input.devices[0].position_id = "unbound";
    if (change === "missing-space")
      input.sections[0].positions[0].mounting_space_in = null;
    const target = change === "missing-device" ? "missing" : "F2";
    assert.equal(engineeringAssignmentPair(input, "F1", target), null);
    assert.equal(swapEngineeringAssignments(input, "F1", target), input);
  });
}

test("Outlook offers only initial creation and explicit backend safe-retry states", () => {
  for (const status of [undefined, "failed", "permission_required"])
    assert.equal(engineeringOutlookCanRetry(status), true);
  for (const status of [
    "",
    "creating",
    "created",
    "existing_draft",
    "manual_attention",
    "discarded",
    "sent_for_review",
    "unknown",
  ])
    assert.equal(engineeringOutlookCanRetry(status), false);
});

test("review choices are limited to supported scalar fields on one exact current model target", () => {
  const model = design();
  model.devices.push({ id: "main", role: "main" });
  const row = (key, subject_id = "order", flags = {}) => ({
    id: "requirement",
    key,
    subject_id,
    value: 225,
    confirmed: true,
    source_refs: [],
    ...flags,
  });
  for (const key of [
    "voltage_v",
    "service_rating_a",
    "code_edition",
    "main.trip_a",
    "main.nameplate",
  ])
    assert.equal(engineeringReviewRequirementSupported(row(key), model), true);
  for (const key of ["trip_a", "poles", "part_number"])
    assert.equal(
      engineeringReviewRequirementSupported(row(key, "F1"), model),
      true,
    );
  for (const key of ["spd", "metering", "main.kind", "frame_a"])
    assert.equal(engineeringReviewRequirementSupported(row(key), model), false);
  for (const flags of [
    { dismissed: true },
    { excluded: true },
    { unresolved_role: true },
    { origin_role: "reference" },
  ])
    assert.equal(
      engineeringReviewRequirementSupported(row("trip_a", "F1", flags), model),
      false,
    );
  assert.equal(
    engineeringReviewRequirementSupported(
      row("trip_a", "missing-device"),
      model,
    ),
    false,
  );
  assert.equal(
    engineeringReviewRequirementSupported(row("trip_a", "F1"), null),
    false,
  );
  model.devices[0].tag = model.devices[1].tag = "7";
  assert.equal(
    engineeringReviewRequirementSupported(row("trip_a", "7"), model),
    false,
  );
});
