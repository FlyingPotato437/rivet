import type { ProjectFlow, ProjectCommunication } from "./project-flow";
import type { LifecycleGate, LifecyclePackage } from "./lifecycle-work";
import {
  actionExecutionRows,
  actionLabel,
  type ActionExecutionView,
} from "./action-execution.ts";

/** Plain values from the server, used to write specific sentences. */
export type EventFacts = {
  operation?: string;
  status?: string;
  counts?: Record<string, number>;
  total?: string | number;
  currency?: string;
  change_order_total?: string | number;
  purchase_order_number?: string;
  part_number?: string;
  lead_time_days?: string | number;
  current_ship_date?: string;
  proposed_ship_date?: string;
  mismatches_count?: number;
  scope_mismatches_count?: number;
  exceptions_count?: number;
  action?: string;
  mode?: string;
  target?: string;
  name?: string;
  comments_total?: number;
  comments_prepared?: number;
  comments_failed?: number;
  sources?: number;
  prepared?: string[];
};
export type WorkingItem = {
  order_id: string;
  number: string;
  status: string;
  comments_total: number;
  comments_prepared: number;
};

export type ActivityEvent = {
  id: string;
  order_id: string;
  number: string;
  at: string;
  kind: string;
  title: string;
  detail: string;
  outlook_url: string;
  actor?: "rivet" | "people" | "systems";
  event_type?: string;
  facts?: EventFacts;
};
export type WaitingItem = {
  id: string;
  order_id: string;
  due_at: string;
  sent_at: string;
  status: string;
  outlook_url: string;
};
export type DailyBrief = {
  prepared_runs: number;
  confirmed_sent_replies: number;
  decisions: {
    order_id: string;
    number: string;
    package_id: string;
    title: string;
  }[];
  waiting_items_due: number;
  waiting_items?: WaitingItem[];
  items: ActivityEvent[];
  working?: WorkingItem[];
};
export type DecisionItem = {
  id: string;
  orderId: string;
  number: string;
  package: LifecyclePackage;
  gate: LifecycleGate;
  title: string;
  reason: string;
  at: string;
  version?: number;
};
export type StoryEvent = {
  id: string;
  orderId: string;
  at: string;
  title: string;
  subtitle?: string;
  state: "done" | "current" | "upcoming" | "attention";
  raw?: ActivityEvent;
  email?: ProjectCommunication;
  package?: LifecyclePackage;
  reply?: boolean;
  sourceIds?: string[];
  earlier?: ActivityEvent[];
  execution?: ActionExecutionView;
  relatedIds?: string[];
};

