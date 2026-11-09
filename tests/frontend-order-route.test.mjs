import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const source = readFileSync(
  new URL("../apps/web/src/order-route.ts", import.meta.url),
  "utf8",
);
const exports = {};
vm.runInNewContext(
  ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText,
  { exports, URLSearchParams },
);
const route = (query) =>
  exports.usesEngineeringWorkspace(new URLSearchParams(query));

test("default order and unified review stay out of specialist engineering tools", () => {
  for (const query of [
    "",
    "review=requests",
    "review=changes",
    "review=reply",
    "stage=unknown",
  ])
    assert.equal(route(query), false, query);
});

test("all existing explicit engineering stages remain reachable", () => {
  for (const stage of [
    "requirements",
    "configure",
    "drawings",
    "review",
    "release",
  ])
    assert.equal(route(`stage=${stage}`), true, stage);
});

test("approval and evidence links keep priority over engineering stage", () => {
  for (const query of [
    "approval=123",
    "source=123",
    "comment=123",
    "tab=documents",
    "view=advanced",
    "review=reply",
  ])
    assert.equal(route(`stage=configure&${query}`), false, query);
});
