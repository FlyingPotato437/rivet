import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import ts from "typescript";

// Exercise the production click handler with a small DOM/JSX adapter. No browser,
// session credentials, or second test runner are required for download regressions.
function moduleUrl(source, jsx = false) {
  const output = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      jsx: jsx ? ts.JsxEmit.React : undefined,
    },
  }).outputText;
  return `data:text/javascript;base64,${Buffer.from(output).toString("base64")}`;
}
const frontend = "https://workspace.example.com";
const backend = "https://api.example.com";
globalThis.window = {
  location: { origin: frontend },
  setTimeout: () => 0,
  clearTimeout: () => {},
};
const routing = moduleUrl(
  await readFile(
    new URL("../apps/web/src/api-routing.ts", import.meta.url),
    "utf8",
  ),
);
const apiSource = (
  await readFile(new URL("../apps/web/src/api.ts", import.meta.url), "utf8")
)
  .replace('from "./api-routing"', `from ${JSON.stringify(routing)}`)
  .replace("import.meta.env.VITE_API_URL", JSON.stringify(backend));
const apiUrl = moduleUrl(apiSource);
const { setTokenProvider } = await import(apiUrl);
const source = await readFile(
  new URL("../apps/web/src/EquipmentCorrections.tsx", import.meta.url),
  "utf8",
);
const parsed = ts.createSourceFile(
  "EquipmentCorrections.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const component = parsed.statements.find(
  (node) =>
    ts.isFunctionDeclaration(node) && node.name?.text === "CorrectionFile",
);
assert.ok(component, "the shared protected download component exists");
const typesUrl = new URL(
  "../apps/web/src/equipment-correction-types.ts",
  import.meta.url,
).href;
const { CorrectionFile } = await import(
  moduleUrl(
    `
import { apiFetch } from ${JSON.stringify(apiUrl)};
import { correctionArtifactPath } from ${JSON.stringify(typesUrl)};
const useState = (value) => [value, () => {}];
const React = { createElement: (type, props, ...children) => ({ type, props: { ...props, children } }) };
const FileText = "file-icon", CircleNotch = "busy-icon", ArrowUpRight = "open-icon", DownloadSimple = "download-icon";
${component.getText(parsed)}
`,
    true,
  )
);
const artifact = {
  name: "BOM-B-R1.xlsx",
  media_type:
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  url: "/orders/order-1/equipment/corrections/bundle-1/files/BOM-B-R1.xlsx",
};
function button(node) {
  if (node?.type === "button") return node;
  return node?.props?.children?.flat(Infinity).map(button).find(Boolean);
}
async function click(file) {
  const view = CorrectionFile({
    orderId: "order-1",
    bundleId: "bundle-1",
    artifact: file,
  });
  button(view).props.onClick();
  await setImmediate();
}

test("order outcome files reuse the protected download component", async () => {
  const panel = await readFile(
    new URL("../apps/web/src/OrderPanels.tsx", import.meta.url),
    "utf8",
  );
  assert.match(
    panel,
    /<CorrectionFile\s+orderId=\{order\.id\}\s+bundleId=\{bundle\.id\}/,
  );
  assert.doesNotMatch(panel, /href=\{file\.url\}/);
});

test("clicking a corrected BOM downloads from the authenticated API with its original filename", async (t) => {
  const requests = [],
    downloads = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    requests.push({ url, init });
    return new Response("workbook bytes");
  });
  const previous = globalThis.document;
  globalThis.document = {
    createElement: () => {
      const link = {
        click() {
          downloads.push({ href: this.href, name: this.download });
        },
      };
      return link;
    },
  };
  t.after(() => {
    globalThis.document = previous;
  });
  t.after(setTokenProvider(async () => "test-session"));
  await click(artifact);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, backend + "/api" + artifact.url);
  assert.equal(requests[0].init.headers.Authorization, "Bearer test-session");
  assert.equal(requests[0].init.credentials, "omit");
  assert.equal(requests[0].init.redirect, "error");
  assert.equal(downloads[0].name, artifact.name);
  assert.match(downloads[0].href, /^blob:/);
});

test("a corrected file for another order or an external origin never requests credentials or content", async (t) => {
  let tokens = 0;
  const fetch = t.mock.method(globalThis, "fetch", async () => new Response());
  t.after(
    setTokenProvider(async () => {
      tokens++;
      return "test-session";
    }),
  );
  for (const url of [
    "https://attacker.example/file.xlsx",
    artifact.url.replace("order-1", "other-order"),
  ]) {
    await click({ ...artifact, url });
  }
  assert.equal(tokens, 0);
  assert.equal(fetch.mock.callCount(), 0);
});

test("a corrected PDF opens the authenticated blob rather than its protected URL", async (t) => {
  const preview = {
    opener: {},
    document: { title: "", body: { style: {} } },
    location: {},
    close() {},
  };
  window.open = () => preview;
  t.mock.method(globalThis, "fetch", async () => new Response("PDF bytes"));
  t.after(setTokenProvider(async () => "test-session"));
  await click({
    ...artifact,
    name: "Drawing-B-R1.pdf",
    media_type: "application/pdf",
    url: artifact.url.replace("BOM-B-R1.xlsx", "Drawing-B-R1.pdf"),
  });
  assert.equal(preview.opener, null);
  assert.match(preview.location.href, /^blob:/);
});

test("an expired session closes the PDF preview without a download", async (t) => {
  let closed = false;
  const preview = {
    document: { body: { style: {} } },
    location: {},
    close() {
      closed = true;
    },
  };
  window.open = () => preview;
  t.mock.method(globalThis, "fetch", async () =>
    Response.json({ detail: "Sign in again." }, { status: 401 }),
  );
  t.after(setTokenProvider(async () => "expired-session"));
  await click({ ...artifact, media_type: "application/pdf" });
  assert.equal(closed, true);
  assert.equal(preview.location.href, undefined);
});
