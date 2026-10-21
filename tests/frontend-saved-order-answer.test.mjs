import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// Render the actual OrderStory component as a JSX tree with deterministic query
// and hook adapters, so reopening history can be tested without model calls.
const source = await readFile(
  new URL("../apps/web/src/OrderStory.tsx", import.meta.url),
  "utf8",
);
const ast = ts.createSourceFile(
  "OrderStory.tsx",
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);
const component = ast.statements.find(
  (node) => ts.isFunctionDeclaration(node) && node.name?.text === "OrderStory",
);
const names = [];
for (const node of ast.statements) {
  if (!ts.isImportDeclaration(node) || node.importClause?.isTypeOnly) continue;
  const clause = node.importClause;
  if (clause?.name) names.push(clause.name.text);
  if (clause?.namedBindings && ts.isNamedImports(clause.namedBindings)) {
    names.push(
      ...clause.namedBindings.elements
        .filter((entry) => !entry.isTypeOnly)
        .map((entry) => entry.name.text),
    );
  }
}
const React = {
  createElement: (type, props, ...children) => ({
    type,
    props: { ...props, children },
  }),
};
let state, queries, calls, invalidations, data, cursor;
const mocks = Object.fromEntries(names.map((name) => [name, name]));
Object.assign(mocks, {
  useState(initial) {
    const index = cursor++;
    if (!(index in state)) state[index] = initial;
    return [
      state[index],
      (value) => {
        state[index] = value;
      },
    ];
  },
  useEffect() {},
  useRef: () => ({ current: "" }),
  useQuery(options) {
    queries.push(options);
    return { data: data[options.queryKey[0]] };
  },
  useQueryClient: () => ({
    invalidateQueries(options) {
      invalidations.push(options);
      return Promise.resolve();
    },
  }),
  useAccount: () => ({ role: "org:viewer" }),
  useEquipmentView: () => ({}),
  api: async (...args) => {
    calls.push(args);
    return data.answer;
  },
  orderStory: () => [],
  decisionItems: () => [],
  liveWork: () => null,
  orderOutcome: () => ({
    tone: "waiting",
    label: "Watching",
    title: "Watching for mail",
  }),
  replyPreview: () => "",
  pmSentence: (value, fallback) => value || fallback,
  when: (value) => value,
});
globalThis.__orderStoryTestMocks = mocks;
globalThis.location = { hash: "#order/order-1" };
const output = ts.transpileModule(
  `
const React = globalThis.__orderStoryTestReact;
const { ${names.join(", ")} } = globalThis.__orderStoryTestMocks;
${component.getText(ast)}
`,
  {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      jsx: ts.JsxEmit.React,
    },
  },
).outputText;
globalThis.__orderStoryTestReact = React;
const { OrderStory } = await import(
  `data:text/javascript;base64,${Buffer.from(output).toString("base64")}`
);
const result = {
  answer: "The agreed CB-01 trip is 125 A.",
  source_ids: ["spec-r1"],
  comment_ids: [],
  change_ids: [],
  version: 1,
};
function reset(conversation = { order_id: "order-1", messages: [] }) {
  state = [];
  queries = [];
  calls = [];
  invalidations = [];
  data = {
    "project-work": {
      id: "order-1",
      name: "Demo PDU",
      number: "PDU-1",
      status: "up_to_date",
      progress: [],
      document_count: 1,
      thread_count: 0,
    },
    "record-conversation": conversation,
    record: { documents: [], sources: [] },
    answer: result,
  };
}
function render() {
  cursor = 0;
  return OrderStory({ id: "order-1", navigate() {}, notify() {} });
}
function nodes(node) {
  if (!node || typeof node !== "object") return [];
  return [node, ...(node.props?.children || []).flat(Infinity).flatMap(nodes)];
}
function savedButton(tree) {
  return nodes(tree).find(
    (node) =>
      node.type === "button" && node.props.children.includes("Saved answer"),
  );
}

test("saved order history uses the existing read-only, order-scoped conversation query", async () => {
  reset();
  render();
  const query = queries.find(
    (item) => item.queryKey[0] === "record-conversation",
  );
  assert.deepEqual(query.queryKey, ["record-conversation", "order-1"]);
  await query.queryFn();
  assert.deepEqual(calls, [["/orders/order-1/record/conversation"]]);
});

test("Saved answer reopens the latest persisted answer and citations without generating anything", () => {
  const latest = {
    id: "latest",
    question: "What main breaker did we agree?",
    result,
  };
  reset({
    order_id: "order-1",
    messages: [
      {
        id: "earlier",
        question: "Older question",
        result: { ...result, answer: "Older answer" },
      },
      latest,
    ],
  });
  const button = savedButton(render());
  assert.equal(button.props.type, "button");
  button.props.onClick();
  assert.deepEqual(state[0], {
    kind: "answer",
    question: latest.question,
    answer: result,
  });
  assert.deepEqual(calls, []);
  assert.deepEqual(invalidations, []);
});

test("no Saved answer is offered for empty history or another order's cached conversation", () => {
  for (const conversation of [
    { order_id: "order-1", messages: [] },
    {
      order_id: "other-order",
      messages: [{ question: "Private question", result }],
    },
  ]) {
    reset(conversation);
    assert.equal(savedButton(render()), undefined);
  }
});

test("a newly asked question refreshes only the current order's saved conversation", async () => {
  reset();
  render();
  state[5] = "What main breaker did we agree?";
  const form = nodes(render()).find((node) => node.type === "form");
  await form.props.onSubmit({ preventDefault() {} });
  assert.deepEqual(calls, [
    [
      "/orders/order-1/record/ask",
      { question: state[0].question, history: [] },
    ],
  ]);
  assert.deepEqual(invalidations, [
    { queryKey: ["record-conversation", "order-1"] },
  ]);
  assert.equal(state[5], "");
});
