import test from "node:test";
import assert from "node:assert/strict";
import {
  nativeChangeDescription,
  nativeChangeLabel,
  nativeChangeTarget,
  nativeChangeValues,
  nativeChecksPass,
  nativeDrawingValid,
  nativeViewport,
} from "../apps/web/src/native-drawing-types.ts";
import {
  correctedBundleReady,
  literalCorrectionEdits,
} from "../apps/web/src/equipment-correction-types.ts";

const before = {
  units: "mm",
  devices: [
    {
      tag: "CB-12",
      bounds: [20, 20, 60, 80],
      terminals: [{ id: "CB-12:T1", position: [40, 80] }],
    },
    {
      tag: "TB-1",
      bounds: [100, 10, 130, 30],
      terminals: [{ id: "TB-1:1", position: [115, 30] }],
    },
  ],
  wires: [
    {
      id: "W-1201",
      from: "CB-12:T1",
      to: "TB-1:1",
      points: [
        [40, 80],
        [40, 100],
        [115, 100],
        [115, 30],
      ],
    },
  ],
  enclosures: [{ id: "PANEL-A", bounds: [0, 0, 200, 140] }],
};
const after = {
  ...before,
  devices: [
    {
      ...before.devices[0],
      bounds: [50, 20, 90, 80],
      terminals: [{ id: "CB-12:T1", position: [70, 80] }],
    },
    before.devices[1],
  ],
  wires: [
    {
      ...before.wires[0],
      points: [
        [70, 80],
        [70, 100],
        [115, 100],
        [115, 30],
      ],
    },
  ],
};

test("native before/after uses a shared physical extent and preserves native geometry", () => {
  const viewport = nativeViewport(before, after);
  assert.ok(viewport);
  const origin = viewport.point([0, 0]),
    moved = viewport.point([30, 0]);
  assert.ok(Math.abs(moved[0] - origin[0] - 30 * viewport.unitScale) < 1e-8);
  assert.equal(viewport.point([40, 80])[0] < viewport.point([70, 80])[0], true);
  assert.equal(
    viewport.point([40, 80])[1] > viewport.point([40, 100])[1],
    true,
  );
  assert.deepEqual(before.wires[0].points[0], [40, 80]);
});
test("the native viewer refuses invalid coordinates, duplicate tags and missing routes instead of drawing a plausible fallback", () => {
  assert.equal(nativeDrawingValid(before), true);
  for (const drawing of [
    { ...before, devices: [...before.devices, before.devices[0]] },
    {
      ...before,
      devices: [{ ...before.devices[0], bounds: [20, 20, 20, 80] }],
    },
    {
      ...before,
      wires: [
        {
          ...before.wires[0],
          points: [
            [40, 80],
            [NaN, 100],
          ],
        },
      ],
    },
    { ...before, wires: [{ ...before.wires[0], points: [] }] },
    {
      ...before,
      enclosures: [{ id: "PANEL-A", bounds: [0, 0, Infinity, 140] }],
    },
  ])
    assert.equal(nativeDrawingValid(drawing), false);
  assert.equal(nativeViewport(before, { ...after, units: "in" }), null);
  assert.equal(nativeViewport(before, undefined), null);
});
test("native verification requires every actual saved-file check to pass, not a positive overall label", () => {
  const verified = {
    status: "verified",
    checks: [
      {
        id: "wire-terminal",
        status: "pass",
        label: "Wire terminates on TB-1:1",
      },
    ],
  };
  assert.equal(nativeChecksPass(verified), true);
  assert.equal(nativeChecksPass({ ...verified, checks: [] }), false);
  assert.equal(
    nativeChecksPass({ ...verified, checks: [{ status: "unknown" }] }),
    false,
  );
  assert.equal(
    nativeChecksPass({ ...verified, checks: [{ status: "fail" }] }),
    false,
  );
  assert.equal(nativeChecksPass({ ...verified, status: "blocked" }), false);
  assert.equal(
    nativeChecksPass({
      ...verified,
      blockers: [{ reason: "Route leaves the enclosure" }],
    }),
    false,
  );
  const bundle = {
    status: "prepared_draft",
    current: true,
    attachable: true,
    verification: {
      summary: {
        remaining: 0,
        cannot_verify: 0,
        outcome: "supported_fields_match",
      },
    },
    native_verification: { ...verified, checks: [{ status: "unknown" }] },
  };
  assert.equal(correctedBundleReady(bundle), false);
  assert.equal(
    correctedBundleReady({ ...bundle, native_verification: verified }),
    true,
  );
});
test("native changes show practical targets and readable coordinates", () => {
  assert.equal(nativeChangeLabel({ kind: "move_device" }), "Device moved");
  assert.equal(nativeChangeTarget({ wire_id: "W-1201" }), "W-1201");
  assert.equal(
    nativeChangeDescription({ from: "CB-12:T1", to: "TB-1:1" }),
    "from: CB-12:T1 · to: TB-1:1",
  );
  assert.equal(nativeChangeDescription([70, 80]), "70, 80");
});

test("real native diff objects become concise changed facts without handles or terminal manifests", () => {
  const source = {
    tag: "KA-1",
    handle: "BF",
    position: [350, 235],
    width: 80,
    height: 80,
    rotation: 0,
    attributes: { PART_NUMBER: "RVT-AUX-RELAY" },
    terminals: [{ id: "KA-1:A1", position: [310, 255] }],
  };
  const moved = {
    ...source,
    position: [500, 235],
    terminals: [{ id: "KA-1:A1", position: [460, 255] }],
  };
  const change = {
    kind: "device_geometry",
    device_tag: "KA-1",
    before: source,
    after: moved,
  };
  assert.equal(nativeChangeLabel(change), "Device moved");
  assert.deepEqual(nativeChangeValues(change, "mm"), [
    "(350, 235) mm",
    "(500, 235) mm",
  ]);
  const wire = {
    id: "W102",
    handle: "105",
    from: "K86-1:12",
    to: "CB-12:TC1",
    points: [
      [0, 0],
      [0, 100],
      [100, 100],
    ],
  };
  assert.deepEqual(
    nativeChangeValues(
      { kind: "rewire", before: wire, after: { ...wire, to: "CB-13:TC1" } },
      "mm",
    ),
    ["K86-1:12 → CB-12:TC1", "K86-1:12 → CB-13:TC1"],
  );
  assert.deepEqual(
    nativeChangeValues(
      {
        kind: "reroute",
        before: wire,
        after: {
          ...wire,
          points: [
            [0, 0],
            [100, 0],
            [100, 50],
            [200, 50],
          ],
        },
      },
      "mm",
    ),
    ["2 segments · 200 mm", "3 segments · 250 mm"],
  );
  assert.deepEqual(
    nativeChangeValues(
      { kind: "add_device", before: null, after: moved },
      "mm",
    ),
    ["Not present", "RVT-AUX-RELAY · (500, 235) mm"],
  );
});

test("native plan evidence replaces generic unknown-value rows without concealing literal equipment changes", () => {
  const edits = [
    {
      kind: "rewire",
      device_tag: "W102",
      field: "rewire",
      before: null,
      after: "CB-13:TC1",
    },
    {
      device_tag: "CB-12",
      field: "trip_unit",
      before: "TU-2500",
      after: "TU-3000",
    },
  ];
  assert.deepEqual(
    literalCorrectionEdits({
      plan: { edits, native_plan: { status: "ready" } },
    }),
    [edits[1]],
  );
  assert.deepEqual(literalCorrectionEdits({ plan: { edits } }), edits);
});
