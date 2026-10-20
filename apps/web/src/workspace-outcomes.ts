import type { ProjectFlow } from "./project-flow";

export function orderOutcome(
  order: Pick<ProjectFlow, "status" | "outlook" | "last_sent_at">,
) {
  if (order.status === "ready" && order.outlook.native)
    return {
      label: "Ready in Outlook",
      tone: "ready",
      title: "Your reply is already in Outlook.",
      detail:
        "The unsent draft is in the conversation. Review it and send from Outlook.",
    };
  if (order.status === "prepared" || order.status === "ready")
    return {
      label: "Response prepared",
      tone: "prepared",
      title: "The response and its sources are ready.",
      detail:
        "Read the prepared response, or save it to Outlook when a mailbox is connected.",
    };
  if (order.status === "handling")
    return {
      label: "In progress",
      tone: "working",
      title: "Rivet is working on this order.",
      detail:
        "The latest work appears here as it finishes. You can keep working in Outlook.",
    };
  if (order.status === "waiting_customer" && order.last_sent_at)
    return {
      label: "Waiting for customer",
      tone: "waiting",
      title: "Your reply was sent. Rivet is watching for the answer.",
      detail: "The next customer message will resume work on this order.",
    };
  if (order.status === "needs_you")
    return {
      label: "Needs your input",
      tone: "attention",
      title: "One part of the order needs your input.",
      detail: "See the specific decision or missing evidence below.",
    };
  return {
    label: "Watching for updates",
    tone: "watching",
    title: "Order documents and history, in one place.",
    detail: "Rivet checks new order mail and connected files as they arrive.",
  };
}

export function todayWorkSummary(input: {
  ready: number;
  prepared: number;
  decisions: number;
  working: number;
}) {
  const parts: string[] = [];
  if (input.ready)
    parts.push(
      `${input.ready} ${input.ready === 1 ? "reply draft" : "reply drafts"} ready in Outlook`,
    );
  if (input.prepared)
    parts.push(
      `${input.prepared} ${input.prepared === 1 ? "response" : "responses"} prepared from files`,
    );
  if (input.decisions)
    parts.push(
      `${input.decisions} ${input.decisions === 1 ? "decision needs" : "decisions need"} you`,
    );
  if (!parts.length && input.working)
    return `Rivet is working on ${input.working} ${input.working === 1 ? "order" : "orders"}.`;
  return parts.length
    ? parts.join(" · ") + "."
    : "Nothing needs your attention right now.";
}

export function replyPreview(
  order: Pick<ProjectFlow, "reply_preparation">,
  maximum = 260,
) {
  const body = order.reply_preparation?.prepared_reply?.body || "";
  const text = body
    .replace(/^\s*(?:hello|hi|dear)[^\n]*\n+/i, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[#*_`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > maximum ? text.slice(0, maximum).trimEnd() + "…" : text;
}
