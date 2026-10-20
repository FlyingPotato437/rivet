import { workOverview } from "./review-queue.ts";
import type { WorkOverviewItem } from "./review-queue.ts";
import { workTime } from "./work-time.ts";

export type WorkMail = {
  receipt_id?: string | null;
  order_id?: string | null;
  intake_id?: string | null;
  internet_message_id?: string;
  subject: string;
  sender: string;
  outlook_url: string;
  order: string | null;
};
export type WorkActivity = {
  summary: {
    emails_read: number;
    needs_you: number;
    filed_to_orders?: number;
    waiting_in_outlook?: number;
  };
  waiting_in_outlook: (WorkMail & {
    drafted_at: string | null;
    attachments: string[];
    origin?: string;
  })[];
  needs_you: (WorkMail & { kind: string; detail: string })[];
  feed: (WorkMail & {
    at: string | null;
    kind: string;
    detail: string;
    preparation?: { status?: string; detail?: string };
    draft_status?: string;
    outcome?: string;
    learning_status?: string;
    coverage?: { package_id?: string };
  })[];
};
export type WorkOrder = {
  id: string;
  number: string;
  title: string;
  updated_at: string;
};
export type StreamItem = {
  id: string;
  orderId?: string;
  orderNumber: string;
  subject: string;
  sender: string;
  state: "question" | "attention" | "ready" | "working" | "done";
  detail: string;
  at: string;
  outlookUrl: string;
  internetMessageId: string;
  nativeDraft: boolean;
  existingDraft: boolean;
  attachments: string[];
  preparedOutputs: number;
  preparedLabel?: string;
  preparedByAI?: boolean;
  outcomeDetail?: string;
  openPoint?: string;
  openPointTitle?: string;
  source: "mail" | "order" | "system";
  origin: string;
  outcome?: string;
  learningStatus?: string;
};

