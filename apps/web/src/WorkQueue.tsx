import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowsClockwise,
  CheckCircle,
  Clock,
  FileText,
  GitDiff,
  ListChecks,
  MagnifyingGlass,
  Plus,
  ChatCircleText,
} from "@phosphor-icons/react";
import { api, dateLabel } from "./api";
import { Busy, Empty, ErrorNote } from "./ui";
import type { WorkQueueItem, WorkQueueView } from "./coordination-types";
import "./work-queue.css";

type QueueStatus = WorkQueueItem["status"];
const labels: Record<QueueStatus, string> = {
  attention: "Needs you",
  waiting: "Waiting on a reply",
  review: "Ready to review",
  processing: "In progress",
  complete: "Complete",
};
const rank: Record<QueueStatus, number> = {
  attention: 0,
  review: 1,
  processing: 2,
  waiting: 3,
  complete: 4,
};
const icons = {
  clarification: ChatCircleText,
  proposal: GitDiff,
  run: ListChecks,
  document: FileText,
  review: CheckCircle,
  setup: Plus,
};
const actionFor = (item: WorkQueueItem) =>
  item.kind === "clarification"
    ? item.status === "waiting"
      ? "Record reply"
      : "Open issue"
    : item.kind === "proposal"
      ? "Review change"
      : item.kind === "document"
        ? "Check document"
        : item.kind === "setup"
          ? "Add quote"
          : item.status === "complete"
            ? "Open quote"
            : item.kind === "run"
              ? "View progress"
              : "Review quote";

