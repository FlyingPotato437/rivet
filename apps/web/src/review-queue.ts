import { workTime } from "./work-time.ts";

export type ReviewItemIdentity = {
  id: string;
  order_id: string;
  kind: string;
  updated_at: string;
  comment_id?: string;
  proposal_id?: string;
  run_id?: string;
  task_id?: string;
  superseded_by?: string;
};

/** Keep distinct decisions; coalesce only recorded identities, never similar wording. */
export function currentReviewItems<T extends ReviewItemIdentity>(
  items: T[],
): T[] {
  const byAction = new Map<string, T>();
  for (const item of items) {
    if (item.kind === "run" && item.superseded_by) continue;
    const commentReview =
      item.comment_id &&
      ["reference_review", "comment_review", "response"].includes(item.kind);
    const identity = commentReview
      ? `${item.order_id}:comment-review:${item.comment_id}`
      : `${item.order_id}:${item.kind}:${item.run_id && item.kind === "run" ? item.run_id : item.proposal_id || item.task_id || item.id}`;
    const previous = byAction.get(identity);
    if (!previous || workTime(item.updated_at) > workTime(previous.updated_at))
      byAction.set(identity, item);
  }
  return [...byAction.values()];
}

export type WorkOverviewItem = ReviewItemIdentity & {
  status: "needs_review" | "waiting" | "ready";
  origin: string;
  title: string;
  detail: string;
  run_status?: string;
  needs_refresh?: boolean;
  review_required?: boolean;
  prepared_outputs?: number;
  lifecycle?: {
    version: number;
    mode: string;
    open_point: string;
    prepared_label: string;
    packages: {
      id: string;
      status: string;
      fingerprint: string;
      evidence_current: boolean;
    }[];
  };
};

/** Imported comments are recorded context, not a request to re-check every row.
 * Keep human tasks, failed work, and explicit agent questions in the decision queue.
 * This changes presentation only; no comment is marked reviewed or resolved.
 */
export function workOverview<T extends WorkOverviewItem>(items: T[]) {
  const groups = new Map<
    string,
    { lead: T; items: T[]; status: T["status"]; recordedOnly: boolean }
  >();
  for (const item of currentReviewItems(items)) {
    const recorded =
      item.origin === "imported" &&
      ["reference_review", "comment_review", "response"].includes(item.kind);
    const prepared =
      item.kind === "run" &&
      item.run_status === "waiting_for_review" &&
      !item.needs_refresh;
    const status =
      item.review_required === true
        ? "needs_review"
        : recorded || prepared
          ? "ready"
          : item.status;
    const key = item.order_id;
    const group = groups.get(key);
    if (group) {
      group.items.push(item);
      const priority = { needs_review: 0, waiting: 1, ready: 2 };
      if (
        (!recorded && group.recordedOnly) ||
        priority[status] < priority[group.status] ||
        (status === group.status &&
          !recorded &&
          workTime(item.updated_at) > workTime(group.lead.updated_at))
      )
        group.lead = item;
      if (priority[status] < priority[group.status]) group.status = status;
      group.recordedOnly = group.recordedOnly && recorded;
    } else
      groups.set(key, {
        lead: item,
        items: [item],
        status,
        recordedOnly: recorded,
      });
  }
  // A work result already links to the complete source record for that order.
  // Do not show a second 'captured comments' card underneath it.
  return [...groups.values()].map((group) => ({
    ...group,
    items: group.recordedOnly
      ? group.items
      : group.items.filter(
          (item) =>
            !(
              item.origin === "imported" &&
              ["reference_review", "comment_review", "response"].includes(
                item.kind,
              )
            ),
        ),
  }));
}

/** Saved output remains discoverable even when the same order has an open decision. */
export function workInStatus(
  group: { status: WorkOverviewItem["status"]; items: WorkOverviewItem[] },
  status: WorkOverviewItem["status"],
) {
  return (
    group.status === status ||
    (status === "ready" &&
      group.items.some((item) => (item.prepared_outputs || 0) > 0))
  );
}
