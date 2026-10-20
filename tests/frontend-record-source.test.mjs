import test from "node:test";
import assert from "node:assert/strict";
import {
  resolveRecordSource,
  sortDocumentSources,
} from "../apps/web/src/record-source-navigation.ts";

const record = {
  documents: [{ id: "returned" }, { id: "drawing" }],
  sources: [
    { id: "annotation-A", document_id: "returned" },
    { id: "annotation-B", document_id: "returned" },
    { id: "sheet", document_id: "drawing" },
    { id: "unlinked-text", document_id: "returned" },
  ],
  comments: [
    {
      id: "comment-A",
      document_id: "returned",
      source_ids: ["annotation-A"],
      target_source_id: "sheet",
    },
    {
      id: "comment-B",
      document_id: "returned",
      source_ids: ["annotation-B"],
      target_source_id: "",
    },
  ],
};

test("a source link selects the exact annotation, not the comment drawing target", () => {
  assert.deepEqual(resolveRecordSource(record, "annotation-A"), {
    sourceId: "annotation-A",
    documentId: "returned",
    commentId: "comment-A",
  });
});
test("switching source links within an order resolves the new comment", () => {
  assert.equal(
    resolveRecordSource(record, "annotation-B", "comment-A").commentId,
    "comment-B",
  );
});
test("a source without a recorded comment association does not choose an unrelated comment", () => {
  assert.equal(
    resolveRecordSource(record, "unlinked-text", "comment-A").commentId,
    null,
  );
});
test("unknown and foreign evidence never falls back to the first document", () => {
  assert.equal(resolveRecordSource(record, "other-order-source"), null);
});
test("document links open the document without inventing an annotation", () => {
  assert.deepEqual(resolveRecordSource(record, "drawing"), {
    sourceId: "drawing",
    documentId: "drawing",
    commentId: null,
  });
});

test("source text follows page and numeric line order without mutating evidence", () => {
  const sources = [
    { id: "ten", location: { page: 1, line: 10 } },
    { id: "next-page", location: { page: 2, line: 1 } },
    { id: "two", location: { page: 1, line: 2 } },
    { id: "one", location: { page: 1, line: 1 } },
  ];
  assert.deepEqual(
    sortDocumentSources(sources).map((s) => s.id),
    ["one", "two", "ten", "next-page"],
  );
  assert.equal(sources[0].id, "ten");
});

test("spreadsheet cells use natural ordering and unlocated excerpts remain stable", () => {
  const sources = [
    { id: "unlocated-first", location: {} },
    { id: "ten", location: { cells: "A10" } },
    { id: "two", location: { cells: "A2" } },
    { id: "unlocated-second", location: {} },
  ];
  assert.deepEqual(
    sortDocumentSources(sources).map((s) => s.id),
    ["two", "ten", "unlocated-first", "unlocated-second"],
  );
});