/** Stable receipts, rather than subjects, keep separate customer returns separate. */
export function buildWorkStream(
  activity: WorkActivity | undefined,
  records: (WorkOverviewItem & {
    order_title?: string;
    order_number?: string;
  })[],
  orders: WorkOrder[],
) {
  const rows = new Map<string, StreamItem>();
  const sentPackages = new Map<string, Set<string>>();
  for (const event of activity?.feed || []) {
    if (
      event.kind === "sent" &&
      event.draft_status === "sent" &&
      ["accepted", "edited"].includes(event.outcome || "") &&
      event.order_id &&
      event.coverage?.package_id
    ) {
      const packages = sentPackages.get(event.order_id) || new Set<string>();
      packages.add(event.coverage.package_id);
      sentPackages.set(event.order_id, packages);
    }
  }
  const orderById = new Map(orders.map((o) => [o.id, o]));
  const byNumber = new Map(orders.map((o) => [o.number, o]));
  const keyFor = (mail: WorkMail) =>
    mail.receipt_id ||
    `${mail.order_id || mail.order || ""}:${mail.internet_message_id || mail.outlook_url || mail.subject}`;
  const make = (mail: WorkMail, at: string | null): StreamItem => {
    const order = mail.order_id
      ? orderById.get(mail.order_id)
      : byNumber.get(mail.order || "");
    return {
      id: keyFor(mail),
      orderId: mail.order_id || order?.id,
      orderNumber: mail.order || order?.number || "",
      subject: mail.subject || "Customer email",
      sender: mail.sender,
      state: "done",
      detail: "Order record updated.",
      at: at || "",
      outlookUrl: mail.outlook_url,
      internetMessageId: mail.internet_message_id || "",
      nativeDraft: false,
      existingDraft: false,
      attachments: [],
      preparedOutputs: 0,
      source: "mail",
      origin: "automatic",
    };
  };
  // Oldest first: the latest state of the same receipt replaces earlier activity.
  for (const event of [...(activity?.feed || [])].sort(
    (a, b) => workTime(a.at) - workTime(b.at),
  )) {
    if (!event.order && !event.order_id) continue;
    const row = make(event, event.at);
    row.detail = event.detail;
    if (
      event.kind === "sent" ||
      ["sent", "discarded", "superseded"].includes(event.draft_status || "")
    ) {
      row.state = "done";
      row.origin = "human";
      row.outcome = event.outcome;
      row.learningStatus = event.learning_status;
    } else if (
      ["queued", "running"].includes(event.preparation?.status || "")
    ) {
      row.state = "working";
      row.detail =
        event.preparation?.detail ||
        "Rivet is reading the sources and preparing the reply.";
    } else if (
      ["blocked", "failed"].includes(event.preparation?.status || "")
    ) {
      row.state = "attention";
      row.detail =
        event.preparation?.detail || "Rivet could not finish this reply.";
    } else if (
      event.preparation?.status === "ready" ||
      event.kind === "drafted"
    ) {
      row.state = "ready";
      row.detail = "Reply prepared. Check its current state in Outlook.";
      row.origin = "ai";
    }
    const previous = rows.get(row.id);
    // Folder reads describe the same incoming mail, never an undo of a send.
    // This also protects terminal facts when timestamps are equal or missing.
    if (!(previous?.outcome && previous.state === "done" && !row.outcome))
      rows.set(row.id, row);
  }
  for (const mail of activity?.waiting_in_outlook || []) {
    const row = make(mail, mail.drafted_at);
    row.state = "ready";
    row.nativeDraft = true;
    row.existingDraft = mail.origin === "existing";
    row.origin = row.existingDraft ? "human" : "ai";
    row.attachments = mail.attachments;
    row.detail = row.existingDraft
      ? "Your Outlook draft is preserved. Rivet’s prepared wording is available in the order."
      : `Reply drafted in Outlook${row.attachments.length ? ` with ${row.attachments.length} ${row.attachments.length === 1 ? "attachment" : "attachments"}` : ""}. Check it and send when ready.`;
    rows.set(row.id, row);
  }
  for (const mail of activity?.needs_you || []) {
    // Match questions have their own inline Yes/No entry; never duplicate it here.
    if (mail.kind === "which_job") continue;
    const id = keyFor(mail);
    const row = rows.get(id) || make(mail, null);
    if (row.state === "done" && row.outcome) {
      // A real Outlook send is authoritative. A stale preparation warning on
      // that receipt cannot put the finished reply back in the active queue.
      row.openPoint = mail.detail;
      row.openPointTitle = "Order follow-up";
      rows.set(id, row);
      continue;
    }
    row.state = "attention";
    row.detail = mail.detail;
    if (!row.orderId) {
      row.source = "system";
      row.subject =
        mail.kind === "permission_required"
          ? "Allow Rivet to save Outlook drafts"
          : mail.subject || "Mailbox needs attention";
    }
    rows.set(id, row);
  }
  const remainingRecords = records.filter((item) => {
    if (
      item.kind !== "lifecycle" ||
      item.status !== "ready" ||
      item.review_required ||
      (item.lifecycle?.open_point || "").trim() ||
      !item.lifecycle?.packages.length
    )
      return true;
    // Coalesce only exact, current approved packages recorded in a sent native
    // reply. Another email's send, or an unsent/newer package, proves nothing
    // about this work. Other tasks and manual drafts remain separate records.
    const sent = sentPackages.get(item.order_id);
    return !item.lifecycle.packages.every(
      (package_) =>
        package_.status === "approved" &&
        package_.evidence_current &&
        sent?.has(package_.id),
    );
  });
  for (const group of workOverview(remainingRecords)) {
    const response = group.items.find(
      (item) => item.kind === "prepared_reply" && item.status === "ready",
    );
    const lifecycle = group.items.find((item) => item.kind === "lifecycle");
    const outcome = lifecycle || response;
    const interrupted =
      group.lead.kind === "run" && group.lead.run_status === "failed";
    // A run title is the full agent request, not a customer-facing task label.
    // Keep the actual failure detail visible without printing the system prompt.
    const openPointTitle =
      group.lead.kind === "run"
        ? interrupted
          ? "Additional preparation needs retry"
          : "Supporting work"
        : group.lead.title;
    const order = orderById.get(group.lead.order_id);
    const active = [...rows.values()]
      .filter(
        (row) => row.orderId === group.lead.order_id && row.state !== "done",
      )
      .sort((a, b) => workTime(b.at) - workTime(a.at))[0];
    const prepared = lifecycle
      ? lifecycle.prepared_outputs || 0
      : group.items.reduce((n, item) => n + (item.prepared_outputs || 0), 0);
    if (active) {
      if (active.state === "working") {
        // Receipt-level preparation is the current operation. Older saved order
        // results and unresolved scope questions cannot turn it into a warning
        // or imply this new email's reply is already prepared.
        if (group.status === "needs_review") {
          active.openPoint = group.lead.detail;
          active.openPointTitle = openPointTitle;
        }
        continue;
      }
      active.preparedOutputs = prepared;
      active.preparedLabel = lifecycle?.lifecycle?.prepared_label;
      active.preparedByAI =
        !lifecycle &&
        group.items.some(
          (item) => item.origin === "ai" && item.prepared_outputs,
        );
      // A completed reply does not resolve a separate engineering or commercial choice.
      if (group.status === "needs_review") {
        active.state = "attention";
        active.detail = group.lead.detail;
      }
      if (outcome) {
        active.outcomeDetail = outcome.detail;
        if (group.status === "needs_review")
          active.openPoint = group.lead.detail;
        if (active.openPoint) active.openPointTitle = openPointTitle;
        if (interrupted && prepared > 0) active.state = "ready";
      }
      if (lifecycle?.lifecycle?.open_point) {
        active.openPoint = [lifecycle.lifecycle.open_point, active.openPoint]
          .filter(Boolean)
          .join(" ");
        active.openPointTitle = "To finish this order";
      }
      continue;
    }
    const sent = [...rows.values()].find(
      (row) =>
        row.orderId === group.lead.order_id &&
        row.state === "done" &&
        !!row.outcome &&
        response &&
        Date.parse(row.at) >= Date.parse(response.updated_at),
    );
    // A reviewed reply stays in the order record; it is not a new task after sending.
    // Separate human decisions or investigations still get their own visible row.
    if (sent && group.items.every((item) => item.kind === "prepared_reply"))
      continue;
    if (group.recordedOnly) continue; // The Orders view holds the raw log.
    const row: StreamItem = {
      id: `order:${group.lead.order_id}`,
      orderId: group.lead.order_id,
      orderNumber: order?.number || group.lead.order_number || "",
      subject: order?.title || group.lead.order_title || "Order work",
      sender: "",
      state:
        group.status === "needs_review"
          ? "attention"
          : group.status === "waiting"
            ? "working"
            : "ready",
      detail: outcome
        ? outcome.detail
        : group.status === "ready"
          ? `${prepared || group.items.length} source-linked ${prepared === 1 ? "draft" : "results"} saved with this order.`
          : group.lead.detail,
      at: group.items.reduce(
        (latest, item) =>
          workTime(item.updated_at) > workTime(latest)
            ? item.updated_at
            : latest,
        group.lead.updated_at,
      ),
      outlookUrl: "",
      internetMessageId: "",
      nativeDraft: false,
      existingDraft: false,
      attachments: [],
      preparedOutputs: prepared,
      preparedLabel: lifecycle?.lifecycle?.prepared_label,
      source: "order",
      origin:
        !lifecycle &&
        prepared &&
        group.items.some(
          (item) => item.origin === "ai" && item.prepared_outputs,
        )
          ? "ai"
          : lifecycle?.origin || group.lead.origin,
      outcomeDetail: outcome?.detail,
      openPoint:
        outcome && group.status === "needs_review"
          ? group.lead.detail
          : undefined,
      openPointTitle:
        outcome && group.status === "needs_review" ? openPointTitle : undefined,
    };
    if (outcome && interrupted && prepared > 0) row.state = "ready";
    if (lifecycle?.lifecycle?.open_point) {
      row.openPoint = [lifecycle.lifecycle.open_point, row.openPoint]
        .filter(Boolean)
        .join(" ");
      row.openPointTitle = "To finish this order";
    }
    rows.set(row.id, row);
  }
  const priority = { question: 0, attention: 0, ready: 1, working: 2, done: 3 };
  return [...rows.values()].sort(
    (a, b) =>
      priority[a.state] - priority[b.state] || workTime(b.at) - workTime(a.at),
  );
}
