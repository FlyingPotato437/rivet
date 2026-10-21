import test from "node:test";
import assert from "node:assert/strict";
import { responseNoteWrite, visibleProjectComments } from "../apps/web/src/project-records.ts";

const prior = { id: "a", number: "1", log_reference: "C-001", revision: "B", text: "Cable entry", response: "Bottom entry", author: "Engineer" };
const latest = { ...prior, id: "b", log_reference: "C-002", revision: "C", text: "Confirm trip unit", response: "Please confirm TU-3000" };
const record = { comments: [prior, latest], revision_review: { latest_revision: "C" } };

test("normal review opens the latest return, while earlier comments remain searchable", () => {
  assert.deepEqual(visibleProjectComments(record, "").map(c => c.id), ["b"]);
  assert.deepEqual(visibleProjectComments(record, " bottom ", "all").map(c => c.id), ["a"]);
  assert.deepEqual(visibleProjectComments(record, "C-002", "all").map(c => c.id), ["b"]);
  assert.deepEqual(visibleProjectComments(record, "trip", "B"), []);
});

test("saving a response note preserves review, workflow, source identity and conflict version", () => {
  const c = { ...latest, status: "open", reviewed: false, workflow_status: "needs_review", target_source_id: "source-4", linked_email_id: "email-3", authored_at: "2026-10-12" };
  const original = structuredClone(c);
  const write = responseNoteWrite(c, "Human clarification", 8, "Project manager");
  assert.equal(write.expected_version, 8);
  assert.equal(write.response, "Human clarification");
  assert.equal(write.reviewed, false);
  assert.equal(write.status, "open");
  assert.equal(write.workflow_status, "needs_review");
  assert.equal(write.target_source_id, "source-4");
  assert.equal(write.linked_email_id, "email-3");
  assert.equal(write.number, "1");
  assert.equal(write.responder, "Project manager");
  assert.deepEqual(c, original);
});

test("prepared response text is searchable without replacing a recorded response", () => {
  const w = { ...record, response_log: { comments: { b: { response: "Checked draft revision B-R1, 125 A" } } } };
  assert.deepEqual(visibleProjectComments(w, "125 A", "all").map(c => c.id), ["b"]);
  assert.equal(w.comments[1].response, "Please confirm TU-3000");
});