/** Technical originals remain available in Activity and the details drawer. */
export function pmSentence(
  text: string,
  fallback = "Rivet updated the order.",
) {
  const first =
    text
      .replace(/\r/g, "")
      .split("\n")
      .map((line) =>
        line.replace(/^\s*(?:#{1,6}\s*|[-*]\s*|\d+[.)]\s*)/, "").trim(),
      )
      .find(
        (line) =>
          !!line &&
          !/^(?:prepared|summary|completed|outcome|work completed|result)[:.]?$/i.test(
            line,
          ),
      ) || "";
  const clean = first
    .replace(/prepared package snapshot/gi, "Rivet’s draft")
    .replace(/execution receipts and source versions/gi, "Details")
    .replace(
      /recorded\s*·\s*reuse needs approval/gi,
      "Rivet learned from your edits",
    )
    .replace(/release blockers/gi, "What’s left before release")
    .replace(/obligations/gi, "Promises to the customer")
    .replace(/downstream verification/gi, "Final check")
    .replace(/[*_`]/g, "");
  if (
    !clean ||
    /\b(receipts?|hash(?:es)?|snapshots?|gates?|adapters?|provenance)\b|\bin test\b|local[_ -](?:demo|test)|[a-f\d]{32,}/i.test(
      clean,
    )
  )
    return fallback;
  return clean.length > 180
    ? clean.slice(0, 177).replace(/\s+\S*$/, "") + "…"
    : clean;
}

export function activityActor(
  event: ActivityEvent,
): "rivet" | "people" | "systems" {
  if (event.actor) return event.actor;
  if (
    event.kind === "work" ||
    event.kind === "attention" ||
    event.detail === "Rivet"
  )
    return "rivet";
  if (
    event.kind === "sent" ||
    event.kind === "decision" ||
    event.detail === "Your team"
  )
    return "people";
  return "systems";
}

const plural = (n: number, one: string, many = one + "s") =>
  `${n} ${n === 1 ? one : many}`;

function joinList(parts: string[]) {
  return parts.length < 2
    ? parts.join("")
    : `${parts.slice(0, -1).join(", ")} and ${parts.at(-1)}`;
}

export function money(value: unknown, currency = "USD") {
  const n = Number(value);
  if (value == null || value === "" || !Number.isFinite(n)) return "";
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: 0,
    }).format(n);
  } catch {
    return `${currency} ${Math.round(n).toLocaleString("en-US")}`;
  }
}

function poLabel(n?: string) {
  return n ? (/^PO\b/i.test(n) ? n : `PO ${n}`) : "";
}

function shortDate(value?: string) {
  if (!value || !/^\d{4}-\d{2}-\d{2}/.test(value)) return "";
  const d = new Date(value.slice(0, 10) + "T00:00:00Z");
  return Number.isNaN(d.getTime())
    ? ""
    : new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      }).format(d);
}

/** The same plain values the server sends as event facts, read from a package. */
export function packageFacts(p: LifecyclePackage): EventFacts {
  const payload = (p.payload || {}) as Record<string, unknown>;
  const facts: EventFacts = { operation: p.operation, status: p.status };
  if (payload.counts && typeof payload.counts === "object")
    facts.counts = payload.counts as Record<string, number>;
  for (const key of [
    "total",
    "currency",
    "purchase_order_number",
    "part_number",
    "lead_time_days",
    "current_ship_date",
    "proposed_ship_date",
  ] as const)
    if (["string", "number"].includes(typeof payload[key]))
      (facts as Record<string, unknown>)[key] = payload[key];
  const change = payload.change_order as { total?: unknown } | undefined;
  if (change && ["string", "number"].includes(typeof change.total))
    facts.change_order_total = change.total as string | number;
  for (const key of ["mismatches", "scope_mismatches", "exceptions"] as const)
    if (Array.isArray(payload[key]))
      (facts as Record<string, unknown>)[key + "_count"] = (
        payload[key] as unknown[]
      ).length;
  return facts;
}

/** What Rivet prepared, in one specific sentence. Falls back to the package title. */
export function preparedSentence(f: EventFacts, title: string) {
  const counts = f.counts || {};
  const currency = f.currency || "USD";
  if (f.operation === "revision_review") {
    const parts: string[] = [];
    if (counts.answer)
      parts.push(`drafted answers to ${plural(counts.answer, "comment")}`);
    if (counts.text_edit)
      parts.push(
        `edited ${plural(counts.text_edit, "drawing copy", "drawing copies")}`,
      );
    if (counts.layout_task)
      parts.push(
        `sent ${plural(counts.layout_task, "layout change")} to a drafter`,
      );
    const price = money(f.change_order_total, currency);
    if (price) parts.push(`priced the extra scope at ${price}`);
    if (parts.length) return `Rivet ${joinList(parts)}.`;
  }
  if (f.operation === "quote") {
    const price = money(f.total, currency);
    if (price)
      return `Rivet prepared a ${price} quote${f.exceptions_count ? ` with ${plural(f.exceptions_count, "exception")}` : ""}.`;
  }
  if (f.operation === "kickoff") {
    const po = poLabel(f.purchase_order_number) || "the purchase order";
    const diffs = (f.mismatches_count || 0) + (f.scope_mismatches_count || 0);
    if (f.mismatches_count != null || f.scope_mismatches_count != null)
      return diffs
        ? `Rivet checked ${po} against the quote and found ${plural(diffs, "difference")}.`
        : `Rivet checked ${po} against the quote. It matches.`;
  }
  if (f.operation === "purchasing" && f.part_number) {
    const from = shortDate(f.current_ship_date),
      to = shortDate(f.proposed_ship_date);
    const lead = f.lead_time_days ? `a ${f.lead_time_days}-day lead time` : "";
    if (from && to)
      return `Rivet flagged ${f.part_number}: ${lead ? lead + " moves" : "it moves"} the ship date from ${from} to ${to}.`;
    return `Rivet flagged ${f.part_number}${lead ? `: ${lead}` : ""}.`;
  }
  return pmSentence(title, "Rivet prepared changes for this order.");
}

export function eventSentence(e: ActivityEvent) {
  const f = e.facts || {};
  if (e.event_type === "enterprise_publication") {
    const where = /sharepoint/i.test(f.target || "")
      ? "SharePoint"
      : "the shared folder";
    return e.title.endsWith("published_copy")
      ? f.name
        ? `Rivet published ${f.name} to ${where}.`
        : "Rivet published the approved file copy."
      : "Rivet paused a connected file update for someone to check.";
  }
  if (e.event_type === "workflow_action") {
    const label = f.action ? actionLabel(f.action) : undefined;
    if (label)
      return f.mode === "local_http_demo"
        ? `Rivet rehearsed a step in ${label.system}: ${label.label.toLowerCase()}.`
        : `Rivet updated ${label.system}: ${label.label.toLowerCase()}.`;
    return "Rivet recorded work across connected systems.";
  }
  if (e.event_type === "lifecycle_prepared" && f.operation)
    return preparedSentence(f, e.title);
  if (e.kind === "work" && f.comments_total) {
    const sources = f.sources ? ` using ${plural(f.sources, "source")}` : "";
    const failed = f.comments_failed
      ? `, ${f.comments_failed} need another look`
      : "";
    return `Rivet drafted answers to ${f.comments_prepared ?? 0} of ${plural(f.comments_total, "comment")}${sources}${failed}.`;
  }
  if (e.kind === "received")
    return `Received ${pmSentence(e.title.replace(/^File received\s*·\s*/i, ""), "a new file")}`;
  if (e.kind === "sent")
    return pmSentence(e.title, "Your team sent the reply from Outlook.");
  if (e.kind === "attention")
    return "Rivet paused this task before finishing. Open it to see what’s missing.";
  if (e.kind === "work") {
    const sentence = pmSentence(
      e.detail,
      "Rivet prepared responses and checked the available sources.",
    ).split(/(?<=[.!?])\s+(?=[A-Z])/)[0];
    return /^(Rivet|The |Your |A |An )/i.test(sentence)
      ? sentence
      : `Rivet ${sentence.charAt(0).toLowerCase()}${sentence.slice(1)}`;
  }
  return pmSentence(e.title);
}

function needsAttention(e: ActivityEvent) {
  return (
    e.kind === "attention" ||
    (e.kind === "work" &&
      /no (?:changes|change set) (?:were|was) prepared|(?:could not|couldn't|unable to) (?:stage|prepare)|staging attempts failed/i.test(
        e.detail,
      )) ||
    (e.event_type === "enterprise_publication" &&
      !e.title.endsWith("published_copy"))
  );
}

export function decisionItems(
  packages: LifecyclePackage[],
  order: { id: string; number: string; version?: number; customer?: string },
): DecisionItem[] {
  const who = order.customer ? ` for ${order.customer}` : "";
  return packages
    .filter(
      (p) =>
        p.status === "awaiting_decision" &&
        p.evidence_current &&
        !p.blockers.length,
    )
    .flatMap((p) =>
      p.pending_gates.map((gate) => {
        const f = packageFacts(p);
        const price = money(
          f.change_order_total ?? f.total,
          f.currency || "USD",
        );
        const edits = f.counts?.text_edit || 0;
        const po = f.purchase_order_number
          ? ` on ${poLabel(f.purchase_order_number)}`
          : "";
        const ship = shortDate(f.proposed_ship_date);
        const added = (
          p.payload.change_order as
            | { lines?: { description?: string }[] }
            | undefined
        )?.lines
          ?.map((l) => l.description)
          .filter(Boolean)
          .slice(0, 2)
          .join(", ");
        const labels: Record<LifecycleGate, string> = {
          money:
            p.operation === "quote"
              ? `Approve the ${price ? price + " " : ""}quote${who}`
              : `Approve ${price ? price + " " : "the price for this "}change order${who}`,
          scope: added
            ? `Confirm the added scope: ${added}`
            : p.operation === "kickoff"
              ? `Confirm the scope${po}${who}`
              : "Confirm the customer’s scope change",
          engineering: edits
            ? `Check ${plural(edits, "drawing edit")}${who}`
            : "Check the proposed drawing changes",
          customer_commitment:
            p.operation === "quote"
              ? `Approve sending the ${price ? price + " " : ""}quote${who}`
              : p.operation === "purchasing"
                ? `Approve the new ship date${ship ? `: ${ship}` : ""}`
                : p.operation === "revision_review"
                  ? `Approve the reply${order.customer ? ` to ${order.customer}` : " to the customer"}`
                  : "Approve the proposed customer response",
        };
        return {
          id: `${order.id}:${p.id}:${gate}`,
          orderId: order.id,
          number: order.number,
          package: p,
          gate,
          title: labels[gate],
          reason: preparedSentence(f, p.title),
          at: p.prepared_at,
          version: order.version,
        };
      }),
    )
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
}

export function todayReplies(orders: ProjectFlow[]) {
  return orders.filter((p) => ["ready", "prepared"].includes(p.status));
}

export function atRisk(
  orders: ProjectFlow[],
  waiting: WaitingItem[] = [],
  now = Date.now(),
) {
  const map = new Map(orders.map((p) => [p.id, p]));
  const newest = new Map<string, WaitingItem>();
  for (const item of waiting) {
    const order = map.get(item.order_id);
    // Never turn an ordinary waiting order into a risk, or show an old timer
    // after a newer reply. These are the backend's actual waiting timers.
    if (
      !order ||
      order.status !== "waiting_customer" ||
      Date.parse(item.due_at) > now ||
      !order.last_sent_at ||
      Date.parse(order.last_sent_at) !== Date.parse(item.sent_at)
    )
      continue;
    if (
      !newest.has(order.id) ||
      Date.parse(item.sent_at) > Date.parse(newest.get(order.id)!.sent_at)
    )
      newest.set(order.id, item);
  }
  return [...newest.values()].map((item) => ({
    ...item,
    order: map.get(item.order_id)!,
    days: Math.max(1, Math.floor((now - Date.parse(item.sent_at)) / 86400000)),
    drafted: ["drafted", "existing_draft"].includes(item.status),
  }));
}

export function rivetToday(events: ActivityEvent[], now = Date.now()) {
  const recent = events
    .filter(
      (e) =>
        Date.parse(e.at) >= now - 86400000 &&
        activityActor(e) === "rivet" &&
        !needsAttention(e),
    )
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const grouped: { event: ActivityEvent; title: string; count: number }[] = [];
  for (const event of recent) {
    const title = eventSentence(event);
    const existing = grouped.find(
      (g) => g.event.order_id === event.order_id && g.title === title,
    );
    if (existing) existing.count++;
    else grouped.push({ event, title, count: 1 });
  }
  return grouped;
}

export function liveWork(
  runs: {
    status: string;
    comment_progress?: {
      total: number;
      prepared: number;
      failed: number;
      working: number;
    } | null;
  }[],
) {
  const run = runs.find((r) => ["queued", "running"].includes(r.status));
  if (!run) return null;
  const p = run.comment_progress;
  if (p?.total)
    return {
      text: `Rivet is working: ${p.prepared} of ${p.total} comments prepared${p.failed ? ` · ${p.failed} need another look` : ""}.`,
      total: p.total,
      completed: p.prepared,
    };
  return {
    text:
      run.status === "queued"
        ? "Rivet is getting ready to check this order…"
        : "Rivet is checking the order and its supporting documents…",
    total: 0,
    completed: 0,
  };
}

/** Today's live line: the busiest run's comment progress, or a plain count. */
export function workingSentence(items: WorkingItem[], handling = 0) {
  const counted = items
    .filter((i) => i.comments_total > 0)
    .sort(
      (a, b) =>
        b.comments_total - b.comments_prepared -
        (a.comments_total - a.comments_prepared),
    );
  const orders = new Set(items.map((i) => i.order_id)).size || handling;
  if (!orders) return null;
  const more = orders > 1 ? ` · ${plural(orders - 1, "more order")}` : "";
  const top = counted[0];
  if (top)
    return {
      text: `Rivet is working on ${top.number}: ${top.comments_prepared} of ${plural(top.comments_total, "comment")} prepared${more}.`,
      orderId: top.order_id,
      done: top.comments_prepared,
      total: top.comments_total,
    };
  return {
    text: `Rivet is working on ${plural(orders, "order")}.`,
    orderId: items[0]?.order_id,
    done: 0,
    total: 0,
  };
}

export function orderStory(
  order: ProjectFlow,
  events: ActivityEvent[],
  packages: LifecyclePackage[] = [],
  execution?: ActionExecutionView,
): StoryEvent[] {
  const executionRows = execution
    ? actionExecutionRows(execution.attempts)
    : [];
  const groupedActions = events.filter(
    (e) =>
      e.event_type === "workflow_action" &&
      executionRows.some((r) => e.title.startsWith(r.action + ":")),
  );
  const pastPauses = ["ready", "prepared", "waiting_customer"].includes(
    order.status,
  )
    ? events.filter(
        (e) =>
          needsAttention(e) &&
          Date.parse(e.at) <
            Date.parse(
              order.reply_preparation?.prepared_reply?.created_at ||
                order.updated_at,
            ),
      )
    : [];
  const story: StoryEvent[] = events
    .filter(
      (e) =>
        !pastPauses.includes(e) &&
        !groupedActions.includes(e) &&
        (e.kind !== "sent" ||
          !(order.communications || []).some((m) => m.id === e.id)),
    )
    .map((e) => ({
      id: `event:${e.kind}:${e.id}`,
      orderId: order.id,
      at: e.at,
      title: eventSentence(e),
      subtitle:
        activityActor(e) === "rivet"
          ? "Rivet"
          : activityActor(e) === "people"
            ? "Your team"
            : "Order update",
      state: needsAttention(e) ? "attention" : "done",
      raw: e,
    }));
  if (execution?.attempts.length) {
    const rows = executionRows,
      done = rows.filter((r) => r.applied),
      remaining = rows.length - done.length;
    if (rows.length)
      story.push({
        id: "connected-work",
        orderId: order.id,
        at: rows
          .map((r) => r.attempt.at)
          .sort()
          .at(-1)!,
        title: done.length
          ? `Rivet rehearsed ${done.length} ${done.length === 1 ? "update" : "updates"} across ${[...new Set(done.map((r) => r.system))].join(", ")}.`
          : "Rivet attempted updates across connected systems.",
        subtitle: remaining
          ? `${remaining} ${remaining === 1 ? "update needs" : "updates need"} a check before continuing.`
          : "Open to see the actions and where they ran.",
        state: remaining ? "attention" : "done",
        execution,
        relatedIds: groupedActions.map((e) => `event:${e.kind}:${e.id}`),
      });
  }
  for (const email of order.communications || [])
    story.push({
      id: `email:${email.id}`,
      orderId: order.id,
      at: email.at,
      title:
        email.direction === "sent"
          ? "Your team sent the reply from Outlook."
          : `${email.sender || "The customer"} emailed about ${pmSentence(email.subject, "this order")}`,
      subtitle: email.subject,
      state: "done",
      email,
    });
  // Describe actual prepared artifacts, never claim a draft was published.
  for (const p of packages.filter(
    (p) => !["superseded", "rejected"].includes(p.status),
  ))
    story.push({
      id: `prepared:${p.id}`,
      orderId: order.id,
      at: p.prepared_at,
      title: preparedSentence(packageFacts(p), p.title),
      subtitle: p.evidence_current
        ? "Prepared by Rivet"
        : "Earlier work · sources have since changed",
      state: "done",
      package: p,
      sourceIds: Object.keys(p.sources),
    });
  story.sort(
    (a, b) => Date.parse(a.at) - Date.parse(b.at) || a.id.localeCompare(b.id),
  );
  if (["ready", "prepared"].includes(order.status)) {
    const reply = order.reply_preparation?.prepared_reply;
    if (reply)
      story.push({
        id: "prepared-response",
        orderId: order.id,
        at: reply.created_at || "",
        state: "done",
        reply: true,
        title: `Rivet prepared a cited response using ${reply.coverage.documents_read} ${reply.coverage.documents_read === 1 ? "document" : "documents"}.`,
        subtitle: reply.unresolved.length
          ? `${reply.unresolved.length} open ${reply.unresolved.length === 1 ? "point is" : "points are"} included in the draft.`
          : "Response and supporting sources prepared",
        sourceIds: reply.source_refs.map((s) => s.id),
      });
    story.sort(
      (a, b) =>
        (Date.parse(a.at) || Number.MAX_SAFE_INTEGER) -
          (Date.parse(b.at) || Number.MAX_SAFE_INTEGER) ||
        a.id.localeCompare(b.id),
    );
    story.push({
      id: "current-reply",
      orderId: order.id,
      at: "",
      state: "current",
      reply: true,
      title:
        order.status === "ready"
          ? "Your reply is ready in Outlook."
          : "Your response is ready for a final check.",
      subtitle: "Review and send when you’re ready.",
    });
  } else if (order.status !== "handling")
    story.push({
      id: "current-status",
      orderId: order.id,
      at: "",
      state: "current",
      title:
        order.status === "waiting_customer"
          ? "Waiting for the customer’s response."
          : pmSentence(order.explanation, order.status_label),
    });
  story.push({
    id: "next",
    orderId: order.id,
    at: "",
    state: "upcoming",
    title:
      order.status === "waiting_customer"
        ? "Next: Rivet picks up the customer’s reply and checks what changed."
        : ["ready", "prepared"].includes(order.status)
          ? "Next: send from Outlook. Rivet will watch for the customer’s reply."
          : "Next: Rivet checks new emails and files as they arrive.",
  });
  return story;
}

/** Exactly one authority is requested at a time; the server checks the actor and versions. */
export async function decideItem(
  item: DecisionItem,
  decision: "approve" | "reject",
  request: <T>(path: string, body: unknown) => Promise<T>,
) {
  const card = await request<{
    id: string;
    order_version: number;
    fingerprint: string;
  }>(`/orders/${item.orderId}/approval-cards`, {
    package_id: item.package.id,
    gate: item.gate,
    channel: "outlook",
  });
  if (
    item.version == null ||
    card.order_version !== item.version ||
    card.fingerprint !== item.package.fingerprint
  )
    throw new Error(
      "The order changed since you opened this decision. Refresh and review the current work.",
    );
  return request(`/approval-cards/${card.id}/decision`, { decision });
}
