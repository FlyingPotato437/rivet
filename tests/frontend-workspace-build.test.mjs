import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { allowsAuthenticationMode } from "../apps/web/src/auth-policy.ts";

const root = fileURLToPath(new URL("../", import.meta.url));

test("deployed private surfaces require Clerk and cannot accept development auth", () => {
  assert.equal(allowsAuthenticationMode("clerk", false), true);
  assert.equal(allowsAuthenticationMode("local", false), false);
  assert.equal(allowsAuthenticationMode("", false), false);
  assert.equal(allowsAuthenticationMode("unexpected", false), false);
  assert.equal(allowsAuthenticationMode("local", true), true);
  assert.equal(allowsAuthenticationMode("clerk", true), true);
});

test("the independent workspace release includes authenticated work feed and source viewing", () => {
  const assets = join(root, "apps/web/dist-workspace/assets");
  const files = readdirSync(assets);
  const bundle = files
    .filter((f) => f.endsWith(".js"))
    .map((f) => readFileSync(join(assets, f), "utf8"))
    .join("\n");
  assert.ok(files.some((f) => f.startsWith("PrivateRoot-")));
  assert.ok(files.some((f) => f.startsWith("App-")));
  assert.ok(files.some((f) => f.startsWith("pdf.worker")));
  assert.ok(bundle.includes("/api/auth/config"));
  assert.ok(bundle.includes("Secure sign-in is not configured"));
  assert.ok(bundle.includes("#work"));
});

test("workspace hosting remains separate from the marketing release", () => {
  const workspace = JSON.parse(
    readFileSync(join(root, "apps/web/vercel.workspace.json"), "utf8"),
  );
  const marketing = JSON.parse(
    readFileSync(join(root, "apps/web/vercel.json"), "utf8"),
  );
  assert.equal(workspace.buildCommand, "npm run build:workspace");
  assert.equal(workspace.outputDirectory, "dist-workspace");
  assert.equal(marketing.outputDirectory, "dist");
  assert.notEqual(marketing.buildCommand, workspace.buildCommand);
  assert.ok(
    workspace.rewrites.some(
      (r) => r.source === "/(.*)" && r.destination === "/index.html",
    ),
  );
  assert.ok(
    workspace.headers[0].headers.some(
      (h) => h.key === "X-Robots-Tag" && h.value.includes("noindex"),
    ),
  );
});
