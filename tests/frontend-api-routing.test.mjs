import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// Exercise the production TypeScript without introducing a second test runner.
function moduleUrl(source) {
  const output = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText;
  return `data:text/javascript;base64,${Buffer.from(output).toString("base64")}`;
}
const routingUrl = moduleUrl(await readFile(new URL("../apps/web/src/api-routing.ts", import.meta.url), "utf8"));
const { createApiRouting } = await import(routingUrl);
const frontend = "https://app.example.com";
const backend = "https://api.example.com";

test("local API requests stay on the Vite origin", () => {
  const routing = createApiRouting("", "http://127.0.0.1:5178");
  assert.equal(routing.apiUrl("/api/records"), "http://127.0.0.1:5178/api/records");
});

test("hosted requests, downloads and shared PDF sources use the configured API", () => {
  const routing = createApiRouting(`${backend}/`, frontend);
  for (const path of [
    "/api/auth/config",
    "/api/orders/order-1/record/export?format=pdf",
    "/api/documents/document-1/content",
    "/api/shared/share-token/documents/document-1",
  ]) {
    assert.equal(routing.apiUrl(path), backend + path);
    assert.equal(routing.apiUrl(frontend + path), backend + path);
    assert.equal(routing.apiUrl(backend + path), backend + path);
  }
});

test("API routing refuses external origins, credentials, fragments and non-API paths", () => {
  const routing = createApiRouting(backend, frontend);
  for (const path of [
    "https://attacker.example/api/records", "//attacker.example/api/records",
    "https://api.example.com.attacker.example/api/records",
    "https://user:password@api.example.com/api/records",
    "/api/records#token", "/api/../private", "/api", "/apiculture/records",
    "data:text/plain,hello", "javascript:alert(1)",
  ]) {
    assert.equal(routing.isApiUrl(path), false, path);
    assert.throws(() => routing.apiUrl(path), /not a Rivet API endpoint/, path);
  }
});

test("configuration refuses paths, credentials and insecure API origins on HTTPS", () => {
  for (const base of [
    "https://api.example.com/api", "https://api.example.com?secret=value",
    "https://api.example.com#fragment", "https://user:password@api.example.com",
    "http://api.example.com", "file:///tmp/rivet", "/api",
  ]) assert.throws(() => createApiRouting(base, frontend), undefined, base);
});

globalThis.window = { location: { origin: frontend } };
const apiSource = (await readFile(new URL("../apps/web/src/api.ts", import.meta.url), "utf8"))
  .replace('from "./api-routing"', `from ${JSON.stringify(routingUrl)}`)
  .replace("import.meta.env.VITE_API_URL", JSON.stringify(backend));
const { apiFetch, setTokenProvider } = await import(moduleUrl(apiSource));

test("authenticated downloads send a bearer token only to the API and do not follow redirects", async (t) => {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (...args) => {
    requests.push(args);
    return new Response("download", { headers: { "Content-Disposition": 'attachment; filename="record.pdf"' } });
  });
  const clear = setTokenProvider(async () => "session-test-token");
  t.after(clear);
  const response = await apiFetch(`${frontend}/api/orders/order-1/record/export?format=pdf`);
  assert.equal(await response.text(), "download");
  assert.equal(requests[0][0], `${backend}/api/orders/order-1/record/export?format=pdf`);
  assert.equal(requests[0][1].headers.Authorization, "Bearer session-test-token");
  assert.equal(requests[0][1].credentials, "omit");
  assert.equal(requests[0][1].redirect, "error");
});

test("an untrusted URL is rejected before asking Clerk for a session token", async (t) => {
  let tokens = 0;
  const fetch = t.mock.method(globalThis, "fetch", async () => new Response());
  const clear = setTokenProvider(async () => { tokens++; return "session-test-token"; });
  t.after(clear);
  await assert.rejects(apiFetch("https://attacker.example/api/export"), /not a Rivet API endpoint/);
  assert.equal(tokens, 0);
  assert.equal(fetch.mock.callCount(), 0);
});

test("a signed-out shared record still uses its token-scoped public API route", async (t) => {
  let headers;
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(url, `${backend}/api/shared/public-token/documents/document-1`);
    headers = init.headers;
    return new Response("public PDF");
  });
  const response = await apiFetch("/api/shared/public-token/documents/document-1");
  assert.equal(await response.text(), "public PDF");
  assert.equal(headers.Authorization, undefined);
});

test("changing teams while a token is loading aborts the request", async (t) => {
  let resolveToken;
  const fetch = t.mock.method(globalThis, "fetch", async () => new Response());
  const clearOld = setTokenProvider(() => new Promise((resolve) => { resolveToken = resolve; }));
  const pending = apiFetch("/api/records");
  clearOld();
  const clearNew = setTokenProvider(async () => "new-team-session");
  t.after(clearNew);
  resolveToken("old-team-session");
  await assert.rejects(pending, /workspace changed/);
  assert.equal(fetch.mock.callCount(), 0);
});
