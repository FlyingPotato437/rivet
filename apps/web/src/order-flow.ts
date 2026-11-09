export type OrderReviewView = "requests" | "changes" | "reply";

const reviewViews: OrderReviewView[] = ["requests", "changes", "reply"];

/** Read the selected workbench view from Rivet's hash route. */
export function orderReviewFromRoute(route: string): OrderReviewView {
  const query = route.includes("?") ? route.slice(route.indexOf("?") + 1) : "";
  const selected = new URLSearchParams(query).get("review");
  return reviewViews.includes(selected as OrderReviewView)
    ? (selected as OrderReviewView)
    : "requests";
}

export function orderReviewRoute(id: string, view: OrderReviewView): string {
  return `order/${encodeURIComponent(id)}?review=${view}`;
}

export function orderReviewLabel(view: OrderReviewView): string {
  return {
    requests: "Customer requests",
    changes: "Corrected files",
    reply: "Outlook reply",
  }[view];
}

export function outlookReplyStatus(input: {
  lastDirection?: "received" | "sent" | null;
  nativeDraft: boolean;
  threadUrl?: string;
  waitingForCustomer?: boolean;
  lastSentAt?: string | null;
}): string {
  if (input.waitingForCustomer && input.lastSentAt) return "Reply sent";
  if (input.lastDirection === "sent") return "Reply sent";
  if (input.nativeDraft) return "Draft in Outlook";
  if (input.threadUrl) return "Open email thread";
  return "Draft not saved in Outlook";
}

export function drawingReviewNotice(
  run?: {
    current: boolean;
    status: string;
    remaining_issues?: string[];
    totals?: { needs_information?: number };
  } | null,
): string | null {
  if (!run) return null;
  if (
    !run.current ||
    ["failed", "needs_information", "changes_requested"].includes(run.status) ||
    (run.remaining_issues?.length || 0) > 0 ||
    (run.totals?.needs_information || 0) > 0
  )
    return "Drawing review incomplete";
  if (run.status === "queued" || run.status === "running")
    return "Drawing changes are being prepared";
  if (run.status === "ready_for_review")
    return "Drawing review awaits engineer approval";
  return null;
}
