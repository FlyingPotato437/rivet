import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
import * as orderFlow from "../apps/web/src/order-flow.ts";
import { visibleProjectComments } from "../apps/web/src/project-records.ts";

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
  ...orderFlow,
  visibleProjectComments,
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
  orderOutcome: () =>
    data?.outcome || {
      tone: "waiting",
      label: "Watching",
      title: "Watching for mail",
    },
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
  globalThis.location.hash = "#order/order-1";
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
      outlook: { native: false, url: "" },
    },
    "record-conversation": conversation,
    record: { documents: [], sources: [], comments: [] },
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

test("PDF-only orders show extracted requests and their prepared findings without native CAD", () => {
  reset();
  data.record.comments = [
    {
      id: "c1",
      text: "Change the nameplate",
      source_ids: ["source-1"],
      document_id: "drawing-1",
    },
    {
      id: "mail",
      text: "Please see attached",
      source_ids: ["mail-source"],
      document_id: "email-1",
    },
  ];
  data.record.response_log = {
    comments: {
      c1: {
        status: "prepared",
        label: "Response prepared",
        response: "Use the customer-specified nameplate.",
      },
      mail: {
        status: "correspondence",
        label: "Correspondence",
        response: "Email retained.",
      },
    },
  };
  const tree = nodes(render());
  assert.ok(
    tree.some(
      (node) =>
        node.type === "p" &&
        node.props.children.includes("Change the nameplate"),
    ),
  );
  assert.ok(
    tree.some(
      (node) =>
        node.type === "blockquote" &&
        node.props.children.includes("Use the customer-specified nameplate."),
    ),
  );
  assert.ok(
    !tree.some(
      (node) =>
        node.type === "p" &&
        node.props.children.includes("Please see attached"),
    ),
  );
  assert.deepEqual(calls, []);
});

test("corrected-files view retains equipment checks and never starts preparation on navigation", () => {
  reset();
  globalThis.location.hash = "#order/order-1?review=changes";
  const tree = nodes(render());
  assert.ok(tree.some((node) => node.type === "CorrectedFiles"));
  assert.ok(tree.some((node) => node.type === "RevisionCheck"));
  assert.ok(
    tree.some(
      (node) =>
        node.type === "DrawingRedesignReview" &&
        node.props.screen === "changes",
    ),
  );
  assert.deepEqual(calls, []);
});

test("a prepared file-only reply retains the Outlook draft action without claiming the mailbox is disconnected", () => {
  reset();
  location.hash = "#order/order-1?review=reply";
  data.outcome = {
    tone: "prepared",
    label: "Response prepared",
    title: "Response prepared",
  };
  data["project-work"].status = "prepared";
  data["project-work"].reply_preparation = {
    prepared_reply: { id: "reply-1" },
  };
  const tree = nodes(render());
  const panel = tree.find(
    (node) => node.props?.["aria-label"] === "Outlook reply status",
  );
  assert.ok(nodes(panel).some((node) => node.type === "ReplyAction"));
  assert.ok(
    !nodes(panel).some((node) =>
      node.props.children.includes("Outlook not connected"),
    ),
  );
  assert.deepEqual(calls, []);
});

test("activity links open the timeline even though specialist tools start collapsed", () => {
  reset();
  location.hash = "#order/order-1?event=saved-change";
  assert.equal(
    nodes(render()).find((node) => node.props?.className === "order-more-tools")
      .props.open,
    true,
  );
  location.hash = "#order/order-1";
  assert.equal(
    nodes(render()).find((node) => node.props?.className === "order-more-tools")
      .props.open,
    false,
  );
});
