import test from "node:test";
import assert from "node:assert/strict";
import {
  drawingConnectionSegments,
  drawingCoverageLine,
  drawingPageImagePath,
  drawingSourceKey,
  selectDrawingPage,
  selectVectorDrawing,
  tracedDrawingConnections,
} from "../apps/web/src/drawing-model-types.ts";

function fixture() {
  const evidence = {
    source_id: "drawing-D",
    sha256: "a".repeat(64),
    page: 2,
    coordinate_system: "visible-page-points",
  };
  const connection = {
    id: "actual-branched-net",
    page: 2,
    terminal_ids: ["CB-12:T1", "KA-1:11", "TB-1:2"],
    segments: [
      {
        points: [
          [10, 20],
          [70, 20],
        ],
        evidence,
      },
      {
        points: [
          [40, 20],
          [40, 90],
        ],
        evidence,
      },
    ],
  };
  const page = {
    page: 2,
    width_pt: 600,
    height_pt: 800,
    devices: [],
    connections: [connection],
    coverage: { status: "bounded" },
  };
  const drawing = {
    source: {
      document_id: "drawing-D",
      name: "Control D.pdf",
      revision: "D",
      sha256: evidence.sha256,
    },
    side: "target",
    coordinate_system: "visible-page-points",
    physical_scale: null,
    pages: [page],
    coverage: { status: "bounded" },
  };
  return { drawing, page, connection };
}

test("PDF source selection defaults to actual target but preserves an explicit baseline", () => {
  const { drawing } = fixture();
  const baseline = {
    ...drawing,
    side: "baseline",
    source: { ...drawing.source, document_id: "drawing-C", revision: "C" },
  };
  assert.equal(selectVectorDrawing([baseline, drawing], ""), drawing);
  assert.equal(
    selectVectorDrawing([baseline, drawing], drawingSourceKey(baseline)),
    baseline,
  );
  assert.equal(selectVectorDrawing([], ""), undefined);
  const pages = {
    ...drawing,
    pages: [
      { ...drawing.pages[0], page: 1, width_pt: Number.NaN },
      ...drawing.pages,
    ],
  };
  assert.equal(selectDrawingPage(pages, 1).page, 2);
  assert.equal(selectDrawingPage(pages, 2).page, 2);
});

test("image requests stay inside the owned order endpoint with encoded document and page", () => {
  const path = drawingPageImagePath("order /?x", "drawing & other", 2);
  assert.equal(
    path,
    "/api/orders/order%20%2F%3Fx/equipment/evidence?document_id=drawing+%26+other&page=2",
  );
  for (const page of [0, -1, 1.5, Number.NaN, Infinity])
    assert.equal(drawingPageImagePath("order", "doc", page), null);
  assert.equal(drawingPageImagePath("", "doc", 2), null);
  assert.equal(drawingPageImagePath("order", "", 2), null);
});

test("branched terminal network preserves exact separate ink pieces without pairwise routes", () => {
  const { drawing, page, connection } = fixture();
  assert.deepEqual(tracedDrawingConnections(page), [connection]);
  const segments = drawingConnectionSegments(drawing, page, connection);
  assert.equal(segments, connection.segments);
  assert.deepEqual(
    segments.map((segment) => segment.points),
    [
      [
        [10, 20],
        [70, 20],
      ],
      [
        [40, 20],
        [40, 90],
      ],
    ],
  );
  assert.equal(connection.terminal_ids.length, 3);
  assert.equal(segments.length, 2);
});

test("a stale, other-page or unanchored ink trace never gets a plausible overlay", () => {
  for (const edit of [
    (value) => (value.segments[0].evidence.sha256 = "b".repeat(64)),
    (value) => (value.segments[0].evidence.source_id = "another-document"),
    (value) => (value.segments[0].evidence.page = 1),
    (value) => (value.segments[0].evidence.coordinate_system = "mm"),
    (value) => (value.segments[0].evidence = undefined),
    (value) =>
      (value.segments[0].points = [
        [10, 20],
        [Number.NaN, 20],
      ]),
    (value) =>
      (value.segments[0].points = [
        [10, 20],
        [601, 20],
      ]),
    (value) => (value.segments[0].points = [[10, 20]]),
    (value) => (value.page = 1),
  ]) {
    const { drawing, page, connection } = fixture();
    edit(connection);
    assert.equal(drawingConnectionSegments(drawing, page, connection), null);
  }
  const { drawing, page, connection } = fixture();
  assert.equal(
    drawingConnectionSegments(
      { ...drawing, physical_scale: 1 },
      page,
      connection,
    ),
    null,
  );
  assert.equal(
    drawingConnectionSegments(
      { ...drawing, coordinate_system: "native-mm" },
      page,
      connection,
    ),
    null,
  );
});

test("duplicate identities, mixed pages and incomplete endpoint names do not become selectable traces", () => {
  const { page, connection } = fixture();
  assert.deepEqual(
    tracedDrawingConnections({
      ...page,
      connections: [connection, connection],
    }),
    [],
  );
  assert.deepEqual(
    tracedDrawingConnections({
      ...page,
      connections: [{ ...connection, page: 1 }],
    }),
    [],
  );
  assert.deepEqual(
    tracedDrawingConnections({
      ...page,
      connections: [{ ...connection, terminal_ids: ["CB-12:T1"] }],
    }),
    [],
  );
  assert.deepEqual(
    tracedDrawingConnections({
      ...page,
      connections: [{ ...connection, terminal_ids: ["CB-12:T1", "CB-12:T1"] }],
    }),
    [],
  );
});

test("bounded and unknown coverage both keep full circuit and physical dimensions unverified", () => {
  const { drawing, page } = fixture();
  assert.match(
    drawingCoverageLine(drawing, page),
    /physical dimensions remain unverified/,
  );
  assert.match(
    drawingCoverageLine(drawing, { ...page, coverage: { status: "unknown" } }),
    /unaccounted for/,
  );
  assert.match(
    drawingCoverageLine({ ...drawing, coverage: undefined }, page),
    /do not verify the full circuit/,
  );
});
