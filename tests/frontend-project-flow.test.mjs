import test from "node:test";
import assert from "node:assert/strict";
import { projectMatches } from "../apps/web/src/project-flow.ts";

const project = {
  name: "North campus switchgear",
  number: "JOB-24118",
  customer: "Campus group",
  status: "prepared",
  last_communication: { subject: "Rev C cable entry question" },
};
test("project search works with the name, stable job, customer and email subject", () => {
  for (const search of [
    " north CAMPUS ",
    "24118",
    "campus group",
    "cable entry",
  ])
    assert.equal(projectMatches(project, search, "all"), true);
  assert.equal(projectMatches(project, "another order", "all"), false);
});
test("ready includes prepared responses but never claims a waiting or working project is ready", () => {
  assert.equal(projectMatches(project, "", "ready"), true);
  for (const status of ["handling", "waiting_customer", "needs_you"])
    assert.equal(projectMatches({ ...project, status }, "", "ready"), false);
  assert.equal(
    projectMatches(
      { ...project, status: "waiting_customer" },
      "north",
      "waiting_customer",
    ),
    true,
  );
});