export function WorkQueue({
  navigate,
  onNew,
}: {
  navigate: (to: string) => void;
  onNew: () => void;
}) {
  const [filter, setFilter] = useState<"open" | QueueStatus>("open");
  const [search, setSearch] = useState("");
  const queue = useQuery({
    queryKey: ["work-queue"],
    queryFn: () => api<WorkQueueView>("/work-queue"),
    refetchInterval: 4000,
  });
  const items = queue.data?.items ?? [];
  const counts = queue.data?.counts ?? {
    attention: 0,
    waiting: 0,
    review: 0,
    processing: 0,
    complete: 0,
  };
  const visible = items
    .filter(
      (item) =>
        (filter === "open"
          ? item.status !== "complete"
          : item.status === filter) &&
        `${item.title} ${item.detail} ${item.project_title} ${item.customer} ${item.quote_number}`
          .toLowerCase()
          .includes(search.trim().toLowerCase()),
    )
    .sort(
      (a, b) =>
        rank[a.status] - rank[b.status] ||
        (a.due_date ?? "9999").localeCompare(b.due_date ?? "9999") ||
        b.updated_at.localeCompare(a.updated_at),
    );
  const open = (item: WorkQueueItem) =>
    navigate(
      `project/${item.project_id}?${item.target}${item.clarification_id ? `&issue=${encodeURIComponent(item.clarification_id)}` : ""}`,
    );
  return (
    <main className="page work-queue-page">
      <div className="page-heading">
        <div>
          <span className="queue-focus">
            Data center bids <span>·</span> Low-voltage switchgear
          </span>
          <h1>Keep the bid moving.</h1>
          <p>Rivet prepares the work. Your team makes the decisions.</p>
        </div>
        <button className="primary" onClick={onNew}>
          <Plus size={17} />
          New bid
        </button>
      </div>
      <div className="queue-overview" aria-label="Work summary">
        {(
          ["attention", "waiting", "review", "processing"] as QueueStatus[]
        ).map((status) => (
          <button
            key={status}
            className={filter === status ? "selected" : ""}
            aria-pressed={filter === status}
            onClick={() => setFilter(filter === status ? "open" : status)}
          >
            <span>{labels[status]}</span>
            <strong>{counts[status]}</strong>
            <ArrowRight size={16} />
          </button>
        ))}
      </div>
      <section className="queue-inbox" aria-labelledby="queue-heading">
        <div className="queue-toolbar">
          <div>
            <h2 id="queue-heading">
              {filter === "open" ? "Work queue" : labels[filter]}
            </h2>
            <span>
              {visible.length} {visible.length === 1 ? "item" : "items"}
            </span>
          </div>
          <div className="queue-tools">
            <label className="queue-search">
              <MagnifyingGlass size={16} />
              <input
                aria-label="Search work queue"
                placeholder="Find a bid or issue…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <select
              aria-label="Filter work queue"
              value={filter}
              onChange={(e) => setFilter(e.target.value as typeof filter)}
            >
              <option value="open">All active work</option>
              {(Object.keys(labels) as QueueStatus[]).map((status) => (
                <option key={status} value={status}>
                  {labels[status]}
                </option>
              ))}
            </select>
            <button
              className="icon-button queue-refresh"
              aria-label="Refresh work queue"
              onClick={() => void queue.refetch()}
              disabled={queue.isFetching}
            >
              <ArrowsClockwise size={17} />
            </button>
          </div>
        </div>
        {queue.isError ? (
          <div className="queue-message">
            <ErrorNote message={(queue.error as Error).message} />
            <button className="secondary" onClick={() => void queue.refetch()}>
              Try again
            </button>
          </div>
        ) : queue.isLoading ? (
          <div className="queue-message">
            <Busy text="Gathering the next actions…" />
          </div>
        ) : visible.length ? (
          <>
            <div className="queue-columns" aria-hidden="true">
              <span>Next action</span>
              <span>Bid</span>
              <span>Status</span>
              <span />
            </div>
            <div className="queue-rows">
              {visible.map((item) => {
                const Icon = icons[item.kind] ?? ListChecks;
                return (
                  <button
                    className="queue-row"
                    key={item.id}
                    onClick={() => open(item)}
                    aria-label={`${item.title} — ${item.project_title}`}
                  >
                    <span className={`queue-item-icon is-${item.status}`}>
                      <Icon size={20} />
                    </span>
                    <span className="queue-item-work">
                      <strong>{item.title}</strong>
                      <span>{item.detail}</span>
                    </span>
                    <span className="queue-item-project">
                      <strong>{item.project_title}</strong>
                      <span>{item.customer}</span>
                      {item.due_date && (
                        <small>
                          <Clock size={11} />
                          Due {dateLabel(item.due_date)}
                        </small>
                      )}
                    </span>
                    <span className={`queue-item-state is-${item.status}`}>
                      <i />
                      {labels[item.status]}
                    </span>
                    <span className="queue-item-action">
                      {actionFor(item)}
                      <ArrowRight size={15} />
                    </span>
                  </button>
                );
              })}
            </div>
          </>
        ) : (
          <div className="queue-empty">
            <Empty
              title={
                search
                  ? "No matching work"
                  : filter === "complete"
                    ? "No completed bids yet"
                    : filter !== "open"
                      ? `Nothing ${filter === "attention" ? "needs your attention" : filter === "review" ? "is ready for review" : filter === "waiting" ? "is waiting for a reply" : "is in progress"}`
                      : items.length
                        ? "Your active queue is clear"
                        : "Start with a quote that changed"
              }
              icon={<ListChecks size={31} />}
              action={
                <button
                  className="secondary"
                  onClick={
                    search || filter !== "open"
                      ? () => {
                          setSearch("");
                          setFilter("open");
                        }
                      : items.length
                        ? () => navigate("quotes")
                        : onNew
                  }
                >
                  {search || filter !== "open"
                    ? "Show all active work"
                    : items.length
                      ? "View quotes"
                      : "Create a bid"}
                </button>
              }
            >
              {search
                ? "Try a bid name, customer, or a few words from the issue."
                : "Add the existing quote and the new project change. Review the proposed updates, track the open questions, and pick up where each reply leaves off."}
            </Empty>
          </div>
        )}
      </section>
      <div className="queue-footer">
        <span>
          <CheckCircle size={15} />
          Product selections and commercial changes stay subject to your
          approval.
        </span>
        <button className="text-button" onClick={() => navigate("quotes")}>
          All quotes
          <ArrowRight size={14} />
        </button>
      </div>
    </main>
  );
}
